"""The protection ladder — moving risk off the table as a trade works.

A trade starts fully exposed: a stop-out costs 1R. As it runs in your favour
the stop ratchets up behind it, and at some point the trade becomes *free* —
a stop-out costs nothing. Past that, every rung books a profit even if the
market turns straight around. The ladder never unwinds; a stop only ever
tightens.

Three things this gets right that a naive "move to breakeven at +1R" does not:

**Breakeven is not the entry price.** You paid the spread getting in and you
will pay it getting out. A stop at exactly the entry books a small *loss*. The
breakeven rung sits at entry plus the round-trip cost, so "free trade" means
free.

**Moving too early destroys expectancy.** Being stopped at breakeven on a trade
that would have worked is not a neutral outcome — it is a winner converted to a
scratch, and doing it habitually is one of the more expensive retail mistakes.
So every rung is gated on *both* an R multiple and an ATR distance. Price has
to be genuinely clear of the noise band before the stop comes up behind it.

**The risk unit is the original stop.** Once the stop trails, `entry - stop` is
no longer 1R — at breakeven it is zero. Every R figure here is measured against
`position.initial_stop`, which never changes.

Nothing in this module talks to a broker. It computes where the stop *should*
be; moving it in the market is the trader's job, and the plan says so loudly
whenever the two have drifted apart.
"""
from __future__ import annotations

from typing import List, Optional

from ..instruments import Instrument
from ..schemas.positions import Position, PositionSide
from ..schemas.risk import ProtectionPlan, ProtectionState, ProtectionTier

#: The rungs, as (name, trigger in R, R secured once taken, note).
#:
#: The gap between trigger and lock is deliberate and is the whole design: at
#: +1.6R the stop moves to +0.5R, not to +1.6R. A stop parked right under the
#: price gets hit by ordinary noise, and a trade stopped at +1.5R that was
#: going to +4R is a worse outcome than one stopped at +0.5R.
LADDER: List[tuple] = [
    ("Breakeven", 1.0, 0.0,
     "The trade is free. A stop-out from here costs nothing but the commission."),
    ("Lock ½R", 1.6, 0.5,
     "Half a risk unit is banked. The worst case is now a small win."),
    ("Lock 1R", 2.5, 1.0,
     "A full risk unit is secured — one loss elsewhere is already paid for."),
    ("Lock 2R", 4.0, 2.0,
     "Two units banked. Let the rest run on the structural trail."),
]

#: A rung only arms when price is also this many ATR clear of the stop it would
#: move to. Without it, a tight-stopped scalp arms every rung on one candle and
#: the position is stopped on the first pullback.
MIN_ATR_CLEARANCE = 1.0

#: Below this, the instrument's own noise is wider than the whole trade and
#: laddering is meaningless.
MIN_RISK_ATR = 0.25


def round_trip_cost(instrument: Instrument, spread_pips: Optional[float] = None) -> float:
    """Price distance a round trip costs — spread in, spread out.

    Used to place the breakeven rung. Commission is not modelled per-instrument
    here, so this is a floor on the true cost rather than the whole of it.
    """
    pips = spread_pips if spread_pips is not None else instrument.typical_spread_pips
    return instrument.from_pips(max(pips, 0.0) * 2.0)


