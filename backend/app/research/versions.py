"""Which code produced a result.

A walk-forward test is evidence about the code that ran it. Change the
engine afterwards and the test is about something that no longer exists —
so every result records a fingerprint of the files that decide a trade, the
files that simulate one and the files that price one, and evidence whose
fingerprints no longer match the running code is not counted.
"""
from __future__ import annotations

import hashlib
import subprocess
from functools import lru_cache
from pathlib import Path
from typing import Dict, Iterable, Tuple

APP = Path(__file__).resolve().parent.parent

#: What decides the live engine's verdict and plan. `instruments.py` is in
#: here because pip sizes and spreads shape every level the plan sets.
ENGINE_FILES: Tuple[str, ...] = ("analysis", "instruments.py", "backtest/replay.py")
#: How an order's life is simulated.
EXECUTION_FILES: Tuple[str, ...] = ("backtest/engine.py", "research/simulator.py")
#: What a trade is charged. `costs_india.py` holds the equity schedules that
#: `costs.py` prices an order by: change a rate there and evidence charged
#: by the old one stops counting.
COST_FILES: Tuple[str, ...] = ("costs.py", "costs_india.py")
#: The candidate strategies studied offline.
RESEARCH_FILES: Tuple[str, ...] = ("research/strategies.py", "analysis/indicators.py")


def _files(entries: Iterable[str]) -> Iterable[Path]:
    for entry in entries:
        path = APP / entry
        if path.is_dir():
            yield from sorted(p for p in path.rglob("*.py"))
        elif path.exists():
            yield path


def fingerprint(entries: Iterable[str]) -> str:
    digest = hashlib.sha256()
    for path in _files(entries):
        digest.update(str(path.relative_to(APP)).encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()[:12]


@lru_cache(maxsize=None)
def engine_version() -> str:
    return "engine@" + fingerprint(ENGINE_FILES)


@lru_cache(maxsize=None)
def execution_version() -> str:
    return "exec@" + fingerprint(EXECUTION_FILES)


@lru_cache(maxsize=None)
def cost_version() -> str:
    return "costs@" + fingerprint(COST_FILES)


@lru_cache(maxsize=None)
def research_version() -> str:
    return "research@" + fingerprint(RESEARCH_FILES)


def strategy_version(strategy: str) -> str:
    """The fingerprint of the code behind a strategy name. The live engine
    and the named plays come out of the same analysis pass."""
    from ..backtest.engine import ENGINE, PLAYS, RANDOM

    if strategy in (ENGINE, RANDOM) + tuple(PLAYS):
        return engine_version()
    return research_version()


@lru_cache(maxsize=None)
def git_state() -> Dict[str, object]:
    """The commit a result was produced at, and whether the tree had
    uncommitted changes — in which case the commit alone does not identify
    the code, and the fingerprints above are what does."""
    def git(*args: str) -> str:
        return subprocess.run(("git",) + args, cwd=str(APP), capture_output=True,
                              text=True, timeout=10, check=True).stdout.strip()
    try:
        return {"commit": git("rev-parse", "HEAD"),
                "dirty": bool(git("status", "--porcelain"))}
    except (OSError, subprocess.SubprocessError):
        return {"commit": "unknown", "dirty": True}


def current() -> Dict[str, object]:
    """Everything an experiment records about the code that ran it."""
    return {"engine": engine_version(), "execution": execution_version(),
            "costs": cost_version(), "research": research_version(), "git": git_state()}
