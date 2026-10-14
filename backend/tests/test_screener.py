"""The screener is a snapshot, not a stream — a row with no timestamp on it
is indistinguishable from one just scanned, which is exactly the UX gap
docs/RISK_CONTROL_AUDIT.md names: "Screener signals do not display an
adequate 'as of' timestamp." This is the backend half of that fix.
"""
from __future__ import annotations

import time

from fastapi.testclient import TestClient

from app.main import app


def test_every_row_carries_when_it_was_generated():
    # Every instrument the synthetic feed knows, not just one or two: the
    # synthetic generator's day-seeded randomness means any single symbol
    # can land on WAIT (dropped from the screener) on a given test run, but
    # across the whole default universe at least one is reliably BUY/SELL.
    with TestClient(app) as client:
        before = time.time()
        response = client.get("/api/analysis/screener", params={
            "timeframe": "15m", "tradable_only": False,
        })
        after = time.time()
    assert response.status_code == 200
    rows = response.json()
    assert rows, "every instrument came back WAIT — vanishingly unlikely; check the engine"
    for row in rows:
        assert before <= row["generated_at"] <= after, (
            "generated_at must be a real timestamp from this scan, not a "
            "default or a stale value")
        assert row["bar_time"] > 0
