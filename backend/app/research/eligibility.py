"""May this strategy be traded on this market — and by whose say-so?

One function, `evaluate`, takes the evidence on record for one strategy
version on one market, timeframe and venue, and answers with one of five
states:

    INELIGIBLE                 the evidence is there and it says no
    INSUFFICIENT EVIDENCE      there is not enough to say anything
    RESEARCH APPROVED          the history says yes; nothing else has been asked
    PAPER VALIDATION           being paper traded; not yet enough of it
    ELIGIBLE FOR HUMAN REVIEW  every automatic check has passed

None of them trades. The last one means a person may now decide; the live
gate also needs that person's approval on record (`approvals.py`), and all
of the gate's own checks still apply on top.

Evidence is specific: a strategy *version*, a market, a timeframe, a data
source, a cost model, an execution model. Change any of them and it is
evidence about something else — the code moved on, or the result was for a
different venue — and it stops counting.

The thresholds are constants with a version. They are not arguments, so a
caller cannot loosen them, and a result records which version judged it.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

RULES_VERSION = "1"

INELIGIBLE = "INELIGIBLE"
INSUFFICIENT = "INSUFFICIENT EVIDENCE"
RESEARCH_APPROVED = "RESEARCH APPROVED"
PAPER_VALIDATION = "PAPER VALIDATION"
HUMAN_REVIEW = "ELIGIBLE FOR HUMAN REVIEW"
STATES = (INELIGIBLE, INSUFFICIENT, RESEARCH_APPROVED, PAPER_VALIDATION, HUMAN_REVIEW)

THRESHOLDS: Dict[str, float] = {
    "min_trades": 30,               # out-of-sample trades before a verdict means anything
    "max_age_days": 30,             # an edge measured last quarter is about last quarter
    "random_p_value": 0.05,         # must beat entries at random bars with the same exits
    "grid_positive": 0.50,          # share of nearby parameter sets that also made money
    "max_drawdown_r": 20.0,         # deepest fall, in units of one trade's risk
    "min_paper_trades": 20,         # closed paper trades before paper says anything
    "paper_shortfall_r": 0.30,      # how far paper may fall short of the backtest
    "max_runs_same_question": 6,    # asking again until it passes is not a pass
}

NOT_A_MARKET = ("synthetic", "unknown", "", None)


@dataclass
class Evidence:
    """Everything on record about one strategy on one market."""
    strategy: str
    symbol: str
    timeframe: str
    #: False for a strategy that exists only in research: nothing can send it.
    live_capable: bool = False
    #: The experiment on record — None if there is none.
    experiment_id: Optional[str] = None
    record_intact: bool = True
    data_source: Optional[str] = None
    quality_passed: bool = False
    age_days: float = 0.0
    #: Fingerprints at the time of the test, and of the code running now.
    tested: Dict[str, str] = field(default_factory=dict)
    current: Dict[str, str] = field(default_factory=dict)
    cost_profile: Optional[str] = None
    expected_cost_profile: Optional[str] = None
    trades: int = 0
    expectancy: float = 0.0
    ci_low: float = 0.0
    ci_high: float = 0.0
    #: Whether it survives correction for every market tested in the window.
    survives_multiple_testing: bool = False
    tests_considered: int = 1
    random_p_value: Optional[float] = None
    grid_positive: Optional[float] = None
    max_drawdown_r: Optional[float] = None
    #: Whether the most recent stretch left thresholds to trade with.
    tradable_now: bool = False
    runs_same_question: int = 1
    paper_trades: int = 0
    paper_expectancy: float = 0.0
    #: Whether the broker path has been checked end to end with a real order.
    broker_validated: bool = False
    #: A hard block on the venue or segment, with its reason, if there is one.
    venue_block: Optional[str] = None


@dataclass
class Decision:
    state: str
    reasons: List[str]
    rules_version: str = RULES_VERSION
    thresholds: Dict[str, float] = field(default_factory=lambda: dict(THRESHOLDS))

    @property
    def reviewable(self) -> bool:
        return self.state == HUMAN_REVIEW

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _stale_code(e: Evidence) -> List[str]:
    names = {"engine": "The strategy's code", "research": "The strategy's code",
             "execution": "The execution model", "costs": "The cost model"}
    out = []
    for key, label in names.items():
        if key in e.tested and key in e.current and e.tested[key] != e.current[key]:
            out.append("{} has changed since this was tested ({} then, {} now) — the "
                       "test is about code that no longer runs.".format(
                           label, e.tested[key], e.current[key]))
    return out


def _missing(e: Evidence) -> List[str]:
    """Reasons there is nothing to judge yet."""
    if e.experiment_id is None:
        return ["No walk-forward test of {} on {} {} is on record.".format(
            e.strategy, e.symbol, e.timeframe)]
    out = []
    if e.age_days > THRESHOLDS["max_age_days"]:
        out.append("The test on record is {:.0f} days old (limit {:.0f}) — run it "
                   "again.".format(e.age_days, THRESHOLDS["max_age_days"]))
    if e.trades < THRESHOLDS["min_trades"]:
        out.append("Only {} out-of-sample trades (need {:.0f}) — too few to tell an edge "
                   "from luck.".format(e.trades, THRESHOLDS["min_trades"]))
    return out


def _invalid(e: Evidence) -> List[str]:
    """Reasons the evidence cannot be counted, whatever it says."""
    out = []
    if not e.record_intact:
        out.append("The experiment record no longer matches its hash — it was changed "
                   "after it was written.")
    if e.data_source in NOT_A_MARKET:
        out.append("The test did not run on a real market's data ({}).".format(
            e.data_source or "source not recorded"))
    elif not e.quality_passed:
        out.append("The data the test ran on failed its quality checks.")
    out += _stale_code(e)
    if (e.expected_cost_profile is not None and e.cost_profile is not None
            and e.cost_profile != e.expected_cost_profile):
        out.append("The test was charged as {}, but this market trades as {} — the "
                   "result is for a different venue.".format(
                       e.cost_profile, e.expected_cost_profile))
    return out


def _negative(e: Evidence) -> List[str]:
    """Reasons the evidence itself says no."""
    out = []
    if e.expectancy <= 0:
        out.append("Out-of-sample expectancy after costs is {:+.3f}R.".format(e.expectancy))
    elif e.ci_low <= 0:
        out.append("Out-of-sample expectancy is {:+.3f}R but its interval ({:+.3f}R to "
                   "{:+.3f}R) includes zero.".format(e.expectancy, e.ci_low, e.ci_high))
    elif not e.survives_multiple_testing:
        out.append("It passes alone but not among the {} markets tested in the same "
                   "period — among that many, one this good is expected by chance."
                   .format(e.tests_considered))
    if e.random_p_value is None:
        out.append("It has not been compared with entries at random bars.")
    elif e.random_p_value > THRESHOLDS["random_p_value"]:
        out.append("No better than entries at random bars with the same exits and "
                   "costs (p = {:.3f}).".format(e.random_p_value))
    if e.grid_positive is not None and e.grid_positive < THRESHOLDS["grid_positive"]:
        out.append("Only {:.0f}% of nearby parameter sets made money — the result "
                   "depends on one setting.".format(100 * e.grid_positive))
    if e.max_drawdown_r is not None and e.max_drawdown_r > THRESHOLDS["max_drawdown_r"]:
        out.append("Its deepest drawdown was {:.1f}R (limit {:.0f}R).".format(
            e.max_drawdown_r, THRESHOLDS["max_drawdown_r"]))
    if e.runs_same_question > THRESHOLDS["max_runs_same_question"]:
        out.append("This exact test has been run {} times in the period. Re-running "
                   "until one passes is a search, and the pass is its luckiest draw."
                   .format(e.runs_same_question))
    if not e.tradable_now:
        out.append("Nothing made money on the most recent stretch, so there are no "
                   "thresholds to trade with right now.")
    return out


def _paper(e: Evidence) -> Optional[Decision]:
    """What paper trading adds, once the history has said yes."""
    needed = int(THRESHOLDS["min_paper_trades"])
    if e.paper_trades == 0:
        return Decision(RESEARCH_APPROVED, [
            "The history supports it. It has not been paper traded yet — {} closed "
            "paper trades are needed before anything further.".format(needed)])
    if e.paper_trades < needed:
        return Decision(PAPER_VALIDATION, [
            "{} of {} paper trades so far ({:+.2f}R each).".format(
                e.paper_trades, needed, e.paper_expectancy)])
    shortfall = e.expectancy - e.paper_expectancy
    if e.paper_expectancy <= 0 or shortfall > THRESHOLDS["paper_shortfall_r"]:
        return Decision(INELIGIBLE, [
            "Paper trading disagrees with the backtest: {} paper trades averaged "
            "{:+.2f}R against {:+.2f}R expected. Until those agree, the backtest is "
            "the one that is wrong.".format(e.paper_trades, e.paper_expectancy,
                                            e.expectancy)])
    return None


def evaluate(e: Evidence) -> Decision:
    """The state this evidence supports. Every reason for it is returned,
    not only the first."""
    invalid = _invalid(e) if e.experiment_id is not None else []
    if invalid:
        return Decision(INELIGIBLE, invalid)
    missing = _missing(e)
    if missing:
        return Decision(INSUFFICIENT, missing)
    negative = _negative(e)
    if negative:
        return Decision(INELIGIBLE, negative)

    paper = _paper(e)
    if paper is not None:
        return paper

    holding = []
    if not e.live_capable:
        holding.append("This strategy exists only in research: there is no live "
                       "implementation of it to review.")
    if e.venue_block:
        holding.append(e.venue_block)
    if not e.broker_validated:
        holding.append("The broker path has not been validated with a real order "
                       "(LIVE_BROKER_VALIDATED is not set — see TRADING.md §3a).")
    if holding:
        return Decision(PAPER_VALIDATION, [
            "History and paper trading agree ({} paper trades, {:+.2f}R each)."
            .format(e.paper_trades, e.paper_expectancy)] + holding)
    return Decision(HUMAN_REVIEW, [
        "Out-of-sample expectancy {:+.3f}R over {} trades (interval {:+.3f}R to "
        "{:+.3f}R), better than random entries, stable across parameters, and "
        "{} paper trades at {:+.2f}R agree. A person may now review it; nothing "
        "trades until one approves.".format(
            e.expectancy, e.trades, e.ci_low, e.ci_high, e.paper_trades,
            e.paper_expectancy)])
