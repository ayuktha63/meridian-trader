"""The study: a handful of hypotheses, a declared grid, three stretches of
history, and thresholds fixed before any of it is run.

History is cut once, by date, the same way for every market:

* **research** — everything before `VALIDATION_START`. Parameters are chosen
  here, one set per strategy and timeframe for the *whole universe*: a rule
  that needs a different setting on every coin has found nine accidents.
* **validation** — the next stretch, with those parameters frozen. The
  first look at data the choice never saw.
* **holdout** — the most recent stretch. Opened once, for what survived the
  first two, and never used to choose anything.

A family that fails a stage goes no further, so the later stretches stay
unseen for whatever is tried next. Every configuration evaluated is counted,
and the best of them has to clear a bar that rises with the count.

Nothing in this module changes a threshold after seeing a result. They are
constants, they carry a version, and each record says which version judged it.
"""
from __future__ import annotations

import calendar
import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from .. import costs
from ..config import DATA_DIR
from ..instruments import Instrument, get_instrument, get_timeframe
from . import datasets, metrics, stats, strategies
from .audit import audit
from .datasets import Dataset
from .simulator import MARKET, Bars, Signal, Trade, reference, simulate
from .strategies import Params, Plan, Strategy

log = logging.getLogger("meridian.research.study")

PROTOCOL_VERSION = "1"
WEEK = 604_800


def _utc(date: str) -> float:
    return float(calendar.timegm(time.strptime(date, "%Y-%m-%d")))


VALIDATION_START = _utc("2024-07-01")
HOLDOUT_START = _utc("2025-10-01")
#: A market needs history from here or earlier to have a research stretch
#: worth the name.
HISTORY_REQUIRED_FROM = _utc("2021-03-01")

TIMEFRAMES: Tuple[str, ...] = ("1h", "4h", "1d")
PROFILES: Tuple[str, ...] = ("crypto_perp", "crypto_spot")
STAGES: Tuple[str, ...] = ("research", "validation", "holdout")

#: What a family has to show at each stage. Fixed before the first run.
THRESHOLDS: Dict[str, Dict[str, float]] = {
    "research": {
        "min_trades": 200,          # pooled over the universe
        "interval_level": 0.90,     # its lower end must be above zero
        "deflated_sharpe": 0.95,    # after every configuration tried
        "neighbours_positive": 0.60,  # share of the family's own grid that made money
        "markets_positive": 0.60,   # share of markets it made money on…
        "market_min_trades": 20,    # …among those with enough trades to say
        "cost_stress": 1.5,         # still positive with costs this much higher
        "random_p_value": 0.05,     # against entries at random bars
        "random_draws": 200,
    },
    "validation": {"min_trades": 100, "interval_level": 0.90, "markets_positive": 0.50,
                   "market_min_trades": 10, "alpha": 0.05},
    "holdout": {"min_trades": 100, "interval_level": 0.90, "market_min_trades": 20},
}

REJECTED, INSUFFICIENT, APPROVED = "REJECTED", "INSUFFICIENT EVIDENCE", "RESEARCH APPROVED"
VALIDATED, CONFIRMED = "VALIDATED", "CONFIRMED ON HOLDOUT"

EXECUTION_ASSUMPTIONS = (
    "A decision at a bar's close acts at the next bar's open, as a market order.",
    "A protective stop triggers on a touch and fills at the open on a gap.",
    "A target is a resting order and fills only when the market trades through it.",
    "When one bar reaches both stop and target, the stop is taken.",
    "One position per market at a time; a signal that arrives during one is not taken.",
    "A position open at the end of a window is closed at that window's last close.",
    "Signals are computed on the spot book's bars; the perpetual profile assumes the "
    "perpetual tracks them — basis is not modelled.",
    "No partial fills, no latency beyond the one-bar delay, no market impact: sizes "
    "are assumed small against the book.",
)


# ── the universe ──────────────────────────────────────────────

