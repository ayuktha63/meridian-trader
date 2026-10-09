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

#: Taker fee per side, as a fraction of notional. CoinDCX charges 0.05% on the
#: USDT books at the default tier; a discount broker's F&O brokerage lands
#: near this on a small order.
COMMISSION_RATE = 0.0005

#: Slippage against you on a stop fill, in ticks. A stop is a market order
#: once triggered, and it fills where the book is.
STOP_SLIPPAGE_PIPS = 0.5

#: GST on brokerage and exchange charges (India).
GST = 0.18

#: Per side, as a fraction of notional: STT/CTT (sell side, halved), exchange
#: transaction charge, SEBI fee, stamp duty (buy side, halved).
STATUTORY_PER_SIDE = {
    "stock": 0.000125 + 0.0000297 + 0.000001 + 0.000015,         # equity intraday
    "index": 0.0001 + 0.0000173 + 0.000001 + 0.00001,            # index futures
    "currency_fut": 0.0000035 + 0.000001 + 0.0000005,            # no STT
    "commodity": 0.00005 + 0.000021 + 0.000001 + 0.00001,        # non-agri futures
}


def per_side_rate(instrument: Instrument) -> float:
    """Brokerage plus statutory charges for one side, as a fraction of notional."""
    statutory = STATUTORY_PER_SIDE.get(instrument.asset_class, 0.0)
    brokerage = COMMISSION_RATE * ((1.0 + GST) if statutory else 1.0)
    return brokerage + statutory


def round_trip_cost(instrument: Instrument, entry: float, exit_price: float,
                    stopped: bool = False) -> float:
    """Cost of one unit, in price terms: the spread crossed once in and once
    out (half each way), charges on both notionals, and slippage if the exit
    was a stop."""
    spread = instrument.from_pips(instrument.typical_spread_pips)
    charges = (abs(entry) + abs(exit_price)) * per_side_rate(instrument)
    slippage = instrument.from_pips(STOP_SLIPPAGE_PIPS) if stopped else 0.0
    return spread + charges + slippage
