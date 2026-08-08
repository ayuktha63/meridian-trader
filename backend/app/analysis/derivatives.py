"""Turning a directional call into a contract you can actually place.

A signal that says "long NIFTY at 24,500, stop 24,400" is not yet an order.
The trader has to choose between the future and an option, pick a strike and an
expiry, and know what the position costs and what it risks. That translation is
what this module does.

It is arithmetic and convention, not prediction — and it is deliberately blunt
about the parts that lose money quietly:

* an option's stop is in *premium*, and premium does not move one-for-one with
  the underlying, so the underlying stop has to be converted through delta;
* theta charges rent every day whether you are right or not, and the closer to
  expiry the higher the rent;
* the maximum loss on a bought option is the whole premium, which is smaller
  than the futures loss in absolute terms but is a *100% loss* far more often.

Greeks are approximated with Black-Scholes on an implied vol inferred from
recent realised volatility. Real IV comes from the option chain and will differ
— often a lot around events. Treat the premium here as an order of magnitude,
not a quote.
"""
from __future__ import annotations

import math
from datetime import date, datetime, timedelta
from typing import List, Optional

from ..instruments import Instrument
from ..schemas.derivatives import (
    DerivativeSuggestion, ExpiryChoice, OptionLeg,
)

#: Annual risk-free rate used in the pricing model. The Indian 10y sits around
#: here; the sensitivity of a near-dated option's premium to this is tiny.
RISK_FREE = 0.065

TRADING_DAYS = 252


# ── expiry arithmetic ─────────────────────────────────────────

def _next_weekday(from_date: date, weekday: int) -> date:
    """Next occurrence of `weekday` (0=Mon) on or after `from_date`."""
    delta = (weekday - from_date.weekday()) % 7
    return from_date + timedelta(days=delta)


def _monthly_expiry(year: int, month: int, weekday: int) -> date:
    """Last `weekday` of the month — the NSE monthly expiry convention."""
    if month == 12:
        first_next = date(year + 1, 1, 1)
    else:
        first_next = date(year, month + 1, 1)
    last = first_next - timedelta(days=1)
    while last.weekday() != weekday:
        last -= timedelta(days=1)
    return last


def expiries(instrument: Instrument, today: Optional[date] = None) -> List[ExpiryChoice]:
    """Upcoming expiries for an instrument.

    Weekly expiries exist only for the indices; stocks are monthly. The
    weekday differs by product and the exchange has changed it more than once,
    so this is the one place to correct if the NSE moves it again.
    """
    today = today or date.today()
    out: List[ExpiryChoice] = []

    if instrument.asset_class == "index":
        # NIFTY weeklies expire Thursday; BANKNIFTY weeklies were discontinued
        # in Nov 2024, so only NIFTY gets the weekly line.
        if instrument.symbol == "NIFTY":
            weekly = _next_weekday(today, 3)
            if weekly == today:
                weekly = _next_weekday(today + timedelta(days=1), 3)
            out.append(ExpiryChoice(
                date=weekly.isoformat(), kind="weekly",
                days_to_expiry=(weekly - today).days,
                note="Cheapest premium and the fastest theta burn. Only for "
                     "moves you expect to complete this week.",
            ))

    monthly_weekday = 3  # Thursday
    this_month = _monthly_expiry(today.year, today.month, monthly_weekday)
    if this_month <= today:
        nxt = today.replace(day=1) + timedelta(days=32)
        this_month = _monthly_expiry(nxt.year, nxt.month, monthly_weekday)
    out.append(ExpiryChoice(
        date=this_month.isoformat(), kind="monthly",
        days_to_expiry=(this_month - today).days,
        note="The liquid contract for most swing trades. Roll before the last "
             "week if the move has not started.",
    ))

    follow = this_month + timedelta(days=20)
    next_month = _monthly_expiry(follow.year, follow.month, monthly_weekday)
    out.append(ExpiryChoice(
        date=next_month.isoformat(), kind="next-month",
        days_to_expiry=(next_month - today).days,
        note="Slowest decay, widest spread. Worth it when the thesis needs "
             "room to be early.",
    ))
    return out


