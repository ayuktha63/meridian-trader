"""The opening-range breakout of `arshadakl/nse-orb-backtest`, tested.

    python -m app.research.orb_repo_study freeze     write the protocol and its hash
    python -m app.research.orb_repo_study fetch-index  take NIFTY 50 5-minute bars (read-only)
    python -m app.research.orb_repo_study run        run it, once, against that protocol

The protocol (`research/orb_repo_5m_study/protocol.json`, described in
`docs/ORB_REPO_PROTOCOL.md`) is written before any result exists. `run`
recomputes it from this file and refuses if it differs from the one on disk.
Two variants are tested, the two the source offers; nothing is tuned. The
first stretch is judged under the whole gate, and a stretch is opened only
if every earlier one passed. Nothing here can place an order.
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import json
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from .. import costs_india
from . import datasets, india, india_costing, orb_repo, stats
from .exchange_calendar import NSE_EQUITY
from .india_costing import Account, Costed
from .india_study import STRETCHES
from .orb_repo import EXECUTABLE, PUBLISHED, Rules, Setup
from .simulator import MARKET, Trade

PROTOCOL_VERSION = "1"
REPO_DIR = Path(__file__).resolve().parents[3]
FOLDER = REPO_DIR / "research" / "orb_repo_5m_study"
PROTOCOL_PATH = FOLDER / "protocol.json"
WEEK = 604_800
IST = 19_800

#: The source's own fifteen, less the ones not in today's Nifty 50 under that
#: name (TATAMOTORS is now TMPV after the demerger and its history is not the
#: same company's). Fixed before any data was fetched.
UNIVERSE = ("HDFCBANK", "ICICIBANK", "SBIN", "AXISBANK", "BAJFINANCE", "INFY", "TCS",
            "RELIANCE", "LT", "ADANIPORTS", "MARUTI", "HINDALCO", "TITAN", "DRREDDY")
INDEX = "NIFTY"
#: Opened in order; a later one only if every earlier one passed.
ORDER = ("research", "oos")
STAGES: Dict[str, Tuple[float, float]] = {
    "research": STRETCHES["research"],
    "oos": (STRETCHES["validation"][0], STRETCHES["holdout"][1]),
}
ACCOUNT = Account(equity=100_000.0, risk_fraction=0.005, product=costs_india.INTRADAY,
                  leverage=1.0)
COST_MULTIPLES = (1.0, 1.5, 2.0)
RANDOM_DRAWS = 200
TRIALS = 2                       # the two variants; the source's own tuning is unknown
GATE: Dict[str, float] = {
    "min_trades": 200, "interval_level": 0.95, "deflated_sharpe": 0.95, "cost_stress": 1.5,
    "random_p_value": 0.05, "markets_positive": 0.60, "market_min_trades": 20,
    "delay_bars": 1,
}

LIMITS = (
    "The universe is today's Nifty 50 members among the source's fifteen: survivors. Every "
    "result on their past is flattered by that.",
    "The source states a 57% win rate from 'a published Nifty backtest (Jul–Oct 2025)'. If its "
    "rules were chosen on that period, the first stretch (to 2025-07-01) precedes it and is "
    "out-of-sample for the choice; the second contains it and is not.",
    "Spread and slippage are assumed (conservative scenario), not measured.",
    "Sized against a fixed Rs 1,00,000 at 0.5% risk, 1x, not compounded; the source sizes "
    "differently and forces a minimum of one share.",
    "Adjusted prices; 5-minute history from Angel One carries its own gaps, each checked by "
    "`india.accept` before use.",
    "The NIFTY alignment filter reads NIFTY 50 spot bars, not a futures contract.",
)


def protocol() -> Dict[str, Any]:
    return {
        "protocol_version": PROTOCOL_VERSION, "timeframe": "5m",
        "source": orb_repo.SOURCE, "rules_version": orb_repo.VERSION,
        "variants": [r.describe() for r in orb_repo.VARIANTS], "trials": TRIALS,
        "universe": list(UNIVERSE), "index": INDEX,
        "stretches": {n: list(STAGES[n]) for n in ORDER}, "order": list(ORDER),
        "stretches_text": {n: [time.strftime("%Y-%m-%d", time.gmtime(STAGES[n][0] + IST)),
                               time.strftime("%Y-%m-%d", time.gmtime(STAGES[n][1] + IST))]
                           for n in ORDER},
        "account": ACCOUNT.describe(), "cost_scenario": costs_india.ELIGIBILITY_SCENARIO,
        "cost_multiples": list(COST_MULTIPLES), "random_draws": RANDOM_DRAWS, "gate": GATE,
        "execution": {"published": "the source's fills, no costs (diagnostic only)",
                      "executable": "gap-aware fills, entry-bar stop, sell at 15:15 open, "
                                    "order-by-order costs, no forced minimum size"},
        "controls": {"random": "same stop distance (fraction of price) and target multiple, "
                               "random entry bar 09:50–14:25 on a day with a full range, "
                               "long only, same exits",
                     "benchmark": "long at the 09:45 open every day with a full range, range-low "
                                  "stop, no target, sell at 15:15 open"},
        "limitations": list(LIMITS),
    }


def digest_of(body: Dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":"),
                                     default=str).encode()).hexdigest()


def freeze(force: bool = False) -> Dict[str, Any]:
    body = protocol()
    record = {"digest": digest_of(body), "protocol": body}
    if PROTOCOL_PATH.exists() and not force:
        raise SystemExit("A protocol is already frozen at {}.".format(PROTOCOL_PATH))
    FOLDER.mkdir(parents=True, exist_ok=True)
    PROTOCOL_PATH.write_text(json.dumps(record, indent=2, sort_keys=True, default=str))
    return record


def frozen() -> Dict[str, Any]:
    if not PROTOCOL_PATH.exists():
        raise SystemExit("No protocol is frozen: run `freeze` first.")
    record = json.loads(PROTOCOL_PATH.read_text())
    if digest_of(record["protocol"]) != record["digest"]:
        raise SystemExit("The frozen protocol file was edited.")
    if digest_of(json.loads(json.dumps(protocol(), default=str))) != record["digest"]:
        raise SystemExit("The code no longer matches the frozen protocol.")
    return record


# ── data ──────────────────────────────────────────────────────

@dataclass
class Market:
    symbol: str
    dataset_id: str
    frame: pd.DataFrame
    setups: Dict[str, List[Setup]]


def load(say=print) -> Tuple[List[Market], Optional[orb_repo.Index], List[Dict[str, Any]]]:
    """Accepted 5-minute snapshots of the universe, and NIFTY's. Whatever
    cannot be used is returned with the reason; nothing is filled in."""
    universe = india.load_universe("nifty50")
    snapshots = india.latest_snapshots(universe, "5m")
    left_out: List[Dict[str, Any]] = []
    index = _index_from_store(left_out)
    markets: List[Market] = []
    for symbol in UNIVERSE:
        ds = snapshots.get(symbol)
        if ds is None:
            left_out.append({"symbol": symbol, "reason": "no 5-minute snapshot"})
            continue
        why = india.accept(ds)
        if why:
            left_out.append({"symbol": symbol, "reason": "; ".join(why)})
            continue
        frame = ds.frame().reset_index(drop=True)
        setups = {r.name: orb_repo.find_setups(frame, r, index) for r in orb_repo.VARIANTS}
        markets.append(Market(symbol, ds.id, frame, setups))
        say("{:<11} {} {:>6} bars  setups: {}".format(symbol, ds.id, len(frame), ", ".join(
            "{} {}".format(k.replace("orb_repo_", ""), len(v)) for k, v in setups.items())))
    return markets, index, left_out


def _index_from_store(left_out: List[Dict[str, Any]]) -> Optional[orb_repo.Index]:
    for meta in datasets.catalogue():
        if meta.get("symbol") == INDEX and meta.get("timeframe") == "5m":
            ds = datasets.load(meta["sha256"][:16])
            why = india.accept(ds)
            if why:
                left_out.append({"symbol": INDEX, "reason": "; ".join(why)})
                return None
            return orb_repo.Index.from_frame(ds.frame().reset_index(drop=True))
    left_out.append({"symbol": INDEX, "reason": "no 5-minute snapshot"})
    return None


# ── running a variant over a stretch ──────────────────────────

def _in_stage(setups: Sequence[Setup], frame: pd.DataFrame, stage: str) -> List[Setup]:
    lo, hi = STAGES[stage]
    t = frame["time"].to_numpy(dtype=float)
    return [s for s in setups if lo <= t[s.entry] < hi]


@dataclass
class Run:
    variant: str
    stage: str
    by_market: Dict[str, List[Costed]]
    skipped: int
    candidates: int

    @property
    def costed(self) -> List[Costed]:
        return sorted((c for cs in self.by_market.values() for c in cs),
                      key=lambda c: c.trade.exit_time)

    @property
    def r(self) -> np.ndarray:
        return np.array([c.r for c in self.costed], dtype=float)

    @property
    def weeks(self) -> np.ndarray:
        return np.array([int(c.trade.exit_time // WEEK) for c in self.costed], dtype=int)


def run_variant(markets: Sequence[Market], rules: Rules, stage: str, *,
                multiplier: float = 1.0, delay: int = 0,
                account: Account = ACCOUNT) -> Run:
    by_market: Dict[str, List[Costed]] = {}
    skipped = candidates = 0
    for m in markets:
        picked = _in_stage(m.setups[rules.name], m.frame, stage)
        trades = orb_repo.play_all(m.frame, picked, EXECUTABLE, delay=delay)
        candidates += len(picked)
        got = india_costing.cost_trades(trades, account, entry_kind=MARKET,
                                        multiplier=multiplier)
        by_market[m.symbol] = got.taken
        skipped += len(got.skipped)
    return Run(rules.name, stage, by_market, skipped, candidates)


def _valid_days(m: Market) -> List[Tuple[int, int, int]]:
    """(first bar, bar after the range, one past the last) of each day that
    has all six range bars — the days the strategy could have traded."""
    t = m.frame["time"].to_numpy(dtype=float)
    sod = np.mod(t + IST, 86_400)
    _, spans = orb_repo._days(m.frame)
    out = []
    for _, a, b in spans:
        rng = np.flatnonzero(sod[a:b] < orb_repo.OR_END)
        if len(rng) == orb_repo.OR_BARS:
            out.append((a, a + orb_repo.OR_BARS, b))
    return out


def _cost(trades: Sequence[Trade], multiplier: float = 1.0) -> List[Costed]:
    return india_costing.cost_trades(list(trades), ACCOUNT, entry_kind=MARKET,
                                     multiplier=multiplier).taken


def benchmark(markets: Sequence[Market], stage: str) -> List[Costed]:
    """Long at the 09:45 open every day, range-low stop, no target."""
    lo, hi = STAGES[stage]
    out: List[Costed] = []
    for m in markets:
        t = m.frame["time"].to_numpy(dtype=float)
        low = m.frame["low"].to_numpy(dtype=float)
        trades = []
        for a, k, b in _valid_days(m):
            if not lo <= t[k] < hi:
                continue
            s = Setup(k - 1, k, a, b, 0.0, float(low[a:k].min()), 0.0, 0.0)
            trade = orb_repo.play_executable(m.frame, s, rr=1e9)
            if trade is not None:
                trades.append(trade)
        out += _cost(trades)
    return out


def random_means(markets: Sequence[Market], run: Run, draws: int, seed: int = 7
                 ) -> np.ndarray:
    """Mean net R of entries at random bars that keep each real trade's stop
    distance (as a fraction of price) and target multiple."""
    by_symbol = {m.symbol: m for m in markets}
    rng = np.random.default_rng(seed)
    pools: Dict[str, Tuple[np.ndarray, np.ndarray, np.ndarray]] = {}
    lo, hi = STAGES[run.stage]
    for symbol, taken in run.by_market.items():
        if not taken:
            continue
        m = by_symbol[symbol]
        t = m.frame["time"].to_numpy(dtype=float)
        sod = np.mod(t + IST, 86_400)
        ks, firsts, lasts = [], [], []
        for a, k, b in _valid_days(m):
            for j in range(a, b):
                if (orb_repo.OR_END + orb_repo.TF <= sod[j] < orb_repo.NO_NEW_ENTRY_AFTER
                        and lo <= t[j] < hi):
                    ks.append(j); firsts.append(a); lasts.append(b)
        if ks:
            pools[symbol] = (np.array(ks), np.array(firsts), np.array(lasts))
    out = np.empty(draws)
    for d in range(draws):
        pooled: List[float] = []
        for symbol, taken in run.by_market.items():
            if symbol not in pools:
                continue
            m = by_symbol[symbol]
            ks, firsts, lasts = pools[symbol]
            c = m.frame["close"].to_numpy(dtype=float)
            trades = []
            for real in taken:
                decided = real.trade.initial_stop + real.trade.risk
                frac = real.trade.risk / decided
                pick = int(rng.integers(0, len(ks)))
                k0 = int(ks[pick])
                price = float(c[k0 - 1])
                s = Setup(k0 - 1, k0, int(firsts[pick]), int(lasts[pick]), 0.0, 0.0, 0.0, 0.0)
                trade = orb_repo.play_executable(m.frame, s, stop=price * (1 - frac),
                                                 decided=price)
                if trade is not None:
                    trades.append(trade)
            pooled += [x.r for x in _cost(trades)]
        out[d] = float(np.mean(pooled)) if pooled else 0.0
    return out


# ── measuring ─────────────────────────────────────────────────

def _drawdown(pnl: np.ndarray) -> float:
    if not len(pnl):
        return 0.0
    curve = np.cumsum(pnl)
    return float((np.maximum.accumulate(np.r_[0.0, curve])[1:] - curve).max())


def describe(run: Run) -> Dict[str, Any]:
    taken = run.costed
    out: Dict[str, Any] = {"candidates": run.candidates, "skipped_unaffordable": run.skipped,
                           "trades": len(taken)}
    if not taken:
        return out
    r = run.r
    net = np.array([c.net for c in taken])
    gross = np.array([c.gross for c in taken])
    costs = float(sum(c.cost.costs for c in taken))
    wins, losses = r[r > 0], r[r < 0]
    pnl_w, pnl_l = net[net > 0].sum(), -net[net < 0].sum()
    weekly = stats.per_period(r, run.weeks, int(run.weeks.min()), int(run.weeks.max()))
    interval = stats.cluster_mean_interval(r, run.weeks, GATE["interval_level"])
    out.update({
        "net_pnl": round(float(net.sum()), 2), "gross_pnl": round(float(gross.sum()), 2),
        "costs": round(costs, 2),
        "costs_pct_of_gross": round(100 * costs / gross.sum(), 1) if gross.sum() > 0 else None,
        "expectancy_r": round(float(r.mean()), 4),
        "gross_expectancy_r": round(float(np.mean([c.gross_r for c in taken])), 4),
        "expectancy_rupees": round(float(net.mean()), 2),
        "win_rate": round(float((r > 0).mean()), 4),
        "avg_win_r": round(float(wins.mean()), 4) if len(wins) else None,
        "avg_loss_r": round(float(losses.mean()), 4) if len(losses) else None,
        "profit_factor": round(float(pnl_w / pnl_l), 4) if pnl_l > 0 else None,
        "max_drawdown_rupees": round(_drawdown(net), 2),
        "weekly_sharpe": round(stats.sharpe(weekly), 4),
        "interval": {"level": interval.level, "low": round(interval.low, 4),
                     "high": round(interval.high, 4), "weeks": interval.block,
                     "p_value_not_above_zero": interval.p_value},
        "deflated_sharpe": round(stats.deflated_sharpe(weekly, TRIALS), 4),
        "exits": {k: sum(c.trade.reason == k for c in taken)
                  for k in ("stop", "gap", "target", "time", "data_end")},
        "by_market": {s: {"trades": len(cs), "expectancy_r": round(float(np.mean([c.r for c in cs])), 4)}
                      for s, cs in sorted(run.by_market.items()) if cs},
        "by_month": _by_month(taken),
        "by_weekday": _by_weekday(taken),
    })
    return out


def _by_month(taken: Sequence[Costed]) -> Dict[str, Any]:
    rows: Dict[str, List[float]] = {}
    for c in taken:
        day = int(NSE_EQUITY.local_day([c.trade.exit_time])[0])
        rows.setdefault(NSE_EQUITY.iso(day)[:7], []).append(c.r)
    return {k: {"trades": len(v), "expectancy_r": round(float(np.mean(v)), 4)}
            for k, v in sorted(rows.items())}


def _by_weekday(taken: Sequence[Costed]) -> Dict[str, Any]:
    rows: Dict[str, List[float]] = {}
    for c in taken:
        day = int(NSE_EQUITY.local_day([c.trade.exit_time])[0])
        rows.setdefault(orb_repo._WEEKDAYS[int(NSE_EQUITY.weekday(day))], []).append(c.r)
    return {k: {"trades": len(v), "expectancy_r": round(float(np.mean(v)), 4)}
            for k, v in rows.items()}


def judge(measure: Dict[str, Any]) -> Tuple[str, List[str]]:
    """Status and every reason, from the measured numbers alone. A number that
    is missing fails its test; nothing is assumed."""
    why: List[str] = []
    n = measure.get("trades", 0)
    if n < GATE["min_trades"]:
        return "INSUFFICIENT_DATA", ["{} trades; the gate needs {}.".format(
            n, int(GATE["min_trades"]))]
    pf = measure.get("profit_factor")
    if not measure.get("expectancy_r", 0) > 0:
        why.append("Net expectancy is not positive ({}R).".format(measure.get("expectancy_r")))
    if pf is None or not pf > 1:
        why.append("Profit factor is not above 1 ({}).".format(pf))
    low = (measure.get("interval") or {}).get("low")
    if low is None or not low > 0:
        why.append("The {:.0%} interval on expectancy does not clear zero (lower end {}).".format(
            GATE["interval_level"], low))
    dsr = measure.get("deflated_sharpe")
    if dsr is None or dsr < GATE["deflated_sharpe"]:
        why.append("Deflated Sharpe {} is below {}.".format(dsr, GATE["deflated_sharpe"]))
    p = (measure.get("random") or {}).get("p_value")
    if p is None or p > GATE["random_p_value"]:
        why.append("Not distinguishable from random entries (p = {}).".format(p))
    bench = measure.get("benchmark_expectancy_r")
    if bench is None or not measure.get("expectancy_r", 0) > bench:
        why.append("Does not beat the benchmark ({}R).".format(bench))
    if why:
        return "FAILED_OUT_OF_SAMPLE", why
    stress = (measure.get("cost_stress") or {}).get("1.5")
    if stress is None or not stress > 0:
        return "FAILED_COST_STRESS", ["Expectancy at 1.5x costs is {}R.".format(stress)]
    rob: List[str] = []
    delayed = measure.get("delay_1_bar_expectancy_r")
    if delayed is None or not delayed > 0:
        rob.append("One bar of execution delay leaves {}R.".format(delayed))
    share = measure.get("markets_positive_share")
    if share is None or share < GATE["markets_positive"]:
        rob.append("Only {} of markets with enough trades are positive.".format(share))
    if rob:
        return "FAILED_ROBUSTNESS", rob
    return "PAPER_VALIDATION_REQUIRED", [
        "Passed every historical test this protocol measures. Twenty agreeing paper trades and "
        "a person's decision are still required; neither can come from a backtest."]


def evaluate_stage(markets: Sequence[Market], rules: Rules, stage: str,
                   say=print) -> Dict[str, Any]:
    base = run_variant(markets, rules, stage)
    measure = describe(base)
    if not measure["trades"]:
        measure["status"], measure["reasons"] = judge(measure)
        return measure
    measure["cost_stress"] = {"{:g}".format(m): round(float(run_variant(
        markets, rules, stage, multiplier=m).r.mean()), 4) for m in COST_MULTIPLES}
    delayed = run_variant(markets, rules, stage, delay=int(GATE["delay_bars"]))
    measure["delay_1_bar_expectancy_r"] = (round(float(delayed.r.mean()), 4)
                                           if len(delayed.r) else None)
    means = [float(np.mean([c.r for c in cs])) for cs in base.by_market.values()
             if len(cs) >= GATE["market_min_trades"]]
    measure["markets_positive_share"] = round(stats.positive_share(means), 4) if means else None
    measure["markets_with_enough_trades"] = len(means)
    bench = benchmark(markets, stage)
    measure["benchmark_trades"] = len(bench)
    measure["benchmark_expectancy_r"] = (round(float(np.mean([c.r for c in bench])), 4)
                                         if bench else None)
    say("    random-entry control ({} draws)…".format(RANDOM_DRAWS))
    null = random_means(markets, base, RANDOM_DRAWS)
    observed = float(base.r.mean())
    measure["random"] = {"draws": RANDOM_DRAWS, "null_mean_r": round(float(null.mean()), 4),
                         "null_p95_r": round(float(np.quantile(null, 0.95)), 4),
                         "observed_r": round(observed, 4),
                         "p_value": round(stats.permutation_p_value(observed, null), 4)}
    measure["status"], measure["reasons"] = judge(measure)
    measure["_run"] = base
    return measure


def published_reproduction(markets: Sequence[Market], rules: Rules, stage: str
                           ) -> Dict[str, Any]:
    """The source's fills, no costs: what its own report would show on this data."""
    trades: List[Trade] = []
    for m in markets:
        trades += orb_repo.play_all(m.frame, _in_stage(m.setups[rules.name], m.frame, stage),
                                    PUBLISHED)
    return orb_repo.summary_published(trades)


