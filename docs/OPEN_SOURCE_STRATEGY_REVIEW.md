# Open-source strategy review

> **Update, 14 October 2026:** five of the repositories this brief named
> (EDGE, vectorbt-backtesting-skills, pandas-ta, nse-orb-backtest,
> Options-Trading-Bot-India) *were* opened and read afterwards; see
> `OPEN_SOURCE_REPOSITORY_AUDIT.md`. The statement below that no third-party
> source file was opened is true of this document's own session and of the nine
> repositories it lists.

**Read this first.** In this session **no third-party source file was
opened.** The brief asked for an inspection of nine repositories; what was
done instead, and what that supports:

| Done | Evidence |
|---|---|
| Identity, licence, last push, archived? for all nine | GitHub metadata API, 13 Oct 2026 — `OPEN_SOURCE_LICENSE_REVIEW.md` |
| Patterns taken from LEAN, Freqtrade, NautilusTrader, vectorbt, FinRL | **A previous session's notes** (`docs/research/REFERENCES.md`), which say what was read (LEAN's `EquityFillModel.cs`, Freqtrade's `lookahead-analysis` and `recursive-analysis` documentation, NautilusTrader's orders concept page; vectorbt and FinRL by description only). Not re-verified here |
| The four Indian-market repositories (vijaxx, builditwithgk, Gaurav1112, Ayush1906saxena) and the awesome list | Metadata only. **No claim is made about their strategies, their results or their code.** Their READMEs' claims were not read and are not repeated |

Everything below is therefore about Meridian's own implementation of the
ideas, which is checked by tests, not about the projects.

## What the ideas became in Meridian

| Idea (source, per `REFERENCES.md`) | Meridian code | Test that holds it |
|---|---|---|
| Re-run a strategy on truncated history; a decision that changes used the future (Freqtrade docs) | `research/audit.py`; applied to all ten NSE candidates | `test_every_candidate_passes_the_look_ahead_and_warmup_audit` (10) |
| A resting limit order fills only when traded through; a stop fills at the open on a gap (LEAN fill model) | `research/simulator.py`, `backtest/engine.py` | `test_research_simulator.py` |
| An order's lifecycle as one checked table (NautilusTrader concepts) | `broker/order_states.py` | `test_reconciler.py` |
| Screen cheaply, confirm in an event-driven simulation (vectorbt, by description) | `research/study.py` structure | — |
| Three-way chronological split; nothing is a result without a baseline (FinRL, by description) | research / validation / holdout; matched-random baseline | `test_research_study.py`, `test_india_strategies.py` |

## Ideas not adopted, with the reason

| Idea | Why not |
|---|---|
| An event-driven engine (Nautilus, backtrader) | Meridian's research is bar-based with a one-bar delay; swapping the engine is not justified by any defect found. The defects that mattered were in data, costs and bar construction |
| Vectorised parameter sweeps (vectorbt) | Licence; and 40 configurations do not need it |
| Walk-forward optimisation frameworks | The study fixes its grid and stretches in advance; there is nothing to optimise on the later stretches |
| Strategy code from the Indian repositories | Unread, mostly unlicensed or 0-star; not a basis for anything |

## Limits

A real review means reading `freqtrade/strategy/interface.py`'s signal
lifecycle, `freqtrade/optimize/backtesting.py`'s fill handling,
`nautilus_trader/execution/` and the order state machine, and the Indian
repositories' backtests — with their licences in hand — and recording file,
function, test and limitation for each. That has not been done. **Next
action:** read those files, in a clean-room spirit (notes, not copies), and
add a row per file here.
