"""Market history as an immutable, content-addressed snapshot.

A backtest read from a live feed cannot be run again: an hour later the
window has moved and the bars underneath the result are gone. A snapshot is
fetched once, cleaned by rules that are recorded with it, and named by the
hash of its contents — so a result can say exactly which bars produced it,
and anyone holding the same file can check.

Snapshots are only taken from a real venue. The simulator is refused unless
a test asks for it by name.
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

import numpy as np
import pandas as pd

from ..config import DATA_DIR
from ..data.providers.base import resample_rows
from ..data.store import COLUMNS
from ..instruments import get_instrument, get_timeframe
from . import quality

FORMAT = "meridian-dataset-v1"
NOT_A_MARKET = ("synthetic", "unknown")


class DatasetError(RuntimeError):
    pass


def root() -> Path:
    path = DATA_DIR / "research" / "datasets"
    path.mkdir(parents=True, exist_ok=True)
    return path


@dataclass(frozen=True)
class Dataset:
    id: str
    provider: str
    symbol: str
    timeframe: str
    rows: np.ndarray
    meta: Dict[str, Any] = field(default_factory=dict)

    @property
    def bars(self) -> int:
        return len(self.rows)

    @property
    def first(self) -> float:
        return float(self.rows[0, 0]) if len(self.rows) else 0.0

    @property
    def last(self) -> float:
        return float(self.rows[-1, 0]) if len(self.rows) else 0.0

    @property
    def real(self) -> bool:
        return self.provider not in NOT_A_MARKET

    @property
    def quality_passed(self) -> bool:
        return bool((self.meta.get("quality") or {}).get("passed"))

    def frame(self, start: Optional[float] = None, end: Optional[float] = None) -> pd.DataFrame:
        """Bars whose open time is in [start, end), as the engine's frame."""
        rows = self.rows
        if start is not None:
            rows = rows[rows[:, 0] >= start]
        if end is not None:
            rows = rows[rows[:, 0] < end]
        return pd.DataFrame(rows, columns=list(COLUMNS)).reset_index(drop=True)

    def describe(self) -> Dict[str, Any]:
        """What an experiment records about the data it ran on."""
        return {"dataset_id": self.id, "provider": self.provider, "symbol": self.symbol,
                "timeframe": self.timeframe, "bars": self.bars, "first": self.first,
                "last": self.last, "sha256": self.meta.get("sha256"),
                "quality_passed": self.quality_passed,
                "derived_from": self.meta.get("derived_from")}


def digest(provider: str, symbol: str, timeframe: str, rows: np.ndarray) -> str:
    head = "|".join((FORMAT, provider, symbol.upper(), timeframe.lower())).encode()
    body = np.ascontiguousarray(rows, dtype="<f8").tobytes()
    return hashlib.sha256(head + b"|" + body).hexdigest()


def _continuous(symbol: str) -> bool:
    try:
        return get_instrument(symbol).asset_class == "crypto"
    except KeyError:
        return True


def build(provider: str, symbol: str, timeframe: str, raw_rows: Sequence[Sequence[float]],
          *, now: Optional[float] = None, derived_from: Optional[str] = None,
          min_bars: int = quality.MIN_BARS) -> Dataset:
    """Clean and assess rows from a provider and name the result."""
    symbol, timeframe = symbol.upper(), timeframe.lower()
    seconds = get_timeframe(timeframe).seconds
    rows, report = quality.validate(raw_rows, seconds, continuous=_continuous(symbol),
                                    now=now, min_bars=min_bars)
    rows = np.ascontiguousarray(rows, dtype="<f8")
    rows.setflags(write=False)
    sha = digest(provider, symbol, timeframe, rows)
    meta = {
        "format": FORMAT, "sha256": sha, "provider": provider, "symbol": symbol,
        "timeframe": timeframe, "bars": int(len(rows)),
        "first": float(rows[0, 0]) if len(rows) else 0.0,
        "last": float(rows[-1, 0]) if len(rows) else 0.0,
        "created_at": time.time() if now is None else float(now),
        "cleaning_rules": list(quality.CLEANING_RULES),
        "quality": report.to_dict(), "derived_from": derived_from,
    }
    return Dataset(id=sha[:16], provider=provider, symbol=symbol, timeframe=timeframe,
                   rows=rows, meta=meta)