# ── the index, fetched the way a member is ───────────────────

async def fetch_index(years: float = 2.0, pause: float = 0.4, say=print) -> str:
    """NIFTY 50 spot, 5-minute, into the dataset store — the same request path,
    calendar and quality check as `india.snapshot`. Read-only market data."""
    from ..data.angel_history import AngelHistorySource

    universe = india.load_universe("nifty50")
    source = AngelHistorySource(pause=pause)
    end = datetime.now(timezone.utc)
    offset = timezone(timedelta(seconds=universe.session.utc_offset))
    start = (end - timedelta(days=int(round(years * 365.25)))).astimezone(offset).replace(
        hour=0, minute=0, second=0, microsecond=0)
    calendar = await india.reference_calendar(source, universe, start, end)
    scrip = await source.scrip(universe.calendar_reference["exchange"],
                               universe.calendar_reference["trading_symbol"])
    if scrip is None:
        raise SystemExit("NIFTY 50 is not in the broker's scrip master.")
    rows, report = await source.candles(scrip, "5m", start, end)
    ds = datasets.build(report.broker, INDEX, "5m", rows, now=time.time(),
                        session=universe.session, calendar=calendar, explained_moves=[],
                        provenance={**report.to_dict(), "universe": universe.describe()})
    if ds.bars:
        datasets.save(ds)
    why = india.accept(ds)
    say("{} {} {} bars  {}".format(INDEX, ds.id, ds.bars,
                                    "accepted" if not why else "REFUSED: " + "; ".join(why)))
    return ds.id