# ── strikes ───────────────────────────────────────────────────

def strike_step(price: float, asset_class: str) -> float:
    """The strike interval the exchange actually lists."""
    if asset_class == "index":
        return 50.0 if price < 30_000 else 100.0
    if price < 100:
        return 2.5
    if price < 250:
        return 5.0
    if price < 500:
        return 10.0
    if price < 1_000:
        return 20.0
    if price < 2_500:
        return 50.0
    if price < 5_000:
        return 100.0
    if price < 10_000:
        return 250.0
    return 500.0


def _round_to(value: float, step: float) -> float:
    return round(round(value / step) * step, 2)


# ── Black-Scholes ─────────────────────────────────────────────

def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def black_scholes(spot: float, strike: float, days: float, vol: float,
                  call: bool) -> dict:
    """Premium and the greeks that matter for a directional trade."""
    t = max(days, 0.5) / 365.0
    vol = max(vol, 0.05)
    if spot <= 0 or strike <= 0:
        return {"premium": 0.0, "delta": 0.0, "theta": 0.0, "vega": 0.0}

    d1 = (math.log(spot / strike) + (RISK_FREE + 0.5 * vol * vol) * t) / (vol * math.sqrt(t))
    d2 = d1 - vol * math.sqrt(t)
    discount = math.exp(-RISK_FREE * t)

    if call:
        premium = spot * _norm_cdf(d1) - strike * discount * _norm_cdf(d2)
        delta = _norm_cdf(d1)
        theta = (-spot * _norm_pdf(d1) * vol / (2 * math.sqrt(t))
                 - RISK_FREE * strike * discount * _norm_cdf(d2))
    else:
        premium = strike * discount * _norm_cdf(-d2) - spot * _norm_cdf(-d1)
        delta = _norm_cdf(d1) - 1.0
        theta = (-spot * _norm_pdf(d1) * vol / (2 * math.sqrt(t))
                 + RISK_FREE * strike * discount * _norm_cdf(-d2))

    return {
        "premium": max(premium, 0.05),
        "delta": delta,
        "theta": theta / 365.0,            # per calendar day
        "vega": spot * _norm_pdf(d1) * math.sqrt(t) / 100.0,
    }


def implied_vol_from_atr(atr: float, price: float, bar_seconds: int,
                         hours_per_year: float) -> float:
    """Annualised vol inferred from ATR.

    Two conversions, both easy to get wrong:

    * ATR is a mean **range**, not a standard deviation. For a Brownian path
      E[high − low] ≈ 1.6σ, so σ ≈ 0.62 × range.
    * The result is per *bar*, and scaling it to a year needs the number of
      bars in a year — not `sqrt(252)`. Annualising a 15-minute ATR as if it
      were daily understates vol by more than an order of magnitude, which
      collapses every premium the model quotes.

    Real IV additionally carries an event premium this cannot see, which is why
    the output is labelled an estimate everywhere it surfaces.
    """
    if price <= 0 or bar_seconds <= 0:
        return 0.20
    per_bar = (atr / price) * 0.62
    bars_per_year = hours_per_year * 3600.0 / bar_seconds
    return max(0.05, min(per_bar * math.sqrt(bars_per_year), 2.5))


#: Hours the market is actually open in a year — the denominator that turns a
#: per-bar sigma into an annual one. Crypto never closes; NSE equity and F&O
#: run 09:15-15:30 (6.25h) over ~252 sessions; MCX runs to 23:30.
def _hours_per_year(instrument: Instrument) -> float:
    if instrument.asset_class == "crypto":
        return 365.0 * 24.0
    if instrument.venue == "MCX":
        return 252.0 * 14.0
    if instrument.venue == "CDS":
        return 252.0 * 8.0
    if instrument.asset_class in ("fx", "metal"):
        return 252.0 * 24.0
    return 252.0 * 6.25


# ── the suggestion ────────────────────────────────────────────