def resample(dataset: Dataset, timeframe: str) -> Dataset:
    """A coarser timeframe built from this snapshot's own bars, so every
    timeframe of one study comes from the same observations. A coarse bar
    the snapshot only covers part of — at either end — is dropped rather
    than passed off as complete."""
    timeframe = timeframe.lower()
    seconds = get_timeframe(timeframe).seconds
    fine = get_timeframe(dataset.timeframe).seconds
    if seconds <= fine or seconds % fine:
        raise DatasetError("Cannot build {} bars from {} bars.".format(
            timeframe, dataset.timeframe))
    rows = resample_rows(dataset.rows.tolist(), seconds)
    if rows and rows[0][0] < dataset.first:
        rows = rows[1:]                 # the snapshot starts part-way through it
    # The last coarse bar is only complete if the fine bars reach its end.
    complete_until = dataset.last + fine
    return build(dataset.provider, dataset.symbol, timeframe, rows, now=complete_until,
                 derived_from=dataset.id)


def save(dataset: Dataset) -> Path:
    folder = root()
    data_path = folder / (dataset.id + ".npz")
    if not data_path.exists():                 # content-addressed: never rewritten
        np.savez_compressed(data_path, rows=dataset.rows)
        (folder / (dataset.id + ".json")).write_text(
            json.dumps(dataset.meta, indent=2, sort_keys=True))
    return data_path


def exists(dataset_id: str) -> bool:
    return (root() / (dataset_id + ".npz")).exists()


def load(dataset_id: str) -> Dataset:
    """Read a snapshot back, and refuse it if it is not the bytes it claims."""
    folder = root()
    try:
        meta = json.loads((folder / (dataset_id + ".json")).read_text())
        with np.load(folder / (dataset_id + ".npz")) as stored:
            rows = np.ascontiguousarray(stored["rows"], dtype="<f8")
    except (OSError, KeyError, ValueError) as exc:
        raise DatasetError("Dataset {} is not in the store.".format(dataset_id)) from exc
    sha = digest(meta["provider"], meta["symbol"], meta["timeframe"], rows)
    if sha != meta.get("sha256") or not sha.startswith(dataset_id):
        raise DatasetError(
            "Dataset {} does not match its recorded hash — the file was changed "
            "after it was written. Results that cite it cannot be reproduced "
            "from it.".format(dataset_id))
    rows.setflags(write=False)
    return Dataset(id=dataset_id, provider=meta["provider"], symbol=meta["symbol"],
                   timeframe=meta["timeframe"], rows=rows, meta=meta)


def catalogue() -> List[Dict[str, Any]]:
    out = []
    for path in sorted(root().glob("*.json")):
        try:
            out.append(json.loads(path.read_text()))
        except ValueError:
            continue
    return sorted(out, key=lambda meta: meta.get("created_at", 0.0), reverse=True)


async def fetch(symbol: str, timeframe: str, *, bars: int = 150_000,
                allow_simulated: bool = False, now: Optional[float] = None) -> Dataset:
    """Take a snapshot from the venue that serves this symbol and store it."""
    from ..data.providers import build_providers, resolve_provider

    providers = build_providers()
    provider = resolve_provider(symbol.upper(), providers)
    try:
        if provider.name in NOT_A_MARKET and not allow_simulated:
            raise DatasetError(
                "No real venue serves {} right now — a research snapshot is never "
                "taken from the simulator.".format(symbol.upper()))
        raw = await provider.deep_history(symbol.upper(), timeframe.lower(), bars)
    finally:
        for each in providers.values():
            try:
                await each.close()
            except Exception:  # noqa: BLE001 - closing a client must not hide the result
                pass
    if not raw:
        raise DatasetError("{} returned no history for {} {}.".format(
            provider.name, symbol.upper(), timeframe.lower()))
    dataset = build(provider.name, symbol, timeframe, raw,
                    now=time.time() if now is None else now)
    save(dataset)
    return dataset