# ── the run ───────────────────────────────────────────────────

def execute(say=print) -> Dict[str, Any]:
    record = frozen()
    say("protocol {}".format(record["digest"][:12]))
    markets, index, left_out = load(say)
    outcome: Dict[str, Any] = {
        "protocol_digest": record["digest"], "ran_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "universe_used": [m.symbol for m in markets], "left_out": left_out,
        "index_available": index is not None, "variants": {}, "ledgers": {}}
    if not markets:
        outcome["note"] = "No accepted 5-minute history: nothing was tested."
        return outcome
    for rules in orb_repo.VARIANTS:
        entry: Dict[str, Any] = {"rules": rules.describe(), "stages": {}}
        outcome["variants"][rules.name] = entry
        if rules.nifty_filter and index is None:
            entry["status"] = "NOT MEASURED"
            entry["reasons"] = ["The rules need NIFTY 5-minute bars and none passed acceptance."]
            continue
        for stage in ORDER:
            say("  {} / {}".format(rules.name, stage))
            entry["stages"][stage] = {"published_fills": published_reproduction(markets, rules, stage)}
            result = evaluate_stage(markets, rules, stage, say)
            run = result.pop("_run", None)
            entry["stages"][stage]["executable"] = result
            if run is not None:
                outcome["ledgers"]["{}/{}".format(rules.name, stage)] = run.costed
            entry["status"] = result["status"]
            entry["reasons"] = result["reasons"]
            say("    {}  trades {}  net {}R  {}".format(
                result["status"], result.get("trades"), result.get("expectancy_r"),
                "; ".join(result["reasons"])[:110]))
            if result["status"] != "PAPER_VALIDATION_REQUIRED":
                entry["later_stretches"] = "never opened: an earlier stretch failed"
                break
    return outcome


