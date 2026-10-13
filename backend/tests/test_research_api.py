"""The engine's walk-forward as a reproducible experiment, and the research
API — including the one thing in it that is a decision: an approval."""
from __future__ import annotations

import asyncio

import numpy as np
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.backtest import replay
from app.backtest.replay import BarSignal, Setup
from app.config import settings
from app.db.models import AuditRow, BacktestRow
from app.db.session import session_scope
from app.main import app
from app.research import datasets, registry
from app.research.engine_evidence import bars_needed, history_from, walk_forward_on_snapshot
from app.services.edge import latest_edge
from test_backtest_honesty import _edge, _paper, _record, _wipe_evidence

HOUR = 3600
MIDNIGHT = 1_700_006_400


def run(coro):
    return asyncio.run(coro)


def snapshot(symbol="RELIANCE", provider="angelone", count=1700, seed=13):
    rng = np.random.default_rng(seed)
    close = 30_000.0 * np.exp(np.cumsum(rng.normal(0.0012, 0.004, count)))   # a rising market
    opens = np.concatenate([[30_000.0], close[:-1]])
    wick = np.abs(rng.normal(0, 0.003, count)) * close
    rows = [[MIDNIGHT + i * HOUR, opens[i], max(opens[i], close[i]) + wick[i],
             min(opens[i], close[i]) - wick[i], close[i], 10.0] for i in range(count)]
    dataset = datasets.build(provider, symbol, "1h", rows)
    datasets.save(dataset)
    return dataset


def a_setup_every_few_bars(symbol, timeframe, base, higher, instrument, indices, **kwargs):
    """Stands in for the engine's replay, which takes forty milliseconds a bar."""
    close = base["close"].to_numpy()
    return {i: BarSignal(engine=Setup("BUY", float(close[i]), float(close[i]) * 0.99,
                                      float(close[i]) * 1.02, 70.0, "test"))
            for i in indices if i % 4 == 0}


@pytest.fixture
def clean(monkeypatch):
    monkeypatch.setattr(replay, "signals", a_setup_every_few_bars)
    run(_wipe_evidence())
    yield
    run(_wipe_evidence())


def test_a_snapshot_supplies_every_timeframe_the_replay_reads():
    history = history_from(snapshot(), bars=600)
    assert len(history.base) == 600 + replay.ANALYSIS_BARS
    assert set(history.higher) == {"4h", "1d"}
    assert history.source == "angelone"
    four_hour = history.higher["4h"]
    assert (four_hour["time"].diff().dropna() == 4 * HOUR).all()
    assert bars_needed("1h", 4000) == 4000 + replay.ANALYSIS_BARS + 400 * 24
    assert bars_needed("1m", 4000) == 60_000, "capped: nobody needs a year of minutes"


async def _walk_forward(dataset, symbol, profile):
    async with session_scope() as session:
        record = await walk_forward_on_snapshot(
            session, symbol, "1h", bars=1000, cost_profile=profile, dataset=dataset)
    async with session_scope() as session:
        rows = (await session.execute(select(BacktestRow))).scalars().all()
        return record, [dict(r.payload) for r in rows], await registry.get(
            session, record["id"])


def test_a_walk_forward_charged_as_another_venue_leaves_nothing_for_the_gate(clean):
    """An experiment, and on record as one — but not evidence about the venue
    this market actually trades on."""
    dataset = snapshot(symbol="OTCUSD", provider="elsewhere")
    record, rows, _ = run(_walk_forward(dataset, "OTCUSD", "test_perp"))
    assert record["cost_profile"] == "test_perp" and rows == []


def test_the_engine_walk_forward_is_recorded_as_an_experiment(clean):
    dataset = snapshot()

    record, rows, stored = run(_walk_forward(dataset, "RELIANCE", None))
    payload, = rows
    assert record["kind"] == "engine_walk_forward"
    assert record["datasets"][0]["dataset_id"] == dataset.id
    assert record["cost_profile"] == "india:stock" and record["venue"] == "angelone"
    assert record["trials"] == 9
    metrics = record["metrics"]
    assert metrics["trades"] >= 30
    assert 0.0 <= metrics["grid_positive"] <= 1.0 and len(metrics["grid"]) == 9
    assert metrics["random_entries"]["draws"] == 200
    assert 0.0 < metrics["random_entries"]["p_value"] <= 1.0
    assert metrics["max_drawdown_r"] >= 0.0
    assert registry.intact(stored)

    # …and the row the live gate reads points at it.
    assert payload["experiment_id"] == record["id"]
    assert payload["dataset_id"] == dataset.id and payload["data_source"] == "angelone"
    assert payload["code"]["engine"].startswith("engine@")
    assert payload["checks"]["random_entries"]["p_value"] == metrics["random_entries"]["p_value"]


