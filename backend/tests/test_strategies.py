from __future__ import annotations

import numpy as np

from app.analysis import indicators as ind
from app.analysis import mtf, patterns, candlesticks, strategies
from app.analysis import structure as structure_engine
from app.instruments import get_instrument
from conftest import make_df


def build(df, symbol="EURUSD", timeframe="15m"):
    instrument = get_instrument(symbol)
    computed = ind.compute_all(df)
    snap, computed = ind.snapshot(df, instrument, computed, None)
    atr = snap.atr or 0.001
    struct = structure_engine.analyse(df, instrument, atr)
    report = mtf.build_report({timeframe: df})
    price = float(df["close"].iloc[-1])
    return strategies.evaluate(
        df, price, snap, struct, report,
        patterns.detect(df, struct.swings, atr), candlesticks.detect(df),
        instrument, atr, timeframe,
    )


def test_every_strategy_reports(uptrend):
    report = build(uptrend)
    assert len(report.results) == 5
    keys = {r.key for r in report.results}
    assert keys == {"trend_continuation", "mean_reversion", "breakout",
                    "smart_money", "momentum"}


def test_conditions_are_always_populated(uptrend):
    report = build(uptrend)
    for result in report.results:
        assert result.conditions, "{} reported no conditions".format(result.key)
        assert result.conditions_total == len(result.conditions)
        assert result.conditions_met == sum(1 for c in result.conditions if c.met)
        assert 0.0 <= result.conviction <= 100.0


def test_engaged_strategies_have_a_complete_plan(uptrend):
    report = build(uptrend)
    for result in report.results:
        if result.stance == "flat":
            continue
        assert result.entry is not None
        assert result.stop_loss is not None
        assert result.take_profit is not None
        assert result.risk_reward and result.risk_reward > 0
        long = result.stance == "long"
        # Stop below entry for a long, above for a short — never inverted.
        assert (result.stop_loss < result.entry) == long
        assert (result.take_profit > result.entry) == long


def test_projection_starts_at_the_last_bar_and_runs_forward(uptrend):
    report = build(uptrend)
    last_time = int(uptrend["time"].iloc[-1])
    for result in report.results:
        if not result.projection:
            continue
        times = [p.time for p in result.projection]
        assert times[0] == last_time
        assert times == sorted(times)
        assert len(set(times)) == len(times)
        # The path ends near the target, not on top of it (the wobble decays
        # to zero at the end, so the final point should be the target).
        assert result.take_profit is not None
        assert abs(result.projection[-1].price - result.take_profit) < 1e-6


def test_invalidation_path_ends_at_the_stop(uptrend):
    report = build(uptrend)
    for result in report.results:
        if not result.invalidation_path:
            continue
        assert result.stop_loss is not None
        assert abs(result.invalidation_path[-1].price - result.stop_loss) < 1e-6


def test_conviction_below_threshold_stands_aside(uptrend):
    report = build(uptrend)
    for result in report.results:
        if result.conviction < 60:
            assert result.stance == "flat", (
                "{} took a side on {:.0f}% conviction".format(
                    result.key, result.conviction))


def test_mean_reversion_stands_down_in_a_strong_trend():
    """The regime filter is the whole point — reversion must not fire into a
    clean trend, which is exactly where it loses the most money."""
    n = 300
    closes = 1.10 * np.exp(np.cumsum(np.full(n, 0.0012)))
    df = make_df(closes)
    report = build(df)
    reversion = next(r for r in report.results if r.key == "mean_reversion")
    regime = next(c for c in reversion.conditions
                  if c.name.startswith("Market is ranging"))
    assert not regime.met
    assert reversion.stance == "flat"


def test_consensus_only_counts_engaged_strategies(uptrend):
    report = build(uptrend)
    engaged = [r for r in report.results if r.stance != "flat"]
    if not engaged:
        assert report.consensus_stance == "flat"
        assert report.consensus_score == 0.0
    else:
        assert -1.0 <= report.consensus_score <= 1.0
        assert 0.0 <= report.agreement <= 1.0
    assert report.summary


