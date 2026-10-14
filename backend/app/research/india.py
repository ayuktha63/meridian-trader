"""Indian cash equities as research data: a named universe, and a history
of each member that has been checked before anything is tested on it.

    python -m app.research.india universe                 what the universe is, and its limits
    python -m app.research.india snapshot 1d --years 8    take and check a history of each member
    python -m app.research.india snapshot 15m --years 2 --symbols RELIANCE SBIN --pause 1
    python -m app.research.india accepted 1d              which snapshots a study may use, and why not

Nothing here can place an order. It reads a list of companies and a broker's
candles, and writes snapshots.

Three things are decided here rather than left to whoever runs a study:

* **Which stocks.** A universe is a file with a version, a date and a source
  (`universes/`), not a list typed into a command. It also says what it is
  *not* — today's index members are the companies that survived, and a
  result on their past is flattered by that. The limitation travels with
  every snapshot taken from it.
* **Which days.** The exchange's trading days are measured from the index's
  own daily bars over the same stretch, so a stock's missing day is a
  missing day and a holiday is a holiday. Where the index cannot be read,
  the agreement of the universe itself is used, and the record says which.
* **Whether the history may be used.** `accept` is the gate: real venue,
  session-checked, calendar-checked, complete, quality passed. A snapshot
  that fails is kept — it is evidence about the data — and is never tested on.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple


from . import datasets, exchange_calendar
from .exchange_calendar import Session, TradingCalendar

UNIVERSE_DIR = Path(__file__).resolve().parent / "universes"
EXPLAINED_MOVES = UNIVERSE_DIR / "corporate_actions.json"

#: A full year on the NSE has about 245 to 250 trading days. A measured
#: calendar outside this range for a whole year is itself suspect — which is
#: the only check there is on a hole the whole venue shares.
TRADING_DAYS_PER_YEAR = (238, 256)


class IndiaDataError(RuntimeError):
    pass


# ── the universe ──────────────────────────────────────────────

@dataclass(frozen=True)
class Universe:
    id: str
    version: str
    as_of: str
    exchange: str
    series: str
    session: Session
    membership: str
    limitations: Tuple[str, ...]
    constituents: Tuple[Dict[str, str], ...]
    source: Dict[str, str]
    #: What to measure the exchange's open days from.
    calendar_reference: Optional[Dict[str, str]]
    fingerprint: str

    @property
    def symbols(self) -> List[str]:
        return [c["symbol"] for c in self.constituents]

    def trading_symbol(self, symbol: str) -> str:
        return "{}-{}".format(symbol, self.series)

    @property
    def survivorship_free(self) -> bool:
        """Whether a result on this universe's past is free of survivorship
        bias. Only a universe that carries its own membership history is."""
        return self.membership == "historical"

    def describe(self) -> Dict[str, Any]:
        """What a snapshot or an experiment records about its universe."""
        return {"id": self.id, "version": self.version, "as_of": self.as_of,
                "members": len(self.constituents), "membership": self.membership,
                "survivorship_free": self.survivorship_free,
                "fingerprint": self.fingerprint, "source": self.source.get("url")}


def load_universe(name: str = "nifty50", folder: Optional[Path] = None) -> Universe:
    path = (folder or UNIVERSE_DIR) / (name + ".json")
    try:
        raw = path.read_bytes()
        doc = json.loads(raw)
        session = exchange_calendar.by_name(doc["session"])
        if session is None:
            raise IndiaDataError("Universe {} names a session nobody has defined: {}.".format(
                name, doc["session"]))
        symbols = [c["symbol"] for c in doc["constituents"]]
        if len(set(symbols)) != len(symbols) or not symbols:
            raise IndiaDataError("Universe {} lists a symbol twice, or none.".format(name))
        return Universe(
            id=doc["id"], version=doc["version"], as_of=doc["as_of"],
            exchange=doc["exchange"], series=doc["series"], session=session,
            membership=doc["membership"], limitations=tuple(doc["limitations"]),
            constituents=tuple(doc["constituents"]), source=dict(doc["source"]),
            calendar_reference=doc.get("calendar_reference"),
            fingerprint=hashlib.sha256(raw).hexdigest()[:16])
    except OSError as exc:
        raise IndiaDataError("No universe called {}.".format(name)) from exc
    except (KeyError, ValueError, TypeError) as exc:
        raise IndiaDataError("Universe {} cannot be read: {}".format(name, exc)) from exc


def explained_moves(symbol: str, path: Optional[Path] = None) -> List[str]:
    """Dates of overnight moves in this stock that somebody has accounted
    for — a real crash, a demerger — each with its reason on file."""
    try:
        doc = json.loads((path or EXPLAINED_MOVES).read_text())
    except (OSError, ValueError):
        return []
    return [entry["date"] for entry in doc.get(symbol.upper(), []) if entry.get("date")]


# ── the calendar ──────────────────────────────────────────────

def calendar_sanity(calendar: TradingCalendar) -> List[str]:
    """Whole calendar years in which the exchange appears to have opened far
    too little or too much."""
    by_year: Dict[int, int] = {}
    for day in calendar.days:
        year = int(Session.iso(day)[:4])
        by_year[year] = by_year.get(year, 0) + 1
    first_year, last_year = int(Session.iso(calendar.first)[:4]), int(Session.iso(calendar.last)[:4])
    low, high = TRADING_DAYS_PER_YEAR
    return ["{} has {} trading days in the calendar (a year has {} to {}).".format(
        year, count, low, high)
        for year, count in sorted(by_year.items())
        if first_year < year < last_year and not low <= count <= high]


async def reference_calendar(source: Any, universe: Universe, start: datetime,
                             end: datetime) -> Optional[TradingCalendar]:
    """The exchange's open days, measured from the reference series the
    universe names — the index's own daily bars. None when it cannot be had
    whole: half a calendar would call the other half's days missing."""
    reference = universe.calendar_reference
    if not reference:
        return None
    scrip = await source.scrip(reference["exchange"], reference["trading_symbol"])
    if scrip is None:
        return None
    rows, report = await source.candles(scrip, "1d", start, end)
    if not rows or not report.complete:
        return None
    return exchange_calendar.calendar_from_times(
        universe.session, (row[0] for row in rows),
        "daily bars of {} {} (token {})".format(scrip.exchange, scrip.trading_symbol, scrip.token))


