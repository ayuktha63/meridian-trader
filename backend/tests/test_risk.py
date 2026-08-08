from __future__ import annotations

import time

import pytest

from app.analysis import guardrails
from app.analysis import risk as risk_engine
from app.instruments import get_instrument
from app.schemas.positions import Position, PositionSide
from app.schemas.risk import GuardrailStatus, ProtectionState


def make_position(entry=1.1000, stop=1.0900, side=PositionSide.LONG,
                  target=None, lots=1.0, **kwargs) -> Position:
    return Position(
        id="p1", symbol="EURUSD", timeframe="15m", side=side,
        entry=entry, stop_loss=kwargs.pop("stop_loss", stop),
        initial_stop=stop, take_profit=target, lots=lots,
        opened_at=time.time(), **kwargs)


EURUSD = get_instrument("EURUSD")
#: 1R here is 100 pips; an ATR of 20 pips keeps the noise band well inside it
#: so the clearance gate does not dominate the tier tests.
ATR = 0.0020


# ── the risk unit ─────────────────────────────────────────────

def test_r_is_measured_against_the_original_stop_not_the_trailed_one():
    """The bug this guards: once the stop trails to breakeven,
    `entry - stop_loss` is zero. Every R figure would go infinite or report as
    zero, and the position would be declared unmanageable at exactly the moment
    it is working."""
    position = make_position(entry=1.1000, stop=1.0900)
    position.stop_loss = 1.1000              # trailed to breakeven
    assert position.risk_unit == pytest.approx(0.0100)

    plan = risk_engine.build_plan(position, 1.1200, EURUSD, ATR)
    assert plan is not None
    assert plan.r_multiple == pytest.approx(2.0, abs=0.01)


def test_plan_is_none_without_a_risk_unit():
    position = make_position(entry=1.1000, stop=1.1000)
    assert risk_engine.build_plan(position, 1.1100, EURUSD, ATR) is None


# ── the ladder ────────────────────────────────────────────────

def test_nothing_is_protected_at_entry():
    position = make_position()
    plan = risk_engine.build_plan(position, 1.1000, EURUSD, ATR)
    assert plan.state is ProtectionState.AT_RISK
    assert plan.locked_r == pytest.approx(-1.0, abs=0.01)
    assert not plan.action_available
    assert plan.next_tier.name == "Breakeven"


def test_breakeven_arms_at_one_r_and_sits_beyond_the_entry():
    """Breakeven at exactly the entry price books a loss — you paid the spread
    on the way in and pay it again on the way out."""
    position = make_position()
    plan = risk_engine.build_plan(position, 1.1105, EURUSD, ATR)

    assert plan.action_available
    assert plan.state is ProtectionState.ARMED
    assert plan.suggested_stop > position.entry, (
        "a breakeven stop must clear the round-trip cost, not sit on the entry")
    cost = risk_engine.round_trip_cost(EURUSD)
    assert plan.suggested_stop == pytest.approx(position.entry + cost, abs=1e-6)


def test_ladder_ratchets_and_never_loosens():
    """Walk the trade up and confirm the suggested stop only ever moves in the
    protective direction."""
    position = make_position()
    last = position.stop_loss
    for price in (1.1000, 1.1100, 1.1170, 1.1260, 1.1420, 1.1600):
        plan = risk_engine.build_plan(position, price, EURUSD, ATR)
        if plan.suggested_stop is not None:
            assert plan.suggested_stop >= last - 1e-12
            last = plan.suggested_stop
            position.stop_loss = plan.suggested_stop
            position.peak_r = max(position.peak_r, plan.r_multiple)


def test_higher_rungs_lock_progressively_more():
    position = make_position()
    locked = []
    for price, in ((1.1105,), (1.1170,), (1.1260,), (1.1420,)):
        position.peak_r = max(position.peak_r, (price - position.entry) / 0.01)
        plan = risk_engine.build_plan(position, price, EURUSD, ATR)
        active = next((t for t in plan.tiers if t.active), None)
        assert active is not None
        locked.append(active.locked_r)
    assert locked == sorted(locked)
    assert locked[0] == 0.0 and locked[-1] >= 1.0


