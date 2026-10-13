"""What an NSE equity trade costs, order by order — checked against the
figures Angel One's own calculator returned for the same orders."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app import costs, costs_india
from app.costs_india import BUY, DELIVERY, INTRADAY, SELL
from app.data import angel_charges
from app.research import versions

#: SmartAPI `estimateCharges`, RELIANCE on NSE, recorded 2026-10-13:
#: (product, side, order value) -> total charges. The calculator leaves the
#: DP charge out, so these are everything but that.
BROKER_TOTALS = {
    (DELIVERY, BUY, 1_340.0): 7.4910, (DELIVERY, BUY, 13_400.0): 31.7232,
    (DELIVERY, BUY, 268_000.0): 341.8248,
    (DELIVERY, SELL, 1_340.0): 7.2900, (DELIVERY, SELL, 13_400.0): 29.7132,
    (DELIVERY, SELL, 268_000.0): 301.6248,
    (INTRADAY, BUY, 1_340.0): 5.9902, (INTRADAY, BUY, 13_400.0): 16.7152,
    (INTRADAY, BUY, 268_000.0): 41.6648,
    (INTRADAY, SELL, 1_340.0): 6.2900, (INTRADAY, SELL, 13_400.0): 19.6632,
    (INTRADAY, SELL, 268_000.0): 100.6248,
}
#: The same call's line-by-line answer for a Rs 13,400 delivery purchase.
BROKER_LINES = {"brokerage": 13.4, "exchange": 0.4114, "stamp": 2.01, "ipft": 0.0,
                "sebi": 0.0134, "stt": 13.4, "gst": 2.4884}

delivery, intraday = costs_india.schedule(DELIVERY), costs_india.schedule(INTRADAY)


# ── the engine against the broker ─────────────────────────────

@pytest.mark.parametrize("order", sorted(BROKER_TOTALS))
def test_every_order_costs_what_the_brokers_calculator_says(order):
    product, side, value = order
    rates = costs_india.schedule(product)
    engine = angel_charges.without_dp(costs_india.order_charges(rates, side, value), rates.gst)
    assert engine.total == pytest.approx(BROKER_TOTALS[order], abs=0.01)


def test_each_line_of_an_order_is_the_brokers_line():
    engine = costs_india.order_charges(delivery, BUY, 13_400.0)
    for line, amount in BROKER_LINES.items():
        assert getattr(engine, line) == pytest.approx(amount, abs=0.0001), line
    assert engine.dp == 0.0 and engine.turnover == 13_400.0


def test_brokerage_has_a_floor_a_rate_and_a_cap():
    """Lower of Rs 20 and 0.1%, minimum Rs 5, per executed order."""
    assert costs_india.brokerage(delivery, 1_340.0) == 5.0          # 0.1% is 1.34
    assert costs_india.brokerage(delivery, 5_000.0) == 5.0
    assert costs_india.brokerage(delivery, 13_400.0) == pytest.approx(13.4)
    assert costs_india.brokerage(delivery, 20_000.0) == 20.0
    assert costs_india.brokerage(delivery, 268_000.0) == 20.0
    assert costs_india.brokerage(delivery, 0.0) == 0.0
    assert costs_india.brokerage(intraday, 1_340.0) == 5.0          # intraday too, since Nov 2025


def test_the_floor_is_why_a_small_order_costs_far_more_than_the_headline_rate():
    small = costs_india.order_charges(delivery, BUY, 1_000.0)
    large = costs_india.order_charges(delivery, BUY, 15_000.0)
    assert small.brokerage / 1_000.0 == pytest.approx(0.005)        # half a percent
    assert large.brokerage / 15_000.0 == pytest.approx(0.001)       # the headline tenth


# ── what is charged on what ───────────────────────────────────

def test_stt_and_stamp_duty_fall_on_the_sides_the_law_puts_them_on():
    value = 100_000.0
    assert costs_india.order_charges(delivery, BUY, value).stt == pytest.approx(100.0)
    assert costs_india.order_charges(delivery, SELL, value).stt == pytest.approx(100.0)
    assert costs_india.order_charges(intraday, BUY, value).stt == 0.0
    assert costs_india.order_charges(intraday, SELL, value).stt == pytest.approx(25.0)
    assert costs_india.order_charges(delivery, BUY, value).stamp == pytest.approx(15.0)
    assert costs_india.order_charges(intraday, BUY, value).stamp == pytest.approx(3.0)
    assert costs_india.order_charges(delivery, SELL, value).stamp == 0.0


def test_gst_is_on_the_fees_and_not_on_the_taxes():
    order = costs_india.order_charges(delivery, SELL, 100_000.0)
    fees = order.brokerage + order.dp + order.exchange + order.sebi + order.ipft
    assert order.gst == pytest.approx(0.18 * fees)
    assert order.broker == order.brokerage + order.dp
    assert order.statutory == pytest.approx(
        order.stt + order.stamp + order.exchange + order.sebi + order.ipft + order.gst)
    assert order.total == pytest.approx(order.broker + order.statutory)


def test_the_depository_charge_is_per_stock_per_day_and_only_on_delivery():
    sale = costs_india.order_charges(delivery, SELL, 13_400.0)
    assert sale.dp == 20.0
    assert sale.total - angel_charges.without_dp(sale, delivery.gst).total == pytest.approx(23.6)
    assert costs_india.order_charges(delivery, BUY, 13_400.0).dp == 0.0
    assert costs_india.order_charges(intraday, SELL, 13_400.0).dp == 0.0

    two = [6_700.0, 6_700.0]                                  # sold in two orders
    same_day = costs_india.round_trip_charges(delivery, 13_400.0, two, sell_days=1)
    two_days = costs_india.round_trip_charges(delivery, 13_400.0, two)
    assert same_day.dp == 20.0 and two_days.dp == 40.0
    assert two_days.total - same_day.total == pytest.approx(23.6)


def test_an_order_filled_in_pieces_is_charged_once_and_each_exit_is_its_own_order():
    whole = costs_india.order_charges(intraday, BUY, 4_000.0)
    pieces = sum((costs_india.order_charges(intraday, BUY, 1_000.0) for _ in range(4)),
                 costs_india.Charges())
    assert whole.brokerage == 5.0 and pieces.brokerage == 20.0     # the floor, once or four times

    one_exit = costs_india.round_trip_charges(intraday, 40_000.0, [40_000.0])
    two_exits = costs_india.round_trip_charges(intraday, 40_000.0, [20_000.0, 20_000.0])
    assert one_exit.brokerage == 40.0 and two_exits.brokerage == 60.0
    assert two_exits.stt == pytest.approx(one_exit.stt)             # the tax is on value, either way


def test_an_order_that_never_filled_is_charged_nothing():
    assert costs_india.order_charges(delivery, BUY, 0.0) == costs_india.Charges()
    assert costs_india.order_charges(delivery, BUY, 0.0).total == 0.0
    only_entry = costs_india.round_trip_charges(intraday, 10_000.0, [0.0])
    assert only_entry.turnover == 10_000.0 and only_entry.stt == 0.0
    with pytest.raises(ValueError, match="BUY or a SELL"):
        costs_india.order_charges(delivery, "HOLD", 100.0)


def test_shares_cannot_be_sold_short_for_delivery():
    with pytest.raises(ValueError, match="cannot be opened short"):
        costs_india.round_trip_charges(delivery, 10_000.0, [10_000.0], long=False)
    short = costs_india.round_trip_charges(intraday, 10_000.0, [9_800.0], long=False)
    assert short.stt == pytest.approx(2.5) and short.stamp == pytest.approx(0.294)


# ── a whole trade, gross to net ───────────────────────────────

def test_a_trade_is_reported_gross_net_and_line_by_line():
    trade = costs_india.trade_cost(10, 1_000.0, 1_100.0, product=DELIVERY, scenario="base")
    assert trade.gross == 1_000.0
    assert trade.charges.turnover == 21_000.0
    assert trade.execution == pytest.approx(10 * (1_000.0 + 1_100.0) * 0.0004)
    assert trade.costs == pytest.approx(trade.charges.total + trade.execution)
    assert trade.net == pytest.approx(1_000.0 - trade.costs)
    record = trade.to_dict()
    assert record["schedule"] == delivery.id and record["execution_scenario"] == "base"
    assert {"brokerage", "dp", "stt", "stamp", "exchange", "sebi", "ipft", "gst", "broker",
            "statutory", "total"} <= set(record["charges"])
    assert record["gross"] - record["costs"] == pytest.approx(record["net"], abs=0.01)

    short = costs_india.trade_cost(10, 1_000.0, 950.0, product=INTRADAY, long=False)
    assert short.gross == 500.0


def test_a_position_closed_in_parts_adds_up():
    parts = costs_india.trade_cost(100, 500.0, 0.0, product=INTRADAY, scenario="base",
                                   exits=[(60, 520.0), (40, 490.0)])
    assert parts.gross == 60 * 20.0 - 40 * 10.0
    assert parts.charges.turnover == 50_000.0 + 31_200.0 + 19_600.0
    assert parts.charges.brokerage == 20.0 + 20.0 + 19.6
    with pytest.raises(ValueError, match="Exits close"):
        costs_india.trade_cost(100, 500.0, 0.0, exits=[(60, 520.0), (60, 490.0)])


def test_a_stop_gives_up_more_than_a_target_and_a_resting_order_less():
    kwargs = dict(product=INTRADAY, scenario="conservative")
    at_target = costs_india.trade_cost(100, 500.0, 480.0, **kwargs)
    at_stop = costs_india.trade_cost(100, 500.0, 480.0, stopped=True, **kwargs)
    resting = costs_india.trade_cost(100, 500.0, 480.0, entry_passive=True, **kwargs)
    assert at_stop.execution - at_target.execution == pytest.approx(100 * 480.0 * 0.0015)
    assert at_target.execution - resting.execution == pytest.approx(100 * 500.0 * 0.001)
    assert at_stop.charges == at_target.charges                      # the bill is the same


def test_the_three_execution_scenarios_are_in_order_and_say_they_are_assumed():
    costs_by = {name: costs_india.trade_cost(100, 500.0, 480.0, scenario=name, stopped=True).costs
                for name in ("optimistic", "base", "conservative")}
    assert costs_by["optimistic"] < costs_by["base"] < costs_by["conservative"]
    assert costs_india.ELIGIBILITY_SCENARIO == "conservative"
    assert all("not measured" in e.basis for e in costs_india.EXECUTION.values())


def test_a_stress_multiplies_every_cost_and_leaves_the_trade_alone():
    base = costs_india.trade_cost(50, 800.0, 760.0, stopped=True)
    assert costs_india.STRESS_MULTIPLIERS == (1.5, 2.0)
    for multiplier in costs_india.STRESS_MULTIPLIERS:
        stressed = costs_india.trade_cost(50, 800.0, 760.0, stopped=True, multiplier=multiplier)
        assert stressed.gross == base.gross
        assert stressed.charges.turnover == base.charges.turnover
        assert stressed.costs == pytest.approx(multiplier * base.costs)
        assert stressed.charges.stt == pytest.approx(multiplier * base.charges.stt)
        assert stressed.net < base.net


# ── what that does to a small account ─────────────────────────

def test_a_position_is_sized_so_the_whole_loss_fits_the_budget_costs_included():
    for equity in (25_000.0, 100_000.0, 1_000_000.0):
        sized = costs_india.size_position(equity, 0.005, 1_000.0, 950.0)
        assert sized.quantity > 0 and sized.loss_at_stop <= sized.risk_budget
        one_more = costs_india.trade_cost(sized.quantity + 1, 1_000.0, 950.0, stopped=True)
        assert (sized.quantity + 1) * 50.0 + one_more.costs > sized.risk_budget   # not one share more
        assert sized.quantity < equity * 0.005 / 50.0, "costs take shares out of the budget"


def test_a_position_is_never_rounded_up_to_fit_an_account():
    """Ten thousand rupees risking half a percent has fifty to lose. One
    share with a fifty-rupee stop loses that on price alone — and more than
    thirty again in charges. The answer is no shares, with the reason."""
    sized = costs_india.size_position(10_000.0, 0.005, 1_000.0, 950.0)
    assert sized.quantity == 0 and sized.cost_in_r is None
    assert "One share loses" in sized.reason and "risk budget is 50.00" in sized.reason

    dear = costs_india.size_position(10_000.0, 0.05, 12_000.0, 11_400.0)
    assert dear.quantity == 0 and "One share costs 12000.00" in dear.reason

    wrong = costs_india.size_position(100_000.0, 0.005, 1_000.0, 1_050.0)
    assert wrong.quantity == 0 and "wrong side" in wrong.reason
    assert costs_india.size_position(0.0, 0.005, 1_000.0, 950.0).quantity == 0


def test_cash_and_a_notional_limit_cap_the_size_as_well_as_risk():
    tight_stop = costs_india.size_position(100_000.0, 0.01, 1_000.0, 998.0)
    assert tight_stop.notional <= 100_000.0                        # delivery: no leverage
    leveraged = costs_india.size_position(100_000.0, 0.01, 1_000.0, 998.0, product=INTRADAY,
                                          leverage=5.0)
    assert tight_stop.quantity < leveraged.quantity and leveraged.notional <= 500_000.0
    capped = costs_india.size_position(100_000.0, 0.01, 1_000.0, 998.0, max_notional=20_000.0)
    assert capped.notional <= 20_000.0


def test_the_same_trade_costs_a_small_account_several_times_more_in_r():
    table = costs_india.account_table([10_000.0, 25_000.0, 100_000.0, 1_000_000.0])
    by = {row["equity"]: row for row in table}
    assert by[10_000.0]["quantity"] == 0 and by[10_000.0]["reason"]
    in_r = [by[e]["cost_in_r"] for e in (25_000.0, 100_000.0, 1_000_000.0)]
    assert in_r[0] > in_r[1] > in_r[2]
    assert in_r[0] > 0.3 and in_r[2] < 0.12


# ── the model the live limits use ─────────────────────────────

STOCK = SimpleNamespace(asset_class="stock")


def test_the_live_estimate_for_a_stock_is_the_order_level_one():
    """`costs.charges` is what the daily loss limit is enforced on. As a rate
    of 0.053% a side it put a Rs 10,000 round trip at Rs 10.60."""
    for value in (2_000.0, 10_000.0, 25_000.0, 500_000.0):
        exact = costs_india.round_trip_charges(intraday, value, [value]).total
        assert costs.charges(STOCK, value, value) == pytest.approx(exact, abs=0.05)
    assert costs.charges(STOCK, 10_000.0, 10_000.0) == pytest.approx(27.15, abs=0.01)
    assert costs.charges(STOCK, 10_000.0, 10_000.0) > 2.5 * 10.60


def test_the_rate_a_backtest_is_charged_agrees_with_the_order_where_a_rate_can():
    """Paper trading and the backtests are charged a rate per side. Between
    the floor and the cap that is the order's own cost; outside, a rate is
    too little on a small order and too much on a large one — and it is the
    large one a backtest should be wrong about."""
    def as_a_rate(value):
        return 2 * value * costs.per_side_rate(STOCK)

    def as_orders(value):
        return costs_india.round_trip_charges(intraday, value, [value]).total

    for value in (5_000.0, 10_000.0, 20_000.0):
        assert as_a_rate(value) == pytest.approx(as_orders(value), rel=0.002)
    assert as_a_rate(2_000.0) < as_orders(2_000.0)            # the Rs 5 floor
    assert as_a_rate(100_000.0) > as_orders(100_000.0)        # the Rs 20 cap
    assert costs.per_side_rate(STOCK) == pytest.approx(0.001356, abs=1e-6)
    # (0.03% brokerage + exchange + SEBI) with GST, plus STT and stamp duty:
    # (0.0003 + 0.0000173 + 0.000001) x 1.18 + 0.0001 + 0.00001.
    assert costs.per_side_rate(SimpleNamespace(asset_class="index")) == pytest.approx(
        0.0004856, abs=1e-7), "futures are charged as they were"


def test_the_live_estimate_takes_the_dearer_reading_of_which_leg_was_the_sale():
    long = costs_india.round_trip_charges(intraday, 25_000.0, [24_800.0], long=True).total
    short = costs_india.round_trip_charges(intraday, 25_000.0, [24_800.0], long=False).total
    assert costs.charges(STOCK, 25_000.0, 24_800.0) == pytest.approx(max(long, short))
    assert costs.charges(STOCK, 25_000.0, 24_800.0) == pytest.approx(56.06, abs=0.01)


def test_other_segments_are_charged_as_they_were():
    for asset_class in ("index", "currency_fut", "commodity"):
        market = SimpleNamespace(asset_class=asset_class)
        assert costs.charges(market, 100_000.0, 99_000.0) == pytest.approx(
            199_000.0 * costs.per_side_rate(market))


# ── what a result records ─────────────────────────────────────

def test_a_schedule_says_what_it_is_when_it_is_from_and_how_far_it_was_checked():
    for rates in (delivery, intraday):
        assert rates.broker == "Angel One" and rates.exchange == "NSE"
        assert rates.effective_from == "2025-11-17" and rates.retrieved == "2026-10-13"
        assert any("angelone.in" in source for source in rates.sources)
        assert "charge estimator" in rates.verified and "contract note" in rates.verified
    assert delivery.shorting is False and intraday.shorting is True
    described = costs_india.describe(DELIVERY)
    assert described["schedule"]["id"] == delivery.id
    assert described["execution"]["name"] == "conservative"
    assert described["version"].startswith("costs-india@")
    with pytest.raises(KeyError, match="No schedule for 'futures'"):
        costs_india.schedule("futures")


def test_changing_a_rate_changes_the_fingerprint_evidence_is_tied_to():
    assert "costs_india.py" in versions.COST_FILES and "costs.py" in versions.COST_FILES
    with_it = versions.fingerprint(versions.COST_FILES)
    assert with_it != versions.fingerprint(("costs.py",))


# ── reading the calculator ────────────────────────────────────

def estimate_of(total, **lines):
    """A calculator response shaped as the real one is."""
    def line(name, amount, breakup=None):
        return {"name": name, "amount": amount, "msg": "", "breakup": breakup}
    external = [line("Exchange Transaction Charges", lines.get("exchange", 0.0)),
                line("Stamp Duty", lines.get("stamp", 0.0)),
                line("IPFT charges", lines.get("ipft", 0.0)),
                line("SEBI Fees", lines.get("sebi", 0.0))]
    taxes = [line("Security Transaction Tax", lines.get("stt", 0.0)),
             line("GST", lines.get("gst", 0.0))]
    return {"status": True, "message": "SUCCESS", "data": {"summary": {
        "total_charges": total, "trade_value": 13_400, "breakup": [
            line("Angel One Brokerage", lines.get("brokerage", 0.0)),
            line("External Charges", sum(x["amount"] for x in external), external),
            line("Taxes", sum(x["amount"] for x in taxes), taxes)]}}}


def test_the_calculators_answer_is_read_line_by_line():
    parsed = angel_charges.parse(estimate_of(31.7232, **BROKER_LINES))
    assert parsed == {**BROKER_LINES, "total": 31.7232, "turnover": 13_400.0}
    engine = costs_india.order_charges(delivery, BUY, 13_400.0)
    assert angel_charges.differences(engine, parsed, delivery.gst) == []


def test_a_rate_that_has_changed_shows_up_as_the_line_it_is_on():
    dearer = {**BROKER_LINES, "stt": 20.1}                       # as if STT rose to 0.15%
    parsed = angel_charges.parse(estimate_of(31.7232 + 6.7, **dearer))
    found = angel_charges.differences(costs_india.order_charges(delivery, BUY, 13_400.0),
                                      parsed, delivery.gst)
    assert found[0].startswith("stt: engine 13.4000, broker 20.1000")
    assert any(item.startswith("total excluding DP") for item in found)


def test_a_charge_nobody_modelled_is_not_dropped_from_the_comparison():
    response = estimate_of(40.0, **BROKER_LINES)
    response["data"]["summary"]["breakup"].append(
        {"name": "Clearing Charges", "amount": 8.0, "msg": "", "breakup": None})
    with pytest.raises(angel_charges.EstimateUnreadable, match="Clearing Charges"):
        angel_charges.parse(response)
    with pytest.raises(angel_charges.EstimateUnreadable, match="failed: Invalid Token"):
        angel_charges.parse({"status": False, "message": "Invalid Token"})
    with pytest.raises(angel_charges.EstimateUnreadable):
        angel_charges.parse({"status": True, "data": {}})


def test_verification_asks_about_every_product_side_and_size_and_reports_each():
    asked = []

    async def estimate(params):
        order = params["orders"][0]
        asked.append((order["product_type"], order["transaction_type"], order["quantity"]))
        product = DELIVERY if order["product_type"] == "DELIVERY" else INTRADAY
        value = int(order["quantity"]) * float(order["price"])
        rates = costs_india.schedule(product)
        ours = angel_charges.without_dp(
            costs_india.order_charges(rates, order["transaction_type"], value), rates.gst)
        if (product, order["transaction_type"], value) == (INTRADAY, SELL, 13_400.0):
            return {"status": False, "message": "Something Went Wrong"}
        return estimate_of(round(ours.total, 4), **{k: round(getattr(ours, k), 4)
                                                    for k in BROKER_LINES})

    async def no_sleep(_seconds):
        return None

    rows = asyncio.run(angel_charges.verify(estimate, sleep=no_sleep))
    assert len(rows) == len(asked) == 12
    assert {a[:2] for a in asked} == {("DELIVERY", "BUY"), ("DELIVERY", "SELL"),
                                      ("INTRADAY", "BUY"), ("INTRADAY", "SELL")}
    failed = [r for r in rows if r["differences"]]
    assert len(failed) == 1 and failed[0]["broker"] is None
    assert "Something Went Wrong" in failed[0]["differences"][0]
    assert all(r["schedule"] in (delivery.id, intraday.id) for r in rows)
