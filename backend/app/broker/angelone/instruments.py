"""Resolves a Meridian symbol to what Angel One actually needs to trade it.

Angel One publishes a single JSON "scrip master" covering every tradable
instrument across NSE, BSE, NFO, CDS and MCX — tens of thousands of rows,
tokens included. That file is the only authoritative source for a token, lot
size or tick size; hard-coding any of those is how a live order gets rejected
or, worse, filled against the wrong contract.

Angel One does not support crypto at all, so every crypto symbol in Meridian
is unsupported for live execution by construction — this is not a mapping
gap to fix, it is a real product boundary. §5/§6 of the spec this module
implements are explicit that this must be an honest "unsupported", never a
silent substitution.
"""
from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, List, Optional

import httpx

from ...instruments import get_instrument

log = logging.getLogger("meridian.angelone.instruments")

SCRIP_MASTER_URL = (
    "https://margincalculator.angelbroking.com/OpenAPI_File/files/"
    "OpenAPIScripMaster.json"
)

#: Refresh at most this often — the master is tens of MB; Angel One's own
#: docs say it changes a few times a day around contract rollovers, not
#: continuously.
REFRESH_INTERVAL_SECONDS = 6 * 3600

#: The only exchange segments Angel One executes on. Anything else (crypto,
#: an offshore FX book) is out of scope by definition, not a lookup miss.
SUPPORTED_EXCHANGES = {"NSE", "BSE", "NFO", "CDS", "MCX"}

#: Meridian's index/currency-future/commodity instruments quote off a spot or
#: index reference (`Instrument.exchange` is "NSE" for NIFTY, say) but the
#: tradable contract always lives on the derivatives segment, under a
#: different exchange and instrumenttype than the quote. This is the mapping
#: from asset_class to where the *contract* actually is — it deliberately
#: ignores `Instrument.exchange` for these three classes, because using it
#: is exactly how you end up resolving to the untradable index-quote row
#: instead of a real futures contract (lot size 1, no expiry — a symptom, if
#: you ever see it again, that this mapping has drifted from Angel One's).
DERIVATIVE_VENUE = {
    "index": ("NFO", "FUTIDX"),
    "currency_fut": ("CDS", "FUTCUR"),
    "commodity": ("MCX", "FUTCOM"),
}


@dataclass
class AngelInstrumentMatch:
    symbol: str                # Meridian symbol
    exchange: str               # exch_seg from the scrip master
    trading_symbol: str
    token: str
    lot_size: int
    tick_size: float
    instrument_type: str
    expiry: str = ""


class InstrumentUnsupported(RuntimeError):
    """Raised with a reason a trader can actually read on the UI."""


