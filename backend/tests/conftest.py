from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Point the app at a throwaway database *before* anything imports the settings,
# which are cached with lru_cache and read on first access. Without this the API
# tests write positions and journal entries straight into the file the running
# app uses — the guardrails then read those fabricated trades as real ones and
# report a daily P&L that never happened.
_TEST_DB = Path(tempfile.gettempdir()) / "meridian-test.db"
_TEST_DB.unlink(missing_ok=True)
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///" + str(_TEST_DB)

from app.instruments import get_instrument  # noqa: E402

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
