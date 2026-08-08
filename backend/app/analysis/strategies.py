"""Named strategies, evaluated side by side.

The single fused verdict answers "what does the weight of evidence say". This
module answers a different and often more useful question: **which style of
trade is the market currently offering?** A market can be terrible for
breakouts and excellent for mean reversion at the same moment, and a single
score hides that.

Five strategies run on every analysis. Each declares its own preconditions and
reports which ones are met, so a strategy that does not fire still tells you
what it is waiting for. Each also emits a *scenario path* — where price goes
if that thesis is right — which is what the chart draws. It is a conditional,
not a prediction: no strategy here knows the future, and the paths disagree
with each other on purpose.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

import numpy as np
import pandas as pd

from ..instruments import Instrument, get_timeframe
from ..schemas.analysis import Bias, IndicatorSnapshot, MarketStructure, MTFReport, PatternHit
from ..schemas.strategies import Condition, ScenarioPoint, StrategyReport, StrategyResult


@dataclass
class Ctx:
    df: pd.DataFrame
    price: float
    snap: IndicatorSnapshot
    structure: MarketStructure
    mtf: MTFReport
    patterns: List[PatternHit]
    candles: List[PatternHit]
    instrument: Instrument
    atr: float
    timeframe: str

    @property
    def bar_seconds(self) -> int:
        return get_timeframe(self.timeframe).seconds

    @property
    def last_time(self) -> int:
        return int(self.df["time"].iloc[-1])


Strategy = Callable[[Ctx], StrategyResult]
_REGISTRY: List[Strategy] = []


def strategy(fn: Strategy) -> Strategy:
    _REGISTRY.append(fn)
    return fn


# ── shared helpers ────────────────────────────────────────────

def _score(conditions: List[Condition]) -> float:
    total = sum(c.weight for c in conditions) or 1.0
    met = sum(c.weight for c in conditions if c.met)
    return round(met / total * 100.0, 1)


def _finish(result: StrategyResult, ctx: Ctx, conditions: List[Condition],
            long: bool, entry: float, stop: float, target: float,
            horizon: int) -> StrategyResult:
    """Attach conviction, levels and the two scenario paths."""
    result.conditions = conditions
    result.conditions_total = len(conditions)
    result.conditions_met = sum(1 for c in conditions if c.met)
    result.conviction = _score(conditions)
    result.waiting_for = [c.name for c in conditions if not c.met]

    digits = ctx.instrument.digits
    risk = abs(entry - stop)
    if risk <= 0:
        result.stance = "flat"
        result.note = "No valid risk distance at current price."
        return result

    result.entry = round(entry, digits)
    result.stop_loss = round(stop, digits)
    result.take_profit = round(target, digits)
    result.take_profit_2 = round(
        entry + (target - entry) * 1.7 if long else entry - (entry - target) * 1.7, digits)
    result.risk_reward = round(abs(target - entry) / risk, 2)
    result.horizon_bars = horizon

    # A strategy only takes a side once most of its own conditions hold —
    # otherwise it reports the setup it is watching for, with conviction, and
    # stays flat.
    result.stance = ("long" if long else "short") if result.conviction >= 60 else "flat"

    result.projection = _path(ctx, entry, target, horizon)
    result.invalidation_path = _path(ctx, entry, stop, max(3, horizon // 3))
    return result


def _path(ctx: Ctx, start: float, end: float, bars: int) -> List[ScenarioPoint]:
    """A smooth scenario path from now to a level over `bars` bars.

    Eased rather than linear because price rarely travels in a straight line,
    and a straight line reads as a promise.
    """
    bars = max(2, bars)
    step = ctx.bar_seconds
    t0 = ctx.last_time
    points: List[ScenarioPoint] = []
    for i in range(bars + 1):
        progress = i / bars
        # Ease-in-out, plus a shallow wobble so it never looks like a forecast line.
        eased = progress * progress * (3 - 2 * progress)
        wobble = np.sin(progress * np.pi * 2) * ctx.atr * 0.12 * (1 - progress)
        price = start + (end - start) * eased + wobble
        points.append(ScenarioPoint(time=t0 + i * step,
                                    price=round(float(price), ctx.instrument.digits)))
    return points


def _nearest_support(ctx: Ctx) -> Optional[float]:
    levels = [l for l in ctx.structure.support_levels if l < ctx.price]
    return max(levels) if levels else ctx.structure.range_low


def _nearest_resistance(ctx: Ctx) -> Optional[float]:
    levels = [l for l in ctx.structure.resistance_levels if l > ctx.price]
    return min(levels) if levels else ctx.structure.range_high


# ── 1. trend continuation ─────────────────────────────────────

@strategy
def trend_continuation(ctx: Ctx) -> StrategyResult:
    result = StrategyResult(
        key="trend_continuation", name="Trend Continuation", family="trend",
        description="Buy pullbacks in an uptrend, sell rallies in a downtrend. "
                    "Highest expectancy when the higher timeframes agree.",
    )
    long = ctx.mtf.weighted_score >= 0
    snap, structure = ctx.snap, ctx.structure
    ema20 = snap.ema.get("20")
    ema50 = snap.ema.get("50")

    conditions = [
        Condition(
            name="Higher timeframes aligned", weight=1.5,
            met=ctx.mtf.alignment_score >= 0.65 and ctx.mtf.aligned_bias != Bias.NEUTRAL,
            detail="MTF alignment {:.0f}%, bias {}".format(
                ctx.mtf.alignment_score * 100, ctx.mtf.aligned_bias.value)),
        Condition(
            name="Structure trending", weight=1.5,
            met=structure.structure_label in ("uptrend", "downtrend")
                and ((structure.trend == Bias.BULLISH) == long),
            detail="Structure reads {}".format(structure.structure_label)),
        Condition(
            name="EMA stack in direction",
            met=bool(ema20 and ema50 and ((ema20 > ema50) == long)),
            detail="EMA20 {} EMA50".format(">" if (ema20 or 0) > (ema50 or 0) else "<")),
        Condition(
            name="ADX above 20 (trend has conviction)",
            met=(snap.adx or 0) >= 20,
            detail="ADX {:.0f}".format(snap.adx or 0)),
        Condition(
            name="Pulled back, not extended", weight=1.2,
            met=(35 <= (snap.rsi or 50) <= 60) if long else (40 <= (snap.rsi or 50) <= 65),
            detail="RSI {:.0f} — entries are cheaper on a pullback than a breakout "
                   "extension".format(snap.rsi or 50)),
        Condition(
            name="Location favours the trade",
            met=(structure.premium_discount == "discount") if long
                else (structure.premium_discount == "premium"),
            detail="Price is in {}".format(structure.premium_discount)),
    ]

    entry = ctx.price
    anchor = _nearest_support(ctx) if long else _nearest_resistance(ctx)
    stop = ((anchor - ctx.atr * 0.4) if long else (anchor + ctx.atr * 0.4)) if anchor \
        else (entry - ctx.atr * 1.5 if long else entry + ctx.atr * 1.5)
    reward = abs(entry - stop) * 2.2
    target = entry + reward if long else entry - reward

    result.rationale = [
        "Trend trades win by size, not hit rate — the tail of a trend pays for "
        "the false starts.",
        "Entry is a pullback into structure rather than a chase; the stop sits "
        "behind the swing that would end the trend.",
    ]
    if ctx.mtf.conflicts:
        result.rationale.append("Watch: " + "; ".join(ctx.mtf.conflicts[:2]))
    return _finish(result, ctx, conditions, long, entry, stop, target, horizon=18)


# ── 2. mean reversion ─────────────────────────────────────────

@strategy
def mean_reversion(ctx: Ctx) -> StrategyResult:
    result = StrategyResult(
        key="mean_reversion", name="Mean Reversion", family="reversion",
        description="Fade stretched moves back toward VWAP or the band mid. "
                    "Only valid while the market is ranging — lethal in a trend.",
    )
    snap, structure = ctx.snap, ctx.structure
    rsi = snap.rsi or 50
    pctb = snap.bollinger.get("pctb")
    long = rsi < 50

    conditions = [
        Condition(
            # Both gates must hold. An `or` here would let a market with ADX
            # at 90 through whenever the swing labeller happened to say
            # "ranging" — which is precisely the trend that ruins this trade.
            name="Market is ranging, not trending", weight=2.0,
            met=(snap.adx or 0) < 22
                and structure.structure_label not in ("uptrend", "downtrend"),
            detail="Structure {} · ADX {:.0f} — this strategy loses money in a "
                   "trend".format(structure.structure_label, snap.adx or 0)),
        Condition(
            name="Price stretched from the mean", weight=1.5,
            met=(rsi <= 32 or rsi >= 68) or (pctb is not None and (pctb <= 0.05 or pctb >= 0.95)),
            detail="RSI {:.0f}{}".format(rsi, ", %B {:.2f}".format(pctb) if pctb is not None else "")),
        Condition(
            name="At a range extreme",
            met=structure.premium_discount in ("premium", "discount"),
            detail="Price is in {}".format(structure.premium_discount)),
        Condition(
            name="Momentum divergence supports the fade",
            met=bool(snap.rsi_divergence and (
                snap.rsi_divergence.startswith("bullish") == long)),
            detail=snap.rsi_divergence or "no divergence"),
        Condition(
            name="No fresh break of structure against the fade",
            met=not (structure.last_bos and structure.last_bos.bars_ago <= 5
                     and (structure.last_bos.direction == "bearish") == long),
            detail="A fresh BOS means the range is gone and this is a knife-catch"),
    ]

    entry = ctx.price
    mid = snap.bollinger.get("mid") or snap.vwap or structure.equilibrium or ctx.price
    stop = entry - ctx.atr * 1.2 if long else entry + ctx.atr * 1.2
    target = mid if abs(mid - entry) > ctx.atr * 0.8 else (
        entry + ctx.atr * 1.6 if long else entry - ctx.atr * 1.6)

    result.rationale = [
        "Reversion trades win often and lose big — the discipline is the stop, "
        "not the entry.",
        "Target is the mean ({:.5f}), not the opposite extreme.".format(mid),
        "If a break of structure prints against you, the range is over and this "
        "thesis is void immediately.",
    ]
    return _finish(result, ctx, conditions, long, entry, stop, target, horizon=10)


# ── 3. breakout ───────────────────────────────────────────────

@strategy
def breakout(ctx: Ctx) -> StrategyResult:
    result = StrategyResult(
        key="breakout", name="Range Breakout", family="breakout",
        description="Trade a decisive close out of compression. Needs volume — "
                    "a breakout on thin participation is usually a trap.",
    )
    snap, structure = ctx.snap, ctx.structure
    high = structure.range_high or ctx.price
    low = structure.range_low or ctx.price
    long = abs(ctx.price - high) <= abs(ctx.price - low)
    rvol = snap.relative_volume or 1.0

    recent_sweep = next((e for e in structure.events
                         if e.kind == "liquidity_sweep" and e.bars_ago <= 4), None)

    conditions = [
        Condition(
            name="Volatility compressed before the move", weight=1.5,
            met=snap.bb_squeeze or snap.volatility_regime == "compressed",
            detail="Regime {}{}".format(snap.volatility_regime,
                                        ", Bollinger squeeze" if snap.bb_squeeze else "")),
        Condition(
            name="Price at the range edge", weight=1.5,
            met=min(abs(ctx.price - high), abs(ctx.price - low)) <= ctx.atr * 1.2,
            detail="Range {:.5f}–{:.5f}".format(low, high)),
        Condition(
            name="Volume confirming", weight=2.0,
            met=rvol >= 1.2,
            detail="RVOL {:.1f}x — a breakout without volume is the classic trap".format(rvol)),
        Condition(
            name="Break of structure in the last few bars",
            met=bool(structure.last_bos and structure.last_bos.bars_ago <= 4),
            detail=("{} BOS {} bars ago".format(structure.last_bos.direction,
                                                structure.last_bos.bars_ago)
                    if structure.last_bos else "no recent BOS")),
        Condition(
            name="No liquidity sweep rejection", weight=1.2,
            met=recent_sweep is None,
            detail=("{} sweep {} bars ago — that is a failed break, not a real one"
                    .format(recent_sweep.direction, recent_sweep.bars_ago)
                    if recent_sweep else "no recent sweep"),
        ),
    ]

    entry = ctx.price
    width = max(high - low, ctx.atr * 2)
    stop = (low + ctx.atr * 0.3) if long else (high - ctx.atr * 0.3)
    if abs(entry - stop) > ctx.atr * 3:
        stop = entry - ctx.atr * 1.5 if long else entry + ctx.atr * 1.5
    target = entry + width * 0.9 if long else entry - width * 0.9

    result.rationale = [
        "The measured move projects the range height from the break point.",
        "Wait for the bar to *close* beyond the level. Intrabar breaks reverse "
        "far more often than closes do.",
        "Volume is the filter that separates a breakout from a stop hunt.",
    ]
    return _finish(result, ctx, conditions, long, entry, stop, target, horizon=16)


# ── 4. smart money / order block ──────────────────────────────

@strategy
def smart_money(ctx: Ctx) -> StrategyResult:
    result = StrategyResult(
        key="smart_money", name="Order Block Retest", family="smc",
        description="After a break of structure, wait for price to return to the "
                    "origin of the move and continue from it.",
    )
    structure = ctx.structure
    bos = structure.last_bos
    long = bos.direction == "bullish" if bos else ctx.mtf.weighted_score >= 0

    blocks = [z for z in structure.order_blocks
              if z.direction == ("bullish" if long else "bearish")]
    nearest = min(blocks, key=lambda z: z.distance_pct) if blocks else None
    gaps = [z for z in structure.fair_value_gaps
            if z.direction == ("bullish" if long else "bearish") and not z.mitigated]
    sweep = next((e for e in structure.events
                  if e.kind == "liquidity_sweep" and e.bars_ago <= 8
                  and (e.direction == "bullish") == long), None)

    conditions = [
        Condition(
            name="Break of structure established the direction", weight=2.0,
            met=bool(bos and bos.bars_ago <= 25),
            detail=("{} BOS {} bars ago".format(bos.direction, bos.bars_ago)
                    if bos else "no BOS to trade from")),
        Condition(
            name="Unmitigated order block to trade from", weight=2.0,
            met=nearest is not None,
            detail=("{} OB at {:.5f}–{:.5f}, {:.2f}% away".format(
                nearest.direction, nearest.bottom, nearest.top, nearest.distance_pct)
                if nearest else "no order block in range")),
        Condition(
            name="Price near the block",
            met=bool(nearest and nearest.distance_pct <= 0.6),
            detail=("{:.2f}% away".format(nearest.distance_pct) if nearest else "n/a")),
        Condition(
            name="Fair value gap supports the move",
            met=bool(gaps),
            detail="{} unfilled FVG(s) in direction".format(len(gaps))),
        Condition(
            name="Liquidity swept before the move",
            met=sweep is not None,
            detail=("{} sweep {} bars ago — stops taken, then reversal".format(
                sweep.direction, sweep.bars_ago) if sweep else "no recent sweep"),
        ),
        Condition(
            name="Entering from discount (long) / premium (short)",
            met=(structure.premium_discount == "discount") if long
                else (structure.premium_discount == "premium"),
            detail="Price is in {}".format(structure.premium_discount)),
    ]

    if nearest is not None:
        entry = nearest.top if long else nearest.bottom
        stop = (nearest.bottom - ctx.atr * 0.3) if long else (nearest.top + ctx.atr * 0.3)
    else:
        entry = ctx.price
        stop = entry - ctx.atr * 1.4 if long else entry + ctx.atr * 1.4

    mids = [(z.top + z.bottom) / 2 for z in structure.liquidity_pools]
    liquidity = [m for m in mids if (m > entry) == long and m != entry]
    target = (min(liquidity) if long else max(liquidity)) if liquidity else (
        entry + abs(entry - stop) * 2.5 if long else entry - abs(entry - stop) * 2.5)

    result.rationale = [
        "The order block is where the move began; price returning to it is the "
        "cheapest entry with the tightest invalidation.",
        "Target is resting liquidity — that is where the move is being drawn to.",
        "If price closes through the block instead of respecting it, the block "
        "is broken and this thesis is dead.",
    ]
    return _finish(result, ctx, conditions, long, entry, stop, target, horizon=20)


# ── 5. momentum expansion ─────────────────────────────────────

@strategy
def momentum_expansion(ctx: Ctx) -> StrategyResult:
    result = StrategyResult(
        key="momentum", name="Momentum Expansion", family="momentum",
        description="Ride an accelerating move while participation is rising. "
                    "Short holding period; exits on the first stall.",
    )
    snap = ctx.snap
    hist = snap.macd.get("hist", 0.0)
    long = hist >= 0
    rvol = snap.relative_volume or 1.0

    conditions = [
        Condition(
            name="MACD expanding, not fading", weight=1.5,
            met=bool(snap.macd_state and snap.macd_state.endswith("expanding")),
            detail=(snap.macd_state or "flat").replace("_", " ")),
        Condition(
            name="ADX rising above 22", weight=1.5,
            met=(snap.adx or 0) >= 22,
            detail="ADX {:.0f} ({})".format(snap.adx or 0, snap.trend_strength)),
        Condition(
            name="Volume above average", weight=1.5,
            met=rvol >= 1.1,
            detail="RVOL {:.1f}x".format(rvol)),
        Condition(
            name="Volatility expanding",
            met=snap.volatility_regime == "expanded" or not snap.bb_squeeze,
            detail="Regime {}".format(snap.volatility_regime)),
        Condition(
            name="Not already exhausted",
            met=(snap.rsi or 50) < 78 if long else (snap.rsi or 50) > 22,
            detail="RSI {:.0f} — chasing a stretched move is how momentum trades "
                   "lose".format(snap.rsi or 50)),
        Condition(
            name="SuperTrend agrees",
            met=(snap.supertrend_dir or 0) == (1 if long else -1),
            detail="SuperTrend {}".format(
                "long" if (snap.supertrend_dir or 0) > 0 else "short")),
    ]

    entry = ctx.price
    stop = (snap.supertrend if snap.supertrend else
            (entry - ctx.atr * 1.2 if long else entry + ctx.atr * 1.2))
    if (long and stop >= entry) or (not long and stop <= entry):
        stop = entry - ctx.atr * 1.2 if long else entry + ctx.atr * 1.2
    target = entry + abs(entry - stop) * 2.0 if long else entry - abs(entry - stop) * 2.0

    result.rationale = [
        "Momentum trades are held for the expansion, not to a fixed target — "
        "the first stall is the exit.",
        "The SuperTrend line doubles as the trailing stop.",
        "This is the strategy most punished by thin volume; RVOL is the filter.",
    ]
    return _finish(result, ctx, conditions, long, entry, stop, target, horizon=8)


# ── orchestration ─────────────────────────────────────────────

def evaluate(df: pd.DataFrame, price: float, snap: IndicatorSnapshot,
             structure: MarketStructure, mtf: MTFReport,
             patterns: List[PatternHit], candles: List[PatternHit],
             instrument: Instrument, atr: float, timeframe: str) -> StrategyReport:
    ctx = Ctx(df=df, price=price, snap=snap, structure=structure, mtf=mtf,
              patterns=patterns, candles=candles, instrument=instrument,
              atr=max(atr, 1e-9), timeframe=timeframe)

    results: List[StrategyResult] = []
    for fn in _REGISTRY:
        try:
            results.append(fn(ctx))
        except Exception:  # noqa: BLE001 - one bad strategy must not sink the rest
            continue

    results.sort(key=lambda r: -r.conviction)
    report = StrategyReport(results=results)

    # Consensus counts only strategies confident enough to take a side, and
    # weights them by their own conviction.
    engaged = [r for r in results if r.stance in ("long", "short")]
    if engaged:
        score = sum((1 if r.stance == "long" else -1) * r.conviction for r in engaged)
        total = sum(r.conviction for r in engaged) or 1.0
        report.consensus_score = round(score / total, 3)
        dominant = "long" if report.consensus_score > 0 else "short"
        agreeing = sum(r.conviction for r in engaged if r.stance == dominant)
        report.agreement = round(agreeing / total, 3)
        report.consensus_stance = dominant if abs(report.consensus_score) > 0.2 else "flat"
        report.leader = results[0].key

    report.by_family = {r.family: r.stance for r in results}

    if not engaged:
        best = results[0] if results else None
        report.summary = (
            "No strategy has enough of its conditions met to take a side. "
            + ("Closest is {} at {:.0f}% — waiting on: {}.".format(
                best.name, best.conviction, ", ".join(best.waiting_for[:2]))
               if best and best.waiting_for else "")
        )
    else:
        names = ", ".join("{} ({:.0f}%)".format(r.name, r.conviction) for r in engaged[:3])
        report.summary = (
            "{} of {} strategies are engaged and {:.0f}% of that conviction points "
            "{}. Leading: {}.".format(
                len(engaged), len(results), report.agreement * 100,
                report.consensus_stance, names)
        )
        if report.agreement < 0.7:
            report.summary += (
                " Strategies disagree — that usually means the market is between "
                "regimes, and it is the worst time to size up.")

    return report
