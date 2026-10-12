"""Watching a frozen strategy on bars that did not exist when it was frozen.

A historical test, however carefully cut, is run by someone who has seen the
history. The only data nobody has seen is the data that has not happened
yet. A *registration* freezes a strategy's parameters at a moment in time;
a *forward evaluation* trades it, in simulation, on the bars that came
after — and only those.

This is what a strategy that narrowly failed a historical test gets instead
of a second look at the same history: it costs nothing of the unopened
holdout, it cannot be tuned, and it takes as long as it takes.

Nothing here trades. A forward result is evidence for the eligibility of a
research strategy; it reaches no further than PAPER VALIDATION.
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional, Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from .. import costs
from ..instruments import get_timeframe
from . import registry, stats, strategies, study
from .study import Market

#: Forward trades before an interval is worth quoting.
MIN_FORWARD_TRADES = 30
WATCHING, TOO_FEW = "WATCHING", "INSUFFICIENT EVIDENCE"
AGREES, DISAGREES = "FORWARD EVIDENCE AGREES", "FORWARD EVIDENCE DISAGREES"


def first_unseen_bar(timeframe: str, now: Optional[float] = None) -> float:
    """The open time of the first bar that had not begun at `now`."""
    seconds = get_timeframe(timeframe).seconds
    return float((int(now or time.time()) // seconds + 1) * seconds)


async def register(session: AsyncSession, markets: Sequence[Market], strategy_name: str,
                   parameters: Dict[str, float], timeframe: str, profile: str,
                   reason: str, now: Optional[float] = None) -> Dict[str, Any]:
    """Freeze a strategy for forward watching, from the next bar on."""
    strategy = strategies.BY_NAME[strategy_name]
    costs.PROFILES[profile]                       # an unknown venue fails here
    now = time.time() if now is None else now
    record = registry.build(
        kind="registration", stage="forward", strategy=strategy.name, symbol="UNIVERSE",
        timeframe=timeframe, venue=markets[0].data[timeframe].provider,
        cost_profile=profile, verdict=WATCHING,
        datasets=[m.data[timeframe].describe() for m in markets],
        window={"from": first_unseen_bar(timeframe, now)},
        parameters=dict(parameters),
        assumptions={"execution": list(study.EXECUTION_ASSUMPTIONS),
                     "costs": costs.describe(markets[0].instrument, profile)},
        metrics={"symbols": [m.symbol for m in markets]}, reasons=[reason], trials=0,
        now=now)
    return await registry.save(session, record)


async def registrations(session: AsyncSession) -> List[Dict[str, Any]]:
    return await registry.recent(session, 200, kind="registration")


async def evaluate(session: AsyncSession, registration: Dict[str, Any],
                   markets: Sequence[Market], now: Optional[float] = None) -> Dict[str, Any]:
    """Trade a registered strategy on everything since it was frozen."""
    strategy = strategies.BY_NAME[registration["strategy"]["name"]]
    parameters = registration["strategy"]["parameters"]
    timeframe, profile = registration["timeframe"], registration["cost_profile"]
    start = float(registration["window"]["from"])
    watched = [m for m in markets if m.symbol in registration["metrics"]["symbols"]]
    outcome = study.run(watched, strategy, parameters, timeframe, profile,
                        (start, float("inf")), min_bars=1)
    summary = study.summarise(outcome, watched) if outcome.trades else {
        "pooled": {"trades": 0}, "by_market": {}}
    r = outcome.r
    metrics: Dict[str, Any] = {**summary, "registration": registration["id"],
                               "bars_since": {m.symbol: m.span(timeframe, (start, float("inf")))[1]
                                              - m.span(timeframe, (start, float("inf")))[0]
                                              for m in watched}}
    verdict, reasons = TOO_FEW, ["{} forward trades so far; {} are needed before an "
                                 "interval means anything.".format(len(r), MIN_FORWARD_TRADES)]
    if len(r) >= MIN_FORWARD_TRADES:
        estimate = stats.cluster_mean_interval(r, outcome.weeks())
        metrics["interval"] = {"low": round(estimate.low, 4), "high": round(estimate.high, 4),
                               "p_value": round(estimate.p_value, 5), "weeks": estimate.block}
        agrees = estimate.low > 0
        verdict = AGREES if agrees else DISAGREES
        reasons = ["Forward expectancy {:+.3f}R over {} trades (interval {:+.3f}R to "
                   "{:+.3f}R).".format(estimate.mean, len(r), estimate.low, estimate.high)]
    record = registry.build(
        kind="forward", stage="forward", strategy=strategy.name, symbol="UNIVERSE",
        timeframe=timeframe, venue=registration["venue"], cost_profile=profile,
        verdict=verdict, datasets=[m.data[timeframe].describe() for m in watched],
        window={"from": start, "until": time.time() if now is None else now},
        parameters=dict(parameters), assumptions=registration["assumptions"],
        metrics=metrics, reasons=reasons, trials=0, now=now)
    return await registry.save(session, record)
