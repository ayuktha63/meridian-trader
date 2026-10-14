"""The eligibility rules, one at a time, and the record they read from."""
from __future__ import annotations

import asyncio
import json
from dataclasses import replace

import pytest
from sqlalchemy import delete

from app.db.models import ExperimentRow
from app.db.session import init_db, session_scope
from app.research import eligibility, registry
from app.research.eligibility import (
    HUMAN_REVIEW, INELIGIBLE, INSUFFICIENT, PAPER_VALIDATION, RESEARCH_APPROVED, Evidence,
    evaluate,
)

CODE = {"engine": "engine@1", "execution": "exec@1", "costs": "costs@1"}


def good(**changes) -> Evidence:
    """Evidence with nothing wrong with it."""
    base = Evidence(
        strategy="engine", symbol="RELIANCE", timeframe="1h", live_capable=True,
        experiment_id="exp-1", record_intact=True, data_source="angelone",
        quality_passed=True, age_days=3.0, tested=dict(CODE), current=dict(CODE),
        cost_profile="india:stock", expected_cost_profile="india:stock", trades=90,
        expectancy=0.30, ci_low=0.08, ci_high=0.52, survives_multiple_testing=True,
        tests_considered=1, random_p_value=0.01, grid_positive=0.8, max_drawdown_r=7.0,
        tradable_now=True, runs_same_question=1, paper_trades=25, paper_expectancy=0.25,
        broker_validated=True, venue_block=None)
    return replace(base, **changes)


def test_everything_in_order_is_eligible_for_review_and_no_more():
    decision = evaluate(good())
    assert decision.state == HUMAN_REVIEW and decision.reviewable
    assert "nothing trades until one approves" in decision.reasons[0]
    assert decision.rules_version == eligibility.RULES_VERSION
    assert decision.thresholds == eligibility.THRESHOLDS


@pytest.mark.parametrize("change, state, words", [
    (dict(experiment_id=None), INSUFFICIENT, "is on record"),
    (dict(trades=12), INSUFFICIENT, "Only 12 out-of-sample trades"),
    (dict(age_days=45.0), INSUFFICIENT, "45 days old"),
    (dict(record_intact=False), INELIGIBLE, "no longer matches its hash"),
    (dict(data_source="synthetic"), INELIGIBLE, "did not run on a real market"),
    (dict(data_source=None), INELIGIBLE, "source not recorded"),
    (dict(quality_passed=False), INELIGIBLE, "failed its quality checks"),
    (dict(tested=dict(CODE, engine="engine@0")), INELIGIBLE, "strategy's code has changed"),
    (dict(tested=dict(CODE, execution="exec@0")), INELIGIBLE, "execution model has changed"),
    (dict(tested=dict(CODE, costs="costs@0")), INELIGIBLE, "cost model has changed"),
    (dict(cost_profile="test_perp"), INELIGIBLE, "different venue"),
    (dict(expectancy=-0.05, ci_low=-0.3), INELIGIBLE, "expectancy after costs is -0.050R"),
    (dict(ci_low=-0.02), INELIGIBLE, "includes zero"),
    (dict(survives_multiple_testing=False, tests_considered=6), INELIGIBLE, "among the 6 markets"),
    (dict(random_p_value=None), INELIGIBLE, "not been compared with entries at random"),
    (dict(random_p_value=0.2), INELIGIBLE, "No better than entries at random"),
    (dict(grid_positive=0.3), INELIGIBLE, "depends on one setting"),
    (dict(max_drawdown_r=24.0), INELIGIBLE, "deepest drawdown was 24.0R"),
    (dict(runs_same_question=9), INELIGIBLE, "run 9 times"),
    (dict(tradable_now=False), INELIGIBLE, "no thresholds to trade with"),
    (dict(paper_trades=0), RESEARCH_APPROVED, "not been paper traded"),
    (dict(paper_trades=8), PAPER_VALIDATION, "8 of 20 paper trades"),
    (dict(paper_expectancy=-0.1), INELIGIBLE, "Paper trading disagrees"),
    (dict(paper_expectancy=-0.05, expectancy=0.31), INELIGIBLE, "Paper trading disagrees"),
    (dict(expectancy=0.7, ci_low=0.4, paper_expectancy=0.2), INELIGIBLE, "Paper trading disagrees"),
    (dict(broker_validated=False), PAPER_VALIDATION, "LIVE_BROKER_VALIDATED"),
    (dict(venue_block="Live orders on MCX are blocked."), PAPER_VALIDATION, "MCX are blocked"),
    (dict(live_capable=False), PAPER_VALIDATION, "exists only in research"),
])
def test_each_rule_holds_it_back_on_its_own(change, state, words):
    decision = evaluate(good(**change))
    assert decision.state == state
    assert any(words in reason for reason in decision.reasons), decision.reasons
    assert not decision.reviewable