def write(outcome: Dict[str, Any]) -> Path:
    folder = FOLDER / "results" / time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    folder.mkdir(parents=True, exist_ok=True)
    ledgers = outcome.pop("ledgers")
    for key, rows in ledgers.items():
        name = key.replace("/", "_") + "_trades.csv"
        with open(folder / name, "w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["entry_time_utc", "exit_time_utc", "entry", "exit", "stop", "quantity",
                        "reason", "gross", "brokerage", "stt", "exchange", "gst", "stamp",
                        "sebi", "execution", "net", "net_r"])
            for c in rows:
                ch = c.cost.charges
                w.writerow([c.trade.entry_time, c.trade.exit_time, c.trade.entry, c.trade.exit,
                            c.trade.initial_stop, c.quantity, c.trade.reason, round(c.gross, 2),
                            round(ch.brokerage, 2), round(ch.stt, 2), round(ch.exchange, 2),
                            round(ch.gst, 2), round(ch.stamp, 2), round(ch.sebi, 2),
                            round(c.cost.execution, 2), round(c.net, 2), round(c.r, 4)])
    (folder / "results.json").write_text(json.dumps(outcome, indent=2, sort_keys=True,
                                                    default=str))
    return folder


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.research.orb_repo_study",
                                     description=__doc__)
    parser.add_argument("command", choices=("freeze", "fetch-index", "run"))
    args = parser.parse_args(argv)
    if args.command == "freeze":
        record = freeze()
        print("frozen {}".format(record["digest"]))
        return 0
    if args.command == "fetch-index":
        asyncio.run(fetch_index())
        return 0
    outcome = execute()
    if not outcome["universe_used"]:
        print("Nothing was testable; no result was written. Left out: {}".format(
            json.dumps(outcome["left_out"])))
        return 2
    print("results: {}".format(write(outcome)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
