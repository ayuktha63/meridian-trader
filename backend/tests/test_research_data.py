"""History fit to test on: cleaned by recorded rules, measured, and frozen
under the hash of its own contents."""
from __future__ import annotations

import asyncio
import json

import numpy as np
import pytest

from app.research import datasets, quality, versions

HOUR = 3600
T0 = 1_700_000_000 // HOUR * HOUR


def bars(count, start=T0, step=HOUR, price=100.0):
    rng = np.random.default_rng(3)
    closes = price * np.exp(np.cumsum(rng.normal(0, 0.004, count)))
    rows = []
    for i, close in enumerate(closes):
        open_ = closes[i - 1] if i else close
        rows.append([start + i * step, open_, max(open_, close) * 1.001,
                     min(open_, close) * 0.999, close, 10.0 + i % 7])
    return rows


# ── cleaning ──────────────────────────────────────────────────

def test_clean_history_passes_and_is_left_alone():
    rows = bars(600)
    data, report = quality.validate(rows, HOUR)
    assert report.passed, report.problems
    assert (len(data), report.missing, report.gaps) == (600, 0, 0)
    assert np.array_equal(data, np.asarray(rows))


def test_every_row_a_rule_removes_is_counted():
    rows = bars(600)
    rows[10], rows[11] = rows[11], rows[10]                     # out of order
    rows.append(list(rows[50]))                                 # a duplicate
    rows.append([T0 + 77 * HOUR + 120, 100, 101, 99, 100, 1])   # off the hourly grid
    rows[200][2] = rows[200][3] * 0.5                           # high below low
    rows[300][4] = float("nan")
    rows[400][1] = -5.0                                         # a negative price
    data, report = quality.validate(rows, HOUR)
    assert report.out_of_order >= 1
    assert (report.duplicates_dropped, report.misaligned_dropped) == (1, 1)
    assert (report.invalid_dropped, report.non_finite_dropped) == (2, 1)
    assert len(data) == 597
    assert np.all(np.diff(data[:, 0]) > 0), "sorted, one bar per timestamp"


def test_the_bar_still_forming_is_not_a_closed_bar():
    rows = bars(400)
    now = rows[-1][0] + HOUR / 2                  # half way through the last bar
    data, report = quality.validate(rows, HOUR, now=now)
    assert report.incomplete_dropped == 1
    assert data[-1, 0] == rows[-2][0]


def test_a_gap_stays_a_gap_and_is_measured():
    """Nothing is invented to fill it: a bar that was not traded is not data."""
    rows = bars(1000)
    del rows[500:530]
    data, report = quality.validate(rows, HOUR)
    assert len(data) == 970
    assert (report.missing, report.gaps, report.largest_gap_bars) == (30, 1, 30)
    assert report.missing_pct == 3.0
    assert not report.passed
    assert "missing" in report.problems[0]


def test_a_feed_that_went_stale_is_refused():
    rows = bars(600)
    for i in range(100, 180):
        rows[i][1:6] = [50.0, 50.0, 50.0, 50.0, 0.0]
    _, report = quality.validate(rows, HOUR)
    assert report.longest_stale_run == 80
    assert not report.passed


def test_too_short_a_history_is_refused():
    _, report = quality.validate(bars(120), HOUR)
    assert not report.passed
    assert "Only 120" in report.problems[0]