class AngelOneInstrumentService:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        #: Guards the fetch itself. The screener resolves ~15-100 symbols
        #: concurrently on a cold cache; without this, every one of them
        #: sees "not loaded" at the same instant and each kicks off its own
        #: multi-MB download of the scrip master simultaneously — which is
        #: exactly what hung the first version of this under real load.
        #: Created on first use, inside the loop that will wait on it: before
        #: Python 3.10 an asyncio.Lock binds to whatever loop is current when
        #: it is constructed, and this object is built at import time.
        self._refresh_lock: Optional[asyncio.Lock] = None
        self._by_key: Dict[tuple, dict] = {}   # (exchange, tradingsymbol) -> row
        #: (exchange, underlying name, instrumenttype) -> every matching
        #: contract, all expiries. Nearest unexpired one is picked at
        #: resolve time so a stale cache never quietly trades a dead contract.
        self._derivatives: Dict[tuple, List[dict]] = defaultdict(list)
        self._loaded_at: float = 0.0
        self._load_error: Optional[str] = None

    @property
    def loaded(self) -> bool:
        return self._loaded_at > 0

    @property
    def stale(self) -> bool:
        return (time.time() - self._loaded_at) > REFRESH_INTERVAL_SECONDS

    async def ensure_loaded(self) -> None:
        if self.loaded and not self.stale:
            return
        if self._refresh_lock is None:
            self._refresh_lock = asyncio.Lock()
        async with self._refresh_lock:
            # Re-check after acquiring: the caller that got here first has
            # already refreshed by the time everyone else wakes up.
            if self.loaded and not self.stale:
                return
            await self.refresh()

    async def refresh(self) -> None:
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.get(SCRIP_MASTER_URL)
                resp.raise_for_status()
                rows = resp.json()
        except Exception as exc:  # noqa: BLE001 - network/parse failures, all non-fatal
            self._load_error = str(exc)
            log.warning("Angel One scrip master refresh failed: %s", exc)
            if not self.loaded:
                raise
            return  # keep serving the last good load rather than going blank

        by_key = {}
        derivatives: Dict[tuple, List[dict]] = defaultdict(list)
        derivative_types = {t for _, t in DERIVATIVE_VENUE.values()}
        for row in rows:
            exch = row.get("exch_seg")
            tsym = row.get("symbol")
            if not exch or not tsym or exch not in SUPPORTED_EXCHANGES:
                continue
            by_key[(exch, tsym)] = row
            itype = row.get("instrumenttype")
            if itype in derivative_types:
                name = (row.get("name") or "").upper()
                derivatives[(exch, name, itype)].append(row)

        with self._lock:
            self._by_key = by_key
            self._derivatives = derivatives
            self._loaded_at = time.time()
            self._load_error = None
        log.info("Angel One scrip master loaded: %s instruments (%s derivative "
                 "contracts) across %s", len(by_key),
                 sum(len(v) for v in derivatives.values()), SUPPORTED_EXCHANGES)

    @staticmethod
    def _parse_expiry(expiry: str) -> Optional[datetime]:
        try:
            return datetime.strptime(expiry, "%d%b%Y").replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            return None

    def _nearest_contract(self, exchange: str, name: str,
                          instrument_type: str) -> Optional[dict]:
        """The soonest contract that hasn't expired yet, or None."""
        candidates = self._derivatives.get((exchange, name.upper(), instrument_type), [])
        now = datetime.now(timezone.utc)
        dated = []
        for row in candidates:
            expiry = self._parse_expiry(row.get("expiry", ""))
            if expiry is not None and expiry >= now:
                dated.append((expiry, row))
        if not dated:
            return None
        dated.sort(key=lambda pair: pair[0])
        return dated[0][1]

    async def resolve(self, symbol: str) -> AngelInstrumentMatch:
        """The one entry point: Meridian symbol in, broker contract out.

        Raises InstrumentUnsupported with a UI-safe reason on any failure —
        not tradable in India, no Angel One mapping, or the scrip master
        simply doesn't have a matching row today (expiry rolled, etc).
        """
        instrument = get_instrument(symbol)

        if instrument.asset_class == "crypto" or instrument.venue == "crypto":
            raise InstrumentUnsupported(
                "Angel One does not support crypto trading. {} can only be "
                "auto-traded on the demo account.".format(symbol))

        if not instrument.tradable_in_india or not instrument.exchange:
            raise InstrumentUnsupported(
                "{} has no Indian exchange mapping in Meridian, so it cannot "
                "be resolved to an Angel One contract.".format(symbol))

        await self.ensure_loaded()

        underlying = (instrument.broker_symbol or instrument.symbol).upper()

        if instrument.asset_class in DERIVATIVE_VENUE:
            # Ignore instrument.exchange here on purpose — see DERIVATIVE_VENUE.
            exchange, itype = DERIVATIVE_VENUE[instrument.asset_class]
            row = self._nearest_contract(exchange, underlying, itype)
            if row is None:
                raise InstrumentUnsupported(
                    "No unexpired {} contract found for {} on {} — the scrip "
                    "master may not carry it, or every listed expiry has "
                    "rolled past.".format(itype, symbol, exchange))
        else:
            exchange = instrument.exchange.upper()
            if exchange not in SUPPORTED_EXCHANGES:
                raise InstrumentUnsupported(
                    "{} trades on {}, which Angel One's execution API does "
                    "not cover.".format(symbol, exchange))
            # Cash equity: Angel One lists it as SYMBOL-EQ with no
            # instrumenttype. This is the only remaining case where a
            # suffix guess is safe — there's no expiry to disambiguate.
            row = self._by_key.get((exchange, f"{underlying}-EQ"))
            if row is None:
                raise InstrumentUnsupported(
                    "No matching Angel One contract found for {} on {} — the "
                    "scrip master may not list it. Live execution is "
                    "unavailable for this instrument until that's "
                    "confirmed.".format(symbol, exchange))

        try:
            lot_size = int(float(row.get("lotsize") or 1))
            # Angel One's documented convention is price-fields × 100 (paise).
            # Verified correct here for NSE/NFO/MCX (RELIANCE → 0.1, BANKNIFTY
            # → 0.2, GOLD → 1.0 all match known real ticks). NOT verified for
            # CDS: this formula gives USDINR a tick of 250, but the real tick
            # is 0.0025 — a further, currently unknown, scale factor applies
            # to the currency segment. Do not trust this field for CDS orders
            # until that's confirmed against Angel One's docs or a live quote.
            tick_size = float(row.get("tick_size") or 0) / 100.0
        except (TypeError, ValueError) as exc:
            raise InstrumentUnsupported(
                "Angel One's contract data for {} is malformed — refusing "
                "to guess a lot or tick size for a live order.".format(symbol)
            ) from exc

        return AngelInstrumentMatch(
            symbol=symbol, exchange=exchange,
            trading_symbol=row.get("symbol", ""), token=row.get("token", ""),
            lot_size=max(lot_size, 1), tick_size=tick_size or instrument.pip_size,
            instrument_type=row.get("instrumenttype", ""),
            expiry=row.get("expiry", ""),
        )

    async def capability(self, symbol: str) -> dict:
        """Non-throwing version for the UI: can this be executed live, and why not."""
        try:
            match = await self.resolve(symbol)
        except InstrumentUnsupported as exc:
            return {"supported": False, "reason": str(exc)}
        except KeyError:
            return {"supported": False, "reason": "Unknown symbol."}
        return {
            "supported": True, "exchange": match.exchange,
            "trading_symbol": match.trading_symbol, "lot_size": match.lot_size,
            "tick_size": match.tick_size,
        }


angel_instruments = AngelOneInstrumentService()
