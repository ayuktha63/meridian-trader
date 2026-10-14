# Engineering references

What was read in five open-source projects, what was taken from each, and
where it lives in Meridian. Patterns only: no code from any of them is in
this repository, and none was added as a dependency.

Read on 12 October 2026. Repository facts are from the GitHub API on that
date; "active" means pushed to within the previous three days.

| Project | Licence | Status | Could code be copied in? |
|---|---|---|---|
| [QuantConnect/Lean](https://github.com/QuantConnect/Lean) | Apache-2.0 | active, not archived | Yes with attribution — none was |
| [freqtrade/freqtrade](https://github.com/freqtrade/freqtrade) | GPL-3.0 | active | No: it would bind this repository to the GPL |
| [nautechsystems/nautilus_trader](https://github.com/nautechsystems/nautilus_trader) | LGPL-3.0 | active (core now in Rust) | No |
| [polakowo/vectorbt](https://github.com/polakowo/vectorbt) | Reported as "Other" — not a standard open-source licence | active | No |
| [AI4Finance-Foundation/FinRL](https://github.com/AI4Finance-Foundation/FinRL) | MIT | active | Yes with attribution — none was |

---

## QuantConnect LEAN — how an order fills on a bar

**Read:** `Common/Orders/Fills/EquityFillModel.cs` (master branch).

**What it does.** A buy limit fills only when the bar's low is *strictly
below* the limit price — reaching it is not enough. If the bar opens through
the limit in the order's favour, it fills at the open. A stop triggers on a
touch and, when the bar opens beyond it, fills at the open: "unfavorable gap
case: if the bar opens below the stop price, fill at open price". Nothing
fills on a bar that ended before the order existed ("do not fill on stale
data").

**What Meridian took.** Its own backtest filled a resting limit when a bar
merely *touched* its price — which credits fills an order at the back of the
queue would not have had. Both simulators now require the market to trade
through a resting order, entry and target alike; a stop still triggers on a
touch and fills at the open on a gap.

| | |
|---|---|
| Where | `backend/app/backtest/engine.py` (`_limit_fill`, `_exit`), `backend/app/research/simulator.py` (`_limit_fill`, `_stop_entry_fill`, `_exit`) |
| Tests | `test_backtest_honesty.py::test_a_bar_that_only_touches_a_resting_order_does_not_fill_it`, `::test_a_stop_triggers_on_a_touch`; `test_research_simulator.py::test_a_resting_limit_needs_the_market_to_trade_through_it`, `::test_a_target_needs_the_market_to_trade_through_it` |
| Measured effect | None on BTCUSD 1h: the reported walk-forward is identical under both rules (112 trades, −0.111R). Real prices rarely stop exactly on a level; the rule matters for correctness, and on coarser ticks. |

**Not taken.** LEAN gives a limit that gaps favourably *through its target*
the better open price. Meridian's simulators fill a target at its own price
and take no bonus. LEAN's separate fee, slippage and brokerage *model
objects* were the model for filing costs under named venue profiles
(`backend/app/costs.py`), in a much smaller form.

---

## Freqtrade — finding look-ahead by running it again on less data

**Read:** the `lookahead-analysis` and `recursive-analysis` documentation
pages (stable).

**What they do.** `lookahead-analysis` runs a full backtest, then re-runs
"for every entry and exit separately" on data cut off at that signal, and
compares: "This is done by not looking at the strategy code itself, but at
changed indicator values and moved entries/exits compared to the full
backtest." `recursive-analysis` recomputes indicators with different amounts
of start-up history and reports how much the last value moves — the
difference between a backtest with all of history and a live process holding
a few hundred candles. Its documentation is frank about limits: signals that
never fire are never verified.

**What Meridian took.** Both checks, for the research strategies:

- *Look-ahead:* build the strategy on the whole history, then on the history
  cut off right after a bar. If the decision at that bar differs, the full
  version was using bars that had not happened.
- *Warm-up:* build it on only the declared warm-up before a bar. If the
  decision differs, a live implementation would not trade what the research
  traded.

| | |
|---|---|
| Where | `backend/app/research/audit.py`; run on every candidate before the study computes anything (`study._audits`) |
| Tests | `test_research_strategies.py::test_no_candidate_sees_the_future_or_needs_more_history_than_it_says`, `::test_a_strategy_that_reads_the_next_bar_is_caught`, `::test_a_strategy_that_normalises_over_the_whole_history_is_caught`, `::test_a_strategy_that_needs_more_history_than_it_admits_is_caught` |
| Result | All five candidates pass both checks. Three deliberately broken strategies — a shifted index, a whole-series z-score, an under-declared warm-up — are each caught. |

The same caveat applies here as there: the audit samples bars that produced
a signal, and a branch of a strategy that never fired on the audited data
has not been checked.

**Not taken.** Freqtrade's hyper-parameter optimiser. The study here does the
opposite of optimising: 33 combinations declared in advance, one set per
strategy for the whole universe.

---

## NautilusTrader — an order's states as a table

**Read:** the *Orders* concept page (latest). The Cython file the state
machine used to live in (`nautilus_trader/model/orders/base.pyx`) no longer
exists at that path — the core has moved to Rust — so the table itself was
not read, only its documentation.

**What it does.** Fourteen statuses, a set of them terminal (DENIED,
REJECTED, CANCELED, EXPIRED, FILLED, VOIDED), and "the order model validates
the complete transition set". PENDING_UPDATE and PENDING_CANCEL are both
"open and in-flight". A late fill after an expiry still updates the order.

**What Meridian took.** The idea that the lifecycle should be one table that
can be checked, not a dozen assignments that have to be read. Meridian's
table is its own — its states are an execution's, not an order's.

| | |
|---|---|
| Where | `backend/app/broker/order_states.py` |
| Tests | `test_observability.py::test_the_ordinary_life_of_an_order_is_in_the_table`, `::test_what_is_finished_stays_finished`, `::test_a_finished_execution_cannot_be_brought_back`, `::test_any_other_surprise_is_reported_and_allowed` |

**Deliberately different.** Nautilus raises on any invalid transition.
Meridian raises only for a change out of a finished state; any other change
the table does not list raises a critical alert and is allowed. The table
has never been checked against the real broker, and refusing a transition it
did not foresee could stop a real position from being given its stop.

---

## vectorbt — many configurations, quickly, then check the survivors properly

**Read:** the repository's description and licence field only. Given the
licence, the source was not studied — what follows is the project's stated
approach, not a reading of its code.

**What Meridian took.** The division of labour, not the library: screen a
whole parameter grid cheaply, and trust nothing until an event-driven
simulation agrees. Signals are computed with vectorised pandas operations
over the full series; every trade is then simulated bar by bar. The study
evaluates 198 configurations on nine markets in under a minute.

| | |
|---|---|
| Where | `backend/app/research/strategies.py` (signals), `backend/app/research/simulator.py` (trades), `backend/app/research/study.py` (`research_grids`) |
| Cross-check | `test_research_simulator.py::test_it_agrees_with_the_engine_backtest_trade_for_trade` — the research simulator and the engine's own backtest, written independently, produce the same 220 trades on a random market |

---

## FinRL — train, validate, test, and compare against something simple

**Read:** the repository's description and licence field only. No code was
studied, and nothing was adopted beyond the discipline the project is known
for.

**What Meridian took.** The three-way chronological split, and the rule that
a result is only a result next to a baseline. Here: research before
2024-07-01, validation to 2025-10-01, holdout after; and every candidate is
compared with buy-and-hold and with its own trades moved to random bars.

| | |
|---|---|
| Where | `backend/app/research/study.py` (`stage_window`, `matched_random`, `_benchmarks`) |
| Tests | `test_research_study.py::test_stages_do_not_overlap_and_cover_the_history`, `::test_a_stage_is_computed_on_nothing_later_than_its_own_end`, `::test_random_entries_keep_the_exits_and_lose_the_skill` |

**Not taken: reinforcement learning.** Nothing here supports it. Every
intraday family tested loses to its own costs, and the one family with
research-stage support is a 100-bar breakout with a trailing stop. There is
no simpler baseline for a learned policy to beat yet.

---

## Not from any of these

Two pieces of the statistics come from papers, not projects:

- **Deflated Sharpe ratio** — Bailey & López de Prado (2014). Implemented in
  `backend/app/research/stats.py`, with one stated departure: the variance
  of a no-edge Sharpe estimate is taken as its sampling variance rather than
  from the spread of the trials, because the trials here are different
  strategies whose results differ for real reasons. A test shows what the
  paper's version does when misapplied that way
  (`test_research_stats.py::test_the_bar_is_about_luck_not_about_how_different_the_tries_were`).
- **Holm's step-down correction**, already used by the live gate, now also
  across the families that reach validation.
