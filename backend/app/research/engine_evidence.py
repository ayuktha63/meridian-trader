"""The evidence the live gate asks for, produced so that it can be checked.

A walk-forward of the live engine used to read whatever bars the feed held
at that moment, and left behind a verdict and nothing that could reproduce
it. Here the same test runs on a snapshot, and its record says which bars,
which code, which costs — and answers three questions the verdict alone
does not:

* Would entries at random bars, with the same stops, targets, ladder and
  costs, have done as well?
* Do the parameter sets beside the chosen ones make money too, or does the
  result sit on one setting?
* How deep did it fall on the way?

Two rows are written: the experiment (the full record) and — when the run
was charged as the venue the market actually trades on — the `backtests`
row the gate has always read, which now points at it.
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from sqlalchemy.ext.asyncio import AsyncSession

from .. import costs
from ..backtest import engine as bt
from ..backtest import replay
from ..backtest.replay import BarSignal, Setup
from ..db.models import BacktestRow
from ..instruments import MTF_LADDER, Instrument, get_instrument, get_timeframe
from ..schemas.journal import BacktestRequest, BacktestResult, BacktestTrade
from ..services.edge import WALK_FORWARD_PREFIX
from . import datasets, registry, stats, versions
from .datasets import Dataset, DatasetError

RANDOM_DRAWS = 200
#: Daily bars the engine's multi-timeframe read looks back over; a snapshot
#: has to reach that far behind the first tested bar or the replay sees a
#: thinner market than the live engine does.
MTF_DAYS = 400
MAX_FETCH_BARS = 60_000


def bars_needed(timeframe: str, bars: int) -> int:
    seconds = get_timeframe(timeframe).seconds
    return min(bars + replay.ANALYSIS_BARS + int(MTF_DAYS * 86_400 / seconds),
               MAX_FETCH_BARS)


def newest(symbol: str, timeframe: str) -> Optional[Dataset]:
    """The most recent real snapshot of this market at this timeframe."""
    for meta in datasets.catalogue():
        if (meta["symbol"] == symbol.upper() and meta["timeframe"] == timeframe.lower()
                and meta["provider"] not in datasets.NOT_A_MARKET
                and not meta.get("derived_from")):
            return datasets.load(meta["sha256"][:16])
    return None


def history_from(dataset: Dataset, bars: int) -> bt.History:
    """The frames a replay needs, all cut from one snapshot: the chart's own
    bars, and every higher timeframe built from the same observations."""
    base_seconds = get_timeframe(dataset.timeframe).seconds
    higher: Dict[str, Any] = {}
    for timeframe in MTF_LADDER:
        seconds = get_timeframe(timeframe).seconds
        if seconds > base_seconds and seconds % base_seconds == 0:
            coarse = datasets.resample(dataset, timeframe)
            if coarse.bars >= replay.MIN_BARS:
                higher[timeframe] = coarse.frame()
    base = dataset.frame().tail(bars + replay.ANALYSIS_BARS).reset_index(drop=True)
    return bt.History(base=base, higher=higher, source=dataset.provider)


def _drawdown_r(trades: List[BacktestTrade]) -> float:
    r = np.array([t.r_multiple for t in trades if t.outcome != "open"], dtype=float)
    if not len(r):
        return 0.0
    curve = np.cumsum(r)
    return float((np.maximum.accumulate(np.r_[0.0, curve])[1:] - curve).max())


def _closed_r(trades: List[BacktestTrade]) -> np.ndarray:
    return np.array([t.r_multiple for t in trades if t.outcome != "open"], dtype=float)


def grid_positive_share(prep: bt.Prepared, instrument: Instrument,
                        request: BacktestRequest, start: int) -> Tuple[float, List[dict]]:
    """Each parameter set of the walk-forward grid, traded over the whole
    out-of-sample span. Not how the test chose — it chose fold by fold — but
    whether the neighbourhood agrees."""
    rows = []
    for confidence, reward in bt.WF_GRID:
        candidate = request.model_copy(update={"min_confidence": confidence,
                                               "risk_reward": reward})
        trades, _ = bt._simulate(prep, instrument, candidate, start, prep.n)  # noqa: SLF001
        r = _closed_r(trades)
        rows.append({"min_confidence": confidence, "risk_reward": reward,
                     "trades": int(len(r)),
                     "expectancy_r": round(float(r.mean()), 4) if len(r) else None})
    traded = [row["expectancy_r"] for row in rows if row["expectancy_r"] is not None]
    return stats.positive_share(traded), rows


def random_entries(prep: bt.Prepared, instrument: Instrument, request: BacktestRequest,
                   trades: List[BacktestTrade], start: int, draws: int = RANDOM_DRAWS,
                   seed: int = 7) -> np.ndarray:
    """Mean R of `draws` versions of this result with the skill taken out:
    each trade keeps its stop and target distances (in ATRs) and is moved to
    a bar picked at random, in a direction picked at random — then lives the
    same order's life, ladder and costs included."""
    closed = [t for t in trades if t.outcome != "open"]
    shapes = []
    for trade in closed:
        scale = prep.atr14[trade.index]
        if not np.isfinite(scale) or scale <= 0:
            continue
        shapes.append((abs(trade.entry - trade.stop_loss) / scale,
                       abs(trade.take_profit - trade.entry) / scale))
    plain = request.model_copy(update={"strategy": bt.ENGINE, "min_confidence": 0.0,
                                       "risk_reward": 0.0})
    usable = [i for i in range(start, prep.n - 1)
              if np.isfinite(prep.atr14[i]) and prep.atr14[i] > 0]
    rng = np.random.default_rng(seed)
    means = np.zeros(draws)
    if not shapes or len(usable) < len(shapes):
        return means
    for draw in range(draws):
        signals: Dict[int, BarSignal] = {}
        for stop_atrs, target_atrs in shapes:
            i = int(rng.choice(usable))
            long = rng.random() < 0.5 or not prep.shorting
            sign = 1.0 if long else -1.0
            price, unit = float(prep.close[i]), float(prep.atr14[i])
            signals[i] = BarSignal(engine=Setup(
                "BUY" if long else "SELL", price, price - sign * stop_atrs * unit,
                price + sign * target_atrs * unit, 100.0, "random"))
        null = bt.Prepared(**{**prep.__dict__, "signals": signals})
        simulated, _ = bt._simulate(null, instrument, plain, start, prep.n)  # noqa: SLF001
        r = _closed_r(simulated)
        means[draw] = float(r.mean()) if len(r) else 0.0
    return means


