"""The statistics, checked on data whose answer is known in advance."""
from __future__ import annotations

import numpy as np
import pytest

from app.research import stats
from app.services.edge import holm_survivors


def test_an_interval_covers_the_truth_about_as_often_as_it_claims():
    """Two hundred samples from a distribution whose mean is 0.1: a 90%
    interval should miss it about one time in ten."""
    rng = np.random.default_rng(1)
    hits = 0
    for trial in range(200):
        sample = rng.normal(0.1, 1.0, 150)
        estimate = stats.mean_interval(sample, level=0.90, resamples=600, seed=trial)
        hits += estimate.low <= 0.1 <= estimate.high
    assert 165 <= hits <= 195


def test_trades_that_cluster_get_a_wider_interval():
    """Wins and losses arriving in runs carry less information than the same
    numbers arriving independently. Resampling one at a time hides that."""
    rng = np.random.default_rng(5)
    runs = np.repeat(rng.normal(0.0, 1.0, 40), 10) + rng.normal(0.0, 0.2, 400)
    one_at_a_time = stats.mean_interval(runs, block=1)
    in_blocks = stats.mean_interval(runs, block=20)
    assert (in_blocks.high - in_blocks.low) > 1.8 * (one_at_a_time.high - one_at_a_time.low)
    assert stats.mean_interval(runs).block == stats.block_length(400) == 7


def test_the_bootstrap_is_reproducible_and_bounded():
    sample = np.random.default_rng(2).normal(0.3, 1.0, 80)
    first, again = stats.mean_interval(sample, seed=11), stats.mean_interval(sample, seed=11)
    assert first == again
    assert first.low < first.mean < first.high
    assert stats.mean_interval(sample, seed=12) != first
    all_wins = stats.mean_interval(np.full(50, 0.5), resamples=2_000)
    assert all_wins.p_value == pytest.approx(1 / 2_000), "never zero: not seen is not impossible"
    assert stats.mean_interval([]).p_value == 1.0


def test_no_edge_is_reported_as_no_edge():
    noise = np.random.default_rng(3).normal(0.0, 1.0, 300)
    estimate = stats.mean_interval(noise)
    assert estimate.low < 0 < estimate.high
    assert estimate.p_value > 0.05


def test_probabilistic_sharpe_grows_with_evidence():
    rng = np.random.default_rng(4)
    few, many = rng.normal(0.1, 1.0, 30), rng.normal(0.1, 1.0, 3_000)
    assert stats.probabilistic_sharpe(many) > 0.99
    assert stats.probabilistic_sharpe(few) < stats.probabilistic_sharpe(many)
    assert stats.probabilistic_sharpe(np.zeros(50)) == 0.0
    assert stats.probabilistic_sharpe([0.1]) == 0.0


def test_the_best_of_many_tries_has_to_clear_a_higher_bar():
    assert stats.expected_max_sharpe(1, 0.01) == 0.0
    ten, hundred = stats.expected_max_sharpe(10, 0.01), stats.expected_max_sharpe(100, 0.01)
    assert 0 < ten < hundred
    # Bailey & López de Prado's worked case: 100 trials, variance 0.5 → about 1.79.
    assert stats.expected_max_sharpe(100, 0.5) == pytest.approx(1.79, abs=0.02)


def test_a_search_over_noise_does_not_survive_deflation():
    """Fifty strategies with no edge. The best looks respectable on its own
    and is nothing once the search is accounted for."""
    rng = np.random.default_rng(6)
    tries = [rng.normal(0.0, 1.0, 250) for _ in range(50)]
    sharpes = [stats.sharpe(t) for t in tries]
    best = tries[int(np.argmax(sharpes))]
    assert stats.probabilistic_sharpe(best) > 0.95, "taken alone, it would have passed"
    assert stats.deflated_sharpe(best, trials=50, trial_sharpes=sharpes) < 0.80
    assert stats.deflated_sharpe(best, trials=1) == stats.probabilistic_sharpe(best)


def test_a_real_edge_does_survive_it():
    rng = np.random.default_rng(7)
    tries = [rng.normal(0.0, 1.0, 400) for _ in range(20)] + [rng.normal(0.35, 1.0, 400)]
    sharpes = [stats.sharpe(t) for t in tries]
    assert stats.deflated_sharpe(tries[-1], trials=len(tries), trial_sharpes=sharpes) > 0.95


def test_a_permutation_p_value_counts_the_observation_itself():
    null = np.linspace(-1.0, 1.0, 199)
    assert stats.permutation_p_value(2.0, null) == pytest.approx(1 / 200)
    assert stats.permutation_p_value(-2.0, null) == 1.0
    assert stats.permutation_p_value(0.0, null) == pytest.approx((1 + 100) / 200)


def test_holm_matches_the_gate_that_already_uses_it():
    p_values = {"a": 0.001, "b": 0.02, "c": 0.03, "d": 0.5}
    assert stats.holm(p_values) == holm_survivors(p_values) == {"a"}
    assert stats.holm({"a": 0.001, "b": 0.012, "c": 0.02}) == {"a", "b", "c"}
    assert stats.holm({}) == set()


def test_positive_share_of_the_neighbours():
    assert stats.positive_share([0.2, 0.1, -0.1, 0.3]) == 0.75
    assert stats.positive_share([]) == 0.0
