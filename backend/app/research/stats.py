"""How sure can a result be, and how much of it is the search that found it.

Three questions, each with its own tool:

* *Is the mean above zero?* A block bootstrap of the trade sequence. Trades
  cluster — a trending month wins together — and resampling them one at a
  time pretends they do not, which makes an interval too narrow.
* *Would entries with no skill have done as well?* A permutation test
  against a null built by the caller: the strategy's own exits and costs,
  applied at entries chosen without looking.
* *How good is the best of N tries expected to look by luck alone?* The
  deflated Sharpe ratio (Bailey & López de Prado, 2014): the Sharpe the
  winner must beat rises with the number of configurations tried and with
  how much their results differed.

All of it is deterministic given a seed.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, Hashable, Optional, Sequence, Set

import numpy as np
from scipy.stats import norm

EULER_GAMMA = 0.5772156649015329
#: Resamples are drawn in chunks so a long trade list does not need one huge
#: index matrix.
_CHUNK = 1_000


def block_length(n: int) -> int:
    """A block long enough to keep short-range dependence: n^(1/3), the
    usual rate for a block bootstrap of the mean."""
    return max(1, int(round(n ** (1.0 / 3.0))))


def bootstrap_means(values: Sequence[float], resamples: int = 10_000,
                    block: Optional[int] = None, seed: int = 7) -> np.ndarray:
    """Means of circular-block resamples of `values`, in order."""
    x = np.asarray(values, dtype=float)
    n = len(x)
    if n == 0:
        return np.zeros(0)
    length = min(block or block_length(n), n)
    blocks = int(math.ceil(n / length))
    offsets = np.arange(length)
    rng = np.random.default_rng(seed)
    out = np.empty(resamples)
    for start in range(0, resamples, _CHUNK):
        size = min(_CHUNK, resamples - start)
        starts = rng.integers(0, n, size=(size, blocks))
        index = (starts[:, :, None] + offsets[None, None, :]) % n
        out[start:start + size] = x[index.reshape(size, -1)[:, :n]].mean(axis=1)
    return out


@dataclass(frozen=True)
class MeanEstimate:
    n: int
    mean: float
    low: float
    high: float
    #: Share of resamples whose mean is not above zero — a one-sided p-value
    #: for "there is no edge", floored at one in the number of resamples.
    p_value: float
    level: float
    block: int


def mean_interval(values: Sequence[float], level: float = 0.90, resamples: int = 10_000,
                  block: Optional[int] = None, seed: int = 7) -> MeanEstimate:
    x = np.asarray(values, dtype=float)
    n = len(x)
    if n == 0:
        return MeanEstimate(0, 0.0, 0.0, 0.0, 1.0, level, 0)
    length = min(block or block_length(n), n)
    means = bootstrap_means(x, resamples, length, seed)
    tail = (1.0 - level) / 2.0
    p_value = max(float((means <= 0).mean()), 1.0 / resamples)
    return MeanEstimate(n, float(x.mean()), float(np.quantile(means, tail)),
                        float(np.quantile(means, 1.0 - tail)), p_value, level, length)


def sharpe(values: Sequence[float]) -> float:
    """Mean over standard deviation of the series as given — per trade, or
    per period. Not annualised: the caller knows what one observation is."""
    x = np.asarray(values, dtype=float)
    if len(x) < 2:
        return 0.0
    spread = float(x.std(ddof=1))
    return float(x.mean() / spread) if spread > 0 else 0.0


def probabilistic_sharpe(values: Sequence[float], benchmark: float = 0.0) -> float:
    """Probability that the true Sharpe exceeds `benchmark`, given how many
    observations there are and how skewed and fat-tailed they are."""
    x = np.asarray(values, dtype=float)
    n = len(x)
    if n < 3:
        return 0.0
    observed = sharpe(x)
    centred = x - x.mean()
    spread = float(x.std(ddof=0))
    if spread == 0:
        return 0.0
    skew = float((centred ** 3).mean() / spread ** 3)
    kurtosis = float((centred ** 4).mean() / spread ** 4)
    variance = 1.0 - skew * observed + (kurtosis - 1.0) / 4.0 * observed ** 2
    if variance <= 0:
        return 0.0
    return float(norm.cdf((observed - benchmark) * math.sqrt(n - 1) / math.sqrt(variance)))


def expected_max_sharpe(trials: int, variance: float) -> float:
    """The best Sharpe to expect from `trials` strategies with no edge at
    all, whose estimates vary with this variance."""
    if trials <= 1 or variance <= 0:
        return 0.0
    return math.sqrt(variance) * (
        (1.0 - EULER_GAMMA) * norm.ppf(1.0 - 1.0 / trials)
        + EULER_GAMMA * norm.ppf(1.0 - 1.0 / (trials * math.e)))


def deflated_sharpe(values: Sequence[float], trials: int,
                    trial_sharpes: Optional[Sequence[float]] = None) -> float:
    """Probability the true Sharpe is above what the best of `trials` tries
    would show by luck. With one trial this is the probabilistic Sharpe."""
    if trial_sharpes is not None and len(trial_sharpes) > 1:
        variance = float(np.var(np.asarray(trial_sharpes, dtype=float), ddof=1))
    else:
        variance = 0.0
    return probabilistic_sharpe(values, expected_max_sharpe(trials, variance))


def permutation_p_value(observed: float, null: Sequence[float]) -> float:
    """How often the null does at least as well as what was observed. The
    observation counts as one draw, so the answer is never exactly zero."""
    draws = np.asarray(null, dtype=float)
    return float((1 + (draws >= observed).sum()) / (1 + len(draws)))


def holm(p_values: Dict[Hashable, float], alpha: float = 0.05) -> Set[Hashable]:
    """The hypotheses that survive Holm's step-down correction: sorted from
    strongest, the k-th of m must reach alpha / (m - k), and the first that
    does not ends it."""
    ordered = sorted(p_values.items(), key=lambda item: item[1])
    survivors: Set[Hashable] = set()
    for rank, (key, p_value) in enumerate(ordered):
        if p_value > alpha / (len(ordered) - rank):
            break
        survivors.add(key)
    return survivors


def positive_share(values: Sequence[float]) -> float:
    """Share of a set of results that are above zero — used on the
    neighbours of a chosen parameter set: an edge that exists at one grid
    point and not at the ones beside it was fitted, not found."""
    x = np.asarray(values, dtype=float)
    return float((x > 0).mean()) if len(x) else 0.0