#: Below this, `walk_forward` itself already declined to trade ("not enough
#: history") — asking the engine to build frames from the same bars anyway
#: is not a second opinion, it is calling `_prepare` on data it already
#: knows is too thin, which is exactly the shape that crashes resampling a
#: higher timeframe with nothing in it.
_MIN_BASE_BARS = 50


async def extra_checks(request: BacktestRequest, history: bt.History,
                       result: BacktestResult) -> Dict[str, Any]:
    """The three questions the verdict alone does not answer — skipped,
    honestly, rather than crashed through, when there is too little history
    to ask them of in the first place."""
    if len(history.base) < _MIN_BASE_BARS:
        return {
            "block_interval": None, "grid_positive": None, "grid": None,
            "max_drawdown_r": round(_drawdown_r(result.trades), 2),
            "random_entries": None,
            "skipped": "Not enough history ({} bars) to run the robustness "
                       "checks.".format(len(history.base)),
        }
    instrument = get_instrument(request.symbol.upper())
    prep = await bt._prepare(request, history.base, history)       # noqa: SLF001 - cached
    start = int(np.searchsorted(prep.time, result.from_time, "left"))
    closed = _closed_r(result.trades)
    block = stats.mean_interval(closed, level=0.90)
    share, grid = grid_positive_share(prep, instrument, request, start)
    checks: Dict[str, Any] = {
        "block_interval": {"low": round(block.low, 4), "high": round(block.high, 4),
                           "p_value": round(block.p_value, 5), "block": block.block},
        "grid_positive": round(share, 3), "grid": grid,
        "max_drawdown_r": round(_drawdown_r(result.trades), 2),
        "random_entries": None,
    }
    if len(closed) >= bt.MIN_EDGE_TRADES:
        null = random_entries(prep, instrument, request, result.trades, start)
        checks["random_entries"] = {
            "draws": int(len(null)), "mean_r": round(float(null.mean()), 4),
            "best_r": round(float(null.max()), 4),
            "p_value": round(stats.permutation_p_value(float(closed.mean()), null), 5)}
    return checks


async def _dataset(symbol: str, timeframe: str, bars: int,
                   dataset: Optional[Dataset]) -> Dataset:
    if dataset is not None:
        return dataset
    held = newest(symbol, timeframe)
    # A snapshot from the last few days that is deep enough for the test is
    # used as it is — the same bars as the last run, which is the point.
    if (held is not None and time.time() - held.last < 3 * 86_400
            and held.bars >= bars + replay.ANALYSIS_BARS):
        return held
    return await datasets.fetch(symbol, timeframe, bars=bars_needed(timeframe, bars))


