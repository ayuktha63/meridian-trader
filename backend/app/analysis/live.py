"""The fast tier: per-tick position evaluation.

The platform runs on two clocks, deliberately.

* **Structural tier** — the full engine (indicators, market structure, patterns,
  MTF, Claude). Expensive, and its inputs only change meaningfully on a bar
  close. Running it every second would burn money and tell you nothing new.
* **Live tier — this module.** Pure price arithmetic against an open position:
  where is price relative to the stop and the target, right now. It costs
  microseconds, so it runs on every tick.

Splitting them is what makes a one-second exit alert possible without making a
one-second API call. The alerts that need to be instant are *price-level*
alerts, and those need no analysis at all — only the levels you already set.
"""
from __future__ import annotations

from typing import Optional

from ..instruments import Instrument
from ..schemas.positions import LiveTick, Position, PositionSide
from . import risk as risk_engine

#: Fraction of the distance from entry to the level at which we start warning.
#: 0.85 means "price has covered 85% of the way to the stop".
APPROACH_THRESHOLD = 0.85


def evaluate_tick(position: Position, price: float, instrument: Instrument,
                  atr: Optional[float] = None,
                  spread_pips: Optional[float] = None) -> Optional[LiveTick]:
    """Where does an open position stand at this exact price?

    When `atr` is supplied the protection ladder is evaluated too, so a rung
    arming is detected on the tick that arms it rather than on the next
    structural pass. That matters: the whole point of protection is to be in
    place *before* the reversal, and a reversal does not wait for a bar close.
    """
    long = position.side == PositionSide.LONG
    entry = position.entry
    stop = position.stop_loss
    target = position.take_profit

    # 1R is the *original* stop distance. Deriving it from the live stop
    # breaks the moment protection kicks in: at breakeven `entry - stop` is
    # zero, so R goes infinite and the position stops being trackable at
    # exactly the point the trader most wants to watch it.
    risk = position.risk_unit
    if risk <= 0:
        return None

    move = (price - entry) if long else (entry - price)
    unrealised_pips = instrument.to_pips(move)
    tick = LiveTick(
        position_id=position.id,
        symbol=position.symbol,
        price=round(price, instrument.digits),
        r_multiple=round(move / risk, 3),
        unrealised_pips=round(unrealised_pips, 1),
        unrealised_pnl=round(unrealised_pips * instrument.pip_value_per_lot * position.lots, 2),
        distance_to_stop_pips=round(instrument.to_pips(abs(price - stop)), 1),
    )
    if target is not None:
        tick.distance_to_target_pips = round(instrument.to_pips(abs(target - price)), 1)

    # ── protection ladder ────────────────────────────────────
    # Pure arithmetic against levels already known, so it belongs on this tier
    # rather than waiting for the structural pass.
    if atr is not None:
        plan = risk_engine.build_plan(position, price, instrument, atr, spread_pips)
        if plan is not None:
            tick.protection_state = plan.state.value
            tick.locked_r = plan.locked_r
            tick.peak_r = plan.peak_r
            tick.trough_r = plan.trough_r
            tick.suggested_stop = plan.suggested_stop
            tick.protection_tier = next(
                (t.index for t in plan.tiers if t.active), position.protection_tier)
            # A newly armed rung is news; one already taken is not.
            if plan.action_available and tick.protection_tier > position.protection_tier:
                tick.alert = "protect"
                tick.urgency = "elevated"
                tick.headline = plan.headline
                tick.detail = plan.suggested_reason
                tick.suggested_stop = plan.suggested_stop

    # ── terminal: the level is gone ──────────────────────────
    stop_breached = (price <= stop) if long else (price >= stop)
    if stop_breached:
        tick.alert = "stop_hit"
        tick.urgency = "immediate"
        tick.headline = "STOP HIT — you should be out"
        tick.detail = (
            "Price is at {:.{d}f}, through your stop at {:.{d}f}. If your broker "
            "stop was resting in the book you are already flat. If it was mental, "
            "close it now.".format(price, stop, d=instrument.digits))
        return tick

    if target is not None:
        target_reached = (price >= target) if long else (price <= target)
        if target_reached:
            tick.alert = "target_hit"
            tick.urgency = "immediate"
            tick.headline = "TARGET HIT — take it"
            tick.detail = (
                "Price reached {:.{d}f}. This is the level the trade was taken for; "
                "everything past it is a new trade.".format(target, d=instrument.digits))
            return tick

    # ── approaching a level ──────────────────────────────────
    travelled_to_stop = abs(entry - price) / risk if risk else 0.0
    moving_to_stop = (price < entry) if long else (price > entry)
    if moving_to_stop and travelled_to_stop >= APPROACH_THRESHOLD:
        tick.alert = "approaching_stop"
        tick.urgency = "elevated"
        tick.headline = "Approaching your stop"
        tick.detail = (
            "{:.1f} pips from {:.{d}f}. Do not move it — that is how a planned "
            "loss becomes an unplanned one.".format(
                tick.distance_to_stop_pips, stop, d=instrument.digits))
        return tick

    if target is not None:
        reward = abs(target - entry)
        travelled_to_target = abs(price - entry) / reward if reward else 0.0
        moving_to_target = (price > entry) if long else (price < entry)
        if moving_to_target and travelled_to_target >= APPROACH_THRESHOLD:
            tick.alert = "approaching_target"
            tick.urgency = "elevated"
            tick.headline = "Approaching your target"
            tick.detail = (
                "{:.1f} pips from {:.{d}f} at {:+.2f}R. Decide now whether you take "
                "it or trail — not after it reverses.".format(
                    tick.distance_to_target_pips, target, tick.r_multiple,
                    d=instrument.digits))
            return tick

    return tick
