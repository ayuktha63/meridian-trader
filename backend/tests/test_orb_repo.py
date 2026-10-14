"""The ORB of `arshadakl/nse-orb-backtest`: the rules do what the source says,
the executable fills are a market's, nothing looks ahead, and the gate cannot
be passed by default. Synthetic bars only; no broker, no network."""
from __future__ import annotations

import ast
from pathlib import Path
from typing import List, Optional, Tuple

import numpy as np
import pandas as pd
import pytest

from app.research import orb_repo as O
from app.research import orb_repo_study as S
from app.research.simulator import MARKET

IST = 19_800
DAY0 = 20_000                       # days since the epoch; 2024-10-04, a Friday


def day_open(day: int) -> float:
    return day * 86_400 - IST + 9 * 3600 + 15 * 60


def make_day(day: int, bars: List[Tuple[float, float, float, float, float]]) -> pd.DataFrame:
    """(open, high, low, close, volume) per 5-minute bar from 09:15."""
    t0 = day_open(day)
    return pd.DataFrame([[t0 + 300 * i, *b] for i, b in enumerate(bars)],
                        columns=["time", "open", "high", "low", "close", "volume"])


def flat(n: int, price: float = 100.0, vol: float = 1000.0):
    return [(price, price, price, price, vol)] * n


def day_with_breakout(*, rng_low=99.0, rng_high=101.0, breakout_at=10, bo_close=102.0,
                      bo_vol=3000.0, then=None, total=75):
    """Six range bars spanning rng_low..rng_high, quiet inside it until bar
    `breakout_at`, which closes at `bo_close`; `then` replaces the bars after it."""
    bars = [(100.0, rng_high, rng_low, 100.0, 1000.0)] * 6
    bars += [(100.0, 100.5, 99.5, 100.0, 1000.0)] * (breakout_at - 6)
    bars.append((101.0, bo_close + 0.1, 100.9, bo_close, bo_vol))
    rest = then if then is not None else flat(total - len(bars), bo_close)
    return bars + list(rest)


def index_day(day: int, *, range_high=100.0, after=105.0) -> pd.DataFrame:
    bars = [(99.0, range_high, 98.0, 99.0, 1.0)] * 6 + [(99.0, after, 98.5, after, 1.0)] * 69
    return make_day(day, bars)


# ── the oracle: the published rules, replayed row by row ──────

