"""Does a strategy know anything it could not have known?

Two checks, both made by running the strategy again on less data and seeing
whether its decision at a bar changes — the method freqtrade's
`lookahead-analysis` and `recursive-analysis` commands use, applied to this
package's strategies:

* **Look-ahead.** Cut the history off right after bar `i`. If the decision
  at `i` is different from the one made with the whole history in hand, the
  full-history version was using bars that had not happened yet — a shifted
  index, a centred window, a statistic taken over the whole series.
* **Warm-up.** Give the strategy only the `warmup` bars before `i`. If the
  decision differs, a live implementation holding that much history would
  not trade what the research traded.

A strategy that fails either is not tested further: its result would be
about a different strategy from the one that could be run.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

import numpy as np
import pandas as pd

from .simulator import Signal
from .strategies import Params, Plan, Strategy

#: Prices from a recursive average started at different points agree to
#: about this much once warmed up. A difference beyond it is not rounding.
TOLERANCE = 2e-3


@dataclass
class AuditReport:
    strategy: str
    checked: int = 0
    lookahead: List[str] = field(default_factory=list)
    warmup: List[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return self.checked > 0 and not self.lookahead and not self.warmup

    def to_dict(self) -> dict:
        return {"strategy": self.strategy, "checked": self.checked, "passed": self.passed,
                "lookahead": self.lookahead[:5], "warmup": self.warmup[:5]}


def _close(a: Optional[float], b: Optional[float]) -> bool:
    if a is None or b is None:
        return a is b
    return abs(a - b) <= TOLERANCE * max(abs(a), abs(b), 1e-12)


def _same(a: Optional[Signal], b: Optional[Signal]) -> bool:
    if a is None or b is None:
        return a is b
    return (a.direction == b.direction and a.entry == b.entry
            and a.trail_atr == b.trail_atr and a.max_hold == b.max_hold
            and _close(a.stop, b.stop) and _close(a.target, b.target)
            and _close(a.price, b.price))


def _exits(plan: Plan, i: int) -> tuple:
    return (None if plan.exit_long is None else bool(plan.exit_long[i]),
            None if plan.exit_short is None else bool(plan.exit_short[i]))


def _sample(plan: Plan, first: int, last: int, samples: int, seed: int) -> List[int]:
    """Bars to re-check: mostly ones that produced a signal — a leak only
    matters where a decision was made — and some that did not."""
    rng = np.random.default_rng(seed)
    with_signal = [i for i in sorted(plan.signals) if first <= i <= last]
    picked = list(rng.choice(with_signal, size=min(samples, len(with_signal)),
                             replace=False)) if with_signal else []
    quiet = rng.integers(first, last + 1, size=max(samples // 4, 2))
    return sorted({int(i) for i in picked} | {int(i) for i in quiet})


def audit(strategy: Strategy, frame: pd.DataFrame, params: Params,
          samples: int = 24, seed: int = 7) -> AuditReport:
    report = AuditReport(strategy=strategy.name)
    warmup = strategy.warmup(params)
    if len(frame) < warmup + 50:
        report.lookahead.append("history too short to audit")
        return report
    full = strategy.build(frame, params)
    for i in _sample(full, warmup, len(frame) - 2, samples, seed):
        report.checked += 1
        cut = strategy.build(frame.iloc[:i + 1].reset_index(drop=True), params)
        if not _same(full.signals.get(i), cut.signals.get(i)) or _exits(full, i) != _exits(cut, i):
            report.lookahead.append(
                "bar {}: decided differently once the bars after it were removed".format(i))
        # The bar itself and exactly `warmup` bars before it.
        short = strategy.build(frame.iloc[i - warmup:i + 1].reset_index(drop=True), params)
        local = warmup
        if (not _same(full.signals.get(i), short.signals.get(local))
                or _exits(full, i) != _exits(short, local)):
            report.warmup.append(
                "bar {}: decided differently with only its {} warm-up bars".format(i, warmup))
    return report
