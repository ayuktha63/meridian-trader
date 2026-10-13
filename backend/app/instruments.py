"""Instrument + timeframe reference data.

Everything listed here is something Angel One trades. The exchange futures
below are written out; NSE F&O stocks are not — which ones exist, and the lot
and tick each trades in, are read from Angel One's scrip master (see
`refresh_stock_instruments`).
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field, replace
from typing import Dict, List, Optional

from .config import DATA_DIR

log = logging.getLogger("meridian.instruments")

#: What Angel One last listed, kept so that a restart knows its stocks before
#: the broker is connected again — a saved trade or journal entry on one has
#: to resolve whether or not there is a session.
UNIVERSE_PATH = DATA_DIR / "angel_universe.json"


@dataclass(frozen=True)
class Instrument:
    """A tradable instrument.

    `pip_size` is the smallest quoted increment (a tick on an exchange) and
    `contract_size` is the units in one lot, so `pip_value_per_lot` is the
    cash value of one tick per lot in every market.

    `venue` is what you can actually trade it on, and `broker_symbol` is the
    tradingsymbol to search for there — the two fields that turn a signal into
    an order.
    """

    symbol: str
    display: str
    asset_class: str          # currency_fut | index | commodity | stock
    digits: int
    pip_size: float           # smallest price increment (pip / tick)
    contract_size: float      # units per lot
    #: An assumption, used for costs only until a quote supplies the real one.
    typical_spread_pips: float
    quote_currency: str
    sessions: List[str] = field(default_factory=list)
    venue: str = "otc"        # NFO | CDS | MCX
    broker_symbol: Optional[str] = None
    exchange: Optional[str] = None
    tradable_in_india: bool = False
    note: str = ""

    @property
    def pip_value_per_lot(self) -> float:
        """Cash value of one tick for one lot, in the quote currency."""
        return self.pip_size * self.contract_size

    def round_price(self, price: float) -> float:
        return round(price, self.digits)

    def to_pips(self, price_delta: float) -> float:
        return price_delta / self.pip_size

    def from_pips(self, pips: float) -> float:
        return pips * self.pip_size


INSTRUMENTS: Dict[str, Instrument] = {
    i.symbol: i
    for i in [
        # ── NSE currency futures (CDS) — the legal FX route in India ──
        # Tick 0.0025, lot 1000 units of the base currency, so one tick is
        # ₹2.50 per lot. Verify the live lot size on the contract page.
        Instrument("USDINR", "USD/INR Futures", "currency_fut", 4, 0.0025, 1_000,
                   1.0, "INR", ["india"], venue="CDS",
                   broker_symbol="USDINR", exchange="CDS", tradable_in_india=True,
                   note="NSE currency future · 09:00-17:00 IST"),
        Instrument("EURINR", "EUR/INR Futures", "currency_fut", 4, 0.0025, 1_000,
                   2.0, "INR", ["india"], venue="CDS",
                   broker_symbol="EURINR", exchange="CDS", tradable_in_india=True,
                   note="NSE currency future · 09:00-17:00 IST"),
        Instrument("GBPINR", "GBP/INR Futures", "currency_fut", 4, 0.0025, 1_000,
                   2.5, "INR", ["india"], venue="CDS",
                   broker_symbol="GBPINR", exchange="CDS", tradable_in_india=True,
                   note="NSE currency future · 09:00-17:00 IST"),
        Instrument("JPYINR", "JPY/INR Futures", "currency_fut", 4, 0.0025, 100_000,
                   2.5, "INR", ["india"], venue="CDS",
                   broker_symbol="JPYINR", exchange="CDS", tradable_in_india=True,
                   note="NSE currency future · quoted per 100 JPY"),

        # ── NSE indices ───────────────────────────────────────
        # The lot and tick here are stand-ins until Angel One is connected:
        # its scrip master then supplies the current contract's (`adopt_contract`).
        Instrument("NIFTY", "NIFTY 50", "index", 2, 0.05, 75, 1.0,
                   "INR", ["india"], venue="NFO",
                   broker_symbol="NIFTY", exchange="NSE", tradable_in_india=True,
                   note="Index — trade the future/option. Verify current lot size."),
        Instrument("BANKNIFTY", "BANK NIFTY", "index", 2, 0.05, 30, 1.5,
                   "INR", ["india"], venue="NFO",
                   broker_symbol="BANKNIFTY", exchange="NSE", tradable_in_india=True,
                   note="Index — trade the future/option. Verify current lot size."),

        # ── MCX commodities ───────────────────────────────────
        Instrument("MCXGOLD", "Gold (MCX)", "commodity", 0, 1.0, 100, 2.0,
                   "INR", ["india"], venue="MCX",
                   broker_symbol="GOLD", exchange="MCX", tradable_in_india=True,
                   note="MCX GOLD · 100g lot · GOLDM is the 10g mini"),
        Instrument("MCXSILVER", "Silver (MCX)", "commodity", 0, 1.0, 30, 3.0,
                   "INR", ["india"], venue="MCX",
                   broker_symbol="SILVER", exchange="MCX", tradable_in_india=True,
                   note="MCX SILVER · 30kg lot · SILVERM is the 5kg mini"),
        Instrument("MCXCRUDE", "Crude Oil (MCX)", "commodity", 0, 1.0, 100, 2.0,
                   "INR", ["india"], venue="MCX",
                   broker_symbol="CRUDEOIL", exchange="MCX", tradable_in_india=True,
                   note="MCX CRUDEOIL · 100 barrel lot"),
    ]
}


def _stock_instruments() -> List[Instrument]:
    """An Instrument per NSE F&O stock Angel One currently lists.

    `contract_size` is the F&O lot, so `pip_value_per_lot` is the rupee value
    of one tick for one lot, which is what the sizing maths needs.
    """
    from .stocks import UNIVERSE, display_of, sector_of

    out: List[Instrument] = []
    for listing in UNIVERSE:
        out.append(Instrument(
            symbol=listing.symbol,
            display=display_of(listing.symbol),
            asset_class="stock",
            digits=2,
            pip_size=listing.tick_size,
            contract_size=float(listing.lot_size),
            typical_spread_pips=1.0,
            quote_currency="INR",
            sessions=["india"],
            venue="NFO",
            broker_symbol=listing.symbol,
            exchange="NSE",
            tradable_in_india=True,
            note="{} · NSE F&O · lot {} · cash, futures or options".format(
                sector_of(listing.symbol), listing.lot_size),
        ))
    return out


SYMBOLS: List[str] = list(INSTRUMENTS.keys())

#: Stocks streamed from the start, when Angel One lists them. Streaming every
#: one would open far more subscriptions than any one trader watches.
DEFAULT_STOCKS = ("RELIANCE", "HDFCBANK", "INFY", "TATAMOTORS", "SBIN")

#: Symbols the feed streams by default: the majors, with everything else
#: hydrating on demand when it is selected.
DEFAULT_STREAM_SYMBOLS: List[str] = list(SYMBOLS)


def refresh_stock_instruments() -> None:
    """Rebuild the stocks from the universe Angel One last reported, so a
    lot-size revision or a new listing arrives without a restart. The
    containers are changed in place: other modules hold these same objects."""
    fresh = {i.symbol: i for i in _stock_instruments()}
    for symbol in [s for s, inst in INSTRUMENTS.items()
                   if inst.asset_class == "stock" and s not in fresh]:
        del INSTRUMENTS[symbol]
    INSTRUMENTS.update(fresh)

    SYMBOLS[:] = list(INSTRUMENTS.keys())
    DEFAULT_STREAM_SYMBOLS[:] = [
        s for s in SYMBOLS if INSTRUMENTS[s].asset_class != "stock"
    ] + [s for s in DEFAULT_STOCKS if s in INSTRUMENTS]


def adopt_contract(symbol: str, lot_size: int, tick_size: float) -> None:
    """Take a written-down instrument's lot and tick from the contract Angel
    One currently lists for it."""
    current = INSTRUMENTS[symbol]
    INSTRUMENTS[symbol] = replace(
        current, contract_size=float(lot_size), pip_size=tick_size,
        note="Index — trade the future/option · lot {}".format(lot_size))


def save_universe() -> None:
    from .stocks import UNIVERSE

    payload = {
        "saved_at": time.time(),
        "stocks": [[listing.symbol, listing.lot_size, listing.tick_size] for listing in UNIVERSE],
        "contracts": {i.symbol: [int(i.contract_size), i.pip_size]
                      for i in INSTRUMENTS.values() if i.asset_class == "index"},
    }
    try:
        scratch = UNIVERSE_PATH.with_suffix(".tmp")
        scratch.write_text(json.dumps(payload))
        os.replace(scratch, UNIVERSE_PATH)
    except OSError as exc:
        log.warning("could not keep the stock universe for the next start: %s", exc)


def restore_universe() -> None:
    """Adopt the universe kept from the last scrip-master load, if there is
    one. Angel One's own list, a little old; replaced when it is next read."""
    from .stocks import Listing, set_universe

    try:
        payload = json.loads(UNIVERSE_PATH.read_text())
        listings = [Listing(str(symbol), int(lot), float(tick))
                    for symbol, lot, tick in payload["stocks"]]
        contracts = {str(symbol): (int(lot), float(tick))
                     for symbol, (lot, tick) in (payload.get("contracts") or {}).items()}
    except OSError:
        return                                  # never connected: nothing kept yet
    except (ValueError, TypeError, KeyError) as exc:
        log.warning("the kept stock universe is unreadable (%s); starting without it", exc)
        return
    set_universe(listings)
    refresh_stock_instruments()
    for symbol, (lot, tick) in contracts.items():
        if symbol in INSTRUMENTS and INSTRUMENTS[symbol].asset_class == "index":
            adopt_contract(symbol, lot, tick)


