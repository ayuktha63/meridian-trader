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
os.environ["MERIDIAN_DATA_DIR"] = str(_TEST_DATA)
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///" + str(_TEST_DATA / "meridian-test.db")
# The live-order supervisor is driven one pass at a time by the tests that
# exercise it; a background loop racing them would make every one flaky.
os.environ["LIVE_RECONCILER_ENABLED"] = "false"
# The suite runs at any hour and most tests have no broker quote to compare
# against; the tests for these two checks switch them back on.
os.environ["LIVE_ENFORCE_MARKET_HOURS"] = "false"
os.environ["LIVE_PRICE_BAND_PCT"] = "0"
os.environ["LIVE_MAX_TRADES_PER_DAY"] = "0"
os.environ["LIVE_REQUIRE_MEASURED_EDGE"] = "false"

import starlette.testclient as _testclient  # noqa: E402

from app.instruments import get_instrument  # noqa: E402

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
def uptrend():
    return trending_df()
