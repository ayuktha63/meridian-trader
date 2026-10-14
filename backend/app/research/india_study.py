"""The 15-minute NSE equity study: ten intraday hypotheses, a protocol frozen
and hashed before any result, three stretches of history, and every
configuration counted.

    python -m app.research.india_study freeze      write the protocol; prints its hash
    python -m app.research.india_study show        what is frozen, and what data is usable
    python -m app.research.india_study run         research stretch for every family; later
                                                   stretches only for those that pass it

What is held fixed, and where:

* **The protocol** (`research/nse_15m_universe_study/protocol.json`) holds the
  universe, the stretches, the account, the thresholds and every declared
  configuration. `run` recomputes it from the code and refuses to start if it
  differs from the file — the grid cannot change after it was frozen. Its
  hash goes into every record.
* **The thresholds are `study.THRESHOLDS`, unchanged.** Nothing is loosened
  here. A threshold that cannot be computed is reported as NOT EVALUABLE and
  the family does not pass.
* **Data** is only a snapshot `india.accept()` clears. The universe is
  today's Nifty 50 members: *survivorship-biased*, and every result says so.
* **Costs** are charged order by order (`india_costing`) for the declared
  account, as an intraday product. The simulator is run without costs.
* **History is cut by date.** A family that fails a stretch never sees the
  next one. The holdout is opened once, for what survived.

Nothing here writes to the application's database or can place an order. A
record is a JSON file beside the datasets (`registry.digest` hashes it).
"""
from __future__ import annotations

import argparse
import calendar
import csv
import hashlib
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from .. import costs_india
from ..config import REPO_DIR
from ..instruments import Instrument
from . import india, india_costing, india_strategies, registry, stats, study, versions
from .audit import audit
from .exchange_calendar import NSE_EQUITY
from .india_costing import Account
from .india_strategies import Day
from .simulator import MARKET, Bars, Signal, Trade, reference, simulate
from .strategies import Plan, Strategy, label

PROTOCOL_VERSION = "1"
FOLDER = REPO_DIR / "research" / "nse_15m_universe_study"
PROTOCOL_PATH = FOLDER / "protocol.json"
WEEK = 604_800
IST = 19_800


def _ist(date: str) -> float:
    return float(calendar.timegm(time.strptime(date, "%Y-%m-%d"))) - IST


#: Declared before any result. Dates are local midnights (IST).
STRETCHES = {
    "research": (_ist("2024-10-14"), _ist("2025-07-01")),
    "validation": (_ist("2025-07-01"), _ist("2026-03-01")),
    "holdout": (_ist("2026-03-01"), _ist("2026-10-13")),     # to the last complete day
}

#: The 25 most-traded Nifty 50 stocks over the 60 sessions before the study,
#: chosen by session `meridian-trader-0e` before any result and recorded here.
UNIVERSE = (
    "HDFCBANK", "BSE", "RELIANCE", "ICICIBANK", "BHARTIARTL", "INFY", "ETERNAL", "SBIN",
    "BAJFINANCE", "AXISBANK", "TCS", "M&M", "KOTAKBANK", "LT", "MARUTI", "SHRIRAMFIN",
    "ADANIENT", "TATASTEEL", "ITC", "BEL", "ADANIPORTS", "HINDALCO", "TECHM", "JIOFIN",
    "TITAN")

ACCOUNT = Account(equity=100_000.0, risk_fraction=0.005, product=costs_india.INTRADAY,
                  leverage=1.0)
#: Reported beside the primary account, never used to choose.
OTHER_EQUITIES = (50_000.0, 500_000.0)
COST_MULTIPLES = (1.0, 1.5, 2.0)
RANDOM_DRAWS = 200
AUDIT_SAMPLES = 24