# ── taking a snapshot ─────────────────────────────────────────

@dataclass
class Entry:
    symbol: str
    status: str                       # "accepted" | "rejected" | "unavailable"
    dataset_id: Optional[str] = None
    reasons: List[str] = field(default_factory=list)
    summary: Dict[str, Any] = field(default_factory=dict)


@dataclass
class SnapshotReport:
    universe: Dict[str, Any]
    timeframe: str
    requested_from: str
    requested_to: str
    calendar: Optional[Dict[str, Any]]
    calendar_warnings: List[str]
    entries: List[Entry]
    limitations: List[str]

    @property
    def accepted(self) -> List[Entry]:
        return [e for e in self.entries if e.status == "accepted"]

    def to_dict(self) -> Dict[str, Any]:
        return {"universe": self.universe, "timeframe": self.timeframe,
                "requested_from": self.requested_from, "requested_to": self.requested_to,
                "calendar": self.calendar, "calendar_warnings": self.calendar_warnings,
                "accepted": len(self.accepted), "members": len(self.entries),
                "entries": [vars(e) for e in self.entries], "limitations": self.limitations}


def accept(dataset: datasets.Dataset) -> List[str]:
    """Why this snapshot may not be tested on — nothing, if it may. The one
    gate between a history and a study of an exchange-traded market."""
    reasons: List[str] = []
    quality = dataset.meta.get("quality") or {}
    if not dataset.real:
        reasons.append("It is not from a venue ({}).".format(dataset.provider))
    if dataset.session is None:
        reasons.append("It was not held to an exchange session, so what it is missing "
                       "was never measured.")
    elif not quality.get("calendar"):
        reasons.append("Its trading days were not checked against a calendar.")
    if not dataset.provenance:
        reasons.append("It carries no record of the requests it came from.")
    elif not dataset.complete:
        reasons.append("Its history is incomplete: {}".format(
            dataset.provenance.get("incomplete_reason") or "not every request succeeded."))
    if not dataset.quality_passed:
        reasons.extend(problem for problem in (quality.get("problems")
                                               or ["It failed its quality check."])
                       if not problem.startswith("The history is incomplete"))
    return list(dict.fromkeys(reasons))


def _summary(dataset: datasets.Dataset) -> Dict[str, Any]:
    quality = dataset.meta.get("quality") or {}
    session = dataset.session
    day = (lambda ts: Session.iso(int(session.local_day([ts])[0]))) if session else str
    return {
        "bars": dataset.bars,
        "first": day(dataset.first) if dataset.bars else None,
        "last": day(dataset.last) if dataset.bars else None,
        "traded_days": quality.get("traded_days"),
        "missing_days": quality.get("missing_days"),
        "short_days": quality.get("short_days"),
        "missing_pct": quality.get("missing_pct"),
        "zero_volume_pct": quality.get("zero_volume_pct"),
        "out_of_session_dropped": quality.get("out_of_session_dropped"),
        "split_like_moves": quality.get("split_like_moves"),
        "token": dataset.provenance.get("token"),
        "requests": len(dataset.provenance.get("windows") or []),
        "sha256": dataset.meta.get("sha256"),
    }


