"""Trade plan construction.

Levels come from structure first and volatility second: a stop belongs
*behind an invalidation point*, not at an arbitrary ATR multiple. The ATR
floor exists only so a stop is never placed inside the noise band.
"""
from __future__ import annotations

from typing import List, Optional, Tuple

from .. import runtime_settings
from ..instruments import Instrument
from ..schemas.analysis import (
    Decision, IndicatorSnapshot, MarketStructure, TradePlan,
)
from ..schemas.drawings import DrawingAnalytics

MIN_STOP_ATR = 0.9
MAX_STOP_ATR = 3.2
STOP_BUFFER_ATR = 0.35


def _candidate_supports(structure: MarketStructure, price: float,
                        drawings: List[DrawingAnalytics]) -> List[float]:
    levels: List[float] = [l for l in structure.support_levels if l < price]
    levels += [s.price for s in structure.swings
               if s.kind == "swing_low" and s.price < price][-3:]
    levels += [z.bottom for z in structure.demand_zones + structure.order_blocks
               if z.direction == "bullish" and z.bottom < price]
    levels += [z.bottom for z in structure.fair_value_gaps
               if z.direction == "bullish" and z.bottom < price]
    levels += [d.current_value for d in drawings
               if d.role in ("support", "demand") and d.current_value
               and d.current_value < price and d.validity != "invalidated"]
    if structure.range_low and structure.range_low < price:
        levels.append(structure.range_low)
    return sorted({round(l, 10) for l in levels if l and l > 0}, reverse=True)


def _candidate_resistances(structure: MarketStructure, price: float,
                           drawings: List[DrawingAnalytics]) -> List[float]:
    levels: List[float] = [l for l in structure.resistance_levels if l > price]
    levels += [s.price for s in structure.swings
               if s.kind == "swing_high" and s.price > price][-3:]
    levels += [z.top for z in structure.supply_zones + structure.order_blocks
               if z.direction == "bearish" and z.top > price]
    levels += [z.top for z in structure.fair_value_gaps
               if z.direction == "bearish" and z.top > price]
    levels += [d.current_value for d in drawings
               if d.role in ("resistance", "supply") and d.current_value
               and d.current_value > price and d.validity != "invalidated"]
    levels += [z.mid for z in structure.liquidity_pools if z.mid > price]
    if structure.range_high and structure.range_high > price:
        levels.append(structure.range_high)
    return sorted({round(l, 10) for l in levels if l and l > 0})


def _pick_stop(direction: str, price: float, atr: float,
               candidates: List[float]) -> Tuple[float, str]:
    min_dist = atr * MIN_STOP_ATR
    max_dist = atr * MAX_STOP_ATR
    buffer = atr * STOP_BUFFER_ATR

    for level in candidates:
        distance = abs(price - level) + buffer
        if min_dist <= distance <= max_dist:
            stop = level - buffer if direction == "BUY" else level + buffer
            return stop, "behind structural level {:.5f}".format(level)

    stop = price - min_dist * 1.4 if direction == "BUY" else price + min_dist * 1.4
    return stop, "volatility stop ({:.1f} ATR) — no clean structure in range".format(
        MIN_STOP_ATR * 1.4)


def _pick_targets(direction: str, price: float, stop: float, atr: float,
                  candidates: List[float], min_rr: float = 1.5
                  ) -> Tuple[Optional[float], Optional[float], str]:
    risk = abs(price - stop)
    if risk <= 0:
        return None, None, "no risk distance"

    viable = [
        level for level in candidates
        if (level - price if direction == "BUY" else price - level) >= risk * min_rr
    ]
    if viable:
        tp1 = viable[0]
        tp2 = viable[1] if len(viable) > 1 else (
            price + risk * 3.0 if direction == "BUY" else price - risk * 3.0)
        return tp1, tp2, "first target at structural level {:.5f}".format(tp1)

    tp1 = price + risk * 2.0 if direction == "BUY" else price - risk * 2.0
    tp2 = price + risk * 3.0 if direction == "BUY" else price - risk * 3.0
    return tp1, tp2, "measured 2R/3R target — no structure within reach"


