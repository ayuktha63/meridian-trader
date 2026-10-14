"""Market history as an immutable, content-addressed snapshot.

A backtest read from a live feed cannot be run again: an hour later the
window has moved and the bars underneath the result are gone. A snapshot is
fetched once, cleaned by rules that are recorded with it, and named by the
hash of its contents — so a result can say exactly which bars produced it,
and anyone holding the same file can check.

Snapshots are only taken from a real venue. Generated bars are refused
unless a test asks for them by name.

A snapshot also says where it came from. `provenance` is the record of the
requests that produced it — which broker, which token, what was asked for
and what came back — and a history whose requests did not all succeed is
kept, marked incomplete, and fails its quality check: it is on the record,
and it cannot be tested on.
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

import numpy as np
import pandas as pd

from ..config import DATA_DIR
from ..data.providers.base import resample_rows
from ..data.store import COLUMNS
from ..instruments import get_instrument, get_timeframe
from . import exchange_calendar, quality
from .exchange_calendar import DAY, Session, TradingCalendar

FORMAT = "meridian-dataset-v1"
NOT_A_MARKET = ("synthetic", "unknown")


class DatasetError(RuntimeError):
    pass


def root() -> Path:
    path = DATA_DIR / "research" / "datasets"
    path.mkdir(parents=True, exist_ok=True)
    return path


@dataclass(frozen=True)
class Dataset:
    id: str
    provider: str
    symbol: str
    timeframe: str
    rows: np.ndarray
    meta: Dict[str, Any] = field(default_factory=dict)

    @property
    def bars(self) -> int:
        return len(self.rows)

    @property
    def first(self) -> float:
        return float(self.rows[0, 0]) if len(self.rows) else 0.0

    @property
    def last(self) -> float:
        return float(self.rows[-1, 0]) if len(self.rows) else 0.0

    @property
    def real(self) -> bool:
        return self.provider not in NOT_A_MARKET

    @property
    def quality_passed(self) -> bool:
        return bool((self.meta.get("quality") or {}).get("passed"))

    @property
    def session(self) -> Optional[Session]:
        """The exchange session these bars were held to, if any."""
        return exchange_calendar.by_name((self.meta.get("session") or {}).get("name"))

    @property
    def provenance(self) -> Dict[str, Any]:
        return self.meta.get("provenance") or {}

    @property
    def complete(self) -> bool:
        """Every request behind this snapshot succeeded. True for a snapshot
        that recorded no requests at all — one built from rows in hand."""
        return bool(self.provenance.get("complete", True))

    def frame(self, start: Optional[float] = None, end: Optional[float] = None) -> pd.DataFrame:
        """Bars whose open time is in [start, end), as the engine's frame."""
        rows = self.rows
        if start is not None:
            rows = rows[rows[:, 0] >= start]
        if end is not None:
            rows = rows[rows[:, 0] < end]
        return pd.DataFrame(rows, columns=list(COLUMNS)).reset_index(drop=True)

    def describe(self) -> Dict[str, Any]:
        """What an experiment records about the data it ran on."""
        return {"dataset_id": self.id, "provider": self.provider, "symbol": self.symbol,
                "timeframe": self.timeframe, "bars": self.bars, "first": self.first,
                "last": self.last, "sha256": self.meta.get("sha256"),
                "quality_passed": self.quality_passed,
                "derived_from": self.meta.get("derived_from"),
                "session": (self.meta.get("session") or {}).get("name"),
                "complete": self.complete,
                "source": {key: self.provenance.get(key) for key in
                           ("broker", "exchange", "trading_symbol", "token", "interval")
                           if self.provenance.get(key) is not None} or None}


def digest(provider: str, symbol: str, timeframe: str, rows: np.ndarray,
           context: Optional[Dict[str, Any]] = None) -> str:
    """The name of a snapshot. Its bars, and — for a market with a session —
    `context`: what those bars were judged against. A snapshot is never
    rewritten, so the same bars judged against another calendar, or under
    another token, have to be another snapshot; otherwise the first verdict
    would be the one on file for ever. With no context the name is what it
    always was."""
    parts = [FORMAT, provider, symbol.upper(), timeframe.lower()]
    if context is not None:
        parts.append(json.dumps(context, sort_keys=True, separators=(",", ":")))
    body = np.ascontiguousarray(rows, dtype="<f8").tobytes()
    return hashlib.sha256("|".join(parts).encode() + b"|" + body).hexdigest()