def test_the_same_snapshot_gives_the_same_answer(clean):
    dataset = snapshot()

    async def once():
        async with session_scope() as session:
            record = await walk_forward_on_snapshot(
                session, "RELIANCE", "1h", bars=1000, dataset=dataset)
        return {k: record["metrics"][k] for k in
                ("trades", "expectancy_r", "ci_low", "ci_high", "random_entries",
                 "grid_positive", "max_drawdown_r")}
    assert run(once()) == run(once())


def test_no_evidence_is_produced_from_simulated_bars(clean):
    simulated = snapshot(provider="synthetic")

    async def go():
        async with session_scope() as session:
            return await walk_forward_on_snapshot(session, "RELIANCE", "1h", bars=1000,
                                                  dataset=simulated)
    with pytest.raises(datasets.DatasetError, match="simulated"):
        run(go())


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


def test_the_api_shows_what_was_run_and_on_what(clean, client):
    dataset = snapshot()

    async def go():
        async with session_scope() as session:
            return await walk_forward_on_snapshot(session, "RELIANCE", "1h", bars=1000,
                                                  dataset=dataset)
    record = run(go())

    listed = client.get("/api/research/experiments").json()
    assert [row["id"] for row in listed] == [record["id"]]
    assert listed[0]["intact"] is True and listed[0]["datasets"] == [dataset.id]
    assert client.get("/api/research/experiments?symbol=NIFTY").json() == []
    full = client.get("/api/research/experiments/" + record["id"]).json()
    assert full["sha256"] == record["sha256"] and full["intact"] is True
    assert client.get("/api/research/experiments/exp-none").status_code == 404

    catalogue = {row["dataset_id"]: row for row in client.get("/api/research/datasets").json()}
    assert catalogue[dataset.id]["quality_passed"] is True
    assert catalogue[dataset.id]["provider"] == "angelone"
    assert client.get("/api/research/study").json()["run"] is False

    state = client.get("/api/research/eligibility?symbol=RELIANCE&timeframe=1h").json()
    assert state["live_allowed"] is False
    assert state["state"] in ("INELIGIBLE", "INSUFFICIENT EVIDENCE", "RESEARCH APPROVED")
    assert state["experiment_id"] == record["id"]
    assert client.get("/api/research/eligibility?symbol=NOPE&timeframe=1h").status_code == 404


def test_an_approval_is_a_decision_someone_has_to_make(clean, client, monkeypatch):
    monkeypatch.setattr(settings, "live_broker_validated", True)
    approve = {"symbol": "RELIANCE", "timeframe": "1h", "approved_by": "KP",
               "note": "watched it for a month"}

    assert client.post("/api/research/approvals", json=approve).status_code == 428
    refused = client.post("/api/research/approvals", json=dict(approve, confirmed=True))
    assert refused.status_code == 409, "nothing on record: there is nothing to approve"
    assert "INSUFFICIENT EVIDENCE" in refused.json()["detail"]["message"]

    run(_record())
    run(_paper(25, +0.4))
    state = client.get("/api/research/eligibility?symbol=RELIANCE&timeframe=1h").json()
    assert state["state"] == "ELIGIBLE FOR HUMAN REVIEW" and state["live_allowed"] is False
    assert client.post("/api/research/approvals",
                       json=dict(approve, confirmed=True, approved_by="  ")).status_code == 422

    granted = client.post("/api/research/approvals", json=dict(approve, confirmed=True))
    assert granted.status_code == 200
    assert granted.json()["approved_by"] == "KP"
    assert run(_edge())["passed"] is True
    assert client.get("/api/research/eligibility?symbol=RELIANCE&timeframe=1h").json()[
        "live_allowed"] is True
    assert [row["approved_by"] for row in client.get("/api/research/approvals").json()] == ["KP"]

    revoked = client.delete("/api/research/approvals/" + granted.json()["id"])
    assert revoked.json()["revoked_at"] is not None
    assert run(_edge())["passed"] is False
    assert client.delete("/api/research/approvals/nope").status_code == 404

    async def audited():
        async with session_scope() as session:
            rows = (await session.execute(select(AuditRow.event))).scalars().all()
            return set(rows)
    assert {"strategy_approved", "strategy_approval_revoked"} <= run(audited())


def test_approving_does_not_arm_anything(clean, client, monkeypatch):
    """An approval lets the gate's evidence check pass. The mode is still
    PAPER, and every other check still stands in front of an order."""
    monkeypatch.setattr(settings, "live_broker_validated", True)
    run(_record())
    run(_paper(25, +0.4))
    client.post("/api/research/approvals", json={
        "symbol": "RELIANCE", "timeframe": "1h", "approved_by": "KP", "confirmed": True})
    assert client.get("/api/trading/mode").json()["mode"] == "PAPER"

    async def edge():
        async with session_scope() as session:
            return await latest_edge(session, "RELIANCE", "1h")
    assert run(edge())["approved"] is True