@dataclass
class Market:
    symbol: str
    instrument: Instrument
    data: Dict[str, Dataset]
    _bars: Dict[str, Bars] = field(default_factory=dict)
    _frames: Dict[str, pd.DataFrame] = field(default_factory=dict)
    _plans: Dict[tuple, Plan] = field(default_factory=dict)

    def frame(self, timeframe: str) -> pd.DataFrame:
        if timeframe not in self._frames:
            self._frames[timeframe] = self.data[timeframe].frame()
        return self._frames[timeframe]

    def bars(self, timeframe: str) -> Bars:
        if timeframe not in self._bars:
            self._bars[timeframe] = Bars.from_frame(self.frame(timeframe), timeframe)
        return self._bars[timeframe]

    def span(self, timeframe: str, stage: str) -> Tuple[int, int]:
        """Bar indices [first, last) of a stage on this market."""
        times = self.frame(timeframe)["time"].to_numpy()
        start, end = stage_window(stage)
        return int(np.searchsorted(times, start, "left")), int(np.searchsorted(times, end, "left"))

    def plan(self, strategy: Strategy, params: Params, timeframe: str, stage: str) -> Plan:
        """The strategy's decisions, computed on nothing later than the end
        of the stage being traded."""
        key = (strategy.name, strategies.label(params), timeframe, stage)
        if key not in self._plans:
            _, last = self.span(timeframe, stage)
            self._plans[key] = strategy.build(self.frame(timeframe).iloc[:last], params)
        return self._plans[key]


def stage_window(stage: str) -> Tuple[float, float]:
    return {"research": (0.0, VALIDATION_START),
            "validation": (VALIDATION_START, HOLDOUT_START),
            "holdout": (HOLDOUT_START, float("inf"))}[stage]


def load_universe(base_timeframe: str = "1h") -> Tuple[List[Market], List[Dict[str, str]]]:
    """Every market with a usable snapshot, and every one left out with why."""
    newest: Dict[str, Dict[str, Any]] = {}
    for meta in datasets.catalogue():
        if (meta["timeframe"] == base_timeframe and not meta.get("derived_from")
                and meta["provider"] not in datasets.NOT_A_MARKET):
            newest.setdefault(meta["symbol"], meta)
    included: List[Market] = []
    excluded: List[Dict[str, str]] = []
    for symbol in sorted(newest):
        meta = newest[symbol]
        quality = meta.get("quality") or {}
        if not quality.get("passed"):
            excluded.append({"symbol": symbol, "dataset_id": meta["sha256"][:16],
                             "reason": "data quality: " + "; ".join(quality.get("problems", []))})
            continue
        if meta["first"] > HISTORY_REQUIRED_FROM:
            excluded.append({"symbol": symbol, "dataset_id": meta["sha256"][:16],
                             "reason": "history starts {} — no research stretch".format(
                                 time.strftime("%Y-%m-%d", time.gmtime(meta["first"])))})
            continue
        base = datasets.load(meta["sha256"][:16])
        data = {base_timeframe: base}
        for timeframe in TIMEFRAMES:
            if timeframe != base_timeframe:
                data[timeframe] = datasets.resample(base, timeframe)
                datasets.save(data[timeframe])
        included.append(Market(symbol, get_instrument(symbol), data))
    return included, excluded


# ── one configuration ─────────────────────────────────────────