def test_survives_a_flat_market():
    """A dead-flat series makes every derived value degenerate; the engine
    must still return five results rather than raising."""
    df = make_df(np.full(200, 1.1000))
    report = build(df)
    assert len(report.results) == 5


def test_scenario_overlays_only_for_engaged_strategies(uptrend):
    from app.overlays.builder import build_scenario_overlays

    report = build(uptrend)
    overlays = build_scenario_overlays(report)
    engaged = [r for r in report.results if r.stance != "flat"][:3]
    projection_ids = {o.id for o in overlays if o.role == "projection"}
    assert projection_ids == {"scenario-{}".format(r.key) for r in engaged}
    for overlay in overlays:
        assert overlay.kind == "path"
        assert len(overlay.points) >= 2


# ── derivatives ───────────────────────────────────────────────

def test_option_premiums_are_in_a_realistic_band():
    """The annualisation factor is the easiest thing here to get wrong by an
    order of magnitude, and a collapsed premium would look plausible on screen.
    A 21-day ATM index option is worth roughly 1.5-3% of spot."""
    from datetime import date

    from app.analysis import derivatives as dv

    nifty = get_instrument("NIFTY")
    suggestion = dv.suggest(nifty, "long", 24_500.0, 24_400.0, 24_720.0,
                            atr=90.0, bar_seconds=900, risk_amount=25_000.0,
                            today=date(2026, 8, 6))
    atm = next(leg for leg in suggestion.options if leg.moneyness == "ATM")
    ratio = atm.estimated_premium / 24_500.0
    assert 0.010 < ratio < 0.040, "ATM premium {:.2f} is {:.2%} of spot".format(
        atm.estimated_premium, ratio)
    assert 10.0 < suggestion.implied_vol_estimate < 60.0


def test_stocks_get_the_monthly_not_the_next_month():
    """Stocks have no weekly, so selecting the expiry by list position picks
    next-month for them and monthly for indices — the classic off-by-one."""
    from datetime import date

    from app.analysis import derivatives as dv

    today = date(2026, 8, 6)
    for symbol, price in (("NIFTY", 24_500.0), ("RELIANCE", 1_320.0), ("HDFCBANK", 1_680.0)):
        inst = get_instrument(symbol)
        suggestion = dv.suggest(inst, "long", price, price * 0.985, price * 1.03,
                                atr=price * 0.004, bar_seconds=900, today=today)
        monthly = next(c for c in suggestion.expiries if c.kind == "monthly")
        assert suggestion.options[0].expiry == monthly.date
        assert suggestion.futures_expiry == monthly.date


def test_option_stop_sits_between_zero_and_the_premium():
    from datetime import date

    from app.analysis import derivatives as dv

    inst = get_instrument("BANKNIFTY")
    suggestion = dv.suggest(inst, "short", 52_000.0, 52_260.0, 51_400.0,
                            atr=210.0, bar_seconds=900, today=date(2026, 8, 6))
    for leg in suggestion.options:
        assert leg.option_type == "PE"
        assert 0 < leg.premium_stop < leg.estimated_premium
        assert leg.delta < 0, "a put must have negative delta"
        assert leg.cost_per_lot == leg.max_loss_per_lot


def test_spot_fx_has_no_listed_derivative():
    from app.analysis import derivatives as dv

    assert dv.suggest(get_instrument("EURUSD"), "long", 1.085, 1.082, 1.09,
                      atr=0.001, bar_seconds=900) is None


def test_expiry_is_always_in_the_future():
    """Run the calendar across a full year — the last-Thursday rollover is
    where date arithmetic like this usually breaks."""
    from datetime import date, timedelta

    from app.analysis import derivatives as dv

    inst = get_instrument("NIFTY")
    day = date(2026, 1, 1)
    while day < date(2027, 1, 1):
        for choice in dv.expiries(inst, day):
            assert choice.days_to_expiry >= 0
            assert choice.date > day.isoformat() or choice.days_to_expiry == 0
        day += timedelta(days=1)
