"""What a round trip costs — one model for paper trading, the backtest, the
research simulator and the live limits.

The backtest used to charge nothing while the paper account charged spread
and commission, so the two disagreed by construction and the backtest was
the flattering one. All of them now read these numbers.

Costs belong to a venue, not to an asset, so every result records what it
was charged as. Everything Angel One trades is an exchange product, charged
by its segment's own schedule (`STATUTORY_PER_SIDE`); `EXCHANGE` is the name
that schedule goes by where a profile has to be named. A market that is not
an exchange segment is charged by a named `Profile` instead.

The statutory rates are averages per side for an intraday round trip on the
segment, rounded up; they move with the Finance Act and exchange circulars,
so treat them as a floor to keep current, not as a contract note.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional

from . import costs_india
from .instruments import Instrument

#: GST on brokerage and exchange charges (India).
GST = 0.18

#: Brokerage per side on the Indian segments: the discount brokers charge the
#: lower of a flat fee per order (about Rs 20) and 0.03% of turnover for
#: intraday equity and for futures. The percentage is used alone, so a large
#: order — where the flat fee is the smaller — is charged more than it would
#: be. Check it against your own contract note.
INDIA_BROKERAGE_RATE = 0.0003

#: Cash equity is charged more than that. Since 17 November 2025 Angel One
#: takes 0.1% of an equity order, delivery and intraday alike — at most Rs 20
#: and at least Rs 5. As a rate this is exact for an order between Rs 5,000
#: and Rs 20,000, too much above it (the cap) and too little below it (the
#: floor); `costs_india` prices the order itself and is what `charges` uses.
#: A backtest or a paper trade is charged this rate, so a large order is
#: over-charged rather than a small one let off.
EQUITY_BROKERAGE_RATE = 0.001

#: Slippage against you on a stop fill, in ticks. A stop is a market order
#: once triggered, and it fills where the book is.
STOP_SLIPPAGE_PIPS = 0.5

#: Per side, as a fraction of notional, for an intraday round trip:
#: (STT/CTT — charged on the sell side, so halved; exchange transaction
#: charge; SEBI fee; stamp duty — buy side, halved).
STATUTORY_PER_SIDE = {
    "stock": (0.000125, 0.0000297, 0.000001, 0.000015),          # equity intraday
    "index": (0.0001, 0.0000173, 0.000001, 0.00001),             # index futures
    "currency_fut": (0.0, 0.0000035, 0.000001, 0.0000005),       # no STT
    "commodity": (0.00005, 0.000021, 0.000001, 0.00001),         # non-agri futures
}


@dataclass(frozen=True)
class Profile:
    """A venue's charges for a market that is not an Indian exchange segment."""
    name: str
    #: Fee per side as a fraction of notional, before GST: crossing the
    #: spread, and resting in the book.
    taker: float
    maker: float
    gst: bool
    #: Financing charged per eight hours a position is held, as a fraction of
    #: notional. Charged against longs and shorts alike — an allowance, since
    #: which side pays changes with the market.
    funding_per_8h: float
    #: Slippage on a stop fill, as a fraction of price, on top of the tick.
    stop_slippage: float
    source: str
    #: Whether a position can be opened short there. Not on a spot book: you
    #: can only sell what you hold.
    shorting: bool = True


PROFILES: Dict[str, Profile] = {
    # A market with no schedule on file: a flat taker fee, no rebate.
    "generic": Profile(
        "generic", taker=0.0005, maker=0.0005, gst=False,
        funding_per_8h=0.0, stop_slippage=0.0, source="placeholder: 0.05% a side"),
}

#: "Whatever this market's own venue charges": the statutory schedule for an
#: exchange product, which is everything Angel One trades.
EXCHANGE = "exchange"

#: Kept for callers that only want "the usual taker fee".
COMMISSION_RATE = PROFILES["generic"].taker


def profile_for(instrument: Instrument, name: Optional[str] = None) -> Optional[Profile]:
    """The venue profile for this instrument, or None for an Indian exchange
    segment, whose charges are statutory and come from `STATUTORY_PER_SIDE`."""
    if instrument.asset_class in STATUTORY_PER_SIDE:
        return None
    return PROFILES["generic" if name in (None, EXCHANGE) else name]


def known(name: str) -> str:
    """A profile name a result can be charged under. Raises KeyError for any
    other, so a misspelt venue fails before anything is recorded under it."""
    if name != EXCHANGE:
        PROFILES[name]
    return name


def can_short(instrument: Instrument, profile: Optional[str] = None) -> bool:
    """Whether a short can be opened on this market at this venue. Indian
    exchange products traded intraday can be; a spot book cannot."""
    venue = profile_for(instrument, profile)
    return True if venue is None else venue.shorting