LIMITS = (
    "The universe is today's Nifty 50 members: stocks that survived and grew into the "
    "index. Every result on their past is flattered by that; there is no membership "
    "history to correct it.",
    "Two years of 15-minute history were fetched. The research stretch is under nine "
    "months of one market's weather.",
    "Spread and slippage are assumed (conservative scenario), not measured. No fill has "
    "ever been observed.",
    "Positions are flat by the 15:00 bar's close; the live engine squares off 20 minutes "
    "before 15:30. Intraday leverage is not assumed: the account is sized at 1x.",
    "Trades are sized against a fixed Rs 1,00,000 and do not compound.",
    "Angel One's 15-minute history carries flat, zero-volume closing bars on some days "
    "(docs/research/ANGEL_ONE_DATA_QUALITY.md); VWAP uses volume, so those days are noisy.",
    "Adjusted prices: a stock that has since split looks cheaper in its past than it was.",
)


def protocol() -> Dict[str, Any]:
    """Everything that must not change after the first result."""
    return {
        "protocol_version": PROTOCOL_VERSION,
        "timeframe": "15m", "entry": "next bar's open, market order",
        "universe": {"id": "nifty50", "members": list(UNIVERSE), "membership": "current",
                     "survivorship_free": False},
        "stretches": {name: [lo, hi] for name, (lo, hi) in STRETCHES.items()},
        "stretches_text": {name: [time.strftime("%Y-%m-%d", time.gmtime(lo + IST)),
                                  time.strftime("%Y-%m-%d", time.gmtime(hi + IST))]
                           for name, (lo, hi) in STRETCHES.items()},
        "account": ACCOUNT.describe(), "other_equities_reported": list(OTHER_EQUITIES),
        "cost_scenario_for_eligibility": costs_india.ELIGIBILITY_SCENARIO,
        "cost_multiples": list(COST_MULTIPLES),
        "thresholds": study.THRESHOLDS,
        "threshold_source": "app/research/study.py (PROTOCOL_VERSION {}), unchanged".format(
            study.PROTOCOL_VERSION),
        "random_draws": RANDOM_DRAWS, "audit_samples": AUDIT_SAMPLES,
        "trials": india_strategies.trials(),
        "strategies": india_strategies.declared(),
        "not_built": dict(india_strategies.NOT_BUILT),
        "session": {"flat_by": "15:00 bar close", "last_entry": "13:30"},
        "limitations": list(LIMITS),
    }


def digest_of(body: Dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":"),
                                     default=str).encode()).hexdigest()


def freeze(force: bool = False) -> Dict[str, Any]:
    body = protocol()
    if PROTOCOL_PATH.exists() and not force:
        raise RuntimeError("A protocol is already frozen at {}; freezing again would be "
                           "changing it after the fact.".format(PROTOCOL_PATH))
    FOLDER.mkdir(parents=True, exist_ok=True)
    record = {"sha256": digest_of(body), "frozen_at": time.time(), "body": body}
    PROTOCOL_PATH.write_text(json.dumps(record, indent=2, sort_keys=True))
    return record


def frozen() -> Dict[str, Any]:
    """The protocol on file, checked against the code that would run it."""
    if not PROTOCOL_PATH.exists():
        raise RuntimeError("No protocol is frozen. Run `freeze` first: results are not "
                           "recorded against a protocol that was written afterwards.")
    record = json.loads(PROTOCOL_PATH.read_text())
    if digest_of(record["body"]) != record["sha256"]:
        raise RuntimeError("The frozen protocol file was edited after it was written.")
    if digest_of(protocol()) != record["sha256"]:
        raise RuntimeError("The code no longer matches the frozen protocol (a grid, a "
                           "threshold, a stretch or the universe changed). Restore it, or "
                           "freeze a new protocol with --force and say why.")
    return record


# ── markets ───────────────────────────────────────────────────

def _instrument(symbol: str) -> Instrument:
    """An NSE cash share. Costs are order-level (`india_costing`), so only the
    segment matters to the simulator."""
    return Instrument(symbol, symbol, "stock", 2, 0.05, 1.0, 1.0, "INR", ["india"],
                      venue="NSE", broker_symbol=symbol, exchange="NSE",
                      tradable_in_india=True)