def test_state_is_safe_once_the_stop_reaches_breakeven():
    position = make_position()
    cost = risk_engine.round_trip_cost(EURUSD)
    position.stop_loss = position.entry + cost
    plan = risk_engine.build_plan(position, 1.1150, EURUSD, ATR)
    assert plan.state is ProtectionState.SAFE
    assert "cannot lose" in plan.headline.lower()


def test_state_is_locked_once_the_stop_is_beyond_breakeven():
    position = make_position()
    position.stop_loss = 1.1050          # +0.5R
    position.peak_r = 2.0
    plan = risk_engine.build_plan(position, 1.1200, EURUSD, ATR)
    assert plan.state is ProtectionState.LOCKED
    assert plan.locked_r > 0


def test_a_wide_atr_blocks_the_rung_until_price_is_clear():
    """Arming a rung that puts the stop inside the noise band converts winners
    into scratches. With an ATR as wide as the whole risk, +1R is not enough
    clearance."""
    position = make_position()
    tight = risk_engine.build_plan(position, 1.1105, EURUSD, atr=0.0020)
    noisy = risk_engine.build_plan(position, 1.1105, EURUSD, atr=0.0120)
    assert tight.action_available
    assert not noisy.action_available, (
        "the rung armed even though the stop would sit inside one ATR of price")


def test_short_side_mirrors_the_long_side():
    position = make_position(entry=1.1000, stop=1.1100, side=PositionSide.SHORT)
    plan = risk_engine.build_plan(position, 1.0895, EURUSD, ATR)
    assert plan.action_available
    assert plan.suggested_stop < position.entry
    assert plan.r_multiple > 1.0
    # And it still ratchets downward only.
    position.stop_loss = plan.suggested_stop
    position.peak_r = plan.r_multiple
    later = risk_engine.build_plan(position, 1.0800, EURUSD, ATR)
    assert later.suggested_stop is None or later.suggested_stop <= plan.suggested_stop


# ── water marks and giveback ──────────────────────────────────

def test_giveback_is_reported_when_a_run_is_handed_back():
    position = make_position()
    position.peak_r = 3.0
    plan = risk_engine.build_plan(position, 1.1050, EURUSD, ATR)
    assert plan.giveback == pytest.approx(1 - 0.5 / 3.0, abs=0.01)
    assert any("given back" in note for note in plan.notes)


def test_deep_drawdown_before_a_winner_is_noted():
    position = make_position()
    position.trough_r = -0.85
    plan = risk_engine.build_plan(position, 1.1120, EURUSD, ATR)
    assert any("underwater" in note for note in plan.notes)


# ── broker sync ───────────────────────────────────────────────

def test_unmirrored_stop_is_flagged():
    """The most dangerous state: the screen says protected, the broker does
    not know about it."""
    position = make_position()
    position.stop_loss = 1.1050          # auto-protected here
    position.broker_stop = 1.0900        # still the original at the broker
    plan = risk_engine.build_plan(position, 1.1200, EURUSD, ATR)
    assert not plan.broker_synced
    assert "risking more" in plan.broker_warning


def test_synced_stop_raises_no_warning():
    position = make_position()
    position.stop_loss = position.broker_stop = 1.1050
    plan = risk_engine.build_plan(position, 1.1200, EURUSD, ATR)
    assert plan.broker_synced
    assert plan.broker_warning == ""


# ── account guardrails ────────────────────────────────────────

def closed(realised_r: float, when: float) -> Position:
    return Position(
        id="c", symbol="EURUSD", timeframe="15m", side=PositionSide.LONG,
        entry=1.1, stop_loss=1.09, initial_stop=1.09, opened_at=when,
        status="closed", closed_at=when, realised_r=realised_r)


def test_clear_when_nothing_is_wrong():
    report = guardrails.evaluate([], [], 100_000.0, 1.0)
    assert report.status is GuardrailStatus.CLEAR
    assert report.can_open_new


def test_daily_loss_limit_halts_trading():
    now = time.time()
    losses = [closed(-1.0, now), closed(-1.0, now), closed(-1.1, now)]
    report = guardrails.evaluate([], losses, 100_000.0, 1.0, now=now)
    assert report.status is GuardrailStatus.HALT
    assert not report.can_open_new
    assert "stop trading" in report.headline.lower()