@dataclass
class Outcome:
    strategy: str
    params: Dict[str, float]
    timeframe: str
    profile: str
    stage: str
    by_market: Dict[str, List[Trade]]

    @property
    def trades(self) -> List[Trade]:
        return sorted((t for ts in self.by_market.values() for t in ts),
                      key=lambda t: t.exit_time)

    @property
    def r(self) -> np.ndarray:
        return np.array([t.r for t in self.trades], dtype=float)

    def weeks(self) -> np.ndarray:
        """The calendar week each trade closed in — trades that share one
        are resampled together."""
        return np.array([int(t.exit_time // WEEK) for t in self.trades], dtype=int)

    def weekly(self, markets: Sequence["Market"]) -> np.ndarray:
        """Total R per calendar week across the whole stage, quiet weeks
        included: one series for the pooled result, whose observations are
        weeks rather than trades that overlap on nine coins at once."""
        first, last = [], []
        for market in markets:
            if market.symbol not in self.by_market:
                continue
            times = market.bars(self.timeframe).time
            start, end = market.span(self.timeframe, self.stage)
            first.append(int(times[start] // WEEK))
            last.append(int(times[end - 1] // WEEK))
        if not first:
            return np.zeros(0)
        return stats.per_period(self.r, self.weeks(), min(first), max(last))

    def market_means(self, min_trades: int) -> Dict[str, float]:
        return {symbol: float(np.mean([t.r for t in trades]))
                for symbol, trades in self.by_market.items() if len(trades) >= min_trades}


def run(markets: Sequence[Market], strategy: Strategy, params: Params, timeframe: str,
        profile: str, stage: str, cost_multiplier: float = 1.0) -> Outcome:
    by_market: Dict[str, List[Trade]] = {}
    for market in markets:
        first, last = market.span(timeframe, stage)
        if last - first < 50:
            continue
        plan = market.plan(strategy, params, timeframe, stage)
        by_market[market.symbol] = simulate(
            market.bars(timeframe), plan.signals, market.instrument, start=first, end=last,
            profile=profile, cost_multiplier=cost_multiplier,
            exit_long=plan.exit_long, exit_short=plan.exit_short)
    return Outcome(strategy.name, dict(params), timeframe, profile, stage, by_market)


def matched_random(market: Market, timeframe: str, stage: str, plan: Plan,
                   trades: Sequence[Trade], shorting: bool,
                   rng: np.random.Generator) -> Dict[int, Signal]:
    """Entries with no skill, built from the strategy's own trades: each one
    keeps its stop and target distances (in ATRs), its trailing rule and how
    long it was held — and is moved to a bar picked at random, in a direction
    picked at random. What is left is the exits and the costs."""
    bars = market.bars(timeframe)
    first, last = market.span(timeframe, stage)
    null: Dict[int, Signal] = {}
    for trade in trades:
        signal = plan.signals.get(trade.signal_index)
        scale = bars.atr[trade.signal_index]
        if signal is None or scale <= 0:
            continue
        ref = reference(signal, bars.close[trade.signal_index])
        stop_atrs = abs(ref - signal.stop) / scale
        target_atrs = None if signal.target is None else abs(signal.target - ref) / scale
        for _ in range(20):                         # a bar with a usable ATR, not yet used
            j = int(rng.integers(first, max(last - 1, first + 1)))
            if bars.atr[j] > 0 and j not in null:
                break
        else:
            continue
        direction = (1 if rng.random() < 0.5 else -1) if shorting else 1
        price, unit = bars.close[j], bars.atr[j]
        null[j] = Signal(
            direction, price - direction * stop_atrs * unit,
            target=None if target_atrs is None else price + direction * target_atrs * unit,
            entry=MARKET, trail_atr=signal.trail_atr, max_hold=max(trade.bars_held, 1),
            tag="random")
    return null


def random_baseline(markets: Sequence[Market], strategy: Strategy, outcome: Outcome,
                    draws: int, seed: int = 7) -> np.ndarray:
    """Pooled mean R of `draws` matched-random versions of an outcome."""
    by_symbol = {m.symbol: m for m in markets}
    rng = np.random.default_rng(seed)
    means = np.empty(draws)
    for draw in range(draws):
        pooled: List[float] = []
        for symbol, trades in outcome.by_market.items():
            market = by_symbol[symbol]
            plan = market.plan(strategy, outcome.params, outcome.timeframe, outcome.stage)
            first, last = market.span(outcome.timeframe, outcome.stage)
            null = matched_random(market, outcome.timeframe, outcome.stage, plan, trades,
                                  costs.can_short(market.instrument, outcome.profile), rng)
            pooled += [t.r for t in simulate(
                market.bars(outcome.timeframe), null, market.instrument, start=first,
                end=last, profile=outcome.profile)]
        means[draw] = float(np.mean(pooled)) if pooled else 0.0
    return means


# ── judging ───────────────────────────────────────────────────

def _interval(outcome: Outcome, level: float) -> stats.MeanEstimate:
    return stats.cluster_mean_interval(outcome.r, outcome.weeks(), level=level)


def break_even_cost_multiple(markets: Sequence[Market], strategy: Strategy,
                             outcome: Outcome) -> Optional[float]:
    """How many times the modelled costs this result could bear before its
    expectancy reaches zero. Costs enter each trade linearly, so this is
    gross over cost."""
    trades = outcome.trades
    cost = float(np.mean([t.cost_r for t in trades])) if trades else 0.0
    gross = float(np.mean([t.gross_r for t in trades])) if trades else 0.0
    return round(gross / cost, 2) if cost > 0 else None


def summarise(outcome: Outcome, markets: Sequence[Market]) -> Dict[str, Any]:
    """Pooled and per-market figures for one configuration on one stage."""
    spans = []
    by_symbol = {m.symbol: m for m in markets}
    for symbol in outcome.by_market:
        market = by_symbol[symbol]
        first, last = market.span(outcome.timeframe, outcome.stage)
        times = market.bars(outcome.timeframe).time
        spans.append(times[last - 1] - times[first])
    seconds = get_timeframe(outcome.timeframe).seconds
    pooled = metrics.summarise(outcome.trades, window_seconds=max(spans) if spans else 0.0,
                               tf_seconds=seconds)
    # Exposure pooled over markets is per market-window, not per window.
    if spans and outcome.trades:
        held = sum(t.bars_held for t in outcome.trades) * seconds
        pooled["exposure_pct"] = round(min(held / sum(spans), 1.0) * 100, 2)
    per_market = {}
    for symbol, trades in sorted(outcome.by_market.items()):
        r = [t.r for t in trades]
        per_market[symbol] = {
            "trades": len(r), "expectancy_r": round(float(np.mean(r)), 4) if r else None,
            "total_r": round(float(np.sum(r)), 2) if r else 0.0}
    return {"pooled": pooled, "by_market": per_market}


@dataclass
class Family:
    """One strategy on one timeframe at one venue: its grid on the research
    stretch, the configuration chosen there, and how far it got."""
    strategy: str
    timeframe: str
    profile: str
    grid: List[Dict[str, Any]] = field(default_factory=list)
    chosen: Optional[Dict[str, float]] = None
    stages: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    state: str = INSUFFICIENT
    reasons: List[str] = field(default_factory=list)

    @property
    def key(self) -> str:
        return "{}/{}/{}".format(self.strategy, self.timeframe, self.profile)


def research_grid(markets: Sequence[Market], strategy: Strategy, timeframe: str,
                  profile: str) -> Tuple[Family, Dict[str, Outcome]]:
    family = Family(strategy.name, timeframe, profile)
    outcomes: Dict[str, Outcome] = {}
    for params in strategy.grid:
        outcome = run(markets, strategy, params, timeframe, profile, "research")
        r = outcome.r
        outcomes[strategies.label(params)] = outcome
        family.grid.append({
            "parameters": dict(params), "label": strategies.label(params),
            "trades": int(len(r)), "expectancy_r": round(float(r.mean()), 4) if len(r) else None,
            "sharpe_per_trade": round(stats.sharpe(r), 4) if len(r) > 1 else None})
    return family, outcomes


def judge_research(family: Family, outcomes: Dict[str, Outcome], markets: Sequence[Market],
                   strategy: Strategy, trials: int, audited: Dict[str, bool]) -> None:
    """Choose the family's configuration and decide whether it goes on."""
    rule = THRESHOLDS["research"]
    reasons = family.reasons
    enough = [row for row in family.grid if row["trades"] >= rule["min_trades"]]
    if not audited.get(strategy.name, False):
        family.state = REJECTED
        reasons.append("Failed the look-ahead audit: not tested further.")
        return
    if not enough:
        family.state = INSUFFICIENT
        reasons.append("No configuration made {} trades on the research stretch "
                       "(most: {}).".format(int(rule["min_trades"]),
                                            max((row["trades"] for row in family.grid), default=0)))
        return
    best = max(enough, key=lambda row: row["expectancy_r"])
    family.chosen = dict(best["parameters"])
    outcome = outcomes[best["label"]]
    summary = summarise(outcome, markets)
    estimate = _interval(outcome, rule["interval_level"])
    means = outcome.market_means(int(rule["market_min_trades"]))
    neighbours = stats.positive_share([row["expectancy_r"] for row in family.grid
                                       if row["expectancy_r"] is not None])
    breadth = stats.positive_share(list(means.values()))
    # On weekly totals, not on trades: trades on several coins in one week
    # are not separate observations, and the test assumes they are.
    weekly = outcome.weekly(markets)
    deflated = stats.deflated_sharpe(weekly, trials)
    stressed = run(markets, strategy, family.chosen, family.timeframe, family.profile,
                   "research", cost_multiplier=rule["cost_stress"])
    stressed_mean = float(stressed.r.mean()) if len(stressed.r) else 0.0
    stage: Dict[str, Any] = {
        "parameters": family.chosen, **summary,
        "interval": {"level": estimate.level, "low": round(estimate.low, 4),
                     "high": round(estimate.high, 4), "p_value": round(estimate.p_value, 5),
                     "weeks": estimate.block, "method": "cluster bootstrap by calendar week"},
        "deflated_sharpe": round(deflated, 4), "trials_deflated_for": trials,
        "weekly_sharpe": round(stats.sharpe(weekly), 4), "weeks": int(len(weekly)),
        "neighbours_positive": round(neighbours, 3),
        "markets_positive": round(breadth, 3), "markets_judged": len(means),
        "expectancy_at_stressed_costs_r": round(stressed_mean, 4),
        "break_even_cost_multiple": break_even_cost_multiple(markets, strategy, outcome),
    }
    family.stages["research"] = stage

    if estimate.mean <= 0:
        reasons.append("Net expectancy on the research stretch is {:+.3f}R — it lost "
                       "money where its parameters were chosen.".format(estimate.mean))
    elif estimate.low <= 0:
        reasons.append("Expectancy {:+.3f}R, but its {:.0f}% interval ({:+.3f}R to "
                       "{:+.3f}R) includes zero.".format(
                           estimate.mean, 100 * estimate.level, estimate.low, estimate.high))
    if deflated < rule["deflated_sharpe"]:
        reasons.append("Deflated Sharpe {:.2f} (need {:.2f}): not distinguishable from "
                       "the best of {} tries on noise.".format(
                           deflated, rule["deflated_sharpe"], trials))
    if neighbours < rule["neighbours_positive"]:
        reasons.append("Only {:.0f}% of its own parameter grid made money (need "
                       "{:.0f}%) — the result sits on one setting.".format(
                           100 * neighbours, 100 * rule["neighbours_positive"]))
    if breadth < rule["markets_positive"]:
        reasons.append("Positive on {:.0f}% of markets (need {:.0f}%).".format(
            100 * breadth, 100 * rule["markets_positive"]))
    if stressed_mean <= 0:
        reasons.append("Loses money with costs {:g}x the modelled ones ({:+.3f}R).".format(
            rule["cost_stress"], stressed_mean))
    if reasons:
        family.state = REJECTED
        return

    # The expensive check last, and only for what has passed everything else.
    null = random_baseline(markets, strategy, outcome, int(rule["random_draws"]))
    p_value = stats.permutation_p_value(estimate.mean, null)
    stage["random_entries"] = {
        "draws": int(rule["random_draws"]), "mean_r": round(float(null.mean()), 4),
        "best_r": round(float(null.max()), 4), "p_value": round(p_value, 5)}
    if p_value > rule["random_p_value"]:
        family.state = REJECTED
        reasons.append("No better than entries at random bars with the same exits "
                       "(p = {:.3f}).".format(p_value))
        return
    family.state = APPROVED


def judge_later(family: Family, markets: Sequence[Market], strategy: Strategy,
                stage_name: str) -> Outcome:
    """Trade the frozen configuration on a stretch it has never seen."""
    rule = THRESHOLDS[stage_name]
    outcome = run(markets, strategy, family.chosen, family.timeframe, family.profile, stage_name)
    summary = summarise(outcome, markets)
    estimate = _interval(outcome, rule["interval_level"])
    means = outcome.market_means(int(rule["market_min_trades"]))
    stage = {"parameters": family.chosen, **summary,
             "interval": {"level": estimate.level, "low": round(estimate.low, 4),
                          "high": round(estimate.high, 4),
                          "p_value": round(estimate.p_value, 5), "weeks": estimate.block,
                          "method": "cluster bootstrap by calendar week"},
             "markets_positive": round(stats.positive_share(list(means.values())), 3),
             "markets_judged": len(means),
             "break_even_cost_multiple": break_even_cost_multiple(markets, strategy, outcome)}
    family.stages[stage_name] = stage
    return outcome


def _failed_later(stage: Dict[str, Any], rule: Dict[str, float], name: str) -> List[str]:
    reasons: List[str] = []
    pooled, interval = stage["pooled"], stage["interval"]
    if pooled["trades"] < rule["min_trades"]:
        reasons.append("Only {} trades on the {} stretch (need {}).".format(
            pooled["trades"], name, int(rule["min_trades"])))
        return reasons
    if interval["low"] <= 0:
        reasons.append("On the {} stretch: expectancy {:+.3f}R, interval {:+.3f}R to "
                       "{:+.3f}R — it includes zero.".format(
                           name, pooled["expectancy_r"], interval["low"], interval["high"]))
    floor = rule.get("markets_positive")
    if floor is not None and stage["markets_positive"] < floor:
        reasons.append("On the {} stretch: positive on {:.0f}% of markets (need "
                       "{:.0f}%).".format(name, 100 * stage["markets_positive"], 100 * floor))
    return reasons


# ── the whole study ───────────────────────────────────────────

def _audits(markets: Sequence[Market], timeframe: str) -> Dict[str, Dict[str, Any]]:
    """Every candidate, checked for look-ahead on the first market's
    research bars before a single result is computed."""
    market = markets[0]
    out: Dict[str, Dict[str, Any]] = {}
    for strategy in strategies.CANDIDATES:
        frame = market.frame(timeframe).iloc[:6000].reset_index(drop=True)
        reports = [audit(strategy, frame, params, samples=16).to_dict()
                   for params in (strategy.grid[0], strategy.grid[-1])]
        out[strategy.name] = {"passed": all(r["passed"] for r in reports), "reports": reports}
    return out


def execute(markets: Sequence[Market], excluded: Sequence[Dict[str, str]],
            profiles: Sequence[str] = PROFILES, timeframes: Sequence[str] = TIMEFRAMES,
            progress=None) -> Dict[str, Any]:
    """Run the protocol. Returns everything a record of it needs."""
    say = progress or (lambda message: None)
    audits = _audits(markets, timeframes[0])
    audited = {name: report["passed"] for name, report in audits.items()}
    say("audit: " + ", ".join("{} {}".format(n, "ok" if ok else "FAILED")
                              for n, ok in audited.items()))

    families: List[Tuple[Family, Dict[str, Outcome], Strategy]] = []
    for profile in profiles:
        for timeframe in timeframes:
            for strategy in strategies.CANDIDATES:
                family, outcomes = research_grid(markets, strategy, timeframe, profile)
                families.append((family, outcomes, strategy))
                say("research grid {}: best {}".format(family.key, max(
                    (row["expectancy_r"] for row in family.grid
                     if row["expectancy_r"] is not None), default=None)))
    trials = sum(len(family.grid) for family, _, _ in families)

    for family, outcomes, strategy in families:
        judge_research(family, outcomes, markets, strategy, trials, audited)
        say("research verdict {}: {}".format(family.key, family.state))

    # ── validation: frozen, and corrected for how many reached it ──
    reached = [(f, s) for f, _, s in families if f.state == APPROVED]
    p_values: Dict[str, float] = {}
    for family, strategy in reached:
        judge_later(family, markets, strategy, "validation")
        failed = _failed_later(family.stages["validation"], THRESHOLDS["validation"],
                               "validation")
        if failed:
            family.state, family.reasons = REJECTED, family.reasons + failed
        else:
            p_values[family.key] = family.stages["validation"]["interval"]["p_value"]
    survivors = stats.holm(p_values, THRESHOLDS["validation"]["alpha"])
    for family, strategy in reached:
        if family.state != APPROVED:
            continue
        if family.key not in survivors:
            family.state = REJECTED
            family.reasons.append(
                "Passed validation on its own (p = {:.4f}) but not once the {} families "
                "that reached validation are corrected for.".format(
                    p_values[family.key], len(p_values)))
        else:
            family.state = VALIDATED
        say("validation verdict {}: {}".format(family.key, family.state))

    # ── holdout: opened once, for what is left ─────────────────
    opened = [(f, s) for f, s in reached if f.state == VALIDATED]
    for family, strategy in opened:
        outcome = judge_later(family, markets, strategy, "holdout")
        failed = _failed_later(family.stages["holdout"], THRESHOLDS["holdout"], "holdout")
        floor = int(THRESHOLDS["holdout"]["market_min_trades"])
        family.stages["holdout"]["markets_for_paper"] = sorted(
            symbol for symbol, mean in outcome.market_means(floor).items() if mean > 0)
        if failed:
            family.state, family.reasons = REJECTED, family.reasons + failed
        else:
            family.state = CONFIRMED
        say("holdout verdict {}: {}".format(family.key, family.state))

    return {
        "protocol_version": PROTOCOL_VERSION, "thresholds": THRESHOLDS,
        "windows": {"validation_start": VALIDATION_START, "holdout_start": HOLDOUT_START,
                    "history_required_from": HISTORY_REQUIRED_FROM},
        "universe": [next(iter(m.data.values())).describe() for m in markets],
        "excluded": list(excluded), "audits": audits, "trials": trials,
        "profiles": {p: costs.describe(markets[0].instrument, p) for p in profiles},
        "execution_assumptions": list(EXECUTION_ASSUMPTIONS),
        "holdout_opened_for": [f.key for f, _ in opened],
        "benchmarks": _benchmarks(markets, timeframes[-1]),
        "families": [family for family, _, _ in families],
    }


def _benchmarks(markets: Sequence[Market], timeframe: str) -> Dict[str, Dict[str, Any]]:
    """Buy and hold over the stretches the study has looked at — what doing
    nothing clever returned. Not the holdout: that is not looked at here."""
    out: Dict[str, Dict[str, Any]] = {}
    for market in markets:
        close = market.bars(timeframe).close
        out[market.symbol] = {}
        for stage in ("research", "validation"):
            first, last = market.span(timeframe, stage)
            out[market.symbol][stage] = metrics.buy_and_hold(close[first:last])
    return out


def artifacts_dir(experiment_id: str) -> Path:
    path = DATA_DIR / "research" / "artifacts" / experiment_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def family_dict(family: Family) -> Dict[str, Any]:
    return {"key": family.key, "strategy": family.strategy, "timeframe": family.timeframe,
            "profile": family.profile, "state": family.state, "reasons": family.reasons,
            "chosen": family.chosen, "grid": family.grid, "stages": family.stages}


def write_artifacts(experiment_id: str, result: Dict[str, Any]) -> str:
    path = artifacts_dir(experiment_id) / "families.json"
    path.write_text(json.dumps([family_dict(f) for f in result["families"]], indent=1,
                               default=str))
    return str(path)
