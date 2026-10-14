# Open-source repository audit — the five named repositories

**14 October 2026.** Each repository was cloned (`--depth 1`) into scratch space
and read. Nothing from any of them is copied into Meridian. This document
supersedes the "no third-party source file was opened" admission in
`OPEN_SOURCE_STRATEGY_REVIEW.md` **for these five**; the nine others in that
document remain metadata-only.

| # | Repository | Commit (default branch) | Last commit | Licence | Verdict |
|---|---|---|---|---|---|
| 1 | https://github.com/ajxv/edge | `2aa1a0073f4cf73c86c3502e53d57052201357f1` (`main`) | 10 Aug 2026 | MIT (`LICENSE`, "Copyright (c) 2026 ajxv") | **Inspected; patterns noted; no code adopted; one strategy class excluded by scope** |
| 2 | https://github.com/marketcalls/vectorbt-backtesting-skills | `05d9e8b12fa408f1373c6b79620f8a5473fb874c` (`master`) | 12 Jul 2026 | README says MIT; **no LICENSE file** | **Inspected; guidance only; vectorbt itself not adopted** |
| 3 | https://github.com/twopirllc/pandas-ta | **unavailable** | — | PyPI `pandas-ta 0.4.71b0`: licence field empty | **Unavailable; not integrated** |
| 4 | https://github.com/arshadakl/nse-orb-backtest | `6130c7a2526235870830b45c052c86586582c5a6` (`main`) | 3 May 2026 | README says MIT; **no LICENSE file** | **Rules re-implemented from source, reproduced exactly, then tested: `ORB_REPO_RESULTS.md`** |
| 5 | https://github.com/SukhendraRompally/Options-Trading-Bot-India | `e9fd3bed0a07a84403f3823fbdcdc96993205fa4` (`main`) | 29 Aug 2026 | MIT (`LICENSE`, "Copyright (c) 2026 Sukhendra Rompally") | **Inspected for patterns; none worth taking; options logic excluded** |

## 1. EDGE

**Files read:** `README.md`, `DISCLAIMER.md`, `LICENSE`, `requirements.txt`,
`src/strategies/base_strategy.py`, the head of `momentum_breakout.py`,
`docs/strategy_research_log.md` (verdicts and parameter sweeps), the fee
functions in `backtest/backtest_engine.py`. **Not read in full:**
`sr_fvg_breakout.py`, `options_directional.py` (options: out of scope), `bot.py`,
the services and broker adapters, `deploy/`.
**Dependencies:** Python ≥ 3.10; pinned `smartapi-python 1.5.5`, `pandas 2.2.3`,
`numpy 2.1.0`, `pydantic 2.12.5`, `neo-api-client` from a git SHA.
**Its tests:** 100 passed (Python 3.11, throwaway venv, run 14 Oct 2026).

**What it is.** A broker-agnostic intraday engine for MCX/NSE/NFO built on a
strategy-plugin core: a strategy is a stateless class with
`calculate_indicators(df)` and `generate_signal(ind_df)`, registered by decorator
with a Pydantic parameter model; exits live in a separate service; a bar-by-bar
backtester uses the same interface; `walk_forward.py` issues a Go/No-Go verdict.

**What Meridian took from the architecture: nothing new, because it already
has it.** `research/strategies.py` and `india_strategies.py` define a
`Strategy` as a pure function of bars returning a `Plan` of signals; signal
generation is separate from simulation (`simulator.py`) and from any order
(`broker/` is unimportable from `research/`, tested); each candidate is selectable
by name and its parameter grid is frozen in a hashed protocol; `audit.py`
re-runs each strategy on truncated history to detect look-ahead. Adding a second
engine would duplicate this.

**What its research log says, and how far to trust it.** Its own record (not
reproduced here: its data is not available to me) is that seven of eight
strategies tried had no edge, including every one on the permitted universe:

| EDGE strategy | Instrument | Its verdict |
|---|---|---|
| Supertrend flip | NSE equities 15m | NO-GO (win rate never above 45%) |
| Opening Drive Breakout (an ORB) | NSE equities 15m | NO-GO (9 exit variants, −0.08R to −0.13R OOS) |
| N-bar momentum breakout | NIFTY/BANKNIFTY spot 5m | NO-GO (40 configurations, −0.249R to −0.440R) |
| VWAP-band fade | NIFTY/BANKNIFTY 5m | NO-GO |
| VWAP pullback | NSE equities 15m/60m | NO-GO (0 positive symbols) |
| Range scalp (RSI + S/R) | NIFTY/BANKNIFTY 5m | NO-GO |
| S/R breakout + FVG | MCX SILVERM/GOLDGUINEA | NO-GO |
| **N-bar momentum breakout** | **MCX SILVERM 15m, BUY_ONLY** | **"GO": OOS +0.193R / +0.218R, 85–89 trades** |