def test_yesterdays_losses_do_not_halt_today():
    now = time.time()
    yesterday = now - 86_400 * 1.5
    losses = [closed(-1.0, yesterday) for _ in range(5)]
    report = guardrails.evaluate([], losses, 100_000.0, 1.0, now=now)
    daily = next(g for g in report.guardrails if g.key == "daily_loss")
    assert daily.status is GuardrailStatus.CLEAR


def test_consecutive_losses_halt_even_without_hitting_the_daily_limit():
    now = time.time()
    small = [closed(-0.3, now - 30), closed(-0.3, now - 20), closed(-0.3, now - 10)]
    report = guardrails.evaluate([], small, 100_000.0, 1.0, now=now)
    assert report.consecutive_losses == 3
    assert report.status is GuardrailStatus.HALT


def test_a_win_resets_the_streak():
    now = time.time()
    history = [closed(-1.0, now - 40), closed(-1.0, now - 30),
               closed(+2.0, now - 20), closed(-0.5, now - 10)]
    report = guardrails.evaluate([], history, 100_000.0, 1.0, now=now)
    assert report.consecutive_losses == 1


def test_open_risk_sums_across_positions():
    positions = [
        make_position(entry=1.1000, stop=1.0900, lots=1.0),
        make_position(entry=1.1000, stop=1.0950, lots=1.0),
    ]
    report = guardrails.evaluate(positions, [], 100_000.0, 1.0)
    open_rail = next(g for g in report.guardrails if g.key == "open_risk")
    # 100 pips + 50 pips at $10/pip on a standard lot = $1,500 against a
    # $1,000 per-trade budget.
    assert report.open_risk_amount == pytest.approx(1_500.0, abs=1.0)
    assert open_rail.value == pytest.approx(1.5, abs=0.01)


def test_a_protected_position_contributes_no_open_risk():
    """Once the stop is beyond entry the trade cannot lose, so it must not
    count against the open-risk budget."""
    position = make_position(entry=1.1000, stop=1.0900)
    position.stop_loss = 1.1100          # locked in profit
    report = guardrails.evaluate([position], [], 100_000.0, 1.0,
                                 prices={"EURUSD": 1.1200})
    assert report.open_risk_amount == 0.0


# ── cash figures ──────────────────────────────────────────────

def test_a_locked_trade_reports_no_cash_at_risk():
    """A stop beyond the entry cannot produce a loss. Reporting the
    entry-to-stop distance as `risk_amount` would label locked-in profit as
    exposure — the same mistake as counting unrealised gains as capital."""
    position = make_position(entry=1.1000, stop=1.0900, lots=1.0)
    position.stop_loss = 1.1200          # +2R locked
    position.peak_r = 4.5
    plan = risk_engine.build_plan(position, 1.1450, EURUSD, ATR)

    assert plan.state is ProtectionState.LOCKED
    assert plan.risk_amount == 0.0
    # 200 pips secured on a standard lot at $10/pip.
    assert plan.secured_amount == pytest.approx(2_000.0, abs=1.0)


def test_an_unprotected_trade_reports_the_full_risk():
    position = make_position(entry=1.1000, stop=1.0900, lots=1.0)
    plan = risk_engine.build_plan(position, 1.1030, EURUSD, ATR)
    assert plan.risk_amount == pytest.approx(1_000.0, abs=1.0)
    assert plan.secured_amount == 0.0


def test_risk_and_secured_are_never_both_positive():
    """They are opposite sides of the entry, so at most one can be non-zero."""
    position = make_position(entry=1.1000, stop=1.0900)
    for stop in (1.0850, 1.0900, 1.0999, 1.1000, 1.1001, 1.1300):
        position.stop_loss = stop
        position.peak_r = 5.0
        plan = risk_engine.build_plan(position, 1.1500, EURUSD, ATR)
        assert plan.risk_amount == 0.0 or plan.secured_amount == 0.0, (
            "stop {} reported both risk and secured".format(stop))
