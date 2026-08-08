"""Instrument + timeframe reference data.

Pip size, digits, contract size and typical spread drive risk maths, lot
sizing and the synthetic feed's realism. Values reflect standard retail
FX/metal/crypto conventions.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass(frozen=True)
class Instrument:
    """A tradable instrument.

    `pip_size` is the smallest quoted increment (a pip in FX, a tick on an
    exchange-traded future) and `contract_size` is the units in one lot, so
    `pip_value_per_lot` is the cash value of one tick per lot in every market.

    `venue` is what you can actually trade it on, and `broker_symbol` is the
    tradingsymbol to search for there — the two fields that turn a signal into
    an order.
    """

    symbol: str
    display: str
    asset_class: str          # fx | metal | crypto | currency_fut | index | commodity
    digits: int
    pip_size: float           # smallest price increment (pip / tick)
    contract_size: float      # units per lot
    typical_spread_pips: float
    daily_vol_pct: float      # daily sigma used by the simulated feed
    base_price: float
    quote_currency: str
    sessions: List[str] = field(default_factory=list)
    venue: str = "otc"        # otc | NFO | CDS | MCX | crypto
    broker_symbol: Optional[str] = None
    exchange: Optional[str] = None
    tradable_in_india: bool = False
    note: str = ""

    @property
    def pip_value_per_lot(self) -> float:
        """USD value of one pip for one standard lot (approximate)."""
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
        # ── Spot FX and metals (OTC) ──────────────────────────
        # Not accessible to Indian residents: under FEMA only INR-paired
        # currency derivatives on a recognised exchange are permitted. Kept
        # for analysis and for users outside India.
        Instrument("EURUSD", "EUR/USD", "fx", 5, 0.0001, 100_000, 0.6, 0.45,
                   1.0850, "USD", ["london", "newyork"], venue="otc",
                   note="Spot FX — not permitted for Indian residents"),
        Instrument("GBPUSD", "GBP/USD", "fx", 5, 0.0001, 100_000, 0.9, 0.55,
                   1.2720, "USD", ["london", "newyork"], venue="otc",
                   note="Spot FX — not permitted for Indian residents"),
        Instrument("USDJPY", "USD/JPY", "fx", 3, 0.01, 100_000, 0.8, 0.50,
                   151.40, "JPY", ["tokyo", "newyork"], venue="otc",
                   note="Spot FX — not permitted for Indian residents"),
        Instrument("AUDUSD", "AUD/USD", "fx", 5, 0.0001, 100_000, 0.9, 0.52,
                   0.6580, "USD", ["sydney", "tokyo"], venue="otc",
                   note="Spot FX — not permitted for Indian residents"),
        Instrument("USDCAD", "USD/CAD", "fx", 5, 0.0001, 100_000, 1.2, 0.44,
                   1.3620, "CAD", ["newyork"], venue="otc",
                   note="Spot FX — not permitted for Indian residents"),
        Instrument("XAUUSD", "Gold / USD", "metal", 2, 0.10, 100, 2.5, 1.05,
                   2338.0, "USD", ["london", "newyork"], venue="otc",
                   note="Spot gold — Indian residents trade MCX GOLD instead"),

        # ── Crypto, USDT-quoted (live from CoinDCX / Binance, keyless) ──
        # `contract_size` is 1 because crypto is traded in units of the coin,
        # not lots, so one "lot" here means one coin and pip_value_per_lot
        # equals the tick size.
        Instrument("BTCUSD", "Bitcoin / USDT", "crypto", 2, 1.0, 1, 1.0, 3.10,
                   67250.0, "USD", ["24h"], venue="crypto",
                   broker_symbol="BTCUSDT", exchange="COINDCX", tradable_in_india=True,
                   note="CoinDCX BTCUSDT · 30% tax + 1% TDS in India"),
        Instrument("ETHUSD", "Ethereum / USDT", "crypto", 2, 0.10, 1, 0.5, 3.60,
                   3480.0, "USD", ["24h"], venue="crypto",
                   broker_symbol="ETHUSDT", exchange="COINDCX", tradable_in_india=True,
                   note="CoinDCX ETHUSDT · 30% tax + 1% TDS in India"),
        Instrument("SOLUSD", "Solana / USDT", "crypto", 3, 0.01, 1, 1.0, 5.20,
                   74.5, "USD", ["24h"], venue="crypto",
                   broker_symbol="SOLUSDT", exchange="COINDCX", tradable_in_india=True,
                   note="CoinDCX SOLUSDT · high volatility"),
        Instrument("XRPUSD", "XRP / USDT", "crypto", 4, 0.0001, 1, 1.0, 4.80,
                   1.07, "USD", ["24h"], venue="crypto",
                   broker_symbol="XRPUSDT", exchange="COINDCX", tradable_in_india=True,
                   note="CoinDCX XRPUSDT"),
        Instrument("DOGEUSD", "Dogecoin / USDT", "crypto", 5, 0.00001, 1, 1.0, 6.50,
                   0.0702, "USD", ["24h"], venue="crypto",
                   broker_symbol="DOGEUSDT", exchange="COINDCX", tradable_in_india=True,
                   note="CoinDCX DOGEUSDT · sentiment-driven, thin structure"),
        Instrument("BNBUSD", "BNB / USDT", "crypto", 2, 0.10, 1, 1.0, 3.40,
                   600.0, "USD", ["24h"], venue="crypto",
                   broker_symbol="BNBUSDT", exchange="COINDCX", tradable_in_india=True,
                   note="CoinDCX BNBUSDT"),
        Instrument("ADAUSD", "Cardano / USDT", "crypto", 4, 0.0001, 1, 1.0, 4.60,
                   0.19, "USD", ["24h"], venue="crypto",
                   broker_symbol="ADAUSDT", exchange="COINDCX", tradable_in_india=True,
                   note="CoinDCX ADAUSDT"),
        Instrument("AVAXUSD", "Avalanche / USDT", "crypto", 3, 0.001, 1, 1.0, 5.40,
                   6.68, "USD", ["24h"], venue="crypto",
                   broker_symbol="AVAXUSDT", exchange="COINDCX", tradable_in_india=True,
                   note="CoinDCX AVAXUSDT"),
        Instrument("LINKUSD", "Chainlink / USDT", "crypto", 3, 0.001, 1, 1.0, 4.90,
                   8.22, "USD", ["24h"], venue="crypto",
                   broker_symbol="LINKUSDT", exchange="COINDCX", tradable_in_india=True,
                   note="CoinDCX LINKUSDT"),
        Instrument("DOTUSD", "Polkadot / USDT", "crypto", 4, 0.0001, 1, 2.0, 5.00,
                   0.853, "USD", ["24h"], venue="crypto",
                   broker_symbol="DOTUSDT", exchange="COINDCX", tradable_in_india=True,
                   note="CoinDCX DOTUSDT"),
        Instrument("LTCUSD", "Litecoin / USDT", "crypto", 2, 0.01, 1, 1.0, 3.80,
                   45.4, "USD", ["24h"], venue="crypto",
                   broker_symbol="LTCUSDT", exchange="COINDCX", tradable_in_india=True,
                   note="CoinDCX LTCUSDT"),
        Instrument("TRXUSD", "TRON / USDT", "crypto", 5, 0.00001, 1, 10.0, 3.20,
                   0.328, "USD", ["24h"], venue="crypto",
                   broker_symbol="TRXUSDT", exchange="COINDCX", tradable_in_india=True,
                   note="CoinDCX TRXUSDT"),

        # ── Crypto, INR-quoted (CoinDCX) ──────────────────────
        # The INR book is what an Indian resident actually settles in, and it
        # usually carries a small premium to the USDT price. Suffix "C" keeps
        # these distinct from the NSE currency futures above (USDINR etc.).
        Instrument("BTCINRC", "Bitcoin / INR", "crypto", 0, 1.0, 1, 23800.0, 3.10,
                   6_402_000.0, "INR", ["24h"], venue="crypto",
                   broker_symbol="BTCINR", exchange="COINDCX", tradable_in_india=True,
                   note="CoinDCX BTCINR · settles in rupees · ~0.37% spread, 20x the USDT book — swing only, never scalp · 30% tax + 1% TDS"),
        Instrument("ETHINRC", "Ethereum / INR", "crypto", 1, 0.1, 1, 8100.0, 3.60,
                   190_805.0, "INR", ["24h"], venue="crypto",
                   broker_symbol="ETHINR", exchange="COINDCX", tradable_in_india=True,
                   note="CoinDCX ETHINR · settles in rupees · ~0.42% spread — the USDT book is far cheaper to trade"),
        Instrument("SOLINRC", "Solana / INR", "crypto", 1, 0.1, 1, 410.0, 5.20,
                   7_424.0, "INR", ["24h"], venue="crypto",
                   broker_symbol="SOLINR", exchange="COINDCX", tradable_in_india=True,
                   note="CoinDCX SOLINR · settles in rupees · ~0.55% spread — the USDT book is far cheaper to trade"),
        Instrument("XRPINRC", "XRP / INR", "crypto", 3, 0.001, 1, 450.0, 4.80,
                   106.7, "INR", ["24h"], venue="crypto",
                   broker_symbol="XRPINR", exchange="COINDCX", tradable_in_india=True,
                   note="CoinDCX XRPINR · settles in rupees · ~0.42% spread — the USDT book is far cheaper to trade"),
        Instrument("DOGEINRC", "Dogecoin / INR", "crypto", 4, 0.0001, 1, 440.0, 6.50,
                   6.99, "INR", ["24h"], venue="crypto",
                   broker_symbol="DOGEINR", exchange="COINDCX", tradable_in_india=True,
                   note="CoinDCX DOGEINR · settles in rupees · ~0.63% spread — the USDT book is far cheaper to trade"),

        # ── NSE currency futures (CDS) — the legal FX route in India ──
        # Tick 0.0025, lot 1000 units of the base currency, so one tick is
        # ₹2.50 per lot. Verify the live lot size on the contract page.
        Instrument("USDINR", "USD/INR Futures", "currency_fut", 4, 0.0025, 1_000,
                   1.0, 0.30, 83.20, "INR", ["india"], venue="CDS",
                   broker_symbol="USDINR", exchange="CDS", tradable_in_india=True,
                   note="NSE currency future · 09:00-17:00 IST"),
        Instrument("EURINR", "EUR/INR Futures", "currency_fut", 4, 0.0025, 1_000,
                   2.0, 0.45, 90.10, "INR", ["india"], venue="CDS",
                   broker_symbol="EURINR", exchange="CDS", tradable_in_india=True,
                   note="NSE currency future · 09:00-17:00 IST"),
        Instrument("GBPINR", "GBP/INR Futures", "currency_fut", 4, 0.0025, 1_000,
                   2.5, 0.55, 105.60, "INR", ["india"], venue="CDS",
                   broker_symbol="GBPINR", exchange="CDS", tradable_in_india=True,
                   note="NSE currency future · 09:00-17:00 IST"),
        Instrument("JPYINR", "JPY/INR Futures", "currency_fut", 4, 0.0025, 100_000,
                   2.5, 0.50, 55.20, "INR", ["india"], venue="CDS",
                   broker_symbol="JPYINR", exchange="CDS", tradable_in_india=True,
                   note="NSE currency future · quoted per 100 JPY"),

        # ── NSE indices ───────────────────────────────────────
        Instrument("NIFTY", "NIFTY 50", "index", 2, 0.05, 75, 1.0, 0.85,
                   24_500.0, "INR", ["india"], venue="NFO",
                   broker_symbol="NIFTY", exchange="NSE", tradable_in_india=True,
                   note="Index — trade the future/option. Verify current lot size."),
        Instrument("BANKNIFTY", "BANK NIFTY", "index", 2, 0.05, 30, 1.5, 1.10,
                   52_000.0, "INR", ["india"], venue="NFO",
                   broker_symbol="BANKNIFTY", exchange="NSE", tradable_in_india=True,
                   note="Index — trade the future/option. Verify current lot size."),

        # ── MCX commodities ───────────────────────────────────
        Instrument("MCXGOLD", "Gold (MCX)", "commodity", 0, 1.0, 100, 2.0, 0.95,
                   71_500.0, "INR", ["india"], venue="MCX",
                   broker_symbol="GOLD", exchange="MCX", tradable_in_india=True,
                   note="MCX GOLD · 100g lot · GOLDM is the 10g mini"),
        Instrument("MCXSILVER", "Silver (MCX)", "commodity", 0, 1.0, 30, 3.0, 1.60,
                   88_000.0, "INR", ["india"], venue="MCX",
                   broker_symbol="SILVER", exchange="MCX", tradable_in_india=True,
                   note="MCX SILVER · 30kg lot · SILVERM is the 5kg mini"),
        Instrument("MCXCRUDE", "Crude Oil (MCX)", "commodity", 0, 1.0, 100, 2.0, 2.10,
                   6_400.0, "INR", ["india"], venue="MCX",
                   broker_symbol="CRUDEOIL", exchange="MCX", tradable_in_india=True,
                   note="MCX CRUDEOIL · 100 barrel lot"),
    ]
}

def _stock_instruments() -> List[Instrument]:
    """Generate an Instrument per NSE F&O stock from the compact table.

    Tick size on NSE equity is ₹0.05 across the board, and `contract_size` is
    the F&O lot — so `pip_value_per_lot` is the rupee value of one tick for one
    lot, which is what the sizing maths needs.
    """
    from .stocks import NSE_FO_STOCKS

    out: List[Instrument] = []
    for symbol, display, sector, lot, price in NSE_FO_STOCKS:
        # Bigger names move less in percentage terms; this only seeds the
        # simulator, so a coarse split by price band is enough.
        vol = 1.9 if price < 300 else 1.5 if price < 2_000 else 1.2
        out.append(Instrument(
            symbol=symbol,
            display=display,
            asset_class="stock",
            digits=2,
            pip_size=0.05,
            contract_size=float(lot),
            typical_spread_pips=1.0,
            daily_vol_pct=vol,
            base_price=price,
            quote_currency="INR",
            sessions=["india"],
            venue="NFO",
            broker_symbol=symbol,
            exchange="NSE",
            tradable_in_india=True,
            note="{} · NSE F&O · lot {} · cash, futures or options".format(sector, lot),
        ))
    return out


INSTRUMENTS.update({i.symbol: i for i in _stock_instruments()})

SYMBOLS: List[str] = list(INSTRUMENTS.keys())

#: Symbols the feed streams by default. Streaming all ~200 instruments would
#: open far more subscriptions than any one trader watches, so the background
#: feed covers the majors and everything else hydrates on demand when selected.
DEFAULT_STREAM_SYMBOLS: List[str] = [
    s for s in SYMBOLS
    if INSTRUMENTS[s].asset_class != "stock"
] + ["RELIANCE", "HDFCBANK", "INFY", "TATAMOTORS", "SBIN"]


def get_instrument(symbol: str) -> Instrument:
    key = symbol.upper().replace("/", "").replace("-", "")
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