The single positive result is **outside the permitted universe (MCX)**. Even
on its own terms it is short of this project's gate: 85–89 out-of-sample trades
against 200 required; picked from a 32-combination sweep after the both-sides
and sell-only variants lost (`BUY_ONLY` is a post-hoc choice); one instrument
during a +330% twelve-month rally by the log's own account; in-sample +0.018R.
Its fee model is Kotak Neo's zero-brokerage schedule, not Angel One's.
**Decision: not adopted; classified INSUFFICIENT_DATA and out of scope.**

Where it is useful is as independent prior evidence: EDGE's author, on different
code and different data, found the same thing Meridian's 15-minute study found
(`NSE_15M_RESEARCH_RESULTS.md`): Supertrend, an opening-range breakout, and
VWAP rules do not survive costs on NSE equities. One caution it raises that
applies here: its log notes **cash-index bars from Angel One carry zero volume**,
so a volume filter on NIFTY spot does nothing. The ORB study below uses NIFTY
only for price (its range high), never for volume.

## 2. VectorBT Backtesting Skills

**Files read:** `README.md`, `.claude/skills/vectorbt-expert/rules/` —
`indian-market-costs.md`, `pitfalls.md`, `walk-forward.md`, `robustness-testing.md`
(headings and the cost table), `backtesting/SBIN_ema_crossover_backtest.py`
(calls). `requirements.txt` is UTF-16 and pins `vectorbt 0.28.4`, `numba`,
`TA-Lib`, `openalgo`.
**Tests:** none (it is a set of agent skills plus four example scripts).

**What it is.** Markdown guidance for an AI coding agent, and example scripts
that call `vbt.Portfolio.from_signals`. It is a how-to, not a library.

**Cost table versus Meridian's.** Its intraday-equity row — brokerage Rs 20 per
order, STT 0.025% on the sell side, exchange 0.00307%, GST 18% on brokerage +
exchange, SEBI 0.0001%, stamp 0.003% on the buy side — agrees with the rates in
`costs_india.py`. Its way of applying them does not: it folds statutory charges
into one percentage per side and adds a flat Rs 20 per order. Angel One's brokerage
is the lower of Rs 20 and 0.1% of the order, minimum Rs 5 (`costs_india.py`), so
a flat Rs 20 is wrong for small orders, and the weight of the fixed parts depends
on order size. That dependence is exactly what decided the 15-minute study (Rs 12+ per
round trip against a Rs 500 risk budget). `india_costing.py` charges order by
order. **Not adopted.**

**Ideas in it and where Meridian stands:**

| Idea (file) | In Meridian |
|---|---|
| Shift indicator levels by one bar; "removing the last bar must change no earlier signal" (`pitfalls.md`) | `research/audit.py` does exactly this for every candidate; `test_indicator_references.py` now does it for every adopted indicator |
| Survivorship bias warning for Nifty 50 backtests (`pitfalls.md`) | Stated on every result; no membership history exists to correct it |
| Walk-forward efficiency, anchored/rolling (`walk-forward.md`) | Stretches are fixed and opened in order; nothing is fitted, so there is nothing to walk forward |
| Entry/exit delay test, cross-symbol, period stability (`robustness-testing.md`) | Delay (`orb_repo_study`, one bar), breadth by stock, by month; all measured |
| Monte Carlo trade shuffle, price-noise perturbation (`robustness-testing.md`) | **Not built.** Block bootstrap and week-clustered resampling cover the order dependence; a noise test is not run |

**Why vectorbt itself is not used.** Not installed (numba/TA-Lib are heavy on
a machine with ~1 GB free), so **no claim is made about its fill semantics** — the
brief says not to assume them, and they were not tested. The event simulator
already expresses a gap-through stop, a limit that must be traded through and
order-level Indian charges, and has tests for each. Two fixed variants and 14
stocks need no sweep engine. A decision, not an oversight — and if a sweep over many
parameters is ever run, it is the right tool for screening, with the event
simulator confirming.

## 3. Pandas TA

**Unavailable.** `git clone https://github.com/twopirllc/pandas-ta.git` →
"Repository not found"; `api.github.com/repos/twopirllc/pandas-ta` → 404. PyPI
still serves `pandas-ta 0.4.71b0` (beta) whose metadata points at that dead
repository and requires **Python ≥ 3.12**; this project runs 3.9 (CI: 3.11). No
source, licence text or tests could be inspected, so it is neither verified nor
integrated.

**What was done instead.** The indicators Meridian's strategies use are its own
(`analysis/indicators.py`). `tests/test_indicator_references.py` (33 tests) now
checks EMA, SMA, Bollinger, MACD, true range, ATR, RSI, ADX/±DI and VWAP against
textbook definitions written longhand, plus, for eight indicators: no value depends
on a later bar (history cut at four points), too little history yields NaN, a NaN
bar does not poison later values, and the input frame is not mutated. Findings:

* The module docstring claimed pandas-ta/TA-Lib were "used for cross-validation
  in the test suite". They were not, and no such test existed. **Corrected.**
* Wilder smoothing is seeded with the first value, the textbook with the mean of
  the first n. The tests measure the gap (below 1e-5 after a warm-up of
  `ln(1e-6)/ln(1−1/n) + n` bars: 200 for n = 14) rather than assuming it.
  RSI/ATR/ADX are matched to the textbook after 40n/600/700+ bars.
