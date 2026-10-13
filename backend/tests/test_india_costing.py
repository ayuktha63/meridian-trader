"""Simulated trades charged as real orders, for a real account."""
from __future__ import annotations

import pytest

from app import costs_india
from app.costs_india import DELIVERY, INTRADAY
from app.research import india_costing
from app.research.india_costing import Account, cost_trades, summary
from app.research.simulator import LIMIT, STOP, Trade

from india_fixtures import ist

DAY1, DAY2 = ist(2024, 7, 1, 9, 15), ist(2024, 7, 8, 9, 15)


def trade(entry=1_000.0, exit_=1_100.0, stop=950.0, reason="signal", direction=1,
          entry_time=DAY1, exit_time=DAY2, cost_r=0.0):
    """A trade as the simulator returns it when it charges nothing."""
    risk = abs(entry - stop)
    gross = direction * (exit_ - entry) / risk
    return Trade(signal_index=0, entry_index=1, exit_index=6, entry_time=entry_time,
                 exit_time=exit_time, direction=direction, entry=entry, exit=exit_,
                 initial_stop=stop, risk=risk, gross_r=gross, cost_r=cost_r, r=gross - cost_r,
                 bars_held=5, reason=reason, mae_r=0.0, mfe_r=max(gross, 0.0))


BIG = Account(equity=1_000_000.0)


def test_a_trade_is_sized_for_the_account_and_charged_as_two_orders():
    run = cost_trades([trade()], BIG)
    assert len(run.taken) == 1 and run.skipped == []
    costed = run.taken[0]
    assert costed.quantity == 88                                  # 5,000 at risk, 50 a share
    assert costed.risk == 88 * 50.0 and costed.gross == 88 * 100.0
    assert costed.gross_r == pytest.approx(2.0)
    expected = costs_india.trade_cost(88, 1_000.0, 1_100.0, product=DELIVERY,
                                      scenario="conservative")
    assert costed.cost.costs == pytest.approx(expected.costs)
    assert costed.net == pytest.approx(8_800.0 - expected.costs)
    assert costed.r == pytest.approx(costed.gross_r - costed.cost_r) and costed.r < 2.0
    assert costed.cost.charges.dp == 20.0 and costed.cost.charges.brokerage == 40.0


def test_the_position_is_sized_at_the_decision_not_at_the_fill():
    """The stop was set from 1,000. The open came at 1,020: the same shares,
    and a worse trade."""
    gapped = trade(entry=1_020.0, exit_=1_100.0, stop=950.0)
    gapped.risk = 50.0                                            # decided at 1,000
    run = cost_trades([gapped], BIG)
    assert run.taken[0].quantity == 88
    assert run.taken[0].gross == 88 * 80.0


def test_what_the_simulator_already_charged_is_not_charged_again():
    with pytest.raises(ValueError, match="charged twice"):
        cost_trades([trade(cost_r=0.05)], BIG)


def test_a_signal_the_account_cannot_afford_is_skipped_and_counted_not_shrunk_or_forced():
    small = Account(equity=10_000.0)
    run = cost_trades([trade(), trade(entry=12_000.0, exit_=12_500.0, stop=11_400.0)], small)
    assert run.taken == [] and len(run.skipped) == 2
    assert "risk budget is 50.00" in run.skipped[0][1]
    report = summary(run)
    assert report["trades"] == 0 and report["skipped"] == 2 and "net_pnl" not in report
    assert sum(report["skipped_because"].values()) == 2


def test_delivery_takes_no_shorts_and_intraday_takes_nothing_overnight():
    short = trade(entry=1_000.0, exit_=950.0, stop=1_050.0, direction=-1)
    run = cost_trades([short], BIG)
    assert run.taken == [] and "cannot be opened short" in run.skipped[0][1]

    day_trader = Account(equity=1_000_000.0, product=INTRADAY, leverage=5.0)
    with pytest.raises(ValueError, match="not an intraday trade"):
        cost_trades([short], day_trader)
    same_day = trade(entry=1_000.0, exit_=990.0, stop=1_010.0, direction=-1,
                     exit_time=ist(2024, 7, 1, 14, 30))
    run = cost_trades([same_day], day_trader)
    assert run.taken[0].gross > 0 and run.taken[0].cost.charges.dp == 0.0