def _context(session: Optional[Session], calendar: Optional[TradingCalendar],
             explained_moves: Iterable[str],
             provenance: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """What a session market's bars were judged against, for its name."""
    if session is None:
        return None
    source = provenance or {}
    return {"session": session.name, "rules": quality.RULES_VERSION,
            "calendar": calendar.fingerprint if calendar is not None else None,
            "explained_moves": sorted(set(explained_moves)),
            "token": source.get("token"), "exchange": source.get("exchange"),
            "complete": bool(source.get("complete", True))}


def _continuous(symbol: str) -> bool:
    """Whether this market trades around the clock, so that any missing bar
    is a hole. Nothing in the catalogue does — each has a session. A snapshot
    of something no longer in it is held to the stricter reading."""
    try:
        get_instrument(symbol)
    except KeyError:
        return True
    return False


def build(provider: str, symbol: str, timeframe: str, raw_rows: Sequence[Sequence[float]],
          *, now: Optional[float] = None, derived_from: Optional[str] = None,
          min_bars: int = quality.MIN_BARS, session: Optional[Session] = None,
          calendar: Optional[TradingCalendar] = None,
          explained_moves: Iterable[str] = (),
          provenance: Optional[Dict[str, Any]] = None) -> Dataset:
    """Clean and assess rows from a provider and name the result.

    `session` holds the bars to an exchange's hours and to the days it was
    open; without one the bars are taken as they come, on the clock's grid,
    and nothing is known about what is missing. `provenance` is the record
    of the requests the rows came from, kept with the snapshot — and if it
    says the history is incomplete, the snapshot does not pass.
    """
    symbol, timeframe = symbol.upper(), timeframe.lower()
    seconds = get_timeframe(timeframe).seconds
    explained_moves = tuple(explained_moves)
    rows, report = quality.validate(
        raw_rows, seconds, continuous=session is None and _continuous(symbol), now=now,
        min_bars=min_bars, session=session, calendar=calendar,
        explained_moves=explained_moves)
    if provenance is not None and not provenance.get("complete", True):
        report.problems.insert(0, "The history is incomplete: {}".format(
            provenance.get("incomplete_reason") or "not every request succeeded."))
        report.passed = False
    rows = np.ascontiguousarray(rows, dtype="<f8")
    rows.setflags(write=False)
    context = _context(session, calendar, explained_moves, provenance)
    sha = digest(provider, symbol, timeframe, rows, context)
    meta = {
        "format": FORMAT, "sha256": sha, "context": context,
        "provider": provider, "symbol": symbol,
        "timeframe": timeframe, "bars": int(len(rows)),
        "first": float(rows[0, 0]) if len(rows) else 0.0,
        "last": float(rows[-1, 0]) if len(rows) else 0.0,
        "created_at": time.time() if now is None else float(now),
        "cleaning_rules": list(quality.cleaning_rules(session)),
        "quality": report.to_dict(), "derived_from": derived_from,
        "session": session.describe() if session is not None else None,
        "calendar": calendar.describe() if calendar is not None else None,
        "provenance": provenance,
    }
    return Dataset(id=sha[:16], provider=provider, symbol=symbol, timeframe=timeframe,
                   rows=rows, meta=meta)


def _resample_session(dataset: Dataset, timeframe: str, session: Session,
                      calendar: Optional[TradingCalendar],
                      explained_moves: Iterable[str]) -> Dataset:
    """Coarser bars for a market with a session: bucketed on the session's
    grid, and a coarse bar is kept only if every fine bar it should hold is
    there. A day built from a morning is not that day's bar."""
    seconds = get_timeframe(timeframe).seconds
    fine = get_timeframe(dataset.timeframe).seconds
    week = exchange_calendar.WEEK
    if seconds <= fine or (seconds < DAY and seconds % fine):
        raise DatasetError("Cannot build {} bars from {} bars.".format(
            timeframe, dataset.timeframe))
    if fine < DAY <= seconds and session.length % fine:
        # 15:15-15:30 is a whole 15-minute bar and a quarter of an hourly one:
        # a day cannot be counted in hours.
        raise DatasetError(
            "Cannot build {} bars from {} bars: the session is not a whole number "
            "of them.".format(timeframe, dataset.timeframe))
    data = dataset.rows
    if not len(data):
        raise DatasetError("Dataset {} has no bars to build from.".format(dataset.id))
    labels = session.bucket(data[:, 0], seconds)
    starts = np.flatnonzero(np.r_[True, labels[1:] != labels[:-1]])
    stops = np.r_[starts[1:], len(data)]
    if seconds < DAY:        # a bar this long, or what is left of the session
        want = np.ceil((session.bar_end(labels[starts], seconds) - labels[starts]) / fine)
    elif seconds < week and fine < DAY:
        want = np.full(len(starts), session.bars_per_day(fine))
    else:                    # a week holds however many days the exchange opened
        want = np.ones(len(starts))
    rows = [[float(labels[a]), float(data[a, 1]), float(data[a:b, 2].max()),
             float(data[a:b, 3].min()), float(data[b - 1, 4]), float(data[a:b, 5].sum())]
            for a, b, need in zip(starts, stops, want) if b - a >= need]
    complete_until = float(session.bar_end(data[-1:, 0], fine)[0])
    return build(dataset.provider, dataset.symbol, timeframe, rows, now=complete_until,
                 derived_from=dataset.id, session=session, calendar=calendar,
                 explained_moves=explained_moves)


def resample(dataset: Dataset, timeframe: str, *,
             calendar: Optional[TradingCalendar] = None,
             explained_moves: Iterable[str] = ()) -> Dataset:
    """A coarser timeframe built from this snapshot's own bars, so every
    timeframe of one study comes from the same observations. A coarse bar
    the snapshot only covers part of — at either end — is dropped rather
    than passed off as complete."""
    timeframe = timeframe.lower()
    seconds = get_timeframe(timeframe).seconds
    fine = get_timeframe(dataset.timeframe).seconds
    if dataset.session is not None:
        return _resample_session(dataset, timeframe, dataset.session, calendar,
                                 explained_moves)
    if seconds <= fine or seconds % fine:
        raise DatasetError("Cannot build {} bars from {} bars.".format(
            timeframe, dataset.timeframe))
    rows = resample_rows(dataset.rows.tolist(), seconds)
    if rows and rows[0][0] < dataset.first:
        rows = rows[1:]                 # the snapshot starts part-way through it
    # The last coarse bar is only complete if the fine bars reach its end.
    complete_until = dataset.last + fine
    return build(dataset.provider, dataset.symbol, timeframe, rows, now=complete_until,
                 derived_from=dataset.id)


def save(dataset: Dataset) -> Path:
    folder = root()
    data_path = folder / (dataset.id + ".npz")
    if not data_path.exists():                 # content-addressed: never rewritten
        np.savez_compressed(data_path, rows=dataset.rows)
        (folder / (dataset.id + ".json")).write_text(
            json.dumps(dataset.meta, indent=2, sort_keys=True))
    return data_path


def exists(dataset_id: str) -> bool:
    return (root() / (dataset_id + ".npz")).exists()


def load(dataset_id: str) -> Dataset:
    """Read a snapshot back, and refuse it if it is not the bytes it claims."""
    folder = root()
    try:
        meta = json.loads((folder / (dataset_id + ".json")).read_text())
        with np.load(folder / (dataset_id + ".npz")) as stored:
            rows = np.ascontiguousarray(stored["rows"], dtype="<f8")
    except (OSError, KeyError, ValueError) as exc:
        raise DatasetError("Dataset {} is not in the store.".format(dataset_id)) from exc
    sha = digest(meta["provider"], meta["symbol"], meta["timeframe"], rows,
                 meta.get("context"))
    if sha != meta.get("sha256") or not sha.startswith(dataset_id):
        raise DatasetError(
            "Dataset {} does not match its recorded hash — the file was changed "
            "after it was written. Results that cite it cannot be reproduced "
            "from it.".format(dataset_id))
    rows.setflags(write=False)
    return Dataset(id=dataset_id, provider=meta["provider"], symbol=meta["symbol"],
                   timeframe=meta["timeframe"], rows=rows, meta=meta)


def catalogue() -> List[Dict[str, Any]]:
    out = []
    for path in sorted(root().glob("*.json")):
        try:
            out.append(json.loads(path.read_text()))
        except ValueError:
            continue
    return sorted(out, key=lambda meta: meta.get("created_at", 0.0), reverse=True)


#: The furthest back a single snapshot asks for.
MAX_HISTORY_DAYS = 3_650
#: What the exchange's open days are measured from when a single stock is
#: snapshotted: the index's own daily bars over the same stretch.
CALENDAR_REFERENCE = ("NSE", "Nifty 50")


def session_of(symbol: str, provider: str) -> Optional[Session]:
    """The exchange session a snapshot of this market is held to, or None
    when none is defined for it yet. Only NSE cash equities have one: a
    future's history is one contract's life rather than a continuous series,
    and the currency and commodity segments keep other hours."""
    if provider != "angelone":
        return None
    try:
        instrument = get_instrument(symbol)
    except KeyError:
        return None
    if instrument.asset_class == "stock" and (instrument.exchange or "").upper() == "NSE":
        return exchange_calendar.NSE_EQUITY
    return None


async def _fetch_equity(symbol: str, timeframe: str, bars: int, session: Session,
                        now: float, source: Any = None) -> Dataset:
    """A cash equity's history, asked for window by window and held to its
    session: every request accounted for, the days checked against the
    index's, and nothing passed that is incomplete."""
    from datetime import datetime, timedelta, timezone

    from ..data.angel_history import INTERVALS, AngelHistorySource, HistoryUnavailable
    from . import india

    symbol, timeframe = symbol.upper(), timeframe.lower()
    if timeframe not in INTERVALS:
        raise DatasetError(
            "Angel One serves no {} bars for {}; a session's coarser bars are built "
            "from a finer snapshot.".format(timeframe, symbol))
    source = source or AngelHistorySource()
    instrument = get_instrument(symbol)
    trading_symbol = "{}-EQ".format((instrument.broker_symbol or instrument.symbol).upper())
    try:
        scrip = await source.scrip("NSE", trading_symbol)
    except HistoryUnavailable as exc:
        raise DatasetError(str(exc)) from exc
    if scrip is None:
        raise DatasetError("{} is not in Angel One's scrip master.".format(trading_symbol))

    seconds = get_timeframe(timeframe).seconds
    trading_days = -(-bars // session.bars_per_day(seconds))
    days = min(int(trading_days * 7 / 5 * 1.08) + 10, MAX_HISTORY_DAYS)
    end = datetime.fromtimestamp(now, tz=timezone.utc)
    # Whole exchange days: see `angel_history.day_start`.
    start = (end - timedelta(days=days)).astimezone(
        timezone(timedelta(seconds=session.utc_offset))).replace(
            hour=0, minute=0, second=0, microsecond=0)

    calendar = None
    reference = await source.scrip(*CALENDAR_REFERENCE)
    if reference is not None:
        index_rows, index_report = await source.candles(reference, "1d", start, end)
        if index_rows and index_report.complete:
            calendar = exchange_calendar.calendar_from_times(
                session, (row[0] for row in index_rows),
                "daily bars of {} {} (token {})".format(*CALENDAR_REFERENCE, reference.token))
    rows, report = await source.candles(scrip, timeframe, start, end)
    if not rows:
        raise DatasetError("Angel One returned no history for {} {}: {}".format(
            symbol, timeframe, report.incomplete_reason or "no bars in the range asked for."))
    dataset = build(report.broker, symbol, timeframe, rows, now=now, session=session,
                    calendar=calendar, explained_moves=india.explained_moves(symbol),
                    provenance=report.to_dict())
    save(dataset)
    return dataset


async def fetch(symbol: str, timeframe: str, *, bars: int = 150_000,
                allow_simulated: bool = False, now: Optional[float] = None) -> Dataset:
    """Take a snapshot from the venue that serves this symbol and store it.

    An NSE cash equity is held to its session (`_fetch_equity`). On the
    clock's grid none of its hourly or daily bars is "on the hour", and the
    snapshot that came back had every one of them thrown away — no bars, and
    a walk-forward that could only ever say "not enough history".
    """
    from ..data.providers import build_providers, resolve_provider

    providers = build_providers()
    provider = resolve_provider(symbol.upper(), providers)
    try:
        if provider is None:
            raise DatasetError(
                "Nothing serves {} right now: Angel One is not connected, and a "
                "research snapshot is taken from nowhere else.".format(symbol.upper()))
        if provider.name in NOT_A_MARKET and not allow_simulated:
            raise DatasetError(
                "{} is not a venue — a research snapshot is never taken from "
                "generated bars.".format(provider.name))
        session = session_of(symbol, provider.name)
        if session is not None:
            return await _fetch_equity(symbol, timeframe, bars, session,
                                       time.time() if now is None else now)
        raw = await provider.deep_history(symbol.upper(), timeframe.lower(), bars)
    finally:
        for each in providers.values():
            try:
                await each.close()
            except Exception:  # noqa: BLE001 - closing a client must not hide the result
                pass
    if not raw:
        raise DatasetError("{} returned no history for {} {}.".format(
            provider.name, symbol.upper(), timeframe.lower()))
    dataset = build(provider.name, symbol, timeframe, raw,
                    now=time.time() if now is None else now)
    save(dataset)
    return dataset