async def snapshot(source: Any, universe: Universe, timeframe: str, start: datetime,
                   end: datetime, *, symbols: Optional[Sequence[str]] = None,
                   now: Optional[float] = None, save: bool = True,
                   explained: Optional[Callable[[str], Iterable[str]]] = None,
                   progress: Optional[Callable[[str], None]] = None) -> SnapshotReport:
    """Take a history of every member (or of `symbols`), check each against
    the session and the calendar, and store what results — passed or not."""
    explained = explained or explained_moves
    say = progress or (lambda _line: None)
    wanted = [s.upper() for s in symbols] if symbols else universe.symbols
    unknown = sorted(set(wanted) - set(universe.symbols))
    if unknown:
        raise IndiaDataError("Not in universe {} {}: {}.".format(
            universe.id, universe.version, ", ".join(unknown)))
    now = time.time() if now is None else now
    # Whole exchange days, for the index and every member alike: the venue
    # stamps a daily bar at local midnight, and a start part-way through a
    # day would give that day to the hourly series and not to the calendar.
    offset = timezone(timedelta(seconds=universe.session.utc_offset))
    start = start.astimezone(offset).replace(hour=0, minute=0, second=0, microsecond=0)

    calendar = await reference_calendar(source, universe, start, end)
    say("calendar: {}".format(calendar.source if calendar else
                              "reference unavailable — falling back to the universe's own agreement"))

    fetched: Dict[str, Tuple[List[List[float]], Any]] = {}
    entries: Dict[str, Entry] = {}
    for symbol in wanted:
        scrip = await source.scrip(universe.exchange, universe.trading_symbol(symbol))
        if scrip is None:
            entries[symbol] = Entry(symbol, "unavailable", reasons=[
                "{} is not in the broker's scrip master.".format(
                    universe.trading_symbol(symbol))])
            say("{:<12} not in the scrip master".format(symbol))
            continue
        rows, report = await source.candles(scrip, timeframe, start, end)
        fetched[symbol] = (rows, report)
        say("{:<12} {:>7} rows in {:>3} requests{}".format(
            symbol, len(rows), len(report.windows),
            "" if report.complete else "  INCOMPLETE: " + str(report.incomplete_reason)))

    if calendar is None and fetched:
        try:
            calendar = exchange_calendar.calendar_from_universe(
                universe.session, [[row[0] for row in rows] for rows, _ in fetched.values()],
                "agreement of {} members of {} {}".format(
                    len(fetched), universe.id, universe.version))
        except ValueError:
            calendar = None       # too few series to agree: every snapshot fails its check

    for symbol, (rows, report) in fetched.items():
        provenance = {**report.to_dict(), "universe": universe.describe()}
        dataset = datasets.build(
            report.broker, symbol, timeframe, rows, now=now, session=universe.session,
            calendar=calendar, explained_moves=explained(symbol), provenance=provenance)
        if save and dataset.bars:
            datasets.save(dataset)
        reasons = accept(dataset)
        entries[symbol] = Entry(symbol, "rejected" if reasons else "accepted",
                                dataset_id=dataset.id if dataset.bars else None,
                                reasons=reasons, summary=_summary(dataset))

    return SnapshotReport(
        universe=universe.describe(), timeframe=timeframe.lower(),
        requested_from=start.isoformat(timespec="minutes"),
        requested_to=end.isoformat(timespec="minutes"),
        calendar=calendar.describe() if calendar else None,
        calendar_warnings=calendar_sanity(calendar) if calendar else
        ["No calendar could be measured."],
        entries=[entries[s] for s in wanted], limitations=list(universe.limitations))


# ── what a study may load ─────────────────────────────────────

def latest_snapshots(universe: Universe, timeframe: str) -> Dict[str, datasets.Dataset]:
    """The most recent stored snapshot of each member taken from this version
    of the universe — accepted or not; `accept` says which."""
    out: Dict[str, datasets.Dataset] = {}
    members = set(universe.symbols)
    for meta in datasets.catalogue():            # newest first
        symbol = meta.get("symbol")
        taken_from = ((meta.get("provenance") or {}).get("universe") or {})
        if (symbol in members and symbol not in out
                and meta.get("timeframe") == timeframe.lower()
                and taken_from.get("fingerprint") == universe.fingerprint):
            out[symbol] = datasets.load(meta["sha256"][:16])
    return out