def test_how_the_order_got_in_and_out_changes_what_the_book_took():
    target, stopped = trade(reason="target"), trade(exit_=950.0, reason="stop")
    market = cost_trades([target, stopped], BIG).taken
    limit = cost_trades([target, stopped], BIG, entry_kind=LIMIT).taken
    breakout = cost_trades([target, stopped], BIG, entry_kind=STOP).taken
    assert limit[0].cost.execution < market[0].cost.execution < breakout[0].cost.execution
    assert market[0].cost.execution == pytest.approx(88 * 1_000.0 * 0.001)   # target rested
    assert market[1].cost.execution == pytest.approx(
        88 * (1_000.0 * 0.001 + 950.0 * (0.001 + 0.0015)))                   # stop slipped
    with pytest.raises(ValueError, match="market, limit or stop"):
        cost_trades([target], BIG, entry_kind="iceberg")


def test_a_run_is_reported_gross_to_net_with_every_cost_named():
    trades = [trade(), trade(exit_=950.0, reason="stop"), trade(exit_=1_060.0, reason="target")]
    report = summary(cost_trades(trades, BIG))
    assert report["trades"] == 3 and report["skipped"] == 0
    assert report["gross_pnl"] == pytest.approx(88 * (100.0 - 50.0 + 60.0))
    assert report["net_pnl"] == pytest.approx(report["gross_pnl"] - report["total_costs"], abs=0.02)
    assert report["total_costs"] == pytest.approx(
        report["broker_fees"] + report["statutory_charges"] + report["execution_estimate"],
        abs=0.03)
    assert set(report["charges"]) == {"brokerage", "dp", "stt", "stamp", "exchange", "sebi",
                                      "ipft", "gst"}
    assert report["charges"]["brokerage"] == 120.0 and report["charges"]["dp"] == 60.0
    assert report["turnover"] == pytest.approx(88 * (3 * 1_000.0 + 1_100.0 + 950.0 + 1_060.0))
    assert report["cost_per_trade"] == pytest.approx(report["total_costs"] / 3, abs=0.01)
    assert report["expectancy_r"] == pytest.approx(
        report["gross_expectancy_r"] - report["cost_r"], abs=1e-4)
    assert report["schedule"] == costs_india.schedule(DELIVERY).id
    assert report["costs_version"].startswith("costs-india@")
    assert report["account"]["compounding"] is False


def test_the_break_even_multiple_is_how_wrong_the_costs_could_be():
    trades = [trade(), trade(exit_=950.0, reason="stop"), trade(exit_=1_060.0, reason="target")]
    base = summary(cost_trades(trades, BIG))
    multiple = base["break_even_cost_multiple"]
    assert multiple > 1.0
    at_it = summary(cost_trades(trades, BIG, multiplier=multiple))
    assert abs(at_it["net_pnl"]) < 0.01 * base["gross_pnl"]
    assert at_it["break_even_cost_multiple"] == pytest.approx(multiple, abs=0.02)
    losing = summary(cost_trades([trade(exit_=950.0, reason="stop")], BIG))
    assert losing["break_even_cost_multiple"] == 0.0


def test_a_stress_charges_the_same_shares_more():
    trades = [trade(), trade(exit_=950.0, reason="stop")]
    table = india_costing.sensitivity(trades, BIG)
    assert len(table) == 9 and {row["trades"] for row in table} == {2}
    by = {(row["scenario"], row["multiplier"]): row["expectancy_r"] for row in table}
    for scenario in ("optimistic", "base", "conservative"):
        assert by[(scenario, 1.0)] > by[(scenario, 1.5)] > by[(scenario, 2.0)]
    assert by[("optimistic", 1.0)] > by[("base", 1.0)] > by[("conservative", 1.0)]
    doubled = cost_trades(trades, BIG, multiplier=2.0).taken
    assert [c.quantity for c in doubled] == [88, 88]


