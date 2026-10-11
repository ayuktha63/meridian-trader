"""What a round trip costs — one model for paper trading and the backtest.

The backtest used to charge nothing while the paper account charged spread
and commission, so the two disagreed by construction and the backtest was
the flattering one. Both now read these numbers.

The statutory rates are averages per side for an intraday round trip on the
segment, rounded up; they move with the Finance Act and exchange circulars,
so treat them as a floor to keep current, not as a contract note.
"""
from __future__ import annotations

from .instruments import Instrument

#: Taker fee per side, as a fraction of notional, where nothing more specific
#: is known. CoinDCX charges 0.05% on the USDT books at the default tier.
COMMISSION_RATE = 0.0005

#: Brokerage per side on the Indian segments: the discount brokers charge the
#: lower of a flat fee per order (about Rs 20) and 0.03% of turnover for
#: intraday equity and for futures. The percentage is used alone, so a large
#: order — where the flat fee is the smaller — is charged more than it would
#: be. Check it against your own contract note.
INDIA_BROKERAGE_RATE = 0.0003

#: Slippage against you on a stop fill, in ticks. A stop is a market order
#: once triggered, and it fills where the book is.
STOP_SLIPPAGE_PIPS = 0.5

#: GST on brokerage and exchange charges (India).
GST = 0.18

#: Per side, as a fraction of notional, for an intraday round trip:
#: (STT/CTT — charged on the sell side, so halved; exchange transaction
#: charge; SEBI fee; stamp duty — buy side, halved).
STATUTORY_PER_SIDE = {
    "stock": (0.000125, 0.0000297, 0.000001, 0.000015),          # equity intraday
    "index": (0.0001, 0.0000173, 0.000001, 0.00001),             # index futures
    "currency_fut": (0.0, 0.0000035, 0.000001, 0.0000005),       # no STT
    "commodity": (0.00005, 0.000021, 0.000001, 0.00001),         # non-agri futures
}


def per_side_rate(instrument: Instrument) -> float:
    """Brokerage plus statutory charges for one side, as a fraction of notional."""
    charges = STATUTORY_PER_SIDE.get(instrument.asset_class)
    if charges is None:
        return COMMISSION_RATE
    transaction_tax, exchange, sebi, stamp = charges
    # GST is levied on brokerage and on the exchange and SEBI charges, not on
    # the transaction tax or the stamp duty.
    return (INDIA_BROKERAGE_RATE + exchange + sebi) * (1.0 + GST) + transaction_tax + stamp


def round_trip_cost(instrument: Instrument, entry: float, exit_price: float,
                    stopped: bool = False, entry_passive: bool = False,
                    exit_passive: bool = False) -> float:
    """Cost of one unit, in price terms: half the spread for each side that
    crossed it, charges on both notionals, and slippage if the exit was a
    stop.

    A side is *passive* when it was a resting limit order that the market
    came to — it trades at its own price and pays no spread. What it pays
    instead is adverse selection, and that is not a number to add here: it
    is in which orders get filled at all, which the caller has to model.
    """
    half_spread = instrument.from_pips(instrument.typical_spread_pips) / 2.0
    crossings = (0 if entry_passive else 1) + (0 if exit_passive else 1)
    charges = (abs(entry) + abs(exit_price)) * per_side_rate(instrument)
    slippage = instrument.from_pips(STOP_SLIPPAGE_PIPS) if stopped else 0.0
    return half_spread * crossings + charges + slippage