async def walk_forward_on_snapshot(
        session: AsyncSession, symbol: str, timeframe: str, *, bars: int = 4000,
        cost_profile: Optional[str] = None, strategy: str = bt.ENGINE, folds: int = 4,
        dataset: Optional[Dataset] = None,
        request: Optional[BacktestRequest] = None) -> Dict[str, Any]:
    """Walk-forward one strategy of the live engine on a snapshot and record
    it. Raises `DatasetError` when no real venue can supply the history."""
    symbol, timeframe = symbol.upper(), timeframe.lower()
    get_instrument(symbol)                      # an unknown symbol fails here, not later
    data = await _dataset(symbol, timeframe, bars, dataset)
    if not data.real:
        raise DatasetError("{} {} is simulated data.".format(symbol, timeframe))
    request = (request or BacktestRequest(symbol=symbol, timeframe=timeframe)).model_copy(
        update={"symbol": symbol, "timeframe": timeframe, "bars": bars,
                "strategy": strategy, "cost_profile": cost_profile})
    history = history_from(data, bars)
    result = await bt.walk_forward(request, folds, history=history)
    checks = await extra_checks(request, history, result)
    record = await record_walk_forward(session, request, result, data, checks, folds)
    result.notes.append("Recorded as experiment {} on dataset {}.".format(
        record["id"], data.id))
    record["result"] = result
    return record


async def record_walk_forward(session: AsyncSession, request: BacktestRequest,
                              result: BacktestResult, data: Dataset,
                              checks: Dict[str, Any], folds: int) -> Dict[str, Any]:
    instrument = get_instrument(request.symbol.upper())
    profile = costs.describe(instrument, request.cost_profile)
    edge = result.edge
    metrics = {
        "trades": edge.trades, "expectancy_r": edge.expectancy, "ci_low": edge.ci_low,
        "ci_high": edge.ci_high, "p_value": edge.p_value, "passed": edge.passed,
        "win_rate": result.metrics.win_rate, "profit_factor": result.metrics.profit_factor,
        "total_r": result.metrics.total_r, "bars_tested": result.bars_tested,
        "folds": result.folds, "next_parameters": result.next_parameters, **checks}
    record = registry.build(
        kind="engine_walk_forward", stage="walk_forward", strategy=request.strategy,
        symbol=result.symbol, timeframe=result.timeframe, venue=data.provider,
        cost_profile=str(profile["profile"]),
        verdict="PASSED" if edge.passed else "NO MEASURED EDGE",
        datasets=[data.describe()],
        window={"from": result.from_time, "to": result.to_time, "folds": folds,
                "bars_requested": request.bars},
        parameters={"grid": [list(point) for point in bt.WF_GRID],
                    "use_ladder": request.use_ladder, "risk_pct": request.risk_pct},
        assumptions={"costs": profile, "execution": [
            "Entry is a limit at the signal price, filled only if the next bar trades "
            "through it; a stop triggers on a touch and fills at the open on a gap; "
            "stop before target when one bar reaches both.",
            "Not reproduced: Claude's verdict, drawings, and timeframes below the chart's."]},
        metrics=metrics, reasons=[edge.reason], trials=len(bt.WF_GRID),
        seeds={"bootstrap": request.seed, "random_entries": 7},
        extra={"quality_passed": data.quality_passed})
    record = await registry.save(session, record)
    if profile["profile"] != costs.describe(instrument)["profile"]:
        return record               # another venue's costs: research, not gate evidence
    session.add(BacktestRow(
        symbol=result.symbol, timeframe=result.timeframe,
        strategy=WALK_FORWARD_PREFIX + result.strategy, bars=result.bars_tested,
        metrics=result.metrics.model_dump(),
        payload={"request": request.model_dump(), "notes": result.notes,
                 "edge": edge.model_dump(), "folds": result.folds,
                 "next_parameters": result.next_parameters,
                 "data_source": result.data_source, "dataset_id": data.id,
                 "quality_passed": data.quality_passed, "experiment_id": record["id"],
                 "code": versions.current(), "cost_profile": profile["profile"],
                 "checks": {key: checks[key] for key in
                            ("grid_positive", "max_drawdown_r", "random_entries",
                             "block_interval")}}))
    await session.flush()
    return record