def build_plan(position: Position, price: float, instrument: Instrument,
               atr: float, spread_pips: Optional[float] = None,
               account_balance: float = 0.0) -> Optional[ProtectionPlan]:
    """Where the stop should be, given how far this trade has run."""
    long = position.side == PositionSide.LONG
    entry = position.entry
    # The risk unit is the *original* stop. Falling back to the live stop is
    # only for rows written before it was persisted.
    initial_stop = position.initial_stop or position.stop_loss
    risk = abs(entry - initial_stop)
    if risk <= 0:
        return None

    atr = max(atr, 1e-9)
    move = (price - entry) if long else (entry - price)
    r = move / risk

    plan = ProtectionPlan(
        position_id=position.id,
        initial_stop=round(initial_stop, instrument.digits),
        current_stop=round(position.stop_loss, instrument.digits),
        r_multiple=round(r, 3),
        peak_r=round(max(position.peak_r, r), 3),
        trough_r=round(min(position.trough_r, r), 3),
    )

    cost = round_trip_cost(instrument, spread_pips)
    breakeven = entry + cost if long else entry - cost

    # ── build the rungs ───────────────────────────────────────
    for index, (name, trigger_r, locked_r, note) in enumerate(LADDER, start=1):
        stop = (breakeven + locked_r * risk) if long else (breakeven - locked_r * risk)
        tier = ProtectionTier(
            index=index, name=name, trigger_r=trigger_r, locked_r=locked_r,
            stop=round(stop, instrument.digits), note=note,
        )
        # A rung is reached when the trade has run far enough in R *and* price
        # is clear of the stop that rung would set.
        clearance = abs(price - stop)
        tier.reached = (plan.peak_r >= trigger_r
                        and clearance >= MIN_ATR_CLEARANCE * atr)
        plan.tiers.append(tier)

    if risk < MIN_RISK_ATR * atr:
        plan.notes.append(
            "Your stop is only {:.2f} ATR wide. That is inside the instrument's "
            "normal noise, so laddering will scratch the trade before it can "
            "work — widen the stop on the *next* trade rather than this one."
            .format(risk / atr))

    reached = [t for t in plan.tiers if t.reached]
    active = reached[-1] if reached else None

    # ── where should the stop be ──────────────────────────────
    if active is not None:
        active.active = True
        plan.locked_r = active.locked_r
        candidate = active.stop
        tighter = (candidate > position.stop_loss) if long else (candidate < position.stop_loss)
        if tighter:
            plan.suggested_stop = candidate
            plan.action_available = True
            plan.suggested_reason = (
                "{} armed at +{:.1f}R. Moving the stop to {:.{d}f} secures "
                "{}.".format(active.name, active.trigger_r, candidate,
                             "the trade against loss" if active.locked_r == 0
                             else "+{:.1f}R".format(active.locked_r),
                             d=instrument.digits))
    else:
        # Still fully exposed — the locked R is whatever the current stop
        # implies, which is negative.
        current_locked = ((position.stop_loss - entry) if long
                          else (entry - position.stop_loss)) / risk
        plan.locked_r = round(current_locked, 3)

    # ── state ─────────────────────────────────────────────────
    stop_at_or_past_be = ((position.stop_loss >= breakeven) if long
                          else (position.stop_loss <= breakeven))
    if stop_at_or_past_be:
        beyond = abs(position.stop_loss - breakeven) > 1e-12
        plan.state = ProtectionState.LOCKED if beyond else ProtectionState.SAFE
    elif plan.action_available:
        plan.state = ProtectionState.ARMED
    else:
        plan.state = ProtectionState.AT_RISK

    # ── the next rung ─────────────────────────────────────────
    pending = [t for t in plan.tiers if not t.reached]
    if pending:
        nxt = pending[0]
        plan.next_tier = nxt
        needed_price = (entry + nxt.trigger_r * risk) if long else (entry - nxt.trigger_r * risk)
        plan.to_next_tier_pips = round(
            instrument.to_pips(abs(needed_price - price)), 1)

    # ── cash figures ──────────────────────────────────────────
    # Both are measured from the entry and clamped at zero, so they are never
    # two names for the same distance. A stop beyond the entry produces a
    # profit if hit, so `risk_amount` is genuinely nothing — reporting the
    # entry-to-stop distance there would label locked-in profit as exposure.
    tick_value = instrument.pip_value_per_lot * position.lots
    loss_distance = max(0.0, (entry - position.stop_loss) if long
                        else (position.stop_loss - entry))
    gain_distance = max(0.0, (position.stop_loss - entry) if long
                        else (entry - position.stop_loss))
    plan.risk_amount = round(instrument.to_pips(loss_distance) * tick_value, 2)
    plan.secured_amount = round(instrument.to_pips(gain_distance) * tick_value, 2)

    if plan.peak_r > 0:
        plan.giveback = round(max(0.0, (plan.peak_r - r) / plan.peak_r), 3)

    _describe(plan, instrument, active)
    _check_broker_sync(plan, position, instrument, long)
    return plan