def suggest(instrument: Instrument, direction: str, entry: float, stop: float,
            target: Optional[float], atr: float, bar_seconds: int = 900,
            risk_amount: Optional[float] = None,
            today: Optional[date] = None) -> Optional[DerivativeSuggestion]:
    """Express a cash-market call as a futures or options position.

    Returns None for instruments with no listed derivative.
    """
    if instrument.venue not in ("NFO", "CDS", "MCX"):
        return None
    if direction not in ("long", "short") or entry <= 0:
        return None

    today = today or date.today()
    choices = expiries(instrument, today)
    if not choices:
        return None
    # The monthly is the default: weeklies decay too fast for a swing thesis
    # and next-month costs spread. Select it by kind — indexing into the list
    # silently picks the wrong contract for stocks, which have no weekly.
    preferred = next((c for c in choices if c.kind == "monthly"), choices[0])

    risk_points = abs(entry - stop)
    lot = instrument.contract_size
    vol = implied_vol_from_atr(atr, entry, bar_seconds,
                               _hours_per_year(instrument))
    long_side = direction == "long"

    suggestion = DerivativeSuggestion(
        symbol=instrument.symbol,
        underlying_price=round(entry, instrument.digits),
        direction=direction,
        lot_size=lot,
        expiries=choices,
        implied_vol_estimate=round(vol * 100, 1),
    )

    # ── the future ────────────────────────────────────────────
    suggestion.futures_symbol = "{}{}FUT".format(
        instrument.broker_symbol or instrument.symbol,
        datetime.strptime(preferred.date, "%Y-%m-%d").strftime("%y%b").upper())
    suggestion.futures_expiry = preferred.date
    suggestion.futures_risk_per_lot = round(risk_points * lot, 2)
    suggestion.futures_reward_per_lot = (
        round(abs((target or entry) - entry) * lot, 2) if target else None)
    if risk_amount and suggestion.futures_risk_per_lot > 0:
        suggestion.futures_lots = max(
            0, int(risk_amount // suggestion.futures_risk_per_lot))
    suggestion.futures_notes = [
        "One lot carries ₹{:,.0f} of risk to your stop, and futures losses are "
        "not capped — the position can lose more than the margin posted."
        .format(suggestion.futures_risk_per_lot),
        "Margin is roughly 15-20% of notional (₹{:,.0f} here), and the broker "
        "will square you off intraday if it falls short."
        .format(entry * lot * 0.18),
    ]
    if suggestion.futures_lots == 0 and risk_amount:
        suggestion.futures_notes.append(
            "At ₹{:,.0f} of risk per trade you cannot afford even one lot. "
            "That is the honest answer, not a rounding problem — options or a "
            "smaller instrument are the alternatives.".format(risk_amount))

    # ── the options ───────────────────────────────────────────
    step = strike_step(entry, instrument.asset_class)
    atm = _round_to(entry, step)
    call = long_side

    # ITM / ATM / OTM, one step apart. Anything further out is a lottery
    # ticket rather than a trade expression.
    ladder = [
        (atm - step if call else atm + step, "ITM"),
        (atm, "ATM"),
        (atm + step if call else atm - step, "OTM"),
    ]

    for strike, moneyness in ladder:
        if strike <= 0:
            continue
        greeks = black_scholes(entry, strike, preferred.days_to_expiry, vol, call)
        delta = abs(greeks["delta"])
        premium = greeks["premium"]
        # The underlying stop converted into premium via delta. This is the
        # number people get wrong: a 40-point stop on the index is not a
        # 40-point stop on the option.
        premium_stop = max(0.05, premium - risk_points * delta)
        premium_target = (premium + abs((target or entry) - entry) * delta
                          if target else None)
        cost = premium * lot

        leg = OptionLeg(
            strike=strike,
            option_type="CE" if call else "PE",
            moneyness=moneyness,
            expiry=preferred.date,
            days_to_expiry=preferred.days_to_expiry,
            estimated_premium=round(premium, 2),
            delta=round(greeks["delta"], 3),
            theta_per_day=round(greeks["theta"], 2),
            vega=round(greeks["vega"], 3),
            cost_per_lot=round(cost, 2),
            max_loss_per_lot=round(cost, 2),
            premium_stop=round(premium_stop, 2),
            premium_target=round(premium_target, 2) if premium_target else None,
            breakeven=round(strike + premium if call else strike - premium, 2),
            theta_pct_per_day=round(abs(greeks["theta"]) / premium * 100, 2)
            if premium > 0 else 0.0,
        )
        leg.contract = "{} {} {} {}".format(
            instrument.broker_symbol or instrument.symbol,
            datetime.strptime(preferred.date, "%Y-%m-%d").strftime("%d%b").upper(),
            int(strike) if strike == int(strike) else strike,
            leg.option_type)

        if risk_amount and cost > 0:
            # For a bought option the honest risk unit is the premium at the
            # stop, not the whole premium — you exit at the stop like any
            # other trade rather than holding to zero.
            per_lot_risk = (premium - premium_stop) * lot
            leg.suggested_lots = max(0, int(risk_amount // per_lot_risk)) \
                if per_lot_risk > 0 else 0

        if moneyness == "ITM":
            leg.note = ("Highest delta, so it tracks the underlying most closely "
                        "and decays slowest in percentage terms. Costs the most.")
        elif moneyness == "ATM":
            leg.note = ("The balanced choice and usually the most liquid. Delta "
                        "near 0.5 means roughly half the underlying's move.")
        else:
            leg.note = ("Cheapest and the one most likely to expire worthless. "
                        "Only sensible when you expect a fast, large move.")

        suggestion.options.append(leg)

    # The ATM leg is the default recommendation; it is the liquidity point.
    suggestion.recommended = next(
        (leg.contract for leg in suggestion.options if leg.moneyness == "ATM"), None)

    atm_leg = next((leg for leg in suggestion.options if leg.moneyness == "ATM"), None)
    if atm_leg:
        suggestion.notes = [
            "Buying the {} costs ₹{:,.0f} per lot, and that whole amount is at "
            "risk if you hold it to expiry out of the money."
            .format(atm_leg.contract, atm_leg.cost_per_lot),
            "Your {:.0f}-point stop on the underlying is a ₹{:.2f} stop on the "
            "premium (delta {:.2f}) — set the alert on the premium, because "
            "that is what your broker fills."
            .format(risk_points, atm_leg.estimated_premium - atm_leg.premium_stop,
                    abs(atm_leg.delta)),
            "Theta costs about ₹{:.2f} per day, {:.1f}% of the premium. Over the "
            "{} days to expiry that is the hurdle the move has to clear before "
            "you make anything."
            .format(abs(atm_leg.theta_per_day), atm_leg.theta_pct_per_day,
                    preferred.days_to_expiry),
            "Break-even at expiry is {:.2f} — the underlying has to travel "
            "{:.2f} points, not just move in your favour."
            .format(atm_leg.breakeven, abs(atm_leg.breakeven - entry)),
        ]

    # A zero here is a real answer, not a rounding artefact, and it is the most
    # important thing on the panel when it happens.
    if risk_amount and atm_leg and not atm_leg.suggested_lots:
        cheapest = min((leg for leg in suggestion.options),
                       key=lambda leg: (leg.estimated_premium - leg.premium_stop) * lot)
        needed = (cheapest.estimated_premium - cheapest.premium_stop) * lot
        suggestion.notes.insert(0, (
            "Your risk budget is {:,.0f} per trade, and the smallest position "
            "here risks {:,.0f} to its stop. You cannot take this trade at your "
            "current sizing — that is the honest answer. Raise the account "
            "balance in settings if it is wrong, or trade a smaller instrument."
        ).format(risk_amount, needed))

    suggestion.disclaimer = (
        "Premiums and greeks are modelled from realised volatility, not quoted "
        "from the live option chain. Real IV differs, especially near results "
        "and policy dates. Check the actual chain in your broker before "
        "placing anything, and verify the lot size — the exchange revises it."
    )
    return suggestion