async def token_changes(source: Any, universe: Universe,
                        snapshots: Dict[str, datasets.Dataset]) -> Dict[str, str]:
    """Members whose snapshot was taken under a token the broker no longer
    lists them by. The symbol is the same word; it may not be the same
    security, and the snapshot is not evidence about the one trading now."""
    changed: Dict[str, str] = {}
    for symbol, dataset in snapshots.items():
        scrip = await source.scrip(universe.exchange, universe.trading_symbol(symbol))
        recorded = str(dataset.provenance.get("token") or "")
        if scrip is None:
            changed[symbol] = "no longer in the scrip master (snapshot token {})".format(recorded)
        elif scrip.token != recorded:
            changed[symbol] = "snapshot token {}, broker now lists {}".format(
                recorded, scrip.token)
    return changed


# ── command line ──────────────────────────────────────────────

def _print_report(report: SnapshotReport) -> None:
    print("\nuniverse {id} {version} ({members} members, membership: {membership})".format(
        **report.universe))
    print("timeframe {}   requested {} → {}".format(
        report.timeframe, report.requested_from[:10], report.requested_to[:10]))
    if report.calendar:
        print("calendar: {days} trading days, {first} → {last}\n  from {source}".format(
            **report.calendar))
    for line in report.calendar_warnings:
        print("  ! " + line)
    print("\n{:<12} {:<11} {:>7} {:>11} {:>11} {:>6} {:>6} {:>7}  {}".format(
        "symbol", "status", "bars", "first", "last", "msDay", "short", "miss%", "notes"))
    for entry in report.entries:
        s = entry.summary
        moves = s.get("split_like_moves") or []
        note = "; ".join(entry.reasons)[:110] if entry.reasons else (
            "{} large overnight move(s), explained".format(len(moves)) if moves else "")
        print("{:<12} {:<11} {:>7} {:>11} {:>11} {:>6} {:>6} {:>7}  {}".format(
            entry.symbol, entry.status, s.get("bars") or 0, s.get("first") or "-",
            s.get("last") or "-", s.get("missing_days") if s else "-",
            s.get("short_days") if s else "-", s.get("missing_pct") if s else "-", note))
    print("\n{} of {} accepted.".format(len(report.accepted), len(report.entries)))


async def _run_snapshot(args: argparse.Namespace) -> int:
    from ..config import DATA_DIR
    from ..data.angel_history import AngelHistorySource

    universe = load_universe(args.universe)
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=int(round(args.years * 365.25)))
    report = await snapshot(AngelHistorySource(pause=args.pause), universe, args.timeframe,
                            start, end, symbols=args.symbols or None, progress=print)
    _print_report(report)
    folder = DATA_DIR / "research" / "snapshots"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "{}-{}-{}-{}.json".format(
        universe.id, universe.version, report.timeframe,
        datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
    path.write_text(json.dumps(report.to_dict(), indent=2, sort_keys=True))
    print("report: {}".format(path))
    return 0 if report.accepted else 1


def _run_accepted(args: argparse.Namespace) -> int:
    universe = load_universe(args.universe)
    snapshots = latest_snapshots(universe, args.timeframe)
    usable = 0
    for symbol in universe.symbols:
        dataset = snapshots.get(symbol)
        if dataset is None:
            print("{:<12} no snapshot".format(symbol))
            continue
        reasons = accept(dataset)
        usable += not reasons
        print("{:<12} {:<9} {}  {}".format(
            symbol, "accepted" if not reasons else "rejected", dataset.id,
            "; ".join(reasons)[:120]))
    print("\n{} of {} members have a snapshot a study may use.".format(
        usable, len(universe.symbols)))
    return 0


def _run_universe(args: argparse.Namespace) -> int:
    universe = load_universe(args.universe)
    print(json.dumps(universe.describe(), indent=2))
    print("\n" + " ".join(universe.symbols))
    print("\nLimitations:")
    for line in universe.limitations:
        print("  - " + line)
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.research.india", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--universe", default="nifty50")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("universe", help="describe the universe and its limitations")
    take = commands.add_parser("snapshot", help="take and check a history of each member")
    take.add_argument("timeframe")
    take.add_argument("--years", type=float, default=8.0)
    take.add_argument("--symbols", nargs="*")
    take.add_argument("--pause", type=float, default=0.4,
                      help="seconds between requests; ask slowly while the market is open, "
                           "the limit is shared with the running application")
    listed = commands.add_parser("accepted", help="which stored snapshots a study may use")
    listed.add_argument("timeframe")
    args = parser.parse_args(argv)
    try:
        if args.command == "snapshot":
            return asyncio.run(_run_snapshot(args))
        if args.command == "accepted":
            return _run_accepted(args)
        return _run_universe(args)
    except IndiaDataError as exc:
        print("error: {}".format(exc))
        return 2
    except RuntimeError as exc:
        if type(exc).__name__ != "HistoryUnavailable":
            raise
        print("error: {}".format(exc))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
