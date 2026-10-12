"""The study protocol on markets whose answer is known: noise must get
nowhere, a planted edge must get all the way, and no stage may see past
its own end."""
from __future__ import annotations

from dataclasses import replace

import numpy as np
import pytest

from app.instruments import get_instrument
from app.research import datasets, strategies, study
from app.research.simulator import reference
from app.research.strategies import DONCHIAN, REVERSION, Plan

FOUR_HOURS = 4 * 3600
START = 1_609_459_200          # 2021-01-01 00:00 UTC
BARS = 13_600                  # to early 2027: a holdout long enough to judge


def rows(seed, trending):
    """A market with no edge in it, or one whose drift holds for weeks at a
    time — the thing a trend-follower is supposed to find."""
    rng = np.random.default_rng(seed)
    drift = np.zeros(BARS)
    if trending:
        sign, i = 1.0, 0
        while i < BARS:
            length = int(rng.integers(150, 400))
            drift[i:i + length] = sign * 0.0035
            sign, i = -sign, i + length
    close = 100.0 * np.exp(np.cumsum(drift + rng.normal(0.0, 0.009, BARS)))
    opens = np.concatenate([[100.0], close[:-1]])
    wick = np.abs(rng.normal(0.0, 0.004, BARS)) * close
    return [[START + i * FOUR_HOURS, opens[i], max(opens[i], close[i]) + wick[i],
             min(opens[i], close[i]) - wick[i], close[i], 50.0] for i in range(BARS)]


def markets(trending):
    return [study.Market(symbol, get_instrument(symbol),
                         {"4h": datasets.build("coindcx", symbol, "4h", rows(seed, trending))})
            for seed, symbol in enumerate(("BTCUSD", "ETHUSD", "SOLUSD"), start=31)]


@pytest.fixture
def small(monkeypatch):
    """Two candidates with three settings each, so a run takes a second."""
    candidates = (
        replace(DONCHIAN, grid=({"n": 20.0, "k": 3.0}, {"n": 55.0, "k": 3.0},
                                {"n": 20.0, "k": 2.0})),
        replace(REVERSION, grid=({"mult": 2.0, "k": 1.5, "adx_max": 25.0},
                                 {"mult": 2.5, "k": 1.5, "adx_max": 25.0},
                                 {"mult": 2.0, "k": 2.5, "adx_max": 25.0})))
    monkeypatch.setattr(strategies, "CANDIDATES", candidates)
    monkeypatch.setitem(study.THRESHOLDS["research"], "random_draws", 40)
    return candidates


def execute(universe):
    return study.execute(universe, [], profiles=("crypto_perp",), timeframes=("4h",))


def test_noise_gets_nowhere_and_the_holdout_stays_shut(small):
    result = execute(markets(trending=False))
    assert result["trials"] == 6
    for family in result["families"]:
        assert family.state == study.REJECTED, family.key
        assert family.reasons
        assert set(family.stages) <= {"research"}, "it never saw the later stretches"
    assert result["holdout_opened_for"] == []
    assert all(report["passed"] for report in result["audits"].values())


def test_a_planted_edge_is_carried_through_every_stage_with_its_setting_frozen(small):
    result = execute(markets(trending=True))
    by_name = {family.strategy: family for family in result["families"]}
    trend, fade = by_name["donchian_trend"], by_name["range_reversion"]

    assert trend.state == study.CONFIRMED, trend.reasons
    assert list(trend.stages) == ["research", "validation", "holdout"]
    assert {stage["parameters"]["n"] for stage in trend.stages.values()} == {trend.chosen["n"]}
    research = trend.stages["research"]
    assert research["interval"]["low"] > 0
    assert research["deflated_sharpe"] >= 0.95
    assert research["random_entries"]["p_value"] <= 0.05
    assert research["random_entries"]["mean_r"] < research["pooled"]["expectancy_r"]
    assert research["break_even_cost_multiple"] > 1.5
    assert trend.stages["holdout"]["markets_for_paper"]
    assert result["holdout_opened_for"] == [trend.key]

    # Fading a market that trends: its trend filter keeps it out, or it loses.
    assert fade.state in (study.REJECTED, study.INSUFFICIENT)
    assert "holdout" not in fade.stages and "validation" not in fade.stages