* The MACD signal line is seeded on the first defined MACD value, not bar 0
  (gap 0.8^k, gone by bar 200).
* `vwap()` anchors sessions to the UTC day; right for NSE (03:45–10:00 UTC) and
  wrong for any session that crosses 00:00 UTC. A zero-volume bar counts with weight 1.
* Duplicate timestamps and missing candles are not handled in `indicators.py`;
  they are refused upstream by the dataset quality check (`research/quality.py`).

## 4. NSE ORB Backtest

**Files read:** `README.md`, `angel_orb_backtest.py` (562 lines; `simulate_day`,
constants, filters, reporting), `bulk_backtest.py` (calls), `requirements.txt`,
`backtest_HDFCBANK.csv`, `backtest_RELIANCE.csv`. **Tests:** none in the repository.
**Dependencies:** `smartapi-python`, `growwapi`, `pyotp`, `python-dotenv`,
`pandas`, `pyarrow`, `requests`.

**Rules (from executable code, not the README):** see `ORB_REPO_PROTOCOL.md`.
Long only; six 5-minute range bars 09:15–09:45; range width ≥ 0.20% of the open;
entry at the open after the first closing break of the range high, until 14:30;
stop = range low; target = entry + 1.5 × (entry − low); exit at the 15:15 bar;
one trade a day. Optional filters (on by default): close ≥ 0.15% above the high,
breakout-bar volume ≥ 1.5× the range bars' mean, skip Friday, NIFTY above its own
range high.

**Defects in the source's simulation** (each handled in the executable layer;
none "fixed" in the published layer, which reproduces them on purpose):

1. **No costs and no slippage.** `grep` for brokerage, charges, slippage,
   commission finds nothing.
2. **A stop is filled at the range low even when the bar opened below it.**
3. **The entry bar's own range is never examined**, so a bar that fills at its open and
   then trades through the stop is not stopped.
4. **`qty = max(1, …)`** forces a trade the risk budget did not allow.
5. **The 15:15 exit takes the close of the 15:15 bar** (15:20).
6. **No look-ahead in the decision**: the breakout is decided on a closed bar,
   the entry is the next open. That is correct, and it is kept.
7. **Its evidence is not its own**: the README attributes "57% win rate" to "a
   published Nifty backtest (Jul–Oct 2025)"; the two committed result files hold 5
   trades each.

**Fidelity.** `orb_repo.play_published` equals the source's `simulate_day` on every
one of 2,642 trades (3 filter configurations × 40 random 30-day histories),
checked by importing the source from a scratch checkout with its broker imports
stubbed. That script is deliberately not committed (it would import unlicensed
code); the committed test uses a row-by-row oracle written from the rules.

**Result:** `ORB_REPO_RESULTS.md`.

## 5. Options Trading Bot India

**Files read:** `README.md`, `LICENSE`, `risk_manager.py`, `state_store.py`,
`engine.py` (head), `broker/` (sizes), `tests/`. **Its tests:** 31 passed
(Python 3.11, throwaway venv, run 14 Oct 2026). **Dependencies:**
`smartapi-python`, `logzero`, `websocket-client`, `pyotp`, `python-dotenv`,
`streamlit`, `pandas`.

**Looked for, per the brief:** paper fills, persistent order records,
idempotency, duplicate-order prevention, broker reconciliation, partial fills,
risk limits, emergency shutdown, restart recovery.

| Pattern | In the bot | In Meridian |
|---|---|---|
| Paper fills | `paper_broker.py` (287 lines): simulated book, charges | `services/paper.py`; quote-based fills, refused without a fresh quote |
| Persistent trades/equity | SQLite `StateStore` (WAL), trades and equity snapshots | `db/` models; `live_executions`, `paper_trades`, audit log |
| Idempotency / duplicate orders | **none** (grep: no `idempot`, `client_order`, `duplicate`) | `risk_gate._check_duplicate` (recommendation-level guard) |
| Reconciliation with the broker | **none** | `broker/angelone/reconciler.py`, `order_states.py` |
| Partial fills | **none** | order-state table |
| Restart recovery | dashboard survives a restart; **positions are not recovered** | the reconciler re-protects positions that earlier LIVE orders opened (`live_executions`) |
| Kill switch | **none** | persisted, trips closed |
| Risk | daily-drawdown halt (resets daily), per-order lot cap, trailing % stop on option premium | independent gate, 1% cap, peak-to-trough lock |

**Decision.** Nothing to take. It is a small, tidy single-strategy bot; the
engineering the brief hoped to borrow is what Meridian already has and the
bot lacks. Its premium-based trailing stop, lot sizing and contract logic are
options-specific and excluded.

## What was not done

* No repository's reported performance was reproduced: none came with data, and
  the ORB repository's committed results are 5 trades per stock.
* EDGE's `walk_forward.py`, `exit_matrix.py` and `performance_reporter.py` were
  not read line by line; the research log was read instead.
* `bulk_backtest.py` was searched, not read in full.
