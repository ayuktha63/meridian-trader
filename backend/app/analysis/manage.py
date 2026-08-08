"""Open-position management.

Entry analysis and position management are different jobs. Entry asks "is
there an edge here?"; management asks "is the reason I took this trade still
true?" — and the honest default answer is **hold until the stop or the target
is hit**. Most money lost by otherwise-competent traders is lost by overriding
that default: cutting winners early, widening stops, exiting on a red candle.

So this engine is deliberately reluctant. It returns HOLD unless something
structural has changed, and every non-HOLD answer has to name the specific
thing that changed. It will never suggest widening a stop.
"""
from __future__ import annotations

from typing import List, Optional

import numpy as np
import pandas as pd

from ..instruments import Instrument
from ..schemas.analysis import (
    Bias, ConfidenceBreakdown, Decision, IndicatorSnapshot, MarketStructure,
)
from ..schemas.positions import ManagementAction, ManagementSignal, Position, PositionSide

#: A position that has gone this many bars without reaching 0.3R is not
#: working. It is not losing either, which is exactly why it is easy to sit in.
STALE_BARS = 40
STALE_PROGRESS_R = 0.3

#: Profit at which protecting capital starts to matter more than squeezing
#: the last tick out of the move.
BREAKEVEN_TRIGGER_R = 1.0
PARTIAL_TRIGGER_R = 1.8


def _bar_index_for(df: pd.DataFrame, bar_time: int) -> int:
    if df.empty or bar_time <= 0:
        return max(0, len(df) - 1)
    times = df["time"].to_numpy()
    return int(np.searchsorted(times, bar_time, side="left"))


