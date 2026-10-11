"""Analysis orchestrator.

One entry point that turns (symbol, timeframe, the trader's drawings) into a
complete `AnalysisResult`: indicators, structure, patterns, drawing analytics,
multi-timeframe fusion, a confidence breakdown, an AI verdict, a sized trade
plan, and the overlays to render it all.

It is built in two layers so the same code can be tested on history. The
decision itself is two pure functions — `evaluate` (what the price history
says) and `conclude` (the verdict, fused confidence and plan) — that take
DataFrames and return values, touching no feed, store, clock or network.
`run` is those two plus the I/O around them. The backtest calls the same two
functions bar by bar (`backtest/replay.py`), so a backtest result is evidence
about the engine that trades, not about a second implementation of it.

The confidence model is computed twice on purpose. The first pass excludes the
AI component and is what Claude receives — so the analyst sees the raw
quantitative picture rather than being handed a conclusion to agree with. The
second pass folds Claude's verdict in at its 10% weight to produce the headline
number.
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import pandas as pd

from .. import runtime_settings
from ..data.feed import feed
from ..data.store import store
from ..instruments import MTF_LADDER, Instrument, get_instrument, get_timeframe
from ..schemas.analysis import (
    AIVerdict, AnalysisResult, ConfidenceBreakdown, Decision, IndicatorSnapshot,
    MarketStructure, MTFReport, TradePlan,
)
from ..schemas.strategies import StrategyReport
from ..schemas.drawings import Drawing
from ..schemas.positions import Position
from ..overlays.builder import build_overlays, build_scenario_overlays
from ..ai import claude_client, fallback as rule_analyst
from ..ai.narrative import build_evidence, build_narrative
from . import candlesticks, confidence, drawings as drawing_engine, manage
from . import derivatives, indicators, mtf, patterns
from . import risk as risk_engine
from . import plan as plan_builder, strategies
from . import structure as structure_engine

log = logging.getLogger("meridian.engine")

MIN_BARS = 60
ANALYSIS_BARS = 600


@dataclass
class Evaluation:
    """Everything the deterministic engine derives from price history —
    before any analyst, human or model, has an opinion."""
    snap: IndicatorSnapshot
    atr: float
    structure: MarketStructure
    patterns: list
    candles: list
    mtf: MTFReport
    drawings: list
    strategies: Optional[StrategyReport]
    pre_ai: ConfidenceBreakdown
    provisional_decision: Decision
    provisional_plan: TradePlan
    #: Echoed so `conclude` rebuilds a plan with the same inputs.
    balance: Optional[float] = None
    risk_pct: Optional[float] = None


@dataclass
class Conclusion:
    verdict: AIVerdict
    confidence: ConfidenceBreakdown
    plan: TradePlan
    warnings: List[str] = field(default_factory=list)


def evaluate(df: pd.DataFrame, frames: Dict[str, pd.DataFrame], instrument: Instrument,
             timeframe: str, price: float, *, daily: Optional[pd.DataFrame] = None,
             drawings: Optional[List[Drawing]] = None,
             spread_pips: Optional[float] = None, balance: Optional[float] = None,
             risk_pct: Optional[float] = None,
             with_strategies: bool = True) -> Evaluation:
    """The engine's read of a market, as a pure function of its inputs.

    No feed, no store, no clock, no network: the same frames give the same
    answer. That is what lets the backtest replay *this* function bar by bar
    instead of an approximation of it — `run` below is this plus I/O.
    """
    drawings = drawings or []
    computed = indicators.compute_all(df)
    snap, computed = indicators.snapshot(df, instrument, computed, daily)
    atr = snap.atr or float((df["high"] - df["low"]).tail(30).mean()) or 1e-6

    market_structure = structure_engine.analyse(df, instrument, atr)
    chart_patterns = patterns.detect(df, market_structure.swings, atr)
    candle_patterns = candlesticks.detect(df)
    mtf_report = mtf.build_report(frames)

    # The trader's own work.
    relevant = [d for d in drawings
                if d.symbol.upper() == instrument.symbol and d.visible]
    drawing_analytics = drawing_engine.analyse_many(relevant, df, instrument, atr)

    # Named strategies, evaluated side by side. These run independently of
    # the fused verdict: a market can be hostile to breakouts and generous to
    # mean reversion at the same moment, and one blended score hides exactly
    # that.
    report = (strategies.evaluate(
        df, price, snap, market_structure, mtf_report, chart_patterns,
        candle_patterns, instrument, atr, timeframe) if with_strategies else None)

    # Quantitative fusion (pre-AI), and what the engine alone would do.
    pre_ai = confidence.compute(
        snap, market_structure, mtf_report, chart_patterns, candle_patterns,
        price, drawing_analytics, verdict=None, atr=atr)
    provisional_decision = confidence.decide(pre_ai, mtf_report)
    provisional_plan = plan_builder.build(
        provisional_decision, price, snap, market_structure, instrument,
        drawing_analytics, spread_pips, balance, risk_pct)

    return Evaluation(
        snap=snap, atr=atr, structure=market_structure, patterns=chart_patterns,
        candles=candle_patterns, mtf=mtf_report, drawings=drawing_analytics,
        strategies=report, pre_ai=pre_ai,
        provisional_decision=provisional_decision,
        provisional_plan=provisional_plan, balance=balance, risk_pct=risk_pct)


def conclude(ev: Evaluation, instrument: Instrument, price: float, *,
             verdict: Optional[AIVerdict] = None,
             spread_pips: Optional[float] = None,
             fallback_reason: Optional[str] = None) -> Conclusion:
    """Turn an evaluation into the verdict, confidence and plan that are
    shown, auto-traded and executed. With no `verdict` from Claude the
    deterministic analyst supplies one — which is always the case in replay.
    """
    warnings: List[str] = []
    snap, atr = ev.snap, ev.atr

    if verdict is None:
        verdict = rule_analyst.build(
            ev.provisional_decision, ev.pre_ai, ev.provisional_plan, snap,
            ev.structure, ev.mtf, ev.patterns, ev.candles, ev.drawings,
            instrument, fallback_reason,
        )

    # Final fusion including the AI component.
    fused = confidence.compute(
        snap, ev.structure, ev.mtf, ev.patterns, ev.candles, price,
        ev.drawings, verdict=verdict, atr=atr,
    )

    # The engine holds a veto: a directional call is only released when the
    # fused, agreement-weighted evidence also supports it.
    engine_decision = confidence.decide(fused, ev.mtf)
    if verdict.decision != Decision.WAIT and engine_decision == Decision.WAIT:
        warnings.append(
            "Analyst proposed {} but fused evidence does not clear the threshold "
            "({}) — downgraded to WAIT".format(verdict.decision.value, fused.reason))
        verdict.decision = Decision.WAIT
        verdict.uncertainty = (
            "Quantitative fusion vetoed this call: {}. ".format(fused.reason)
            + (verdict.uncertainty or "")
        ).strip()
    elif (verdict.decision != Decision.WAIT and engine_decision != Decision.WAIT
          and verdict.decision != engine_decision):
        warnings.append(
            "Analyst and engine disagree on direction — holding at WAIT")
        verdict.decision = Decision.WAIT

    # Final plan.
    if verdict.decision == ev.provisional_decision and verdict.decision != Decision.WAIT:
        final_plan = ev.provisional_plan
    else:
        final_plan = plan_builder.build(
            verdict.decision, price, snap, ev.structure, instrument,
            ev.drawings, spread_pips, ev.balance, ev.risk_pct,
        )

    # Prefer the analyst's own levels when they are sane, since they carry the
    # reasoning; fall back to the structural plan otherwise.
    if verdict.decision != Decision.WAIT and verdict.entry and verdict.stop_loss:
        risk = abs(verdict.entry - verdict.stop_loss)
        sane = 0.4 * atr <= risk <= 5.0 * atr
        correct_side = (
            (verdict.decision == Decision.BUY and verdict.stop_loss < verdict.entry)
            or (verdict.decision == Decision.SELL and verdict.stop_loss > verdict.entry)
        )
        if sane and correct_side:
            final_plan.entry = verdict.entry
            final_plan.stop_loss = verdict.stop_loss
            if verdict.take_profit is not None:
                final_plan.take_profit = verdict.take_profit
            final_plan.take_profit_2 = verdict.take_profit_2 or final_plan.take_profit_2
            final_plan.risk_pips = round(instrument.to_pips(risk), 1)
            if final_plan.take_profit is not None:
                reward = abs(final_plan.take_profit - final_plan.entry)
                final_plan.reward_pips = round(instrument.to_pips(reward), 1)
                final_plan.risk_reward = round(reward / risk, 2) if risk else None
                final_plan.expected_move_pips = final_plan.reward_pips
            if final_plan.risk_amount and final_plan.risk_pips:
                pip_value = instrument.pip_value_per_lot
                if pip_value > 0:
                    final_plan.suggested_lots = round(
                        max(0.01, final_plan.risk_amount /
                            (final_plan.risk_pips * pip_value)), 2)
            final_plan.position_notes.append("Levels taken from the analyst's plan")
        else:
            warnings.append(
                "Analyst levels rejected as implausible — using structural plan")

    verdict.entry = final_plan.entry
    verdict.stop_loss = final_plan.stop_loss
    verdict.take_profit = final_plan.take_profit
    verdict.take_profit_2 = final_plan.take_profit_2
    verdict.risk_reward = final_plan.risk_reward
    return Conclusion(verdict=verdict, confidence=fused, plan=final_plan,
                      warnings=warnings)


async def _frames_for_mtf(symbol: str) -> Dict[str, pd.DataFrame]:
    """Hydrate and collect every timeframe on the MTF ladder."""
    frames: Dict[str, pd.DataFrame] = {}
    for tf in MTF_LADDER:
        try:
            await feed.ensure_history(symbol, tf)
        except Exception as exc:  # noqa: BLE001
            log.debug("mtf hydration failed for %s %s: %s", symbol, tf, exc)
        frame = store.frame(symbol, tf, 400)
        if len(frame) >= MIN_BARS:
            frames[tf] = frame
    return frames


async def run(symbol: str, timeframe: str,
              drawings: Optional[List[Drawing]] = None,
              include_ai: bool = True,
              question: Optional[str] = None,
              position: Optional[Position] = None) -> AnalysisResult:
    symbol = symbol.upper()
    timeframe = timeframe.lower()
    instrument = get_instrument(symbol)
    get_timeframe(timeframe)  # validates
    drawings = drawings or []
    started = time.perf_counter()

    await feed.ensure_history(symbol, timeframe)
    df = store.frame(symbol, timeframe, ANALYSIS_BARS)

    result = AnalysisResult(
        id=uuid.uuid4().hex,
        symbol=symbol,
        timeframe=timeframe,
        generated_at=time.time(),
        price=0.0,
        bar_time=0,
        indicators=IndicatorSnapshot(),
        structure=MarketStructure(),
    )

    if df is None or len(df) < MIN_BARS:
        result.warnings.append(
            "Only {} bars available for {} {} — need at least {} to analyse".format(
                0 if df is None else len(df), symbol, timeframe, MIN_BARS))
        result.narrative = "Insufficient history to analyse this market yet."
        return result

    price = float(df["close"].iloc[-1])
    quote = feed.quote(symbol)
    if quote is not None:
        price = quote.last
    result.price = round(price, instrument.digits)
    result.bar_time = int(df["time"].iloc[-1])

    # ── everything the price history says, before any analyst ─
    daily = store.frame(symbol, "1d", 60)
    frames = await _frames_for_mtf(symbol)
    frames.setdefault(timeframe, df)
    spread = quote.spread_pips if quote else None
    ev = evaluate(df, frames, instrument, timeframe, price,
                  daily=daily if len(daily) >= 2 else None,
                  drawings=drawings, spread_pips=spread)
    snap, atr, market_structure = ev.snap, ev.atr, ev.structure
    result.indicators = snap
    result.structure = market_structure
    result.patterns = ev.patterns
    result.candlestick_patterns = ev.candles
    result.mtf = ev.mtf
    result.drawings = ev.drawings
    result.strategies = ev.strategies

    # ── evidence brief ───────────────────────────────────────
    result.narrative = build_narrative(
        symbol, timeframe, instrument, price, quote, snap, market_structure,
        result.mtf, result.patterns, result.candlestick_patterns,
        result.drawings, ev.pre_ai, len(df),
    )
    result.evidence = build_evidence(
        symbol, timeframe, instrument, price, quote, snap, market_structure,
        result.mtf, result.patterns, result.candlestick_patterns,
        result.drawings, ev.pre_ai,
    )

    # ── ask Claude ───────────────────────────────────────────
    verdict = None
    if include_ai:
        try:
            verdict = await claude_client.analyse(
                result.narrative, result.evidence, instrument, question)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            log.warning("claude analysis failed: %s", exc)
        if verdict is None:
            result.warnings.append(
                "Claude unavailable — deterministic confluence engine produced this verdict")

    # ── verdict, fused confidence, and the plan that goes with them ──
    outcome = conclude(
        ev, instrument, price, verdict=verdict, spread_pips=spread,
        fallback_reason=("Claude disabled or unavailable"
                         if include_ai else "AI analysis not requested"))
    verdict, final_plan = outcome.verdict, outcome.plan
    result.confidence = outcome.confidence
    result.warnings.extend(outcome.warnings)
    result.verdict = verdict
    result.plan = final_plan

    # ── manage an open position ──────────────────────────────
    # Entry analysis answers "should I get in". If the trader is already in,
    # the more useful question is "is the reason still true" — and that is a
    # different computation with different defaults.
    if position is not None and position.symbol == symbol:
        try:
            result.protection = risk_engine.build_plan(
                position, price, instrument, atr,
                quote.spread_pips if quote else None)
        except Exception as exc:  # noqa: BLE001
            log.warning("protection plan failed: %s", exc)
        try:
            result.management = manage.evaluate(
                position, df, price, market_structure, snap,
                result.confidence, verdict.decision, instrument,
            )
        except Exception as exc:  # noqa: BLE001 - never lose the analysis over this
            log.warning("position management failed: %s", exc)
            result.warnings.append("Could not evaluate the open position: {}".format(exc))

    # ── how to actually place it ─────────────────────────────
    # On a directional verdict the contract expresses the plan. On WAIT there
    # is no plan to express, but the leading engaged strategy still has levels
    # — and knowing the contract, premium and decay *before* the setup triggers
    # is exactly when that knowledge is useful. It is labelled as coming from
    # the strategy rather than from the verdict so it is never mistaken for a
    # call the engine is making.
    deriv_side = deriv_entry = deriv_stop = deriv_target = None
    deriv_source = "verdict"
    if final_plan.entry and final_plan.stop_loss:
        deriv_side = "long" if verdict.decision == Decision.BUY else "short"
        deriv_entry = final_plan.entry
        deriv_stop = final_plan.stop_loss
        deriv_target = final_plan.take_profit
    else:
        leading = next((s for s in result.strategies.results
                        if s.stance in ("long", "short")), None)
        if leading and leading.entry and leading.stop_loss:
            deriv_side = leading.stance
            deriv_entry = leading.entry
            deriv_stop = leading.stop_loss
            deriv_target = leading.take_profit
            deriv_source = leading.name

    if deriv_side and deriv_entry and deriv_stop:
        # The plan only carries a risk budget on a directional call, but the
        # sizing question is the same either way — so derive it from settings
        # when the plan has none, rather than showing "—" for lots.
        cfg = runtime_settings.get()
        risk_budget = final_plan.risk_amount or (
            cfg.account_balance * cfg.risk_per_trade_pct / 100.0)
        result.derivatives = derivatives.suggest(
            instrument, deriv_side, deriv_entry, deriv_stop, deriv_target,
            atr, get_timeframe(timeframe).seconds, risk_budget,
        )
        if result.derivatives is not None:
            result.derivatives.source = deriv_source

    # ── overlays ─────────────────────────────────────────────

    result.overlays = build_overlays(
        verdict, final_plan, market_structure, result.patterns,
        instrument, timeframe, result.bar_time, price,
    )
    result.overlays.extend(build_scenario_overlays(result.strategies))

    elapsed = (time.perf_counter() - started) * 1000
    log.info("analysed %s %s in %.0fms → %s @ %.0f%% (%s)",
             symbol, timeframe, elapsed, verdict.decision.value,
             result.confidence.final_confidence, verdict.source)
    return result


async def quick_bias(symbol: str, timeframe: str) -> Dict[str, object]:
    """Cheap directional read for the watchlist — no AI, no patterns."""
    await feed.ensure_history(symbol, timeframe)
    df = store.frame(symbol, timeframe, 320)
    if len(df) < MIN_BARS:
        return {"symbol": symbol, "timeframe": timeframe, "bias": "neutral", "score": 0.0}
    view = mtf.score_timeframe(df, timeframe)
    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "bias": view.bias.value,
        "score": view.score,
        "rsi": view.rsi,
        "adx": view.adx,
        "structure": view.structure,
        "note": view.note,
    }