@dataclass
class Mkt:
    symbol: str
    dataset_id: str
    frame: pd.DataFrame
    bars: Bars
    day: Day
    instrument: Instrument
    _plans: Dict[tuple, Plan] = field(default_factory=dict)

    def span(self, stage: str) -> Tuple[int, int]:
        times = self.frame["time"].to_numpy()
        lo, hi = STRETCHES[stage]
        return int(np.searchsorted(times, lo, "left")), int(np.searchsorted(times, hi, "left"))

    def plan(self, strategy: Strategy, params: Dict[str, float], stage: str) -> Plan:
        key = (strategy.name, label(params), stage)
        if key not in self._plans:
            _, last = self.span(stage)
            self._plans[key] = strategy.build(self.frame.iloc[:last].reset_index(drop=True),
                                              params)
        return self._plans[key]


def make_market(symbol: str, dataset_id: str, frame: pd.DataFrame) -> Mkt:
    return Mkt(symbol, dataset_id, frame, Bars.from_frame(frame, "15m"), Day(frame),
               _instrument(symbol))


def load_markets(universe: Optional[india.Universe] = None
                 ) -> Tuple[List[Mkt], List[Dict[str, Any]]]:
    """Declared members with a snapshot a study may use, and every one left
    out with why."""
    universe = universe or india.load_universe("nifty50")
    snapshots = india.latest_snapshots(universe, "15m")
    markets: List[Mkt] = []
    left_out: List[Dict[str, Any]] = []
    for symbol in UNIVERSE:
        dataset = snapshots.get(symbol)
        if dataset is None:
            left_out.append({"symbol": symbol, "reason": "no 15-minute snapshot"})
            continue
        reasons = india.accept(dataset)
        if reasons:
            left_out.append({"symbol": symbol, "dataset_id": dataset.id, "reason": reasons[0]})
            continue
        markets.append(make_market(symbol, dataset.id, dataset.frame()))
    return markets, left_out


# ── one configuration ─────────────────────────────────────────