def per_side_rate(instrument: Instrument, passive: bool = False,
                  profile: Optional[str] = None) -> float:
    """Fees and statutory charges for one side, as a fraction of notional.
    `passive` is a resting order that the market came to — a maker fill
    where the venue prices those differently."""
    charges = STATUTORY_PER_SIDE.get(instrument.asset_class)
    if charges is None:
        venue = profile_for(instrument, profile)
        fee = venue.maker if passive else venue.taker
        return fee * (1.0 + GST) if venue.gst else fee
    transaction_tax, exchange, sebi, stamp = charges
    # GST is levied on brokerage and on the exchange and SEBI charges, not on
    # the transaction tax or the stamp duty.
    brokerage = (EQUITY_BROKERAGE_RATE if instrument.asset_class == "stock"
                 else INDIA_BROKERAGE_RATE)
    return (brokerage + exchange + sebi) * (1.0 + GST) + transaction_tax + stamp


def charges(instrument: Instrument, entry_value: float, exit_value: float) -> float:
    """Brokerage and statutory charges on a round trip of this size, in the
    account's currency. No spread and no slippage — a real fill already has
    both in its price. This is what the live limits subtract from a gross
    profit or loss; Angel One's order book reports fills, not charges.

    A cash-equity order is priced as the two orders it is (`costs_india`):
    its brokerage has a floor and a cap in rupees, which no rate per side
    can hold. The rate put a Rs 10,000 round trip at Rs 10.60; Angel One's
    own calculator says Rs 27.15, and the daily loss limit is enforced on
    this number. A live order is intraday. Which leg was the sale is not
    known here, so the dearer reading is the one returned.
    """
    if instrument.asset_class == "stock":
        rates = costs_india.schedule(costs_india.INTRADAY)
        return max(costs_india.round_trip_charges(rates, entry_value, [exit_value],
                                                  long=long).total
                   for long in (True, False))
    return (abs(entry_value) + abs(exit_value)) * per_side_rate(instrument)


def stop_slippage(instrument: Instrument, price: float,
                  profile: Optional[str] = None) -> float:
    """What a triggered stop gives up to the book, in price terms."""
    venue = profile_for(instrument, profile)
    proportional = abs(price) * venue.stop_slippage if venue else 0.0
    return instrument.from_pips(STOP_SLIPPAGE_PIPS) + proportional


def carry_cost(instrument: Instrument, price: float, seconds_held: float,
               profile: Optional[str] = None) -> float:
    """Financing for the time a position was held, in price terms per unit.
    Zero where the venue charges none (and for intraday exchange products)."""
    venue = profile_for(instrument, profile)
    if venue is None or not venue.funding_per_8h or seconds_held <= 0:
        return 0.0
    return abs(price) * venue.funding_per_8h * seconds_held / 28_800.0


def round_trip_cost(instrument: Instrument, entry: float, exit_price: float,
                    stopped: bool = False, entry_passive: bool = False,
                    exit_passive: bool = False, profile: Optional[str] = None,
                    seconds_held: float = 0.0) -> float:
    """Cost of one unit, in price terms: half the spread for each side that
    crossed it, fees on both notionals, slippage if the exit was a stop, and
    financing for the time held.

    A side is *passive* when it was a resting limit order that the market
    came to — it trades at its own price and pays no spread. What it pays
    instead is adverse selection, and that is not a number to add here: it
    is in which orders get filled at all, which the caller has to model.
    """
    half_spread = instrument.from_pips(instrument.typical_spread_pips) / 2.0
    crossings = (0 if entry_passive else 1) + (0 if exit_passive else 1)
    fees = (abs(entry) * per_side_rate(instrument, entry_passive, profile)
            + abs(exit_price) * per_side_rate(instrument, exit_passive, profile))
    slippage = stop_slippage(instrument, exit_price, profile) if stopped else 0.0
    carry = carry_cost(instrument, entry, seconds_held, profile)
    return half_spread * crossings + fees + slippage + carry


def describe(instrument: Instrument, profile: Optional[str] = None) -> Dict[str, object]:
    """The assumptions a result was charged by, for its record."""
    venue = profile_for(instrument, profile)
    out: Dict[str, object] = {
        "taker_per_side": per_side_rate(instrument, False, profile),
        "maker_per_side": per_side_rate(instrument, True, profile),
        "half_spread": instrument.from_pips(instrument.typical_spread_pips) / 2.0,
    }
    if venue is None:
        out.update(profile="india:" + instrument.asset_class,
                   source="statutory rates, see STATUTORY_PER_SIDE")
    else:
        out.update(profile=venue.name, funding_per_8h=venue.funding_per_8h,
                   stop_slippage=venue.stop_slippage, shorting=venue.shorting,
                   source=venue.source)
    return out