def test_a_market_with_sessions_is_not_held_to_a_round_the_clock_grid():
    """An exchange that shuts overnight is not missing those bars."""
    rows = [row for row in bars(2000) if 4 <= (row[0] // HOUR) % 24 < 10]
    _, report = quality.validate(rows, HOUR, continuous=False)
    assert report.passed, report.problems
    assert report.expected is None


def test_an_absurd_bar_is_pointed_out():
    rows = bars(600)
    rows[300][4] = rows[300][4] * 3
    rows[300][2] = rows[300][4] * 1.001
    _, report = quality.validate(rows, HOUR)
    assert report.extreme_bars >= 1
    assert any("robust deviations" in warning for warning in report.warnings)


# ── snapshots ─────────────────────────────────────────────────

def test_a_snapshot_is_named_by_its_contents():
    one = datasets.build("coindcx", "BTCUSD", "1h", bars(500))
    same = datasets.build("coindcx", "BTCUSD", "1h", bars(500))
    other = datasets.build("coindcx", "BTCUSD", "1h", bars(501))
    elsewhere = datasets.build("binance", "BTCUSD", "1h", bars(500))
    assert one.id == same.id
    assert len({one.id, other.id, elsewhere.id}) == 3
    assert one.real and one.quality_passed


def test_a_snapshot_reads_back_exactly_and_cannot_be_edited():
    built = datasets.build("coindcx", "ETHUSD", "1h", bars(500))
    datasets.save(built)
    loaded = datasets.load(built.id)
    assert np.array_equal(loaded.rows, built.rows)
    assert loaded.meta["cleaning_rules"] == list(quality.CLEANING_RULES)
    with pytest.raises(ValueError):
        loaded.rows[0, 4] = 1.0


def test_a_snapshot_changed_on_disk_is_refused():
    built = datasets.build("coindcx", "SOLUSD", "1h", bars(500))
    path = datasets.save(built)
    tampered = np.array(built.rows)
    tampered[250, 4] *= 1.01
    np.savez_compressed(path, rows=tampered)
    with pytest.raises(datasets.DatasetError, match="does not match its recorded hash"):
        datasets.load(built.id)


def test_a_missing_snapshot_says_so():
    with pytest.raises(datasets.DatasetError, match="not in the store"):
        datasets.load("0" * 16)


def test_coarser_bars_are_built_from_the_snapshot_and_say_where_from():
    midnight = 1_700_006_400                      # 00:00 UTC, on every grid
    hourly = datasets.build("coindcx", "BTCUSD", "1h", bars(24 * 30 + 5, start=midnight))
    four_hour = datasets.resample(hourly, "4h")
    assert four_hour.meta["derived_from"] == hourly.id
    assert four_hour.timeframe == "4h"
    first = hourly.rows[:4]
    assert four_hour.rows[0, 1] == first[0, 1]
    assert four_hour.rows[0, 2] == first[:, 2].max()
    assert four_hour.rows[0, 4] == first[-1, 4]
    assert four_hour.rows[0, 5] == pytest.approx(first[:, 5].sum())
    # 725 hourly bars end one bar into a four-hour bucket: that bucket is not complete.
    assert four_hour.bars == 181
    assert four_hour.last == midnight + 180 * 4 * HOUR
    with pytest.raises(datasets.DatasetError):
        datasets.resample(four_hour, "1h")


def test_a_coarse_bar_the_snapshot_only_half_covers_is_not_kept():
    """Two of its four hours are not a four-hour candle — its open is wrong."""
    midnight = 1_700_006_400
    hourly = datasets.build("coindcx", "BTCUSD", "1h",
                            bars(24 * 30, start=midnight + 2 * HOUR))
    four_hour = datasets.resample(hourly, "4h")
    assert four_hour.first == midnight + 4 * HOUR
    assert four_hour.last == midnight + 24 * 30 * HOUR - 4 * HOUR


def test_a_window_is_cut_by_time_half_open():
    data = datasets.build("coindcx", "BTCUSD", "1h", bars(500))
    frame = data.frame(start=T0 + 10 * HOUR, end=T0 + 20 * HOUR)
    assert list(frame["time"]) == [T0 + i * HOUR for i in range(10, 20)]
    assert list(frame.columns) == ["time", "open", "high", "low", "close", "volume"]


def test_no_snapshot_is_taken_from_the_simulator():
    """The suite has no venue connected: every symbol resolves to the simulator."""
    with pytest.raises(datasets.DatasetError, match="never taken from the simulator"):
        asyncio.run(datasets.fetch("EURUSD", "1h", bars=500))
    simulated = asyncio.run(datasets.fetch("EURUSD", "1h", bars=500, allow_simulated=True))
    assert not simulated.real
    assert json.loads((datasets.root() / (simulated.id + ".json")).read_text())[
        "provider"] == "synthetic"


# ── fingerprints ──────────────────────────────────────────────

def test_a_fingerprint_follows_the_code_it_names(tmp_path, monkeypatch):
    (tmp_path / "costs.py").write_text("RATE = 0.0005\n")
    monkeypatch.setattr(versions, "APP", tmp_path)
    before = versions.fingerprint(versions.COST_FILES)
    assert before == versions.fingerprint(versions.COST_FILES)
    (tmp_path / "costs.py").write_text("RATE = 0.0006\n")
    assert versions.fingerprint(versions.COST_FILES) != before


def test_the_engine_and_the_plays_share_one_version():
    assert versions.strategy_version("engine") == versions.strategy_version("breakout")
    assert versions.strategy_version("engine").startswith("engine@")
    assert versions.strategy_version("donchian_trend").startswith("research@")
    assert set(versions.current()) == {"engine", "execution", "costs", "research", "git"}