def test_every_reason_is_reported_not_only_the_first():
    decision = evaluate(good(random_p_value=0.3, grid_positive=0.1, max_drawdown_r=40.0))
    assert decision.state == INELIGIBLE
    assert len(decision.reasons) == 3


def test_invalid_evidence_is_refused_before_it_is_read():
    """A spectacular result on simulated data is not a weak pass."""
    decision = evaluate(good(data_source="synthetic", expectancy=2.0, ci_low=1.5))
    assert decision.state == INELIGIBLE
    assert decision.reasons == ["The test did not run on a real market's data (synthetic)."]


def test_a_caller_cannot_loosen_a_threshold():
    with pytest.raises(TypeError):
        evaluate(good(), min_trades=1)                    # noqa


# ── the record ────────────────────────────────────────────────

def run(coro):
    return asyncio.run(coro)


def _experiment(**changes):
    fields = dict(kind="engine_walk_forward", stage="walk_forward", strategy="engine",
                  symbol="RELIANCE", timeframe="1h", venue="angelone",
                  cost_profile="india:stock", verdict="NO MEASURED EDGE",
                  datasets=[{"dataset_id": "abc"}], window={"from": 1, "to": 2},
                  parameters={"grid": [[50.0, 1.5]]}, assumptions={"costs": {}},
                  metrics={"trades": 40, "expectancy_r": -0.1}, reasons=["no edge"],
                  trials=9, seeds={"bootstrap": 7})
    fields.update(changes)
    return registry.build(**fields)


@pytest.fixture
def clean():
    async def wipe():
        await init_db()
        async with session_scope() as session:
            await session.execute(delete(ExperimentRow))
    run(wipe())
    yield
    run(wipe())


def test_a_record_says_what_ran_on_what_with_what_code(clean):
    record = _experiment()
    assert record["id"].startswith("exp-")
    assert set(record["code"]) == {"engine", "execution", "costs", "research", "git"}
    assert record["strategy"]["version"] == record["code"]["engine"]
    assert record["verdict"] == {"state": "NO MEASURED EDGE", "reasons": ["no edge"]}
    assert registry.intact(record)


def test_a_record_is_stored_twice_and_reads_back_whole(clean):
    async def go():
        async with session_scope() as session:
            saved = await registry.save(session, _experiment())
        async with session_scope() as session:
            return saved, await registry.get(session, saved["id"])
    saved, loaded = run(go())
    assert loaded == saved and registry.intact(loaded)
    on_disk = json.loads((registry.root() / (saved["id"] + ".json")).read_text())
    assert on_disk == saved, "the file beside the datasets is the same record"


def test_a_record_that_was_edited_no_longer_matches(clean):
    record = _experiment()
    record["metrics"]["expectancy_r"] = 0.4
    assert not registry.intact(record)
    assert not registry.intact({"id": "exp-x"})


def test_every_try_is_counted(clean):
    async def go():
        async with session_scope() as session:
            for symbol, trials in (("RELIANCE", 9), ("RELIANCE", 9), ("TCS", 33)):
                await registry.save(session, _experiment(symbol=symbol, trials=trials))
            await registry.save(session, _experiment(kind="study", trials=198))
        async with session_scope() as session:
            return (await registry.trials_on_record(session),
                    await registry.trials_on_record(session, kind="engine_walk_forward"),
                    await registry.runs_on_record(
                        session, kind="engine_walk_forward", strategy="engine",
                        symbol="RELIANCE", timeframe="1h"),
                    [r["symbol"] for r in await registry.recent(session, symbol="TCS")],
                    (await registry.latest(session, kind="study", strategy="engine",
                                           symbol="RELIANCE", timeframe="1h"))["trials"])
    total, engine_trials, runs, recent, latest = run(go())
    assert (total, engine_trials, runs, recent, latest) == (249, 51, 2, ["TCS"], 198)