def test_a_stage_is_computed_on_nothing_later_than_its_own_end(monkeypatch):
    seen = {}

    def spy(df, params):
        seen.setdefault("latest", []).append(float(df["time"].iloc[-1]))
        return Plan()

    watcher = replace(DONCHIAN, name="spy", grid=({"n": 20.0, "k": 3.0},), build=spy)
    universe = markets(trending=False)[:1]
    for stage, end in (("research", study.VALIDATION_START),
                       ("validation", study.HOLDOUT_START)):
        seen.clear()
        study.run(universe, watcher, watcher.grid[0], "4h", "crypto_perp", stage)
        assert max(seen["latest"]) < end, stage


def test_stages_do_not_overlap_and_cover_the_history():
    market = markets(trending=False)[0]
    spans = [market.span("4h", stage) for stage in study.STAGES]
    assert spans[0][0] == 0 and spans[-1][1] == BARS
    assert spans[0][1] == spans[1][0] and spans[1][1] == spans[2][0]
    times = market.frame("4h")["time"]
    assert times[spans[1][0]] == study.VALIDATION_START
    assert times[spans[2][0]] == study.HOLDOUT_START


def test_random_entries_keep_the_exits_and_lose_the_skill(small):
    universe = markets(trending=True)
    strategy, params = small[0], small[0].grid[0]
    outcome = study.run(universe, strategy, params, "4h", "crypto_perp", "research")
    market = universe[0]
    trades = outcome.by_market[market.symbol]
    plan = market.plan(strategy, params, "4h", "research")
    bars = market.bars("4h")
    null = study.matched_random(market, "4h", "research", plan, trades, True,
                                np.random.default_rng(3))

    assert 0.9 * len(trades) <= len(null) <= len(trades)
    first, last = market.span("4h", "research")
    assert all(first <= i < last for i in null)
    stop_atrs = sorted(round(abs(reference(s, bars.close[i]) - s.stop) / bars.atr[i], 6)
                       for i, s in null.items())
    assert set(stop_atrs) == {3.0}, "the strategy's own stop distance, in ATRs"
    assert {s.trail_atr for s in null.values()} == {3.0}
    assert sorted(s.max_hold for s in null.values())[-1] == max(t.bars_held for t in trades)
    assert {s.direction for s in null.values()} == {1, -1}
    long_only = study.matched_random(market, "4h", "research", plan, trades, False,
                                     np.random.default_rng(3))
    assert {s.direction for s in long_only.values()} == {1}

    draws = study.random_baseline(universe, strategy, outcome, draws=20)
    assert draws.mean() < float(outcome.r.mean()), "without the timing, the edge is gone"


def test_markets_are_left_out_of_the_universe_for_a_stated_reason():
    hour, deep = 3600, 1_606_780_800                       # 2020-12-01
    count = (1_780_000_000 - deep) // hour

    def hourly(start, n, seed):
        rng = np.random.default_rng(seed)
        close = 50.0 * np.exp(np.cumsum(rng.normal(0, 0.004, n)))
        opens = np.concatenate([[50.0], close[:-1]])
        return [[start + i * hour, opens[i], max(opens[i], close[i]) * 1.001,
                 min(opens[i], close[i]) * 0.999, close[i], 5.0] for i in range(n)]

    good = datasets.build("coindcx", "LINKUSD", "1h", hourly(deep, count, 1))
    holed = hourly(deep, count, 2)
    del holed[20_000:22_000]
    broken = datasets.build("coindcx", "XRPUSD", "1h", holed)
    recent = datasets.build("coindcx", "AVAXUSD", "1h", hourly(1_775_001_600, 1200, 3))
    for dataset in (good, broken, recent):
        datasets.save(dataset)

    included, excluded = study.load_universe()
    reasons = {row["symbol"]: row["reason"] for row in excluded}
    assert "LINKUSD" in [m.symbol for m in included]
    assert "missing" in reasons["XRPUSD"]
    assert "no research stretch" in reasons["AVAXUSD"]
    link = next(m for m in included if m.symbol == "LINKUSD")
    assert set(link.data) == {"1h", "4h", "1d"}
    assert link.data["4h"].meta["derived_from"] == good.id