@dataclass
class Result:
    strategy: str
    params: Dict[str, float]
    stage: str
    costed: List[india_costing.Costed]
    skipped: int
    spanning: int
    by_market: Dict[str, List[india_costing.Costed]]

    @property
    def r(self) -> np.ndarray:
        return np.array([c.r for c in self.costed], dtype=float)

    @property
    def weeks(self) -> np.ndarray:
        return np.array([int(c.trade.exit_time // WEEK) for c in self.costed], dtype=int)

    def weekly(self, stage: str) -> np.ndarray:
        lo, hi = STRETCHES[stage]
        return stats.per_period(self.r, self.weeks, int(lo // WEEK), int((hi - 1) // WEEK))

    def market_means(self, min_trades: int) -> Dict[str, float]:
        return {s: float(np.mean([c.r for c in cs])) for s, cs in self.by_market.items()
                if len(cs) >= min_trades}


def _same_day(trade: Trade) -> bool:
    days = NSE_EQUITY.local_day([trade.entry_time, trade.exit_time])
    return bool(days[0] == days[1])


def trade_market(mkt: Mkt, signals: Dict[int, Signal], stage: str,
                 account: Account = ACCOUNT, multiplier: float = 1.0
                 ) -> Tuple[List[india_costing.Costed], int, int]:
    first, last = mkt.span(stage)
    raw = simulate(mkt.bars, signals, mkt.instrument, start=first, end=last,
                   include_costs=False)
    same = [t for t in raw if _same_day(t)]
    run = india_costing.cost_trades(same, account, entry_kind=MARKET, multiplier=multiplier)
    return run.taken, len(run.skipped), len(raw) - len(same)


def run_config(markets: Sequence[Mkt], strategy: Strategy, params: Dict[str, float],
               stage: str, account: Account = ACCOUNT, multiplier: float = 1.0) -> Result:
    by_market: Dict[str, List[india_costing.Costed]] = {}
    skipped = spanning = 0
    for mkt in markets:
        first, last = mkt.span(stage)
        if last - first < 100:
            continue
        taken, not_sized, crossed = trade_market(
            mkt, mkt.plan(strategy, params, stage).signals, stage, account, multiplier)
        by_market[mkt.symbol] = taken
        skipped += not_sized
        spanning += crossed
    pooled = sorted((c for cs in by_market.values() for c in cs),
                    key=lambda c: c.trade.exit_time)
    return Result(strategy.name, dict(params), stage, pooled, skipped, spanning, by_market)


def matched_random(mkt: Mkt, strategy: Strategy, params: Dict[str, float], stage: str,
                   taken: Sequence[india_costing.Costed], rng: np.random.Generator
                   ) -> Dict[int, Signal]:
    """Entries with no skill, built from the strategy's own trades: each keeps
    its stop and target distances in ATRs, its trail and how long it was held
    (cut to the session) — moved to a random bar, in a random direction."""
    first, last = mkt.span(stage)
    plan = mkt.plan(strategy, params, stage)
    bars, day = mkt.bars, mkt.day
    ok = np.array([i for i in range(first, last - 1)
                   if day.can_enter(i) and day.hold[i] >= 1 and bars.atr[i] > 0])
    null: Dict[int, Signal] = {}
    if not len(ok):
        return null
    for c in taken:
        signal = plan.signals.get(c.trade.signal_index)
        scale = bars.atr[c.trade.signal_index]
        if signal is None or scale <= 0:
            continue
        ref = reference(signal, bars.close[c.trade.signal_index])
        stop_atrs = abs(ref - signal.stop) / scale
        target_atrs = None if signal.target is None else abs(signal.target - ref) / scale
        j = int(rng.choice(ok))
        if j in null:
            continue
        direction = 1 if rng.random() < 0.5 else -1
        price, unit = bars.close[j], bars.atr[j]
        null[j] = Signal(
            direction, price - direction * stop_atrs * unit,
            target=None if target_atrs is None else price + direction * target_atrs * unit,
            trail_atr=signal.trail_atr,
            max_hold=int(min(max(c.trade.bars_held, 1), day.hold[j])), tag="random")
    return null


def random_baseline(markets: Sequence[Mkt], strategy: Strategy, result: Result,
                    draws: int, seed: int = 7) -> np.ndarray:
    by_symbol = {m.symbol: m for m in markets}
    rng = np.random.default_rng(seed)
    means = np.empty(draws)
    for draw in range(draws):
        pooled: List[float] = []
        for symbol, taken in result.by_market.items():
            if not taken:
                continue
            null = matched_random(by_symbol[symbol], strategy, result.params, result.stage,
                                  taken, rng)
            got, _, _ = trade_market(by_symbol[symbol], null, result.stage)
            pooled += [c.r for c in got]
        means[draw] = float(np.mean(pooled)) if pooled else 0.0
    return means


# ── judging ───────────────────────────────────────────────────

NOT_EVALUABLE = "NOT EVALUABLE"


def _longest_losing_run(r: np.ndarray) -> int:
    best = run = 0
    for value in r:
        run = run + 1 if value < 0 else 0
        best = max(best, run)
    return best


def _drawdown(r: np.ndarray) -> float:
    curve = np.cumsum(r)
    return float((np.maximum.accumulate(np.r_[0.0, curve])[1:] - curve).max()) if len(r) else 0.0


def describe(result: Result, level: float) -> Dict[str, Any]:
    r = result.r
    out: Dict[str, Any] = {
        "parameters": result.params, "trades": int(len(r)),
        "signals_not_sized": result.skipped, "dropped_spanning_sessions": result.spanning}
    if not len(r):
        return out
    estimate = stats.cluster_mean_interval(r, result.weeks, level=level)
    taken = result.costed
    summary = india_costing.summary(india_costing.Run(ACCOUNT, 1.0, list(taken), []))
    out.update({
        "expectancy_r": round(float(r.mean()), 4),
        "gross_expectancy_r": round(float(np.mean([c.gross_r for c in taken])), 4),
        "cost_r": round(float(np.mean([c.cost_r for c in taken])), 4),
        "interval": {"level": level, "low": round(estimate.low, 4),
                     "high": round(estimate.high, 4), "p_value": round(estimate.p_value, 5),
                     "weeks": estimate.block, "method": "cluster bootstrap by exit week"},
        "win_rate": round(float((r > 0).mean()), 4),
        "profit_factor": (round(float(r[r > 0].sum() / -r[r < 0].sum()), 3)
                          if (r < 0).any() and (r > 0).any() else None),
        "longest_losing_run": _longest_losing_run(r),
        "max_drawdown_r": round(_drawdown(r), 2),
        "mae_r": round(float(np.mean([c.trade.mae_r for c in taken])), 3),
        "mfe_r": round(float(np.mean([c.trade.mfe_r for c in taken])), 3),
        "gross_pnl": summary.get("gross_pnl"), "total_costs": summary.get("total_costs"),
        "net_pnl": summary.get("net_pnl"),
        "cost_pct_of_turnover": summary.get("cost_pct_of_turnover"),
        "charges": summary.get("charges"),
        "expectancy_rupees": summary.get("expectancy_rupees"),
        "break_even_cost_multiple": summary.get("break_even_cost_multiple"),
    })
    return out


@dataclass
class Family:
    strategy: str
    grid: List[Dict[str, Any]] = field(default_factory=list)
    chosen: Optional[Dict[str, float]] = None
    stages: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    state: str = study.INSUFFICIENT
    reasons: List[str] = field(default_factory=list)
    not_evaluable: List[str] = field(default_factory=list)


def cost_sensitivity(markets: Sequence[Mkt], strategy: Strategy, params: Dict[str, float],
                     stage: str) -> List[Dict[str, Any]]:
    rows = []
    for scenario in ("optimistic", "base", "conservative"):
        for multiple in COST_MULTIPLES:
            account = Account(ACCOUNT.equity, ACCOUNT.risk_fraction, ACCOUNT.product,
                              ACCOUNT.leverage, scenario=scenario)
            r = run_config(markets, strategy, params, stage, account, multiple).r
            rows.append({"scenario": scenario, "multiple": multiple, "trades": int(len(r)),
                         "expectancy_r": round(float(r.mean()), 4) if len(r) else None})
    return rows


def other_accounts(markets: Sequence[Mkt], strategy: Strategy, params: Dict[str, float],
                   stage: str) -> List[Dict[str, Any]]:
    rows = []
    for equity in OTHER_EQUITIES:
        account = Account(equity, ACCOUNT.risk_fraction, ACCOUNT.product, ACCOUNT.leverage)
        result = run_config(markets, strategy, params, stage, account)
        r = result.r
        rows.append({"equity": equity, "trades": int(len(r)), "not_sized": result.skipped,
                     "expectancy_r": round(float(r.mean()), 4) if len(r) else None})
    return rows


def judge_research(markets: Sequence[Mkt], strategy: Strategy, family: Family,
                   results: Dict[str, Result], audited: bool, trials: int) -> None:
    rule = study.THRESHOLDS["research"]
    reasons = family.reasons
    if not audited:
        family.state = study.REJECTED
        reasons.append("Failed the look-ahead audit: not tested further.")
        return
    enough = [row for row in family.grid if row["trades"] >= rule["min_trades"]]
    if not enough:
        family.state = study.INSUFFICIENT
        reasons.append("No configuration made {} trades on the research stretch (most: {}).".format(
            int(rule["min_trades"]), max((row["trades"] for row in family.grid), default=0)))
        return
    best = max(enough, key=lambda row: row["expectancy_r"])
    family.chosen = dict(best["parameters"])
    result = results[best["label"]]
    stage = describe(result, rule["interval_level"])
    interval = stage["interval"]
    weekly = result.weekly("research")
    deflated = stats.deflated_sharpe(weekly, trials) if len(weekly) > 2 else None
    neighbours = stats.positive_share([row["expectancy_r"] for row in family.grid
                                       if row["expectancy_r"] is not None])
    means = result.market_means(int(rule["market_min_trades"]))
    breadth = stats.positive_share(list(means.values())) if means else None
    stress = run_config(markets, strategy, family.chosen, "research",
                        multiplier=rule["cost_stress"])
    stressed = float(stress.r.mean()) if len(stress.r) else 0.0
    stage.update({
        "deflated_sharpe": None if deflated is None else round(deflated, 4),
        "trials_deflated_for": trials, "weeks": int(len(weekly)),
        "neighbours_positive": round(neighbours, 3),
        "markets_positive": None if breadth is None else round(breadth, 3),
        "markets_judged": len(means),
        "expectancy_at_stressed_costs_r": round(stressed, 4),
        "cost_sensitivity": cost_sensitivity(markets, strategy, family.chosen, "research"),
        "other_accounts": other_accounts(markets, strategy, family.chosen, "research"),
        "by_market": {s: {"trades": len(cs),
                          "expectancy_r": round(float(np.mean([c.r for c in cs])), 4)
                          if cs else None} for s, cs in sorted(result.by_market.items())},
    })
    family.stages["research"] = stage

    if stage["expectancy_r"] <= 0:
        reasons.append("Net expectancy on the research stretch is {:+.3f}R.".format(
            stage["expectancy_r"]))
    elif interval["low"] <= 0:
        reasons.append("Expectancy {:+.3f}R, but its {:.0f}% interval ({:+.3f}R to {:+.3f}R) "
                       "includes zero.".format(stage["expectancy_r"], 100 * interval["level"],
                                               interval["low"], interval["high"]))
    if deflated is None:
        family.not_evaluable.append("deflated Sharpe (fewer than 3 weeks)")
        reasons.append("Deflated Sharpe: " + NOT_EVALUABLE + ".")
    elif deflated < rule["deflated_sharpe"]:
        reasons.append("Deflated Sharpe {:.2f} (need {:.2f}) after {} configurations.".format(
            deflated, rule["deflated_sharpe"], trials))
    if neighbours < rule["neighbours_positive"]:
        reasons.append("Only {:.0f}% of its own parameter grid made money (need {:.0f}%).".format(
            100 * neighbours, 100 * rule["neighbours_positive"]))
    if breadth is None:
        family.not_evaluable.append("share of markets profitable (no market had {} trades)".format(
            int(rule["market_min_trades"])))
        reasons.append("Markets profitable: " + NOT_EVALUABLE + ".")
    elif breadth < rule["markets_positive"]:
        reasons.append("Positive on {:.0f}% of markets (need {:.0f}%).".format(
            100 * breadth, 100 * rule["markets_positive"]))
    if stressed <= 0:
        reasons.append("Loses money with costs {:g}x the modelled ones ({:+.3f}R).".format(
            rule["cost_stress"], stressed))
    if reasons:
        family.state = study.REJECTED
        return
    null = random_baseline(markets, strategy, result, int(rule["random_draws"]))
    p_value = stats.permutation_p_value(stage["expectancy_r"], null)
    stage["random_entries"] = {"draws": int(rule["random_draws"]),
                               "mean_r": round(float(null.mean()), 4),
                               "best_r": round(float(null.max()), 4),
                               "p_value": round(p_value, 5)}
    if p_value > rule["random_p_value"]:
        family.state = study.REJECTED
        reasons.append("No better than entries at random bars with the same exits "
                       "(p = {:.3f}).".format(p_value))
        return
    family.state = study.APPROVED


def judge_later(markets: Sequence[Mkt], strategy: Strategy, family: Family,
                name: str) -> Dict[str, Any]:
    rule = study.THRESHOLDS[name]
    result = run_config(markets, strategy, family.chosen, name)
    stage = describe(result, rule["interval_level"])
    means = result.market_means(int(rule["market_min_trades"]))
    stage["markets_positive"] = (round(stats.positive_share(list(means.values())), 3)
                                 if means else None)
    stage["markets_judged"] = len(means)
    family.stages[name] = stage
    return stage


def failed_later(stage: Dict[str, Any], name: str) -> List[str]:
    rule = study.THRESHOLDS[name]
    if stage["trades"] < rule["min_trades"]:
        return ["Only {} trades on the {} stretch (need {}).".format(
            stage["trades"], name, int(rule["min_trades"]))]
    out = []
    if stage["interval"]["low"] <= 0:
        out.append("On the {} stretch: expectancy {:+.3f}R, interval {:+.3f}R to {:+.3f}R "
                   "includes zero.".format(name, stage["expectancy_r"],
                                           stage["interval"]["low"], stage["interval"]["high"]))
    floor = rule.get("markets_positive")
    if floor is not None:
        share = stage["markets_positive"]
        if share is None:
            out.append("Markets profitable on the {} stretch: {}.".format(name, NOT_EVALUABLE))
        elif share < floor:
            out.append("On the {} stretch: positive on {:.0f}% of markets (need {:.0f}%).".format(
                name, 100 * share, 100 * floor))
    return out


def audit_strategy(markets: Sequence[Mkt], strategy: Strategy,
                   params: Dict[str, float]) -> Dict[str, Any]:
    """The look-ahead and warm-up audit on up to three markets' research frames."""
    reports = []
    for mkt in list(markets)[:3]:
        _, last = mkt.span("research")
        reports.append(audit(strategy, mkt.frame.iloc[:last].reset_index(drop=True), params,
                             samples=AUDIT_SAMPLES).to_dict())
    return {"passed": bool(reports) and all(r["passed"] for r in reports), "reports": reports}


# ── the run ───────────────────────────────────────────────────

def evaluate(markets: Sequence[Mkt], say=print) -> Tuple[List[Family], List[Dict[str, Any]]]:
    """Every family on the research stretch; the later stretches for those
    that pass it, one stretch at a time."""
    trials = india_strategies.trials()
    families: List[Family] = []
    configs: List[Dict[str, Any]] = []
    held: Dict[str, Strategy] = {}
    for strategy in india_strategies.CANDIDATES:
        family, results = Family(strategy.name), {}
        for params in strategy.grid:
            result = run_config(markets, strategy, params, "research")
            results[label(params)] = result
            r = result.r
            row = {"parameters": dict(params), "label": label(params), "trades": int(len(r)),
                   "expectancy_r": round(float(r.mean()), 4) if len(r) else None,
                   "gross_expectancy_r": (round(float(np.mean([c.gross_r for c in result.costed])), 4)
                                          if len(r) else None),
                   "not_sized": result.skipped, "dropped_spanning": result.spanning}
            family.grid.append(row)
            configs.append(dict(row, strategy=strategy.name, stage="research"))
        audited = audit_strategy(markets, strategy, strategy.grid[0])
        family.stages["audit"] = audited
        judge_research(markets, strategy, family, results, audited["passed"], trials)
        say("  {:<18} {:<22} best {}".format(strategy.name, family.state, max(
            (row["expectancy_r"] for row in family.grid if row["expectancy_r"] is not None),
            default=None)))
        families.append(family)
        held[strategy.name] = strategy

    for family in families:
        if family.state != study.APPROVED:
            continue
        strategy = held[family.strategy]
        for name, passed in (("validation", study.VALIDATED), ("holdout", study.CONFIRMED)):
            stage = judge_later(markets, strategy, family, name)
            configs.append(dict(stage, strategy=strategy.name, stage=name,
                                label=label(family.chosen)))
            bad = failed_later(stage, name)
            if bad:
                family.state, family.reasons = study.REJECTED, family.reasons + bad
                break
            family.state = passed
        say("  {} → {}".format(family.strategy, family.state))
    return families, configs


def execute(say=print) -> Dict[str, Any]:
    record = frozen()
    markets, left_out = load_markets()
    if not markets:
        raise RuntimeError("No accepted 15-minute snapshot of a declared member exists.")
    started = time.time()
    say("protocol {} · {} markets · {} configurations".format(
        record["sha256"][:12], len(markets), india_strategies.trials()))
    families, configs = evaluate(markets, say)
    return {"protocol": record["sha256"], "started": started,
            "markets": [m.symbol for m in markets],
            "datasets": {m.symbol: m.dataset_id for m in markets}, "left_out": left_out,
            "families": families, "configs": configs}


def write(outcome: Dict[str, Any]) -> Path:
    """The record and its tables, beside the protocol. A rerun makes a new
    folder; nothing is overwritten."""
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(outcome["started"]))
    folder = FOLDER / "results" / stamp
    folder.mkdir(parents=True, exist_ok=True)
    families = [{"strategy": f.strategy, "state": f.state, "reasons": f.reasons,
                 "not_evaluable": f.not_evaluable, "chosen": f.chosen, "grid": f.grid,
                 "stages": f.stages} for f in outcome["families"]]
    body = {"universe": protocol()["universe"], "markets_used": outcome["markets"],
            "datasets": outcome["datasets"], "left_out": outcome["left_out"],
            "configurations_tried": len(outcome["configs"]),
            "families": families, "limitations": list(LIMITS),
            "not_built": dict(india_strategies.NOT_BUILT)}
    def here(path: str) -> str:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()[:12]
    record = registry.build(
        kind="india_study", stage="research", strategy="nse_15m_candidates", symbol="UNIVERSE",
        timeframe="15m", venue="angelone", cost_profile="india:stock:intraday",
        verdict="; ".join("{}={}".format(f.strategy, f.state) for f in outcome["families"]),
        datasets=[{"symbol": s, "dataset_id": d} for s, d in outcome["datasets"].items()],
        window={k: list(v) for k, v in STRETCHES.items()},
        parameters={"protocol": outcome["protocol"]},
        assumptions={"costs": costs_india.describe(costs_india.INTRADAY),
                     "execution": list(study.EXECUTION_ASSUMPTIONS)},
        metrics={"families": {f["strategy"]: f["state"] for f in families}},
        reasons=[], trials=india_strategies.trials(), extra={
            "protocol_sha256": outcome["protocol"], "survivorship_free": False,
            "code": dict(versions.current(), india_strategies=here(india_strategies.__file__),
                         india_study=here(__file__))})
    record["sha256"] = registry.digest(record)
    (registry.root() / (record["id"] + ".json")).write_text(
        json.dumps(record, indent=2, sort_keys=True, default=str))
    (folder / "results.json").write_text(json.dumps(
        dict(body, experiment_id=record["id"]), indent=2, sort_keys=True, default=str))
    with open(folder / "configurations.csv", "w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["strategy", "stage", "parameters", "trades", "expectancy_r",
                         "gross_expectancy_r", "not_sized", "dropped_spanning"])
        for c in outcome["configs"]:
            writer.writerow([c["strategy"], c["stage"], c.get("label") or c.get("parameters"),
                             c.get("trades"), c.get("expectancy_r"), c.get("gross_expectancy_r"),
                             c.get("not_sized", c.get("signals_not_sized")),
                             c.get("dropped_spanning", c.get("dropped_spanning_sessions"))])
    return folder


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.research.india_study",
                                     description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=("freeze", "show", "run"))
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)
    if args.command == "freeze":
        record = freeze(args.force)
        print("frozen {} ({} configurations)".format(record["sha256"], record["body"]["trials"]))
        return 0
    if args.command == "show":
        markets, left_out = load_markets()
        print("usable: {}".format(" ".join(m.symbol for m in markets)))
        for item in left_out:
            print("  left out {:<11} {}".format(item["symbol"], item["reason"][:100]))
        print("configurations declared: {}".format(india_strategies.trials()))
        print("frozen: {}".format(PROTOCOL_PATH.exists()))
        return 0
    folder = write(execute())
    print("written: {}".format(folder))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
