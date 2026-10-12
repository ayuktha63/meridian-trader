"""Research from the command line. Nothing here can place an order.

    python -m app.research.run snapshot BTCUSD ETHUSD        take history snapshots
    python -m app.research.run datasets                      list what is in the store
    python -m app.research.run study                         run the candidate study
    python -m app.research.run diagnose                      look closer at its research stretch
    python -m app.research.run engine BTCUSD 1h --bars 4000  walk-forward the live engine
    python -m app.research.run register donchian_trend 4h crypto_perp --reason "..."
                                                             freeze a candidate for forward watching
    python -m app.research.run forward [--refresh]           trade what is registered on bars since
    python -m app.research.run experiments                   list what is on record

Every run that measures something writes an experiment record: to the
database, and to `<data dir>/research/experiments/<id>.json`.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from typing import Any, Dict, List, Optional

from ..db.session import init_db, session_scope
from . import datasets, forward, registry, study
from .engine_evidence import walk_forward_on_snapshot


def _day(ts: float) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(ts))


async def _snapshot(symbols: List[str], timeframe: str) -> int:
    failed = 0
    for symbol in symbols:
        try:
            ds = await datasets.fetch(symbol, timeframe)
        except datasets.DatasetError as exc:
            print("{:9s} FAILED  {}".format(symbol.upper(), exc))
            failed += 1
            continue
        quality = ds.meta["quality"]
        print("{:9s} {}  {} bars  {} .. {}  missing {:.3f}%  largest gap {}  {}".format(
            ds.symbol, ds.id, ds.bars, _day(ds.first), _day(ds.last),
            quality["missing_pct"], quality["largest_gap_bars"],
            "ok" if quality["passed"] else "REFUSED: " + "; ".join(quality["problems"])))
    return failed


def _datasets() -> None:
    for meta in datasets.catalogue():
        quality = meta.get("quality") or {}
        print("{}  {:8s} {:3s} {:9s} {:6d} bars  {} .. {}  {}{}".format(
            meta["sha256"][:16], meta["symbol"], meta["timeframe"], meta["provider"],
            meta["bars"], _day(meta["first"]), _day(meta["last"]),
            "ok" if quality.get("passed") else "REFUSED",
            "  (from {})".format(meta["derived_from"]) if meta.get("derived_from") else ""))


async def _study(profiles: Optional[List[str]], timeframes: Optional[List[str]]) -> Dict[str, Any]:
    markets, excluded = study.load_universe()
    if not markets:
        raise SystemExit("No usable snapshots. Take some first: "
                         "python -m app.research.run snapshot BTCUSD ETHUSD ...")
    print("universe: {}".format(", ".join(m.symbol for m in markets)))
    for row in excluded:
        print("left out: {} — {}".format(row["symbol"], row["reason"]))
    started = time.time()
    result = study.execute(
        markets, excluded, profiles=tuple(profiles or study.PROFILES),
        timeframes=tuple(timeframes or study.TIMEFRAMES),
        progress=lambda message: print("  " + message, flush=True))

    families = result.pop("families")
    counts: Dict[str, int] = {}
    for family in families:
        counts[family.state] = counts.get(family.state, 0) + 1
    record = registry.build(
        kind="study", stage="research", strategy="candidates", symbol="UNIVERSE",
        timeframe=",".join(timeframes or study.TIMEFRAMES), venue=markets[0].data["1h"].provider,
        cost_profile=",".join(profiles or study.PROFILES),
        verdict=(study.CONFIRMED if counts.get(study.CONFIRMED) else study.REJECTED),
        datasets=result["universe"],
        window={"validation_start": study.VALIDATION_START,
                "holdout_start": study.HOLDOUT_START},
        parameters={"grids": {s.name: list(s.grid) for s in study.strategies.CANDIDATES}},
        assumptions={"costs": result["profiles"],
                     "execution": result["execution_assumptions"]},
        metrics={"families": len(families), "by_state": counts,
                 "seconds": round(time.time() - started, 1)},
        reasons=["{} of {} families survived every stage.".format(
            counts.get(study.CONFIRMED, 0), len(families))],
        trials=result["trials"], seeds={"bootstrap": 7, "random_entries": 7},
        extra={"protocol": {"version": result["protocol_version"],
                            "thresholds": result["thresholds"]},
               "excluded": result["excluded"], "audits": result["audits"],
               "benchmarks": result["benchmarks"],
               "holdout_opened_for": result["holdout_opened_for"],
               "families": [study.family_dict(f) for f in families]})
    result["families"] = families
    record["artifacts"] = {"families": study.write_artifacts(record["id"], result)}
    await init_db()
    async with session_scope() as session:
        record = await registry.save(session, record)
    print("\n{} configurations on {} markets in {:.0f}s — experiment {}".format(
        result["trials"], len(markets), time.time() - started, record["id"]))
    for family in sorted(families, key=lambda f: (f.profile, f.timeframe, f.strategy)):
        best = family.stages.get("research", {}).get("pooled", {})
        print("  {:42s} {:22s} {}".format(
            family.key, family.state,
            "" if not best else "research {:+.3f}R on {} trades".format(
                best.get("expectancy_r", 0.0), best.get("trades", 0))))
    return record


async def _diagnose() -> Dict[str, Any]:
    markets, _ = study.load_universe()
    if not markets:
        raise SystemExit("No usable snapshots.")
    started = time.time()
    result = study.diagnose(markets, say=lambda message: print("  " + message, flush=True))
    record = registry.build(
        kind="diagnostic", stage="research", strategy="candidates", symbol="UNIVERSE",
        timeframe=",".join(study.TIMEFRAMES), venue=markets[0].data["1h"].provider,
        cost_profile=",".join(study.PROFILES), verdict="NOT A VERDICT",
        datasets=[m.data["1h"].describe() for m in markets],
        window={"until": study.VALIDATION_START}, parameters={},
        assumptions={"execution": list(study.EXECUTION_ASSUMPTIONS)},
        metrics=result,
        reasons=["A closer look at the research stretch. It changes no verdict and "
                 "opens no later stretch."],
        trials=0, seeds={"bootstrap": 7, "random_entries": 7})
    await init_db()
    async with session_scope() as session:
        record = await registry.save(session, record)
    print("{} configurations behave like {:.1f} independent ones ({:.0f}s) — {}".format(
        result["trials"], result["effective_trials"], time.time() - started, record["id"]))
    return record


async def _register(strategy: str, timeframe: str, profile: str, reason: str) -> None:
    """Freeze the configuration the last study chose for this family."""
    markets, _ = study.load_universe()
    await init_db()
    async with session_scope() as session:
        studies = await registry.recent(session, 1, kind="study")
        key = "{}/{}/{}".format(strategy, timeframe, profile)
        family = next((f for f in (studies[0].get("families") if studies else [])
                       if f["key"] == key), None)
        if family is None or not family.get("chosen"):
            raise SystemExit("No study on record chose a configuration for " + key)
        record = await forward.register(session, markets, strategy, family["chosen"],
                                        timeframe, profile, reason)
    print("registered {} {} from {} — {}".format(
        key, family["chosen"], _day(record["window"]["from"]), record["id"]))


async def _forward(refresh: bool) -> None:
    await init_db()
    async with session_scope() as session:
        watching = await forward.registrations(session)
    if not watching:
        print("Nothing is registered for forward watching.")
        return
    if refresh:
        symbols = sorted({s for r in watching for s in r["metrics"]["symbols"]})
        await _snapshot(symbols, "1h")
    markets, _ = study.load_universe()
    async with session_scope() as session:
        for registration in watching:
            result = await forward.evaluate(session, registration, markets)
            pooled = result["metrics"]["pooled"]
            print("{}/{}/{} since {}: {} trades{} — {}".format(
                registration["strategy"]["name"], registration["timeframe"],
                registration["cost_profile"], _day(registration["window"]["from"]),
                pooled["trades"],
                "" if not pooled["trades"] else ", {:+.3f}R".format(pooled["expectancy_r"]),
                result["verdict"]["state"]))


async def _engine(symbol: str, timeframe: str, bars: int, profile: Optional[str],
                  strategy: str) -> Dict[str, Any]:
    await init_db()
    async with session_scope() as session:
        record = await walk_forward_on_snapshot(
            session, symbol, timeframe, bars=bars, cost_profile=profile, strategy=strategy)
    pooled = record["metrics"]
    print("{} {} {} [{}] — {} trades, {:+.3f}R, interval {:+.3f}R..{:+.3f}R — {}".format(
        record["symbol"], record["timeframe"], strategy, record["cost_profile"],
        pooled["trades"], pooled["expectancy_r"], pooled["ci_low"], pooled["ci_high"],
        record["verdict"]["state"]))
    for reason in record["verdict"]["reasons"]:
        print("   " + reason)
    print("   experiment " + record["id"])
    return record


async def _experiments(limit: int) -> None:
    await init_db()
    async with session_scope() as session:
        for record in await registry.recent(session, limit):
            print("{}  {}  {:20s} {:9s} {:4s} {:12s} trials={:<4d} {}{}".format(
                record["id"], _day(record["created_at"]), record["kind"], record["symbol"],
                record["timeframe"][:4], record["cost_profile"][:12], record["trials"],
                record["verdict"]["state"],
                "" if registry.intact(record) else "  [RECORD ALTERED]"))


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.research.run", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    snapshot = commands.add_parser("snapshot", help="take history snapshots from the venue")
    snapshot.add_argument("symbols", nargs="+")
    snapshot.add_argument("--timeframe", default="1h")
    commands.add_parser("datasets", help="list the snapshots in the store")
    run_study = commands.add_parser("study", help="run the candidate study")
    run_study.add_argument("--profile", action="append", choices=study.PROFILES)
    run_study.add_argument("--timeframe", action="append", choices=study.TIMEFRAMES)
    engine = commands.add_parser("engine", help="walk-forward the live engine on a snapshot")
    engine.add_argument("symbol")
    engine.add_argument("timeframe", nargs="?", default="1h")
    engine.add_argument("--bars", type=int, default=4000)
    engine.add_argument("--profile", default=None)
    engine.add_argument("--strategy", default="engine")
    commands.add_parser("diagnose", help="a closer look at the research stretch only")
    register = commands.add_parser("register", help="freeze a candidate for forward watching")
    register.add_argument("strategy", choices=sorted(study.strategies.BY_NAME))
    register.add_argument("timeframe", choices=study.TIMEFRAMES)
    register.add_argument("profile", choices=study.PROFILES)
    register.add_argument("--reason", required=True)
    watch = commands.add_parser("forward", help="trade registered candidates on bars since")
    watch.add_argument("--refresh", action="store_true", help="take fresh snapshots first")
    listing = commands.add_parser("experiments", help="list experiments on record")
    listing.add_argument("--limit", type=int, default=30)
    show = commands.add_parser("show", help="print one experiment record")
    show.add_argument("experiment_id")
    args = parser.parse_args(argv)

    if args.command == "snapshot":
        return asyncio.run(_snapshot(args.symbols, args.timeframe))
    if args.command == "datasets":
        _datasets()
    elif args.command == "study":
        asyncio.run(_study(args.profile, args.timeframe))
    elif args.command == "diagnose":
        asyncio.run(_diagnose())
    elif args.command == "register":
        asyncio.run(_register(args.strategy, args.timeframe, args.profile, args.reason))
    elif args.command == "forward":
        asyncio.run(_forward(args.refresh))
    elif args.command == "engine":
        asyncio.run(_engine(args.symbol.upper(), args.timeframe, args.bars, args.profile,
                            args.strategy))
    elif args.command == "experiments":
        asyncio.run(_experiments(args.limit))
    elif args.command == "show":
        path = registry.root() / (args.experiment_id + ".json")
        print(json.dumps(json.loads(path.read_text()), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