restore_universe()


def get_instrument(symbol: str) -> Instrument:
    key = symbol.upper()
    if key in INSTRUMENTS:          # a ticker with its own hyphen: BAJAJ-AUTO
        return INSTRUMENTS[key]
    key = key.replace("/", "").replace("-", "")
    if key not in INSTRUMENTS:
        raise KeyError("Unknown symbol: {}".format(symbol))
    return INSTRUMENTS[key]


# ── timeframes ────────────────────────────────────────────────

@dataclass(frozen=True)
class Timeframe:
    code: str
    label: str
    seconds: int
    rank: int          # ordering, higher = more macro

    @property
    def minutes(self) -> float:
        return self.seconds / 60.0


TIMEFRAMES: Dict[str, Timeframe] = {
    t.code: t
    for t in [
        Timeframe("1m", "1 Minute", 60, 0),
        Timeframe("5m", "5 Minutes", 300, 1),
        Timeframe("15m", "15 Minutes", 900, 2),
        Timeframe("30m", "30 Minutes", 1800, 3),
        Timeframe("1h", "1 Hour", 3600, 4),
        Timeframe("4h", "4 Hours", 14400, 5),
        Timeframe("1d", "Daily", 86400, 6),
        Timeframe("1w", "Weekly", 604800, 7),
    ]
}

TIMEFRAME_CODES: List[str] = list(TIMEFRAMES.keys())

# Timeframes fused by the multi-timeframe engine, macro → micro.
MTF_LADDER: List[str] = ["1d", "4h", "1h", "15m", "5m", "1m"]

# Weight of each timeframe when computing the fused directional bias.
MTF_WEIGHTS: Dict[str, float] = {
    "1w": 0.30, "1d": 0.26, "4h": 0.22, "1h": 0.17,
    "30m": 0.12, "15m": 0.10, "5m": 0.06, "1m": 0.04,
}


def get_timeframe(code: str) -> Timeframe:
    key = code.lower()
    if key not in TIMEFRAMES:
        raise KeyError("Unknown timeframe: {}".format(code))
    return TIMEFRAMES[key]


def higher_timeframe(code: str) -> Optional[str]:
    tf = get_timeframe(code)
    for other in TIMEFRAMES.values():
        if other.rank == tf.rank + 1:
            return other.code
    return None
