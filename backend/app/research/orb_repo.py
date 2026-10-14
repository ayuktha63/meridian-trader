"""The opening-range breakout of `arshadakl/nse-orb-backtest`, as published,
and the same rules under fills a broker could have given.

Source of the rules: `angel_orb_backtest.py` at commit
6130c7a2526235870830b45c052c86586582c5a6 (`simulate_day`, constants at the top
of the file). The repository carries **no LICENSE file** (its README says
"MIT"), so none of its code is copied: this is written from the rules, and a
test compares the two on the same bars (`test_orb_repo.py`, with a transcription
of the published loop kept in the test as the oracle).

Rules, unchanged from the source (5-minute bars, NSE cash equities, long only):

* Opening range: the 09:15–09:45 bars (six). Its width must be at least 0.20%
  of the day's first open, or there is no trade that day.
* Breakout: the first bar that opens between 09:45 and 14:30 and *closes*
  above the range high. Filters, each of which can only reject a bar:
  close at least `min_bo_pct` above the high; volume at least `vol_mult`
  times the mean volume of the range's bars; NIFTY's last closed bar above
  NIFTY's own range high; the weekday is not in `skip_days`.
* Entry: the next bar's open. Stop: the range low. Target: entry plus 1.5
  times (entry − range low), fixed. One trade per stock per day.
* Out: stop, target, or the first bar that opens at or after 15:15.

Two ways to play those rules, never mixed:

* **published** — the source's fills, as written: nothing is charged, the
  stop and the target fill exactly at their price, the entry bar's own range
  is not examined, a time exit takes the *close* of the 15:15 bar (15:20),
  and a stop that was gapped through still fills at the stop. It exists to
  reproduce the source and to show how far its fills are from a market's.
* **executable** — the same entries and the same levels, with a market's
  behaviour: the stop is live from the entry bar; a bar that opens through
  the stop fills at its open; the target is a resting limit that fills only
  when the market trades through it (at the open if it gapped over); when a
  bar reaches both, the stop is taken; the position is sold at the open of
  the 15:15 bar; and the trades are then sized and charged order by order
  (`india_costing`). A trade the account cannot afford one share of is not a
  trade — the source's `max(1, …)` is not kept.

Nothing here knows of a broker or an order.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from .exchange_calendar import NSE_EQUITY
from .simulator import Trade

VERSION = "1"
SOURCE: Dict[str, str] = {
    "repository": "https://github.com/arshadakl/nse-orb-backtest",
    "commit": "6130c7a2526235870830b45c052c86586582c5a6",
    "file": "angel_orb_backtest.py",
    "licence": "No LICENSE file in the repository; its README says MIT. Rules re-implemented, "
               "no code copied.",
}

IST_OFFSET = 19_800
TF = 300
OR_BARS = 6
OR_END = 9 * 3600 + 45 * 60
NO_NEW_ENTRY_AFTER = 14 * 3600 + 30 * 60
SQUARE_OFF = 15 * 3600 + 15 * 60
TARGET_R = 1.5
MIN_OR_WIDTH_PCT = 0.20

PUBLISHED, EXECUTABLE = "published", "executable"


@dataclass(frozen=True)
class Rules:
    """One named variant. The two the source offers are `BASELINE` (its
    `--no-filters`) and `V3` (its defaults); anything else is a new experiment
    and carries its own name."""
    name: str
    min_bo_pct: float = 0.0
    vol_mult: float = 0.0
    skip_days: Tuple[str, ...] = ()
    nifty_filter: bool = False

    def describe(self) -> Dict[str, Any]:
        return {"name": self.name, "min_bo_pct": self.min_bo_pct, "vol_mult": self.vol_mult,
                "skip_days": list(self.skip_days), "nifty_filter": self.nifty_filter,
                "or_bars": OR_BARS, "or_end": "09:45", "no_new_entry_after": "14:30",
                "square_off": "15:15", "target_r": TARGET_R,
                "min_or_width_pct": MIN_OR_WIDTH_PCT, "version": VERSION, "source": SOURCE}


BASELINE = Rules("orb_repo_baseline")
V3 = Rules("orb_repo_v3_default", min_bo_pct=0.15, vol_mult=1.5, skip_days=("Friday",),
           nifty_filter=True)
VARIANTS: Tuple[Rules, ...] = (BASELINE, V3)

_WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


@dataclass
class Setup:
    """A breakout the rules would trade: indices into the frame."""
    breakout: int
    entry: int
    day_first: int
    day_last: int                      # exclusive
    or_high: float
    or_low: float
    bo_pct: float
    vol_ratio: float


@dataclass
class Index:
    """Another series (NIFTY) on the same 5-minute grid, by day."""
    time: np.ndarray
    high: np.ndarray
    close: np.ndarray
    _days: Dict[int, Tuple[int, int]] = field(default_factory=dict)

    @classmethod
    def from_frame(cls, frame: pd.DataFrame) -> "Index":
        t = frame["time"].to_numpy(dtype=float)
        out = cls(t, frame["high"].to_numpy(dtype=float), frame["close"].to_numpy(dtype=float))
        day = NSE_EQUITY.local_day(t)
        for d in np.unique(day):
            idx = np.flatnonzero(day == d)
            out._days[int(d)] = (int(idx[0]), int(idx[-1]) + 1)
        return out

    def range_high(self, day: int) -> Optional[float]:
        span = self._days.get(day)
        if span is None:
            return None
        a, b = span
        sod = np.mod(self.time[a:b] + IST_OFFSET, 86_400)
        mask = sod < OR_END
        return float(self.high[a:b][mask].max()) if mask.any() else None

    def close_at(self, day: int, when: float) -> Optional[float]:
        """Close of the last bar of `day` whose open is at or before `when`."""
        span = self._days.get(day)
        if span is None:
            return None
        a, b = span
        k = int(np.searchsorted(self.time[a:b], when, "right")) - 1
        return float(self.close[a + k]) if k >= 0 else None


def _days(frame: pd.DataFrame) -> Tuple[np.ndarray, List[Tuple[int, int, int]]]:
    """(local day of each bar, [(day, first, last+1)]). The frame must be in
    time order; a day is a run of bars."""
    t = frame["time"].to_numpy(dtype=float)
    day = NSE_EQUITY.local_day(t)
    spans: List[Tuple[int, int, int]] = []
    if not len(t):
        return day, spans
    cuts = np.flatnonzero(np.r_[True, day[1:] != day[:-1], True])
    for a, b in zip(cuts[:-1], cuts[1:]):
        spans.append((int(day[a]), int(a), int(b)))
    return day, spans


def find_setups(frame: pd.DataFrame, rules: Rules,
                index: Optional[Index] = None) -> List[Setup]:
    """Every day's breakout under `rules`, decided from closed bars only: the
    bar that breaks out is known at its close, the entry is the bar after.

    A day without all six range bars is not traded (the source would build a
    range from whatever it had). A rule that needs NIFTY and has none for the
    day does not trade (the source does the same)."""
    t = frame["time"].to_numpy(dtype=float)
    o, h, l, c, v = (frame[k].to_numpy(dtype=float) for k in
                     ("open", "high", "low", "close", "volume"))
    sod = np.mod(t + IST_OFFSET, 86_400)
    _, spans = _days(frame)
    out: List[Setup] = []
    for day, a, b in spans:
        if _WEEKDAYS[int(NSE_EQUITY.weekday(day))] in rules.skip_days:
            continue
        in_range = np.flatnonzero(sod[a:b] < OR_END) + a
        if len(in_range) != OR_BARS:
            continue
        or_high, or_low = float(h[in_range].max()), float(l[in_range].min())
        first_open = float(o[in_range[0]])
        if not first_open > 0 or (or_high - or_low) / first_open * 100 < MIN_OR_WIDTH_PCT:
            continue
        avg_vol = float(v[in_range].mean()) or 1.0
        nifty_high: Optional[float] = None
        if rules.nifty_filter:
            nifty_high = index.range_high(day) if index is not None else None
            if nifty_high is None:
                continue
        window = [k for k in range(a, b) if OR_END <= sod[k] < NO_NEW_ENTRY_AFTER]
        for n, k in enumerate(window):
            if c[k] <= or_high:
                continue
            bo = (c[k] - or_high) / or_high * 100
            if bo < rules.min_bo_pct:
                continue
            ratio = v[k] / avg_vol
            if rules.vol_mult > 0 and ratio < rules.vol_mult:
                continue
            if rules.nifty_filter:
                # The index bar that opened at or before this bar's open has
                # closed by this bar's close; the source reads it the same way.
                now = index.close_at(day, t[k])
                if now is None or now <= nifty_high:
                    continue
            if n + 1 >= len(window):
                break                       # no entry bar inside the entry window
            out.append(Setup(k, window[n + 1], a, b, or_high, or_low, bo, ratio))
            break
    return out


# ── the two ways of playing a setup ───────────────────────────

def _trade(frame_cols: Tuple[np.ndarray, ...], s: Setup, exit_i: int, exit_px: float,
           reason: str, risk: float, stop: float) -> Trade:
    t, o, h, l, c = frame_cols
    entry = float(o[s.entry])
    last = max(exit_i, s.entry)
    best = float(h[s.entry:last + 1].max())
    worst = float(l[s.entry:last + 1].min())
    gross = (exit_px - entry) / risk
    return Trade(
        signal_index=s.breakout, entry_index=s.entry, exit_index=exit_i,
        entry_time=float(t[s.entry]), exit_time=float(t[exit_i]), direction=1,
        entry=entry, exit=float(exit_px), initial_stop=float(stop), risk=float(risk),
        gross_r=gross, cost_r=0.0, r=gross, bars_held=exit_i - s.entry, reason=reason,
        mae_r=(worst - entry) / risk, mfe_r=(best - entry) / risk, tag="orb_repo")


def _columns(frame: pd.DataFrame) -> Tuple[np.ndarray, ...]:
    return tuple(frame[k].to_numpy(dtype=float) for k in ("time", "open", "high", "low", "close"))


def play_published(frame: pd.DataFrame, s: Setup) -> Optional[Trade]:
    """The source's loop, as written. R is the source's: (exit − entry) over
    (entry − range low)."""
    cols = _columns(frame)
    t, o, h, l, c = cols
    sod = np.mod(t + IST_OFFSET, 86_400)
    entry = float(o[s.entry])
    risk = entry - s.or_low
    if risk <= 0:
        return None
    target = entry + TARGET_R * risk
    for k in range(s.entry + 1, s.day_last):
        if l[k] <= s.or_low:
            return _trade(cols, s, k, s.or_low, "stop", risk, s.or_low)
        if h[k] >= target:
            return _trade(cols, s, k, target, "target", risk, s.or_low)
        if sod[k] >= SQUARE_OFF:
            return _trade(cols, s, k, c[k], "time", risk, s.or_low)
    last = s.day_last - 1 if s.day_last - 1 > s.entry else s.entry
    return _trade(cols, s, last, c[last], "data_end", risk, s.or_low)


def play_executable(frame: pd.DataFrame, s: Setup, *, delay: int = 0,
                    stop: Optional[float] = None, rr: float = TARGET_R,
                    decided: Optional[float] = None) -> Optional[Trade]:
    """The same levels under a market's fills. `delay` bars of execution lag
    move the entry later (the stop and target still come from the range).

    Planned risk is the distance from the *decision* price — the breakout
    bar's close — to the stop, as in the other studies (`simulator.py`); the
    result is measured from the fill. Returns None when the fill is at or
    through the stop (no trade) or the bar needed does not exist."""
    cols = _columns(frame)
    t, o, h, l, c = cols
    sod = np.mod(t + IST_OFFSET, 86_400)
    stop = s.or_low if stop is None else stop
    k0 = s.entry + delay
    if k0 >= s.day_last or sod[k0] >= SQUARE_OFF:
        return None
    decided = float(c[s.breakout]) if decided is None else decided
    risk = decided - stop
    entry = float(o[k0])
    if risk <= 0 or entry <= stop:
        return None
    target = entry + rr * (entry - stop)
    shifted = Setup(s.breakout, k0, s.day_first, s.day_last, s.or_high, s.or_low,
                    s.bo_pct, s.vol_ratio)
    if l[k0] <= stop:                       # stopped inside the bar it was filled in
        return _trade(cols, shifted, k0, stop, "stop", risk, stop)
    for k in range(k0 + 1, s.day_last):
        if sod[k] >= SQUARE_OFF:
            return _trade(cols, shifted, k, o[k], "time", risk, stop)
        if o[k] <= stop:
            return _trade(cols, shifted, k, o[k], "gap", risk, stop)
        if l[k] <= stop:
            return _trade(cols, shifted, k, stop, "stop", risk, stop)
        if o[k] >= target:
            return _trade(cols, shifted, k, o[k], "target", risk, stop)
        if h[k] > target:
            return _trade(cols, shifted, k, target, "target", risk, stop)
    last = s.day_last - 1
    return _trade(cols, shifted, last, c[last], "data_end", risk, stop)


def play_all(frame: pd.DataFrame, setups: Sequence[Setup], mode: str,
             **kwargs: Any) -> List[Trade]:
    play = play_published if mode == PUBLISHED else play_executable
    out = []
    for s in setups:
        trade = play(frame, s, **kwargs) if mode == EXECUTABLE else play(frame, s)
        if trade is not None:
            out.append(trade)
    return out


def summary_published(trades: Sequence[Trade]) -> Dict[str, Any]:
    """What the source's own report would say about these trades, before any
    cost: R is the source's R."""
    r = np.array([t.gross_r for t in trades], dtype=float)
    if not len(r):
        return {"trades": 0}
    wins, losses = r[r > 0], r[r < 0]
    return {"trades": len(r), "win_rate": float((r > 0).mean()), "mean_r": float(r.mean()),
            "profit_factor": float(wins.sum() / -losses.sum()) if len(losses) else None,
            "exits": {k: sum(t.reason == k for t in trades)
                      for k in ("stop", "target", "time", "data_end")}}