def oracle(frame: pd.DataFrame, rules: O.Rules, index_frame: Optional[pd.DataFrame] = None):
    """Written from the source's rule text, deliberately unlike the
    implementation: row iteration, one day at a time, the source's own
    ordering of checks and its fill rules."""
    out = []
    sod = (frame["time"] + IST) % 86_400
    day = ((frame["time"] + IST) // 86_400).astype(int)
    for d, g in frame.assign(sod=sod, day=day).groupby("day", sort=True):
        if O._WEEKDAYS[(int(d) + 3) % 7] in rules.skip_days:
            continue
        rng = g[g["sod"] < 9 * 3600 + 45 * 60]
        if len(rng) != 6:
            continue
        hi, lo, first = rng["high"].max(), rng["low"].min(), rng["open"].iloc[0]
        if (hi - lo) / first * 100 < 0.20:
            continue
        avg = rng["volume"].mean() or 1.0
        nh = None
        ng = None
        if rules.nifty_filter:
            ng = index_frame[((index_frame["time"] + IST) // 86_400).astype(int) == d]
            nr = ng[((ng["time"] + IST) % 86_400) < 9 * 3600 + 45 * 60]
            if nr.empty:
                continue
            nh = nr["high"].max()
        post = g[(g["sod"] >= 9 * 3600 + 45 * 60) & (g["sod"] < 14 * 3600 + 30 * 60)].reset_index()
        hit = None
        for i, row in post.iterrows():
            if row["close"] <= hi:
                continue
            if (row["close"] - hi) / hi * 100 < rules.min_bo_pct:
                continue
            if rules.vol_mult > 0 and row["volume"] / avg < rules.vol_mult:
                continue
            if rules.nifty_filter:
                seen = ng[ng["time"] <= row["time"]]
                if seen.empty or seen.iloc[-1]["close"] <= nh:
                    continue
            hit = i
            break
        if hit is None or hit + 1 >= len(post):
            continue
        entry_row = post.iloc[hit + 1]
        entry = entry_row["open"]
        dist = entry - lo
        if dist <= 0:
            continue
        target = entry + 1.5 * dist
        later = g[g["time"] > entry_row["time"]]
        res = None
        for _, row in later.iterrows():
            if row["low"] <= lo:
                res = ("stop", lo, row["time"]); break
            if row["high"] >= target:
                res = ("target", target, row["time"]); break
            if row["sod"] >= 15 * 3600 + 15 * 60:
                res = ("time", row["close"], row["time"]); break
        if res is None:
            last = later.iloc[-1] if len(later) else entry_row
            res = ("data_end", last["close"], last["time"])
        out.append((float(entry_row["time"]), float(entry), res[0], float(res[1]), float(res[2])))
    return out


def mine(frame, rules, index=None):
    setups = O.find_setups(frame, rules, index)
    trades = O.play_all(frame, setups, O.PUBLISHED)
    return [(t.entry_time, t.entry, t.reason, t.exit, t.exit_time) for t in trades]


def random_history(seed: int, days: int = 40) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    frames = []
    price = 100.0
    for d in range(days):
        gap = rng.normal(0, 0.006)
        price *= 1 + gap
        rows = []
        for i in range(75):
            ret = rng.normal(0.0001 * (3 if i > 6 else 0), 0.0016)
            o = price
            c = o * (1 + ret)
            h = max(o, c) * (1 + abs(rng.normal(0, 0.0008)))
            l = min(o, c) * (1 - abs(rng.normal(0, 0.0008)))
            rows.append((o, h, l, c, float(rng.integers(500, 2500)) * (3 if rng.random() < 0.1 else 1)))
            price = c
        frames.append(make_day(DAY0 + d + (d // 5) * 2, rows))
    return pd.concat(frames, ignore_index=True)


@pytest.mark.parametrize("seed", range(8))
@pytest.mark.parametrize("rules", [O.BASELINE, O.V3, O.Rules("filters_no_index", 0.15, 1.5, (), False)],
                         ids=lambda r: r.name)
def test_published_play_matches_the_oracle_on_random_days(seed, rules):
    frame = random_history(seed)
    index = random_history(seed + 100)
    # same dates, so every stock day has an index day
    index["time"] = frame["time"].to_numpy()
    got = mine(frame, rules, O.Index.from_frame(index))
    want = oracle(frame, rules, index)
    assert got == want
    if seed == 0 and rules.name == "orb_repo_baseline":
        assert len(got) > 5, "the generator must produce trades or this test proves nothing"


# ── the rules, one at a time ──────────────────────────────────

def test_a_breakout_enters_at_the_next_open_with_the_range_low_as_stop():
    f = make_day(DAY0 + 3, day_with_breakout())
    s = O.find_setups(f, O.BASELINE)[0]
    assert s.entry == s.breakout + 1 and s.or_low == 99.0 and s.or_high == 101.0
    t = O.play_published(f, s)
    assert t.entry == f["open"].iloc[s.entry]
    assert t.initial_stop == 99.0


def test_the_range_must_be_all_six_bars():
    bars = day_with_breakout()
    f = make_day(DAY0 + 3, bars).drop(index=2).reset_index(drop=True)
    assert O.find_setups(f, O.BASELINE) == []


def test_a_narrow_range_is_not_traded():
    wide = day_with_breakout(rng_low=99.85, rng_high=100.15)      # 0.30% of the open
    assert O.find_setups(make_day(DAY0 + 3, wide), O.BASELINE) != []
    narrow = day_with_breakout(rng_low=99.95, rng_high=100.05)    # 0.10%
    assert O.find_setups(make_day(DAY0 + 3, narrow), O.BASELINE) == []


def test_the_close_must_be_above_the_high_not_a_wick():
    bars = day_with_breakout(bo_close=101.0)                      # closes at, not above
    assert O.find_setups(make_day(DAY0 + 3, bars), O.BASELINE) == []


def test_breakout_strength_and_volume_filters_each_reject():
    weak = day_with_breakout(bo_close=101.1)                      # 0.099% above
    assert O.find_setups(make_day(DAY0 + 3, weak), O.BASELINE) != []
    assert O.find_setups(make_day(DAY0 + 3, weak), O.Rules("a", 0.15)) == []
    light = day_with_breakout(bo_vol=1200.0)
    assert O.find_setups(make_day(DAY0 + 3, light), O.Rules("b", 0.0, 1.5)) == []
    assert O.find_setups(make_day(DAY0 + 3, day_with_breakout()), O.Rules("c", 0.15, 1.5)) != []


def test_skip_days_and_the_nifty_filter():
    friday, monday = DAY0, DAY0 + 3                               # DAY0 is a Friday
    assert O.Rules("x").skip_days == ()
    rules = O.Rules("fri", skip_days=("Friday",))
    assert O.find_setups(make_day(friday, day_with_breakout()), rules) == []
    assert O.find_setups(make_day(monday, day_with_breakout()), rules) != []
    up = O.Index.from_frame(index_day(monday, range_high=100.0, after=105.0))
    down = O.Index.from_frame(index_day(monday, range_high=106.0, after=105.0))
    v = O.Rules("n", nifty_filter=True)
    assert O.find_setups(make_day(monday, day_with_breakout()), v, up) != []
    assert O.find_setups(make_day(monday, day_with_breakout()), v, down) == []
    assert O.find_setups(make_day(monday, day_with_breakout()), v, None) == [], \
        "a rule that needs NIFTY and has none does not trade"


def test_no_entry_when_the_breakout_bar_is_the_last_that_may_be_decided():
    late = day_with_breakout(breakout_at=62)                      # 14:25 bar: entry would be 14:30
    assert O.find_setups(make_day(DAY0 + 3, late), O.BASELINE) == []
    ok = day_with_breakout(breakout_at=61)
    assert O.find_setups(make_day(DAY0 + 3, ok), O.BASELINE) != []


def test_a_decision_uses_only_bars_closed_by_the_breakout_bar():
    f = make_day(DAY0 + 3, day_with_breakout())
    s = O.find_setups(f, O.BASELINE)[0]
    cut = f.iloc[: s.breakout + 1].reset_index(drop=True)
    again = O.find_setups(cut, O.BASELINE)
    assert again == [] or again[0].breakout == s.breakout    # no entry bar yet, so no setup...
    cut2 = f.iloc[: s.entry + 1].reset_index(drop=True)
    assert O.find_setups(cut2, O.BASELINE)[0].breakout == s.breakout
    changed = f.copy()
    changed.loc[s.entry + 1:, ["open", "high", "low", "close"]] = 1.0
    assert O.find_setups(changed, O.BASELINE)[0].breakout == s.breakout


# ── executable fills ──────────────────────────────────────────

def bars_after(*extra, total=75, base=102.0):
    """Bars after the breakout: the entry bar opens at 102 then `extra`."""
    rows = [(102.0, 102.2, 101.8, 102.0, 1000.0)] + list(extra)
    return rows + flat(total - 11 - len(rows), base)


def setup_of(frame):
    return O.find_setups(frame, O.BASELINE)[0]


def test_the_target_fills_only_when_traded_through_and_at_the_open_on_a_gap():
    # entry 102, stop 99, risk 3 → target 106.5
    touch = make_day(DAY0 + 3, day_with_breakout(then=bars_after((102, 106.5, 101.9, 102, 1)), total=75))
    t = O.play_executable(touch, setup_of(touch))
    assert t.reason != "target", "a touch of the target is not a fill"
    through = make_day(DAY0 + 3, day_with_breakout(then=bars_after((102, 106.6, 101.9, 102, 1))))
    t = O.play_executable(through, setup_of(through))
    assert t.reason == "target" and t.exit == pytest.approx(106.5)
    gap = make_day(DAY0 + 3, day_with_breakout(then=bars_after((107.0, 107.5, 106.0, 107, 1))))
    t = O.play_executable(gap, setup_of(gap))
    assert t.reason == "target" and t.exit == 107.0


def test_a_gap_through_the_stop_fills_at_the_open_published_fills_at_the_stop():
    f = make_day(DAY0 + 3, day_with_breakout(then=bars_after((97.0, 97.5, 96.0, 97, 1))))
    s = setup_of(f)
    ex = O.play_executable(f, s)
    assert ex.reason == "gap" and ex.exit == 97.0 and ex.gross_r < -1
    pub = O.play_published(f, s)
    assert pub.reason == "stop" and pub.exit == 99.0 and pub.gross_r == pytest.approx(-1.0)


def test_the_stop_is_live_in_the_entry_bar_the_source_ignores_it_there():
    f = make_day(DAY0 + 3, day_with_breakout(then=[(102.0, 102.3, 98.5, 102.0, 1)] + flat(63, 102.0)))
    s = setup_of(f)
    assert O.play_executable(f, s).reason == "stop"
    assert O.play_published(f, s).reason == "time"


def test_a_bar_that_reaches_stop_and_target_is_a_stop():
    f = make_day(DAY0 + 3, day_with_breakout(then=bars_after((102, 107, 98.9, 102, 1))))
    assert O.play_executable(f, setup_of(f)).reason == "stop"


def test_sold_at_the_1515_open_not_the_close_of_that_bar():
    rows = day_with_breakout()
    f = make_day(DAY0 + 3, rows)
    k = int(np.flatnonzero((f["time"] + IST) % 86_400 == 15 * 3600 + 15 * 60)[0])
    f.loc[k, ["open", "close"]] = [102.5, 103.0]
    s = setup_of(f)
    ex, pub = O.play_executable(f, s), O.play_published(f, s)
    assert ex.reason == "time" and ex.exit == 102.5 and ex.exit_index == k
    assert pub.reason == "time" and pub.exit == 103.0


def test_no_trade_when_the_next_open_is_at_or_through_the_stop():
    f = make_day(DAY0 + 3, day_with_breakout(then=[(98.5, 99.0, 98.0, 98.8, 1)] + flat(63, 99)))
    s = setup_of(f)
    assert O.play_executable(f, s) is None
    assert O.play_published(f, s) is None


def test_delay_moves_the_entry_and_keeps_the_levels():
    f = make_day(DAY0 + 3, day_with_breakout(then=bars_after((103.0, 103.2, 102.8, 103, 1))))
    s = setup_of(f)
    t0, t1 = O.play_executable(f, s), O.play_executable(f, s, delay=1)
    assert t1.entry == 103.0 and t1.entry_index == t0.entry_index + 1
    assert t1.initial_stop == t0.initial_stop == 99.0


# ── costs, sizing and the gate ────────────────────────────────

def test_an_account_that_cannot_afford_one_share_does_not_trade_the_source_would():
    from app.research import india_costing
    f = make_day(DAY0 + 3, day_with_breakout())
    trades = O.play_all(f, O.find_setups(f, O.BASELINE), O.EXECUTABLE)
    tiny = india_costing.Account(equity=300.0, risk_fraction=0.005, product=S.ACCOUNT.product)
    run = india_costing.cost_trades(trades, tiny, entry_kind=MARKET)
    assert run.taken == [] and len(run.skipped) == 1
    big = india_costing.cost_trades(trades, S.ACCOUNT, entry_kind=MARKET)
    assert len(big.taken) == 1 and big.taken[0].cost.costs > 0
    assert big.taken[0].net < big.taken[0].gross


def good():
    return {"trades": 400, "expectancy_r": 0.2, "profit_factor": 1.4,
            "interval": {"low": 0.05}, "deflated_sharpe": 0.97,
            "random": {"p_value": 0.01}, "benchmark_expectancy_r": -0.1,
            "cost_stress": {"1.5": 0.1}, "delay_1_bar_expectancy_r": 0.1,
            "markets_positive_share": 0.8}


def test_the_gate_passes_only_a_complete_good_result_and_even_then_asks_for_paper():
    status, _ = S.judge(good())
    assert status == "PAPER_VALIDATION_REQUIRED"


@pytest.mark.parametrize("change,status", [
    ({"trades": 199}, "INSUFFICIENT_DATA"),
    ({"expectancy_r": 0.0}, "FAILED_OUT_OF_SAMPLE"),
    ({"profit_factor": 1.0}, "FAILED_OUT_OF_SAMPLE"),
    ({"interval": {"low": 0.0}}, "FAILED_OUT_OF_SAMPLE"),
    ({"deflated_sharpe": 0.94}, "FAILED_OUT_OF_SAMPLE"),
    ({"random": {"p_value": 0.06}}, "FAILED_OUT_OF_SAMPLE"),
    ({"benchmark_expectancy_r": 0.3}, "FAILED_OUT_OF_SAMPLE"),
    ({"cost_stress": {"1.5": -0.01}}, "FAILED_COST_STRESS"),
    ({"delay_1_bar_expectancy_r": -0.01}, "FAILED_ROBUSTNESS"),
    ({"markets_positive_share": 0.5}, "FAILED_ROBUSTNESS"),
])
def test_each_gate_criterion_fails_on_its_own(change, status):
    m = good()
    m.update(change)
    assert S.judge(m)[0] == status


@pytest.mark.parametrize("missing", ["profit_factor", "interval", "deflated_sharpe", "random",
                                     "benchmark_expectancy_r", "cost_stress",
                                     "delay_1_bar_expectancy_r", "markets_positive_share"])
def test_a_measure_that_was_not_made_is_a_failure_not_a_pass(missing):
    m = good()
    m.pop(missing)
    assert S.judge(m)[0].startswith(("FAILED", "INSUFFICIENT"))


# ── the protocol and the boundary ─────────────────────────────

def test_the_protocol_digest_is_deterministic_and_names_both_variants():
    a, b = S.protocol(), S.protocol()
    assert S.digest_of(a) == S.digest_of(b)
    assert [v["name"] for v in a["variants"]] == ["orb_repo_baseline", "orb_repo_v3_default"]
    assert a["source"]["commit"] == "6130c7a2526235870830b45c052c86586582c5a6"
    assert a["gate"]["min_trades"] == 200 and a["gate"]["deflated_sharpe"] == 0.95


def test_a_changed_protocol_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(S, "FOLDER", tmp_path)
    monkeypatch.setattr(S, "PROTOCOL_PATH", tmp_path / "protocol.json")
    S.freeze()
    S.frozen()
    monkeypatch.setitem(S.GATE, "min_trades", 50)
    with pytest.raises(SystemExit):
        S.frozen()


def test_research_code_imports_nothing_that_can_place_an_order():
    root = Path(O.__file__).parent
    banned = ("broker", "routers", "services", "angel_one", "smartapi", "SmartApi")
    for name in ("orb_repo.py", "orb_repo_study.py"):
        tree = ast.parse((root / name).read_text())
        for node in ast.walk(tree):
            mods = []
            if isinstance(node, ast.Import):
                mods = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                mods = [("." * node.level) + (node.module or "")] + [a.name for a in node.names]
            for m in mods:
                assert not any(b.lower() in m.lower() for b in banned), (name, m)


# ── the study end to end, on synthetic bars ───────────────────

def synthetic_markets(count=3, days=60):
    out = []
    for k in range(count):
        frame = random_history(10 + k, days)
        index = random_history(200 + k, days)
        index["time"] = frame["time"].to_numpy()
        idx = O.Index.from_frame(index)
        out.append(S.Market("SYN{}".format(k), "synthetic", frame,
                            {r.name: O.find_setups(frame, r, idx) for r in O.VARIANTS}))
    return out


@pytest.fixture
def wide_stages(monkeypatch):
    monkeypatch.setitem(S.STAGES, "research", (0.0, 4e9))
    monkeypatch.setattr(S, "RANDOM_DRAWS", 6)


def test_a_stage_is_measured_controlled_and_judged(wide_stages):
    markets = synthetic_markets()
    m = S.evaluate_stage(markets, O.BASELINE, "research", say=lambda *_: None)
    run = m.pop("_run")
    assert m["trades"] == len(run.costed) > 20
    for key in ("expectancy_r", "profit_factor", "interval", "deflated_sharpe", "cost_stress",
                "random", "benchmark_expectancy_r", "delay_1_bar_expectancy_r", "by_month",
                "by_weekday", "by_market", "exits", "costs_pct_of_gross"):
        assert key in m, key
    assert set(m["cost_stress"]) == {"1", "1.5", "2"}
    assert m["cost_stress"]["2"] <= m["cost_stress"]["1.5"] <= m["cost_stress"]["1"]
    assert m["interval"]["level"] == 0.95
    assert m["status"] in {"INSUFFICIENT_DATA", "FAILED_OUT_OF_SAMPLE", "FAILED_COST_STRESS",
                           "FAILED_ROBUSTNESS", "PAPER_VALIDATION_REQUIRED"}
    assert m["status"] != "PAPER_VALIDATION_REQUIRED", "random bars have no edge"
    # costs only ever reduce a trade
    assert all(c.net <= c.gross for c in run.costed)
    # every trade is one session, long, entered after the range and left by 15:15
    for c in run.costed:
        a, b = (c.trade.entry_time + IST) % 86_400, (c.trade.exit_time + IST) % 86_400
        assert a >= 9 * 3600 + 50 * 60 and b <= 15 * 3600 + 20 * 60
        assert (c.trade.entry_time + IST) // 86_400 == (c.trade.exit_time + IST) // 86_400


def test_the_ledger_is_written_with_itemised_costs(wide_stages, tmp_path, monkeypatch):
    import csv
    import json
    markets = synthetic_markets(2)
    m = S.evaluate_stage(markets, O.BASELINE, "research", say=lambda *_: None)
    run = m.pop("_run")
    monkeypatch.setattr(S, "FOLDER", tmp_path)
    folder = S.write({"variants": {"x": m}, "ledgers": {"orb_repo_baseline/research": run.costed}})
    rows = list(csv.DictReader(open(folder / "orb_repo_baseline_research_trades.csv")))
    assert len(rows) == len(run.costed)
    first = rows[0]
    for col in ("brokerage", "stt", "exchange", "gst", "stamp", "sebi", "execution", "net", "net_r"):
        assert col in first
    assert json.loads((folder / "results.json").read_text())["variants"]["x"]["trades"] == len(rows)


def test_a_run_with_no_accepted_history_writes_no_result(monkeypatch, tmp_path):
    monkeypatch.setattr(S, "FOLDER", tmp_path)
    monkeypatch.setattr(S, "PROTOCOL_PATH", tmp_path / "protocol.json")
    S.freeze()
    monkeypatch.setattr(S, "load", lambda say=print: ([], None, [{"symbol": "X", "reason": "none"}]))
    assert S.main(["run"]) == 2
    assert not (tmp_path / "results").exists()
