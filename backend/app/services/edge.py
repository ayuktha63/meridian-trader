"""The go-live evidence: has this symbol and timeframe shown an edge?

Three tests, all of which have to hold:

* **A recent walk-forward of the live engine passed.** Out-of-sample by
  construction and net of costs — and recent, because an edge measured on
  last quarter's market is a claim about last quarter.
* **It survives the number of markets tested.** Run the test on twenty
  symbols and one will pass at the 5% level by luck alone. Every walk-forward
  on record in the window is counted, and the bar each must clear rises with
  how many there are (Holm's step-down procedure), so the chance that *any*
  of the markets cleared for live trading got there by chance stays at 5%.
* **Paper trading does not contradict it.** If the demo account has traded
  this market enough to say something and lost, the backtest is the one that
  is wrong.
"""
from __future__ import annotations

import time
from typing import Dict, List, Optional, Set, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import BacktestRow, PaperTradeRow

WALK_FORWARD_PREFIX = "walk_forward:"
#: The strategy live orders actually follow — the engine's fused verdict.
LIVE_STRATEGY = "engine"
MAX_AGE_SECONDS = 30 * 86_400
ALPHA = 0.05
#: Fewer closed paper trades than this say nothing either way.
MIN_PAPER_TRADES = 10

NOT_MEASURED = ("No measured edge: no walk-forward test of the live engine has been "
                "run for {} on {}. Run one from the Backtest tab.")


def holm_survivors(p_values: Dict[Tuple[str, str], float],
                   alpha: float = ALPHA) -> Set[Tuple[str, str]]:
    """The hypotheses that survive Holm's step-down correction.

    Sorted from strongest to weakest, the k-th of m has to reach alpha/(m-k).
    The first one that does not ends it: nothing weaker is accepted, even if
    it would clear its own, easier bar. That stopping rule is what holds the
    chance of *any* false acceptance at alpha.
    """
    ordered = sorted(p_values.items(), key=lambda item: item[1])
    survivors: Set[Tuple[str, str]] = set()
    for rank, (key, p_value) in enumerate(ordered):
        if p_value > alpha / (len(ordered) - rank):
            break
        survivors.add(key)
    return survivors


async def _paper_record(session: AsyncSession, symbol: str,
                        timeframe: str) -> Tuple[int, float]:
    stmt = select(PaperTradeRow.realised_r).where(
        PaperTradeRow.symbol == symbol, PaperTradeRow.timeframe == timeframe,
        PaperTradeRow.status == "closed", PaperTradeRow.realised_r.is_not(None))
    values: List[float] = list((await session.execute(stmt)).scalars().all())
    return len(values), (sum(values) / len(values) if values else 0.0)


async def latest_edge(session: AsyncSession, symbol: str, timeframe: str,
                      now: Optional[float] = None) -> dict:
    now = now or time.time()
    strategy = WALK_FORWARD_PREFIX + LIVE_STRATEGY
    stmt = (select(BacktestRow)
            .where(BacktestRow.strategy == strategy)
            .order_by(BacktestRow.created_at.desc()))
    rows = (await session.execute(stmt)).scalars().all()

    # The most recent test of each market inside the window.
    latest: Dict[Tuple[str, str], BacktestRow] = {}
    stale = False
    for row in rows:
        key = (row.symbol, row.timeframe)
        if now - (row.created_at or 0) > MAX_AGE_SECONDS:
            stale = stale or key == (symbol, timeframe)
            continue
        latest.setdefault(key, row)

    mine = latest.get((symbol, timeframe))
    if mine is None:
        reason = ("The last walk-forward test for {} on {} is more than 30 days "
                  "old — run it again.".format(symbol, timeframe) if stale
                  else NOT_MEASURED.format(symbol, timeframe))
        return {"passed": False, "reason": reason, "tests_considered": len(latest)}

    payload = mine.payload or {}
    edge = dict(payload.get("edge") or {})
    edge.setdefault("passed", False)
    edge.setdefault("reason", "No verdict recorded.")
    edge["measured_at"] = mine.created_at
    edge["tests_considered"] = len(latest)
    for key, value in (payload.get("next_parameters") or {}).items():
        edge[key] = value
    if not edge["passed"]:
        return edge

    # ── how many markets were tested to find this one ─────────
    p_values = {key: float(((row.payload or {}).get("edge") or {}).get("p_value", 1.0))
                for key, row in latest.items()}
    if (symbol, timeframe) not in holm_survivors(p_values):
        edge["passed"] = False
        edge["reason"] = (
            "No measured edge: this test passed on its own (p = {:.3f}), but {} "
            "markets have been walk-forward tested in the last 30 days, and among "
            "that many a result this strong is expected by chance. The strongest "
            "of them has to reach p ≤ {:.4f} before any counts.".format(
                p_values[(symbol, timeframe)], len(latest), ALPHA / len(latest)))
        return edge

    # ── does the demo account agree ───────────────────────────
    count, mean_r = await _paper_record(session, symbol, timeframe)
    edge["paper_trades"], edge["paper_expectancy"] = count, round(mean_r, 3)
    if count >= MIN_PAPER_TRADES and mean_r <= 0:
        edge["passed"] = False
        edge["reason"] = (
            "Paper trading disagrees with the backtest: {} demo trades on {} {} "
            "averaged {:+.2f}R. Until those agree, the backtest is the one that "
            "is wrong.".format(count, symbol, timeframe, mean_r))
    return edge