def evaluate(position: Position, df: pd.DataFrame, price: float,
             structure: MarketStructure, indicators: IndicatorSnapshot,
             confidence: ConfidenceBreakdown, decision: Decision,
             instrument: Instrument) -> ManagementSignal:
    long = position.side == PositionSide.LONG
    entry = position.entry
    stop = position.stop_loss
    target = position.take_profit

    # The risk unit is fixed at entry. Using `entry - stop` here would make R
    # shrink as protection is taken — a trade whose stop reached breakeven
    # would report an undefined R and be declared unmanageable at precisely
    # the moment it was working.
    risk = position.risk_unit
    signal = ManagementSignal(position_id=position.id, current_price=price)
    if risk <= 0:
        signal.action = ManagementAction.EXIT
        signal.headline = "Stop equals entry — this position has no defined risk"
        signal.reasons.append("Cannot manage a position without a valid stop level.")
        signal.thesis_intact = False
        return signal

    move = (price - entry) if long else (entry - price)
    signal.unrealised_pips = round(instrument.to_pips(move), 1)
    signal.r_multiple = round(move / risk, 2)
    signal.distance_to_stop_pips = round(instrument.to_pips(abs(price - stop)), 1)
    if target is not None:
        signal.distance_to_target_pips = round(instrument.to_pips(abs(target - price)), 1)

    entry_index = _bar_index_for(df, position.opened_bar_time)
    since = df.iloc[entry_index:] if entry_index < len(df) else df.iloc[-1:]
    signal.bars_held = max(0, len(since) - 1)

    # Best excursion so far — the number that tells you whether you gave back
    # a winner.
    if not since.empty:
        best = float(since["high"].max()) if long else float(since["low"].min())
        best_move = (best - entry) if long else (entry - best)
        signal.max_favourable_r = round(best_move / risk, 2)

    atr = indicators.atr or risk
    reasons: List[str] = []
    cautions: List[str] = []

    # ── 1. terminal outcomes ─────────────────────────────────
    breached = (float(since["low"].min()) <= stop) if long else (float(since["high"].max()) >= stop)
    if breached and not since.empty:
        signal.action = ManagementAction.STOPPED
        signal.urgency = "immediate"
        signal.headline = "Stop was hit — you should be flat"
        signal.reasons = [
            "Price traded through {:.{d}f} after you entered.".format(stop, d=instrument.digits),
            "If your broker stop was in the book you are already out. If it was a "
            "mental stop, close the position now and do not re-enter to 'get it back'.",
        ]
        signal.thesis_intact = False
        signal.invalidation = "Already invalidated."
        return signal

    if target is not None:
        reached = (float(since["high"].max()) >= target) if long else (float(since["low"].min()) <= target)
        if reached:
            signal.action = ManagementAction.TARGET_HIT
            signal.urgency = "elevated"
            signal.headline = "Target reached — take the trade off, or trail the rest"
            signal.reasons = [
                "Price reached {:.{d}f}, the level this trade was taken for.".format(
                    target, d=instrument.digits),
                "The plan is complete. Anything beyond this is a new trade and "
                "needs its own reason.",
            ]
            if position.take_profit_2:
                signal.reasons.append(
                    "If you are running a second target at {:.{d}f}, close most of "
                    "the position here and trail the remainder.".format(
                        position.take_profit_2, d=instrument.digits))
                signal.reduce_fraction = 0.7
            signal.suggested_stop = round(entry, instrument.digits)
            signal.suggested_stop_reason = "Never let a position that hit target become a loss."
            return signal

    # ── 2. has the structural reason gone? ───────────────────
    opposing = Bias.BEARISH if long else Bias.BULLISH
    choch = structure.last_choch
    if (choch is not None and choch.index >= entry_index
            and choch.direction == opposing.value):
        signal.action = ManagementAction.EXIT
        signal.urgency = "immediate"
        signal.headline = "Structure flipped against you — the reason for this trade is gone"
        signal.reasons = [
            "A {} change of character printed {} bars ago at {:.{d}f}, after you "
            "entered.".format(choch.direction, choch.bars_ago, choch.price,
                              d=instrument.digits),
            "Structure turning against an open position is the cleanest exit "
            "signal there is — it does not require the stop to be hit first.",
        ]
        if signal.r_multiple > 0:
            signal.reasons.append(
                "You are {:+.2f}R. Taking it here beats giving it back to the stop."
                .format(signal.r_multiple))
        signal.thesis_intact = False
        signal.invalidation = "Already invalidated by the structure break."
        return signal

    # A fresh BOS against the position is nearly as decisive.
    bos = structure.last_bos
    if (bos is not None and bos.index >= entry_index
            and bos.direction == opposing.value and bos.bars_ago <= 5):
        signal.action = ManagementAction.EXIT
        signal.urgency = "elevated"
        signal.headline = "Break of structure against your position"
        signal.reasons = [
            "A {} break of structure printed {} bars ago at {:.{d}f}.".format(
                bos.direction, bos.bars_ago, bos.price, d=instrument.digits),
            "Price is making progress in the opposite direction to your trade.",
        ]
        signal.thesis_intact = False
        return signal

    # ── 3. the engine now reads the other way ────────────────
    engine_opposes = (
        (long and decision == Decision.SELL) or (not long and decision == Decision.BUY)
    )
    if engine_opposes and confidence.final_confidence >= 55 and not confidence.conflict:
        signal.action = ManagementAction.EXIT if signal.r_multiple < 0.5 else ManagementAction.TAKE_PARTIAL
        signal.urgency = "elevated"
        signal.reduce_fraction = 0.5 if signal.action == ManagementAction.TAKE_PARTIAL else None
        signal.headline = (
            "Fresh evidence now points the other way"
            if signal.action == ManagementAction.EXIT
            else "Evidence has turned — bank half and tighten the rest")
        signal.reasons = [
            "The engine now reads {} at {:.0f}% confidence with {:.0f}% component "
            "agreement.".format(decision.value, confidence.final_confidence,
                                confidence.agreement * 100),
            confidence.reason,
        ]
        signal.thesis_intact = False
        return signal

    # ── 4. protect a winner ──────────────────────────────────
    stop_behind_entry = (stop < entry) if long else (stop > entry)

    if signal.r_multiple >= PARTIAL_TRIGGER_R:
        into_opposing_zone = _into_opposing_zone(structure, price, long, atr)
        fading = _momentum_fading(indicators, long)
        if into_opposing_zone or fading:
            signal.action = ManagementAction.TAKE_PARTIAL
            signal.urgency = "normal"
            signal.reduce_fraction = 0.5
            signal.headline = "Up {:+.2f}R into resistance — bank part of it".format(
                signal.r_multiple)
            if into_opposing_zone:
                reasons.append(into_opposing_zone)
            if fading:
                reasons.append(fading)
            reasons.append(
                "Taking half here locks in the move and leaves the rest free to run "
                "to target with a stop that cannot lose.")
            signal.suggested_stop = round(entry, instrument.digits)
            signal.suggested_stop_reason = "Breakeven on the remainder."
            signal.reasons = reasons
            signal.invalidation = "A close back through {:.{d}f} ends the trade.".format(
                entry, d=instrument.digits)
            return signal

    if signal.r_multiple >= BREAKEVEN_TRIGGER_R and stop_behind_entry:
        signal.action = ManagementAction.TRAIL_STOP
        signal.headline = "Up {:+.2f}R — move the stop to breakeven".format(signal.r_multiple)
        signal.suggested_stop = round(entry, instrument.digits)
        signal.suggested_stop_reason = (
            "At 1R the trade has proved itself. A breakeven stop turns the worst "
            "case from a full loss into nothing.")
        signal.reasons = [
            "Unrealised {:+.1f} pips ({:+.2f}R).".format(
                signal.unrealised_pips, signal.r_multiple),
            "Move the broker stop, not a mental one. Then leave it alone.",
        ]
        signal.invalidation = "Breakeven stop at {:.{d}f}.".format(entry, d=instrument.digits)
        return signal

    # Trail behind newly-formed structure once well in profit.
    trail = _structural_trail(structure, long, entry, stop, atr, instrument)
    if trail is not None and signal.r_multiple >= BREAKEVEN_TRIGGER_R:
        signal.action = ManagementAction.TRAIL_STOP
        signal.headline = "Trail the stop behind the new swing"
        signal.suggested_stop = trail[0]
        signal.suggested_stop_reason = trail[1]
        signal.reasons = [
            trail[1],
            "Trailing behind structure keeps you in the move while the trend holds, "
            "and takes you out when it stops making progress.",
        ]
        return signal

    # ── 5. going nowhere ─────────────────────────────────────
    if signal.bars_held >= STALE_BARS and abs(signal.r_multiple) < STALE_PROGRESS_R:
        signal.action = ManagementAction.EXIT
        signal.urgency = "normal"
        signal.headline = "{} bars with no progress — the setup did not work".format(
            signal.bars_held)
        signal.reasons = [
            "Open {} bars and still only {:+.2f}R.".format(
                signal.bars_held, signal.r_multiple),
            "A setup that was going to work usually works quickly. Holding a flat "
            "position ties up margin and attention for no expected return.",
            "Close it and wait for a cleaner signal rather than hoping this one wakes up.",
        ]
        signal.thesis_intact = False
        return signal

    # ── 6. default: hold ─────────────────────────────────────
    signal.action = ManagementAction.HOLD
    same_side = ((long and structure.trend == Bias.BULLISH)
                 or (not long and structure.trend == Bias.BEARISH))
    if same_side:
        reasons.append("Structure is still {} — the reason you entered is intact.".format(
            structure.structure_label))
    else:
        reasons.append("Structure reads {} but nothing has broken against you yet.".format(
            structure.structure_label))

    if signal.r_multiple >= 0:
        reasons.append("Currently {:+.1f} pips ({:+.2f}R), stop {:.1f} pips away.".format(
            signal.unrealised_pips, signal.r_multiple, signal.distance_to_stop_pips))
    else:
        reasons.append(
            "Currently {:+.1f} pips ({:+.2f}R). Drawdown inside the planned risk is "
            "normal — it is what the stop is for.".format(
                signal.unrealised_pips, signal.r_multiple))

    if target is not None:
        reasons.append("Target {:.{d}f} is {:.1f} pips away.".format(
            target, signal.distance_to_target_pips, d=instrument.digits))

    signal.headline = "Hold — let the stop and target do their job"
    signal.reasons = reasons

    if signal.max_favourable_r >= 1.5 and signal.r_multiple < signal.max_favourable_r - 1.0:
        cautions.append(
            "This trade was {:+.2f}R at its best and is now {:+.2f}R. If that keeps "
            "happening, your targets are too far or your trailing is too loose."
            .format(signal.max_favourable_r, signal.r_multiple))

    if indicators.relative_volume is not None and indicators.relative_volume < 0.6:
        cautions.append("Volume has thinned — moves from here are less reliable.")

    opposing_zone = _into_opposing_zone(structure, price, long, atr)
    if opposing_zone:
        cautions.append(opposing_zone)

    signal.cautions = cautions
    signal.invalidation = "A close beyond {:.{d}f} ends this trade.".format(
        stop, d=instrument.digits)
    return signal


