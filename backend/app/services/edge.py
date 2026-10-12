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

And then two more, which no test result can satisfy by itself:

* **The evidence is about what is running.** `research/eligibility.py` checks
  that the test ran on a real venue's data that passed its quality checks,
  with the code, cost model and execution model that are in use now; that it
  beat entries at random bars; that it does not hang on one parameter
  setting; and that paper trading has gone on long enough to agree.
* **A person has approved it.** When all of that holds the state is
  ELIGIBLE FOR HUMAN REVIEW — and a live order is still refused until an
  approval for this strategy version on this market is on record.
"""
from __future__ import annotations

import time
from typing import Dict, List, Optional, Set, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import costs
from ..config import get_settings
from ..db.models import BacktestRow, ExperimentRow, PaperTradeRow
from ..instruments import get_instrument
from ..research import approvals, eligibility, registry, versions
from ..research.eligibility import Evidence

WALK_FORWARD_PREFIX = "walk_forward:"
#: The strategy live orders actually follow — the engine's fused verdict.
LIVE_STRATEGY = "engine"
MAX_AGE_SECONDS = 30 * 86_400
ALPHA = 0.05
#: Fewer closed paper trades than this say nothing either way.
MIN_PAPER_TRADES = 10

NOT_MEASURED = ("No measured edge: no walk-forward test of the live engine has been "
                "run for {} on {}. Run one from the Backtest tab.")
#: Sources that are not a market. A test run on them is a test of the software.
NOT_A_MARKET = ("synthetic",)
SIMULATED = ("No measured edge: the walk-forward test on record for {} {} ran on "
             "simulated bars, which says nothing about the market. Connect the venue "
             "that serves it and run the test again.")
UNSOURCED = ("No measured edge: the walk-forward test on record for {} {} does not "
             "record what data it ran on, so it cannot be counted. Run it again.")


def _source_problem(payload: dict, symbol: str, timeframe: str) -> Optional[str]:
    source = payload.get("data_source")
    if not source or source == "unknown":
        return UNSOURCED.format(symbol, timeframe)
    if source in NOT_A_MARKET:
        return SIMULATED.format(symbol, timeframe)
    return None


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


AWAITING_APPROVAL = (
    "Every automatic check has passed for {} on {} — it is eligible for review, and "
    "nothing more. A person has to approve this strategy version for this market "
    "before a live order is sent.")


def venue_block(symbol: str) -> Optional[str]:
    """A standing reason this market cannot be traded live at all."""
    from ..broker.risk_gate import UNTRUSTED_TICK_EXCHANGES, UNVERIFIED_QUANTITY_EXCHANGES

    try:
        instrument = get_instrument(symbol)
    except KeyError:
        return "Unknown instrument."
    if instrument.venue == "crypto":
        return "There is no live order path for crypto: Angel One does not trade it."
    if not instrument.tradable_in_india:
        return "Not tradable through an Indian broker: analysis only."
    exchange = (instrument.exchange or "").upper()
    if exchange in UNTRUSTED_TICK_EXCHANGES or instrument.venue in UNTRUSTED_TICK_EXCHANGES:
        return "Live orders on {} are blocked: its tick size is unverified.".format(exchange)
    if (exchange in UNVERIFIED_QUANTITY_EXCHANGES
            or instrument.venue in UNVERIFIED_QUANTITY_EXCHANGES):
        return ("Live orders on {} are blocked: whether a quantity is read in lots or "
                "units is unverified.".format(exchange))
    return None


def _expected_profile(symbol: str) -> Optional[str]:
    try:
        return str(costs.describe(get_instrument(symbol))["profile"])
    except KeyError:
        return None


async def _evidence(session: AsyncSession, symbol: str, timeframe: str, now: float,
                    mine: Optional[BacktestRow], edge: dict, survives: bool,
                    tests: int, runs: int) -> Evidence:
    """Everything on record about the live engine on this market."""
    count, mean_r = await _paper_record(session, symbol, timeframe)
    evidence = Evidence(
        strategy=LIVE_STRATEGY, symbol=symbol, timeframe=timeframe, live_capable=True,
        current={k: v for k, v in versions.current().items() if isinstance(v, str)},
        expected_cost_profile=_expected_profile(symbol), paper_trades=count,
        paper_expectancy=round(mean_r, 3), tests_considered=tests,
        survives_multiple_testing=survives, runs_same_question=runs,
        broker_validated=bool(get_settings().live_broker_validated),
        venue_block=venue_block(symbol))
    if mine is None:
        return evidence
    payload = mine.payload or {}
    checks = payload.get("checks") or {}
    evidence.experiment_id = payload.get("experiment_id")
    if evidence.experiment_id:
        row = await session.get(ExperimentRow, evidence.experiment_id)
        evidence.record_intact = row is not None and registry.intact(dict(row.record))
    evidence.data_source = payload.get("data_source")
    evidence.quality_passed = bool(payload.get("quality_passed"))
    evidence.age_days = (now - (mine.created_at or 0.0)) / 86_400.0
    evidence.tested = {k: v for k, v in (payload.get("code") or {}).items()
                       if isinstance(v, str)}
    evidence.cost_profile = payload.get("cost_profile")
    evidence.trades = int(edge.get("trades") or 0)
    evidence.expectancy = float(edge.get("expectancy") or 0.0)
    evidence.ci_low = float(edge.get("ci_low") or 0.0)
    evidence.ci_high = float(edge.get("ci_high") or 0.0)
    evidence.random_p_value = (checks.get("random_entries") or {}).get("p_value")
    evidence.grid_positive = checks.get("grid_positive")
    evidence.max_drawdown_r = checks.get("max_drawdown_r")
    evidence.tradable_now = "min_confidence" in (payload.get("next_parameters") or {})
    return evidence


async def latest_edge(session: AsyncSession, symbol: str, timeframe: str,
                      now: Optional[float] = None) -> dict:
    """What the live gate believes about this market: the measured-edge
    checks, then eligibility, then a person's approval. `eligibility` is in
    the answer whichever of them said no."""
    now = now or time.time()
    edge, mine, survives, tests, runs = await _measured(session, symbol, timeframe, now)
    # Every walk-forward of this market as the venue it trades on is a try,
    # whether or not it left a row for the gate.
    runs = max(runs, await registry.runs_on_record(
        session, kind="engine_walk_forward", strategy=LIVE_STRATEGY, symbol=symbol,
        timeframe=timeframe, since=now - MAX_AGE_SECONDS,
        cost_profile=_expected_profile(symbol)))
    evidence = await _evidence(session, symbol, timeframe, now, mine, edge, survives,
                               tests, runs)
    decision = eligibility.evaluate(evidence)
    edge["eligibility"] = {"state": decision.state, "reasons": decision.reasons,
                           "rules_version": decision.rules_version,
                           "experiment_id": evidence.experiment_id,
                           "paper_trades": evidence.paper_trades}
    edge["approved"] = False
    if not edge["passed"]:
        return edge
    if not decision.reviewable:
        edge["passed"], edge["reason"] = False, " ".join(decision.reasons)
        return edge
    approval = await approvals.active(session, LIVE_STRATEGY, symbol, timeframe,
                                      versions.strategy_version(LIVE_STRATEGY))
    if approval is None:
        edge["passed"] = False
        edge["reason"] = AWAITING_APPROVAL.format(symbol, timeframe)
        return edge
    edge["approved"] = True
    edge["approval"] = approvals.to_dict(approval)
    return edge


async def _measured(session: AsyncSession, symbol: str, timeframe: str,
                    now: float):
    """The measured-edge checks. Returns (edge, the row it came from, whether
    it survives the multiple-test correction, markets tested, times this
    market was tested in the window)."""
    strategy = WALK_FORWARD_PREFIX + LIVE_STRATEGY
    stmt = (select(BacktestRow)
            .where(BacktestRow.strategy == strategy)
            .order_by(BacktestRow.created_at.desc()))
    rows = (await session.execute(stmt)).scalars().all()

    # The most recent test of each market inside the window.
    latest: Dict[Tuple[str, str], BacktestRow] = {}
    stale: Optional[BacktestRow] = None
    runs = 0
    for row in rows:
        key = (row.symbol, row.timeframe)
        if now - (row.created_at or 0) > MAX_AGE_SECONDS:
            if key == (symbol, timeframe) and stale is None:
                stale = row
            continue
        runs += key == (symbol, timeframe)
        latest.setdefault(key, row)

    mine = latest.get((symbol, timeframe))
    if mine is None:
        reason = ("The last walk-forward test for {} on {} is more than 30 days "
                  "old — run it again.".format(symbol, timeframe) if stale is not None
                  else NOT_MEASURED.format(symbol, timeframe))
        return ({"passed": False, "reason": reason, "tests_considered": len(latest)},
                stale, False, len(latest), runs)

    payload = mine.payload or {}
    edge = dict(payload.get("edge") or {})
    edge.setdefault("passed", False)
    edge.setdefault("reason", "No verdict recorded.")
    edge["measured_at"] = mine.created_at
    edge["tests_considered"] = len(latest)
    edge["data_source"] = payload.get("data_source") or "unknown"
    for key, value in (payload.get("next_parameters") or {}).items():
        edge[key] = value

    p_values = {key: float(((row.payload or {}).get("edge") or {}).get("p_value", 1.0))
                for key, row in latest.items()}
    survives = (symbol, timeframe) in holm_survivors(p_values)
    out = (edge, mine, survives, len(latest), runs)
    if not edge["passed"]:
        return out

    # ── was it measured on a market at all ────────────────────
    problem = _source_problem(payload, symbol, timeframe)
    if problem:
        edge["passed"], edge["reason"] = False, problem
        return out

    # ── how many markets were tested to find this one ─────────
    if not survives:
        edge["passed"] = False
        edge["reason"] = (
            "No measured edge: this test passed on its own (p = {:.3f}), but {} "
            "markets have been walk-forward tested in the last 30 days, and among "
            "that many a result this strong is expected by chance. The strongest "
            "of them has to reach p ≤ {:.4f} before any counts.".format(
                p_values[(symbol, timeframe)], len(latest), ALPHA / len(latest)))
        return out

    # ── is it working now ─────────────────────────────────────
    if "min_confidence" not in edge:
        edge["passed"] = False
        edge["reason"] = (
            "The walk-forward test passed over its whole span, but nothing made "
            "money on its most recent stretch — there are no thresholds to trade "
            "with right now. Run it again when the market has moved on.")
        return out

    # ── does the demo account agree ───────────────────────────
    count, mean_r = await _paper_record(session, symbol, timeframe)
    edge["paper_trades"], edge["paper_expectancy"] = count, round(mean_r, 3)
    if count >= MIN_PAPER_TRADES and mean_r <= 0:
        edge["passed"] = False
        edge["reason"] = (
            "Paper trading disagrees with the backtest: {} demo trades on {} {} "
            "averaged {:+.2f}R. Until those agree, the backtest is the one that "
            "is wrong.".format(count, symbol, timeframe, mean_r))
    return out