def build(decision: Decision, price: float, snap: IndicatorSnapshot,
          structure: MarketStructure, instrument: Instrument,
          drawings: Optional[List[DrawingAnalytics]] = None,
          spread_pips: Optional[float] = None,
          balance: Optional[float] = None,
          risk_pct: Optional[float] = None) -> TradePlan:
    drawings = drawings or []
    atr = snap.atr or (price * 0.002)
    cfg = runtime_settings.get()
    balance = balance if balance is not None else cfg.account_balance
    risk_pct = risk_pct if risk_pct is not None else cfg.risk_per_trade_pct

    plan = TradePlan(
        decision=decision,
        atr_pips=snap.atr_pips,
        spread_pips=spread_pips if spread_pips is not None else instrument.typical_spread_pips,
        account_balance=round(balance, 2),
    )

    if decision == Decision.WAIT:
        supports = _candidate_supports(structure, price, drawings)
        resistances = _candidate_resistances(structure, price, drawings)
        plan.expected_move_pips = round(instrument.to_pips(atr * 1.5), 1)
        if supports:
            plan.position_notes.append(
                "Watch support {:.5f} ({:.1f} pips below)".format(
                    supports[0], instrument.to_pips(price - supports[0])))
        if resistances:
            plan.position_notes.append(
                "Watch resistance {:.5f} ({:.1f} pips above)".format(
                    resistances[0], instrument.to_pips(resistances[0] - price)))
        plan.position_notes.append("No position sized — waiting for confirmation")
        return plan

    direction = decision.value
    candidates_stop = (_candidate_supports(structure, price, drawings) if direction == "BUY"
                       else _candidate_resistances(structure, price, drawings))
    candidates_target = (_candidate_resistances(structure, price, drawings) if direction == "BUY"
                         else _candidate_supports(structure, price, drawings))

    stop, stop_reason = _pick_stop(direction, price, atr, candidates_stop)
    tp1, tp2, target_reason = _pick_targets(direction, price, stop, atr, candidates_target)

    plan.entry = round(price, instrument.digits)
    plan.stop_loss = round(stop, instrument.digits)
    plan.take_profit = round(tp1, instrument.digits) if tp1 else None
    plan.take_profit_2 = round(tp2, instrument.digits) if tp2 else None

    risk = abs(plan.entry - plan.stop_loss)
    plan.risk_pips = round(instrument.to_pips(risk), 1)
    if plan.take_profit is not None:
        reward = abs(plan.take_profit - plan.entry)
        plan.reward_pips = round(instrument.to_pips(reward), 1)
        plan.risk_reward = round(reward / risk, 2) if risk > 0 else None
        plan.expected_move_pips = plan.reward_pips

    # Position sizing: risk a fixed fraction of the account over the stop distance.
    risk_amount = balance * (risk_pct / 100.0)
    plan.risk_amount = round(risk_amount, 2)
    pip_value = instrument.pip_value_per_lot
    if plan.risk_pips and pip_value > 0:
        lots = risk_amount / (plan.risk_pips * pip_value)
        plan.suggested_lots = round(max(0.01, lots), 2)

    plan.position_notes.append("Stop {}".format(stop_reason))
    plan.position_notes.append(target_reason)
    plan.position_notes.append(
        "Sized for {:.1f}% risk ({} {:.2f}) over a {:.1f} pip stop".format(
            risk_pct, cfg.account_currency, risk_amount, plan.risk_pips or 0.0))

    if plan.spread_pips and plan.risk_pips and plan.spread_pips > plan.risk_pips * 0.12:
        plan.position_notes.append(
            "Spread is {:.1f} pips — {:.0f}% of stop distance, materially degrades edge".format(
                plan.spread_pips, plan.spread_pips / plan.risk_pips * 100))

    if plan.risk_reward is not None and plan.risk_reward < 1.5:
        plan.position_notes.append(
            "R:R below 1.5 — the structural target does not justify the risk here")

    return plan