# ── helpers ───────────────────────────────────────────────────

def _into_opposing_zone(structure: MarketStructure, price: float, long: bool,
                        atr: float) -> Optional[str]:
    """Is price running into something that has turned it before?"""
    zones = (structure.supply_zones + [z for z in structure.order_blocks
                                       if z.direction == "bearish"]) if long else (
        structure.demand_zones + [z for z in structure.order_blocks
                                  if z.direction == "bullish"])
    for zone in zones:
        gap = (zone.bottom - price) if long else (price - zone.top)
        if -0.2 * atr <= gap <= 0.8 * atr:
            return "Price is running into a {} zone at {:.5f}–{:.5f}.".format(
                zone.kind.replace("_", " "), zone.bottom, zone.top)

    pools = [z for z in structure.liquidity_pools
             if (z.mid > price if long else z.mid < price)]
    for pool in pools[:2]:
        if abs(pool.mid - price) <= 0.6 * atr:
            return "Liquidity resting just ahead at {:.5f} — {}.".format(pool.mid, pool.note)
    return None


def _momentum_fading(indicators: IndicatorSnapshot, long: bool) -> Optional[str]:
    if indicators.rsi_divergence:
        if long and indicators.rsi_divergence.startswith("bearish"):
            return "Bearish RSI divergence against the move."
        if not long and indicators.rsi_divergence.startswith("bullish"):
            return "Bullish RSI divergence against the move."

    state = indicators.macd_state or ""
    if long and state == "bullish_fading":
        return "MACD histogram is contracting — buying pressure is easing."
    if not long and state == "bearish_fading":
        return "MACD histogram is contracting — selling pressure is easing."

    if indicators.rsi is not None:
        if long and indicators.rsi > 75:
            return "RSI {:.0f} — stretched, and mean reversion gets more likely from here.".format(
                indicators.rsi)
        if not long and indicators.rsi < 25:
            return "RSI {:.0f} — stretched, and mean reversion gets more likely from here.".format(
                indicators.rsi)
    return None


def _structural_trail(structure: MarketStructure, long: bool, entry: float,
                      current_stop: float, atr: float, instrument: Instrument):
    """Propose a stop behind the most recent confirmed swing — never wider."""
    swings = [s for s in structure.swings
              if s.kind == ("swing_low" if long else "swing_high")]
    if not swings:
        return None

    anchor = swings[-1].price
    buffer = atr * 0.35
    proposed = anchor - buffer if long else anchor + buffer

    # Only ever tighten, and only above breakeven.
    improves = (proposed > current_stop) if long else (proposed < current_stop)
    beyond_entry = (proposed >= entry) if long else (proposed <= entry)
    if not (improves and beyond_entry):
        return None

    return (
        round(proposed, instrument.digits),
        "Most recent swing {} sits at {:.{d}f}; a stop just beyond it keeps you in "
        "while the trend keeps making higher {}.".format(
            "low" if long else "high", anchor,
            "lows" if long else "highs", d=instrument.digits),
    )
