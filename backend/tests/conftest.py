from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Point the app at a throwaway data directory *before* anything imports the
# settings, which are cached with lru_cache and read on first access. Without
# this the API tests write positions and journal entries straight into the file
# the running app uses — the guardrails then read those fabricated trades as
# real ones and report a daily P&L that never happened. The same goes for
# settings.json and the persisted kill switch, which live beside the database.
# A fresh directory per run, so two runs at once cannot share state either.
_TEST_DATA = Path(tempfile.mkdtemp(prefix="meridian-test-"))
# …and removed afterwards: a run leaves a database behind, and enough of them
# fill a disk.
import atexit  # noqa: E402
import shutil  # noqa: E402

atexit.register(shutil.rmtree, _TEST_DATA, ignore_errors=True)
os.environ["MERIDIAN_DATA_DIR"] = str(_TEST_DATA)
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///" + str(_TEST_DATA / "meridian-test.db")
# The live-order supervisor is driven one pass at a time by the tests that
# exercise it; a background loop racing them would make every one flaky.
os.environ["LIVE_RECONCILER_ENABLED"] = "false"
# The suite runs at any hour and most tests have no broker quote to compare
# against; the tests for these two checks switch them back on.
os.environ["LIVE_ENFORCE_MARKET_HOURS"] = "false"
os.environ["LIVE_PRICE_BAND_PCT"] = "0"
# …and no end-of-day square-off either, or every position these tests open is
# closed under them whenever the suite runs in NSE's last twenty minutes.
os.environ["LIVE_SQUAREOFF_MINUTES_BEFORE_CLOSE"] = "0"
os.environ["LIVE_MAX_TRADES_PER_DAY"] = "0"
os.environ["LIVE_REQUIRE_MEASURED_EDGE"] = "false"

import starlette.testclient as _testclient  # noqa: E402

from app import costs, instruments, stocks  # noqa: E402
from app.data import feed as feed_module  # noqa: E402
from app.data import providers as providers_package  # noqa: E402
from app.data.providers import registry  # noqa: E402
from app.instruments import Instrument, get_instrument  # noqa: E402

import simulated  # noqa: E402

# ── the market the suite runs on ──────────────────────────────
#
# The application takes everything from Angel One: prices, and the list of
# stocks itself. A test has no broker session, so each of those is supplied
# here instead — and nothing below exists outside the test suite.

#: What the application ships with, before anything is added for the tests.
SHIPPED_SYMBOLS = tuple(instruments.SYMBOLS)

# Stocks arrive the way they do from the scrip master: a universe is adopted
# and the instruments are rebuilt from it.
stocks.set_universe([
    stocks.Listing(symbol, lot, 0.05) for symbol, lot in (
        ("RELIANCE", 500), ("HDFCBANK", 550), ("INFY", 400), ("TATAMOTORS", 800),
        ("SBIN", 750), ("TCS", 175), ("ITC", 1600))])
instruments.refresh_stock_instruments()

# Two markets that are not exchange products, for the tests of arithmetic
# that must hold on any instrument: one quoted to five places, one in whole
# units. Neither is something the application lists or Angel One trades.
for _instrument in (
        Instrument("EURUSD", "EUR/USD", "fx", 5, 0.0001, 100_000, 0.6,
                   "USD", ["london", "newyork"], note="test fixture"),
        Instrument("OTCUSD", "A market off any exchange", "otc", 2, 1.0, 1, 1.0,
                   "USD", ["24h"], note="test fixture")):
    instruments.INSTRUMENTS[_instrument.symbol] = _instrument
instruments.SYMBOLS[:] = list(instruments.INSTRUMENTS)

# Venues the application does not have, for the tests of what a venue profile
# does: one margined and financed, one a spot book that cannot be shorted.
costs.PROFILES["test_perp"] = costs.Profile(
    "test_perp", taker=0.0005, maker=0.0002, gst=True, funding_per_8h=0.0001,
    stop_slippage=0.0002, source="test fixture")
costs.PROFILES["test_spot"] = costs.Profile(
    "test_spot", taker=0.0020, maker=0.0020, gst=True, funding_per_8h=0.0,
    stop_slippage=0.0002, source="test fixture", shorting=False)

# …and prices from the generator. It is named "synthetic", so the evidence
# checks refuse it exactly as they must.
angel_only_providers = registry.build_providers


def _simulated_providers():
    return {simulated.SimulatedProvider.name: simulated.SimulatedProvider()}


