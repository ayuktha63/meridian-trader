"""Is this history fit to test on?

Two steps, kept apart so the record shows both what was wrong with the data
as it arrived and what was done about it:

* `clean` applies a fixed, short list of rules — sort, drop what cannot be a
  candle — and counts every row each rule removed. It never invents a bar: a
  gap stays a gap.
* `assess` measures what is left and decides whether it may be used.

The rules carry a version. A result is only comparable with another that
was cleaned the same way.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

from ..data.store import bar_open_time

RULES_VERSION = "1"

#: Share of expected bars a continuously traded market may be missing.
MAX_MISSING_PCT = 1.0
#: Share of the rows that arrived which may be thrown away as unusable.
MAX_DROPPED_PCT = 1.0
#: Fewer bars than this is not a history.
MIN_BARS = 300
#: Consecutive bars with no movement and no volume before the feed is
#: suspected of having gone stale.
STALE_RUN_BARS = 48
#: A bar's return this many robust deviations from the median is reported.
EXTREME_SIGMAS = 15.0

CLEANING_RULES = (
    "drop rows with a non-finite value",
    "sort by bar open time",
    "keep the last of rows sharing a timestamp",
    "drop bars whose time is not on the timeframe's grid",
    "drop bars with a non-positive price, high below low, or open/close outside high-low",
    "drop the bar still forming when the data was fetched",
)


@dataclass
class QualityReport:
    rules_version: str = RULES_VERSION
    received: int = 0
    bars: int = 0
    first: float = 0.0
    last: float = 0.0
    #: A market that trades around the clock: every bar is expected.
    continuous: bool = True
    expected: Optional[int] = None
    missing: int = 0
    missing_pct: float = 0.0
    gaps: int = 0
    largest_gap_bars: int = 0
    non_finite_dropped: int = 0
    out_of_order: int = 0
    duplicates_dropped: int = 0
    misaligned_dropped: int = 0
    invalid_dropped: int = 0
    incomplete_dropped: int = 0
    zero_volume: int = 0
    zero_volume_pct: float = 0.0
    longest_stale_run: int = 0
    extreme_bars: int = 0
    passed: bool = False
    problems: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _aligned(times: np.ndarray, tf_seconds: int) -> np.ndarray:
    if tf_seconds == 604_800:
        return np.array([bar_open_time(t, tf_seconds) == t for t in times], dtype=bool)
    return (times % tf_seconds) == 0


def clean(rows: Sequence[Sequence[float]], tf_seconds: int,
          now: Optional[float] = None) -> Tuple[np.ndarray, QualityReport]:
    """Apply `CLEANING_RULES`. Returns the usable bars and what was removed."""
    report = QualityReport()
    data = np.asarray(rows, dtype=float).reshape(-1, 6) if len(rows) else np.empty((0, 6))
    report.received = len(data)
    if not len(data):
        return data, report

    finite = np.isfinite(data).all(axis=1)
    report.non_finite_dropped = int((~finite).sum())
    data = data[finite]

    report.out_of_order = int((np.diff(data[:, 0]) < 0).sum())
    data = data[np.argsort(data[:, 0], kind="stable")]

    # The last of a run of equal timestamps is the venue's final word on it.
    keep = np.r_[data[1:, 0] != data[:-1, 0], True]
    report.duplicates_dropped = int((~keep).sum())
    data = data[keep]

    aligned = _aligned(data[:, 0], tf_seconds)
    report.misaligned_dropped = int((~aligned).sum())
    data = data[aligned]

    o, h, low, c, v = (data[:, i] for i in range(1, 6))
    valid = ((np.minimum.reduce([o, h, low, c]) > 0) & (h >= low)
             & (h >= np.maximum(o, c)) & (low <= np.minimum(o, c)) & (v >= 0))
    report.invalid_dropped = int((~valid).sum())
    data = data[valid]

    if now is not None and len(data):
        closed = data[:, 0] + tf_seconds <= now
        report.incomplete_dropped = int((~closed).sum())
        data = data[closed]
    return data, report


def _longest_run(mask: np.ndarray) -> int:
    best = run = 0
    for flag in mask:
        run = run + 1 if flag else 0
        best = max(best, run)
    return int(best)


def assess(data: np.ndarray, tf_seconds: int, report: Optional[QualityReport] = None,
           continuous: bool = True, min_bars: int = MIN_BARS) -> QualityReport:
    """Measure cleaned bars and decide whether they may be tested on."""
    report = report or QualityReport(received=len(data))
    report.continuous = continuous
    report.bars = len(data)
    if not len(data):
        report.problems.append("No usable bars.")
        return report

    times, close, volume = data[:, 0], data[:, 4], data[:, 5]
    report.first, report.last = float(times[0]), float(times[-1])
    steps = np.diff(times) / tf_seconds
    if continuous:
        report.expected = int(round((times[-1] - times[0]) / tf_seconds)) + 1
        report.missing = max(report.expected - len(data), 0)
        report.missing_pct = round(100.0 * report.missing / report.expected, 3)
        holes = steps[steps > 1.5]
        report.gaps = int(len(holes))
        report.largest_gap_bars = int(round(holes.max() - 1)) if len(holes) else 0

    report.zero_volume = int((volume == 0).sum())
    report.zero_volume_pct = round(100.0 * report.zero_volume / len(data), 3)
    flat = (data[:, 2] == data[:, 3]) & (volume == 0)
    report.longest_stale_run = _longest_run(flat)

    returns = np.diff(np.log(close))
    if len(returns) > 30:
        centre = np.median(returns)
        scale = 1.4826 * np.median(np.abs(returns - centre))
        if scale > 0:
            report.extreme_bars = int((np.abs(returns - centre) > EXTREME_SIGMAS * scale).sum())

    dropped = (report.non_finite_dropped + report.misaligned_dropped
               + report.invalid_dropped + report.duplicates_dropped)
    dropped_pct = 100.0 * dropped / max(report.received, 1)

    if len(data) < min_bars:
        report.problems.append("Only {} usable bars (need {}).".format(len(data), min_bars))
    if dropped_pct > MAX_DROPPED_PCT:
        report.problems.append(
            "{:.2f}% of the rows received were unusable (limit {}%).".format(
                dropped_pct, MAX_DROPPED_PCT))
    if continuous and report.missing_pct > MAX_MISSING_PCT:
        report.problems.append(
            "{:.2f}% of the bars this market should have are missing (limit {}%)."
            .format(report.missing_pct, MAX_MISSING_PCT))
    if report.longest_stale_run > STALE_RUN_BARS:
        report.problems.append(
            "{} consecutive bars with no movement and no volume — the feed looks "
            "stale there.".format(report.longest_stale_run))

    if report.out_of_order:
        report.warnings.append("{} rows arrived out of order.".format(report.out_of_order))
    if report.largest_gap_bars > 3:
        report.warnings.append(
            "Largest gap is {} bars; {} gaps in all.".format(
                report.largest_gap_bars, report.gaps))
    if report.extreme_bars:
        report.warnings.append(
            "{} bars moved more than {:g} robust deviations — check them before "
            "trusting a result that depends on them.".format(
                report.extreme_bars, EXTREME_SIGMAS))
    report.passed = not report.problems
    return report


def validate(rows: Sequence[Sequence[float]], tf_seconds: int, *, continuous: bool = True,
             now: Optional[float] = None,
             min_bars: int = MIN_BARS) -> Tuple[np.ndarray, QualityReport]:
    """Clean, then assess. The one call a dataset is built with."""
    data, report = clean(rows, tf_seconds, now)
    return data, assess(data, tf_seconds, report, continuous, min_bars)