def _describe(plan: ProtectionPlan, instrument: Instrument,
              active: Optional[ProtectionTier]) -> None:
    d = instrument.digits
    if plan.state is ProtectionState.LOCKED:
        plan.headline = "PROFIT LOCKED · +{:.1f}R secured".format(plan.locked_r)
        plan.detail = (
            "Your stop at {:.{d}f} is beyond breakeven. If the market reverses "
            "from here you still book +{:.1f}R.".format(
                plan.current_stop, plan.locked_r, d=d))
    elif plan.state is ProtectionState.SAFE:
        plan.headline = "SAFE · this trade cannot lose"
        plan.detail = (
            "Your stop at {:.{d}f} covers the entry plus the round-trip cost. "
            "The worst case is now a scratch.".format(plan.current_stop, d=d))
    elif plan.state is ProtectionState.ARMED and active is not None:
        plan.headline = "PROTECTION AVAILABLE · {}".format(active.name)
        plan.detail = plan.suggested_reason
    else:
        plan.headline = "AT RISK · full {:.0f} exposure".format(1.0)
        if plan.next_tier is not None:
            plan.detail = (
                "Nothing is protected yet. {} arms at +{:.1f}R — {:.1f} pips "
                "away.".format(plan.next_tier.name, plan.next_tier.trigger_r,
                               plan.to_next_tier_pips or 0.0))
        else:
            plan.detail = "Nothing is protected yet."

    if plan.giveback >= 0.5 and plan.peak_r >= 1.0:
        plan.notes.append(
            "This trade peaked at +{:.1f}R and has given back {:.0f}% of it. "
            "Handing back more than half a run is usually a sign the move is "
            "over.".format(plan.peak_r, plan.giveback * 100))

    if plan.trough_r <= -0.7 and plan.r_multiple > 0:
        plan.notes.append(
            "It was {:.1f}R underwater before it worked. That is close enough "
            "to the stop that it was largely luck — worth noting in the journal "
            "rather than reading as skill.".format(plan.trough_r))


def _check_broker_sync(plan: ProtectionPlan, position: Position,
                       instrument: Instrument, long: bool) -> None:
    """Flag the gap between the tracked stop and the one actually resting."""
    plan.broker_stop = position.broker_stop
    if position.broker_stop is None:
        # Never confirmed. Assume the entry stop is what is resting, which is
        # the safe assumption and matches how most people trade.
        plan.broker_synced = abs(position.stop_loss - position.initial_stop) < 1e-12
        if not plan.broker_synced:
            plan.broker_warning = (
                "The stop here has moved to {:.{d}f}, but you have not confirmed "
                "moving it at your broker. Until you do, your real protection is "
                "still {:.{d}f} — this panel is showing you a plan, not a fact."
                .format(position.stop_loss, position.initial_stop,
                        d=instrument.digits))
        return

    plan.broker_synced = abs(position.broker_stop - position.stop_loss) < 1e-12
    if not plan.broker_synced:
        worse = ((position.broker_stop < position.stop_loss) if long
                 else (position.broker_stop > position.stop_loss))
        plan.broker_warning = (
            "Your broker stop is at {:.{d}f}; this panel is managing to "
            "{:.{d}f}. {}".format(
                position.broker_stop, position.stop_loss,
                "You are risking more than the screen says." if worse
                else "Your broker is tighter than the plan.",
                d=instrument.digits))
