"""Peak-to-trough drawdown — the gap the daily-loss limit (reset at
midnight) cannot catch: a losing week made of individually-fine days.
docs/RISK_CONTROL_AUDIT.md finding 1.
"""
from __future__ import annotations

import pytest

from app.broker.drawdown import DrawdownTracker


@pytest.fixture
def tracker():
    return DrawdownTracker()  # in-memory only, same as a unit test wants


def test_a_new_high_raises_the_peak_and_never_locks(tracker, monkeypatch):
    monkeypatch.setattr("app.broker.drawdown.get_settings",
                        lambda: type("S", (), {"live_max_drawdown_pct": 10.0})())
    assert tracker.update(10_000.0) is None
    assert tracker.update(10_500.0) is None
    status = tracker.status()
    assert status.peak_equity == 10_500.0
    assert not status.locked


def test_a_small_pullback_inside_the_limit_does_not_lock(tracker, monkeypatch):
    monkeypatch.setattr("app.broker.drawdown.get_settings",
                        lambda: type("S", (), {"live_max_drawdown_pct": 10.0})())
    tracker.update(10_000.0)
    reason = tracker.update(9_500.0)  # 5% down, limit is 10%
    assert reason is None
    assert not tracker.status().locked


def test_a_pullback_past_the_limit_locks_and_says_why(tracker, monkeypatch):
    monkeypatch.setattr("app.broker.drawdown.get_settings",
                        lambda: type("S", (), {"live_max_drawdown_pct": 10.0})())
    tracker.update(10_000.0)
    reason = tracker.update(8_900.0)  # 11% down
    assert reason is not None and "11.0%" in reason
    status = tracker.status()
    assert status.locked and status.locked_reason == reason


def test_once_locked_it_stays_locked_through_further_small_moves(tracker, monkeypatch):
    monkeypatch.setattr("app.broker.drawdown.get_settings",
                        lambda: type("S", (), {"live_max_drawdown_pct": 10.0})())
    tracker.update(10_000.0)
    tracker.update(8_900.0)
    assert tracker.status().locked
    # Recovering most of the way back up does not self-clear the lock — only
    # `clear()`, a human decision, does that.
    assert tracker.update(9_800.0) is None
    assert tracker.status().locked


def test_a_zero_limit_disables_the_check(tracker, monkeypatch):
    monkeypatch.setattr("app.broker.drawdown.get_settings",
                        lambda: type("S", (), {"live_max_drawdown_pct": 0.0})())
    tracker.update(10_000.0)
    assert tracker.update(1_000.0) is None  # a 90% drawdown, still not locked
    assert not tracker.status().locked


def test_clear_lifts_the_lock_without_erasing_the_peak(tracker, monkeypatch):
    monkeypatch.setattr("app.broker.drawdown.get_settings",
                        lambda: type("S", (), {"live_max_drawdown_pct": 10.0})())
    tracker.update(10_000.0)
    tracker.update(8_900.0)
    assert tracker.status().locked
    tracker.clear()
    status = tracker.status()
    assert not status.locked and status.locked_reason is None
    assert status.peak_equity == 10_000.0, "the high-water mark is history, not undone"


def test_state_persists_across_a_restart(tmp_path, monkeypatch):
    monkeypatch.setattr("app.broker.drawdown.get_settings",
                        lambda: type("S", (), {"live_max_drawdown_pct": 10.0})())
    path = tmp_path / "drawdown.json"
    first = DrawdownTracker(path)
    first.update(10_000.0)
    first.update(8_900.0)
    assert first.status().locked

    second = DrawdownTracker(path)  # a fresh process reading the same file
    status = second.status()
    assert status.locked and status.peak_equity == 10_000.0


def test_an_unreadable_saved_state_locks_towards_no_trading(tmp_path):
    path = tmp_path / "drawdown.json"
    path.write_text("{not valid json")
    tracker = DrawdownTracker(path)
    assert tracker.status().locked, "unreadable state must fail closed, like the kill switch"