def test_the_same_signals_are_worth_less_to_a_small_account_or_nothing():
    trades = [trade(), trade(exit_=950.0, reason="stop"), trade(exit_=1_060.0, reason="target")]
    table = india_costing.by_account(trades, [10_000.0, 25_000.0, 100_000.0, 1_000_000.0])
    by = {row["equity"]: row for row in table}
    assert by[10_000.0]["trades"] == 0 and by[10_000.0]["skipped"] == 3
    assert by[10_000.0]["expectancy_r"] is None
    kept = [by[e]["expectancy_r"] for e in (25_000.0, 100_000.0, 1_000_000.0)]
    assert kept[0] < kept[1] < kept[2]
    assert by[25_000.0]["cost_r"] > 2 * by[1_000_000.0]["cost_r"]


# ── the live engine's own trades ──────────────────────────────

def engine_trade(direction="BUY", entry=1_000.0, stop=990.0, exit_price=1_020.0,
                 exit_reason="target", time=ist(2024, 7, 1, 10, 15),
                 exit_time=ist(2024, 7, 1, 13, 0), cost_r=0.11):
    from app.schemas.journal import BacktestTrade

    risk = abs(entry - stop)
    sign = 1 if direction == "BUY" else -1
    gross = sign * (exit_price - entry) / risk
    return BacktestTrade(index=40, time=int(time), direction=direction, entry=entry,
                         stop_loss=stop, take_profit=entry + sign * 2 * risk,
                         exit_time=int(exit_time), exit_price=exit_price, outcome="win",
                         r_multiple=round(gross - cost_r, 3), bars_held=11, cost_r=cost_r,
                         exit_reason=exit_reason)


DAY_TRADER = Account(equity=100_000.0, product=INTRADAY, leverage=5.0)


def test_the_engines_trades_are_charged_as_orders_and_not_twice():
    """The engine prices a trade with a rate. Its trades come across with
    what happened and without what it charged."""
    engine = [engine_trade(), engine_trade("SELL", 1_000.0, 1_010.0, 985.0, "stop")]
    trades = india_costing.from_engine(engine)
    assert [t.direction for t in trades] == [1, -1]
    assert [t.cost_r for t in trades] == [0.0, 0.0] and trades[0].gross_r == pytest.approx(2.0)
    assert trades[0].initial_stop == 990.0 and trades[0].risk == 10.0
    assert trades[1].reason == "stop" and trades[1].gross_r == pytest.approx(1.5)

    run = cost_trades(trades, DAY_TRADER, entry_kind=LIMIT)
    assert len(run.taken) == 2 and run.skipped == []
    long, short = run.taken
    assert long.quantity > 0 and long.cost.charges.dp == 0.0           # nothing left the demat
    assert long.cost.execution == 0.0                                  # limit in, target out
    assert short.cost.execution == pytest.approx(
        short.quantity * 985.0 * (0.001 + 0.0015))                     # the stop crossed and slipped
    assert long.r < long.gross_r and long.cost.charges.brokerage >= 10.0


def test_an_engine_trade_still_open_or_with_no_stop_distance_is_left_out():
    still_open = engine_trade()
    still_open.exit_price = None
    no_risk = engine_trade()
    no_risk.stop_loss = no_risk.entry
    assert india_costing.from_engine([still_open, no_risk]) == []


def test_a_square_off_crosses_the_spread_and_an_overnight_hold_is_refused():
    squared_off = india_costing.from_engine(
        [engine_trade(exit_price=1_004.0, exit_reason="eod",
                      exit_time=ist(2024, 7, 1, 15, 10))])
    run = cost_trades(squared_off, DAY_TRADER, entry_kind=LIMIT)
    assert run.taken[0].cost.execution == pytest.approx(run.taken[0].quantity * 1_004.0 * 0.001)

    overnight = india_costing.from_engine(
        [engine_trade(exit_reason="timeout", exit_time=ist(2024, 7, 2, 11, 0))])
    with pytest.raises(ValueError, match="not an intraday trade"):
        cost_trades(overnight, DAY_TRADER, entry_kind=LIMIT)