for _module in (registry, providers_package, feed_module):
    _module.build_providers = _simulated_providers

# Without an access token the API only answers this machine (security.py), and
# Starlette's TestClient announces itself as host "testclient" calling
# "testserver". Default every client to what a local browser looks like, so the
# suite exercises the same path a real local request takes; the security tests
# override these to prove a remote caller is turned away.
_original_testclient_init = _testclient.TestClient.__init__
_original_websocket_connect = _testclient.TestClient.websocket_connect


def _local_testclient_init(self, *args, **kwargs):
    kwargs.setdefault("client", ("127.0.0.1", 50000))
    kwargs.setdefault("base_url", "http://localhost")
    _original_testclient_init(self, *args, **kwargs)


def _local_websocket_connect(self, url, *args, **kwargs):
    # websocket_connect ignores base_url and joins against "ws://testserver".
    if url.startswith("/"):
        url = "ws://localhost" + url
    return _original_websocket_connect(self, url, *args, **kwargs)


_testclient.TestClient.__init__ = _local_testclient_init
_testclient.TestClient.websocket_connect = _local_websocket_connect

BAR = 900  # 15 minutes


def make_df(closes, highs=None, lows=None, opens=None, volumes=None,
            start: int = 1_700_000_000) -> pd.DataFrame:
    """Build an OHLCV frame from a close series with sane derived columns."""
    closes = np.asarray(closes, dtype=float)
    n = len(closes)
    if opens is None:
        opens = np.concatenate([[closes[0]], closes[:-1]])
    if highs is None:
        highs = np.maximum(opens, closes) * 1.0005
    if lows is None:
        lows = np.minimum(opens, closes) * 0.9995
    if volumes is None:
        volumes = np.full(n, 1000.0)
    return pd.DataFrame({
        "time": np.arange(start, start + n * BAR, BAR, dtype=float)[:n],
        "open": opens, "high": highs, "low": lows,
        "close": closes, "volume": volumes,
    })


def trending_df(n: int = 300, slope: float = 0.0004, noise: float = 0.0006,
                seed: int = 7) -> pd.DataFrame:
    """A clean uptrend with pullbacks — enough structure to detect swings."""
    rng = np.random.default_rng(seed)
    drift = np.cumsum(np.full(n, slope))
    wave = np.sin(np.linspace(0, 9 * np.pi, n)) * 0.004
    shocks = rng.normal(0, noise, n).cumsum()
    closes = 1.10 * np.exp(drift + wave + shocks)
    opens = np.concatenate([[closes[0]], closes[:-1]])
    spread = np.abs(rng.normal(0, 0.0009, n)) * closes
    highs = np.maximum(opens, closes) + spread
    lows = np.minimum(opens, closes) - spread
    volumes = rng.lognormal(7, 0.3, n)
    return make_df(closes, highs, lows, opens, volumes)


@pytest.fixture
def eurusd():
    return get_instrument("EURUSD")


@pytest.fixture
def universe():
    """For a test that adopts a stock universe of its own: the suite's is
    put back afterwards, and so is every instrument as it was."""
    listings, catalogue = list(stocks.UNIVERSE), dict(instruments.INSTRUMENTS)
    yield
    stocks.set_universe(listings)
    instruments.INSTRUMENTS.clear()
    instruments.INSTRUMENTS.update(catalogue)
    instruments.refresh_stock_instruments()


@pytest.fixture
def uptrend():
    return trending_df()


@pytest.fixture(autouse=True)
def _reset_drawdown_tracker():
    """Global, unlike the per-file resets elsewhere in this suite: the
    drawdown tracker's whole point is a peak that survives everything else
    (restarts, day boundaries), which is exactly what makes it the one
    process-wide singleton here that can silently leak a "peak" set by one
    test's fake funds into an unrelated test's much smaller ones — read as a
    99% drawdown that was never a real one. A file-local reset (as
    test_execution.py and test_reconciler.py also carry, for the tests that
    exercise it directly) is correct but easy to forget in a new file that
    touches live funds; this is the backstop."""
    from app.broker.drawdown import drawdown_tracker

    drawdown_tracker._peak = drawdown_tracker._last = None  # noqa: SLF001
    drawdown_tracker.clear()
    yield
    drawdown_tracker._peak = drawdown_tracker._last = None  # noqa: SLF001
    drawdown_tracker.clear()
