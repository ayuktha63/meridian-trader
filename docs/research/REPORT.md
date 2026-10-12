# Research report — 12 October 2026

**No strategy in this repository has a demonstrated edge. Nothing is
eligible for live trading, and the recommendation is to remain in research.**

Three things were established, and the first matters most:

1. **The reported backtest results were real and were about the wrong
   venue.** They reproduce to the last digit from frozen data. They were
   charged a fee belonging to a different product; charged as the spot books
   they are, the live engine loses on every market tested.
2. **Nothing intraday survives its costs.** The engine, the five plays and
   five new candidates all lose or break even on hourly bars, on 2,300 to
   11,500 trades each. The engine's confidence score does not distinguish
   winners from losers at all.
3. **One idea is worth watching: slow trend-following on 4-hour bars.** It
   passed every test of a pre-registered protocol but one, and it was
   rejected. It has been registered for testing on bars that do not exist
   yet rather than given a second look at history.

Everything below is reproducible from the experiment records named. Related
documents: [BASELINE.md](BASELINE.md) (the repository as found, and the
defects), [STRATEGIES.md](STRATEGIES.md) (each hypothesis and its result),
[REFERENCES.md](REFERENCES.md) (what was learned from other projects).

---

## 1. Initial state

`main` at `876b83b`, fifteen commits ahead of `origin/main`, clean. 362
backend tests passing, lint and typecheck clean, the application running in
PAPER mode. Work was done on a new branch, `research/quant-pipeline`. Nothing
was pushed, and `main` was not touched. Another session was working in the
same directory at the same time — see §13. Detail: [BASELINE.md](BASELINE.md).

## 2. Defects discovered

Seven, each found by reading or running the code, each with a test that
fails without its fix. Detail and evidence: [BASELINE.md](BASELINE.md).

| # | Defect | Consequence | Status |
|---|---|---|---|
| 1 | A result recorded nothing about the data or code behind it | Unreproducible; re-runnable until it passed | Fixed |
| 2 | A walk-forward on **simulated** bars could satisfy the live gate; a failed history request left simulated bars under a live venue's name, mixed with its real quotes | A real order cleared by evidence about a random-number generator | Fixed |
| 3 | Crypto charged 0.05% a side — the venue's futures fee — on spot books, with shorts simulated on a book that cannot be shorted | Costs understated about fivefold; the one positive result was an artefact | Fixed |
| 4 | History paging stopped at the first hole in an exchange's data | Eight years of history returned as two | Fixed |
| 5 | A resting limit order filled on a touch | Fills credited that an order in the queue would not get | Fixed (no measured effect) |
| 6 | A passing backtest was the whole of the evidence for a live order | No check of code version, venue, random baseline, parameter stability or repeat runs; nothing between a pass and an order | Fixed |
| 7 | The confidence score is presented as a probability and is not one | 30% of setups win whether it says 50 or 70+ | **Reported, not fixed** — see §13 |

## 3. Open-source projects studied

| Project | What was read | Pattern taken | Where |
|---|---|---|---|
| QuantConnect LEAN | `EquityFillModel.cs` | A resting order fills only when traded through; a stop fills at the open on a gap | both simulators |
| Freqtrade | `lookahead-analysis`, `recursive-analysis` docs | Re-run a strategy on truncated history; a decision that changes was using the future | `research/audit.py` |
| NautilusTrader | Orders concept page | The lifecycle as one checked table | `broker/order_states.py` |
| vectorbt | description only (non-standard licence) | Screen widely and cheaply, confirm in an event-driven simulation | `research/study.py` |
| FinRL | description only | Three-way chronological split; nothing is a result without a baseline | `research/study.py` |

No code was copied and no dependency was added. Licences, maintenance
status, the tests for each pattern and what was deliberately *not* taken:
[REFERENCES.md](REFERENCES.md).

## 4. Files created and modified

**Created** — `backend/app/research/` (`datasets`, `quality`, `versions`,
`stats`, `metrics`, `simulator`, `strategies`, `audit`, `study`,
`engine_evidence`, `eligibility`, `approvals`, `forward`, `registry`, `run`);
`backend/app/routers/research.py`; `backend/app/observability.py`;
`backend/app/broker/order_states.py`; nine test modules
(`test_provenance`, `test_research_data`, `test_research_stats`,
`test_research_simulator`, `test_research_strategies`, `test_research_study`,
`test_research_eligibility`, `test_research_api`, `test_observability`) and
additions to `test_history` and `test_backtest_honesty`; `docs/research/`.

**Modified** — `costs.py` (venue profiles), `backtest/engine.py` (fills,
venue rules, data source, runs on a snapshot), `data/feed.py`,
`data/store.py`, `data/providers/coindcx.py` and `base.py` (provenance, deep
history), `services/edge.py` (eligibility and approval in the gate),
`db/models.py` (experiments, approvals), `routers/backtest.py`,
`routers/market.py`, `main.py`, `config.py`, `schemas/journal.py`; the
Backtest panel and types in the frontend; `README.md`, `TRADING.md`,
`.env.example`, `.gitignore`.

## 5. Architecture changes

**Research is a separate package that cannot place an order.** It reads
history, simulates and records. Its only route into live trading is
`eligibility.evaluate`, which can refuse and cannot approve.

**Evidence is specific and perishable.** A result is tied to a snapshot's
hash and to fingerprints of the strategy, execution and cost code. The gate
compares those fingerprints with the code that is running; edit the engine
and its evidence stops counting.

**The live gate got stricter, in two ways.** It asks more of the evidence —
real data that passed quality checks, the right venue, better than random
entries, stable across parameters, not the seventh attempt, twenty agreeing
paper trades. And when all of it holds the answer is ELIGIBLE FOR HUMAN
REVIEW: a person's approval for that code version on that market has to be
on record before an order is sent. `LIVE_BROKER_VALIDATED`, false by
default, holds everything one step short of that until the order path has
been checked with a real order.

**Costs belong to a venue.** One model still serves paper trading, both
simulators and the live limits, but every figure is filed under a named
profile and a result records which one charged it.

Nothing was replaced wholesale. The engine, the reconciler, the risk gate's
other checks and the existing backtest are as they were, apart from the
changes listed.

## 6. Strategies tested, and hypotheses rejected

### The protocol

Fixed in commit `22d04f9`, before any result on real data. Five hypotheses,
33 parameter combinations declared in advance, three timeframes, two
venues: **198 configurations**, all counted. History cut by date — research
to 30 June 2024, validation to 30 September 2025, holdout after. One
parameter set per strategy and timeframe for all nine markets.

To leave the research stretch a family needed all of: 200 trades; a
week-clustered 90% interval above zero; a deflated Sharpe of 0.95 after all
198 configurations; 60% of its own grid profitable; 60% of markets
profitable; still positive at 1.5× costs; better than its own trades moved
to random bars. Every candidate was first checked for look-ahead.

### The result

Experiment `exp-20261012-130241-863284`. Re-run as
`exp-20261012-141614-ea32de`: identical in every figure.

**Twenty-seven families rejected, three with too few trades to judge. The
validation and holdout stretches were never opened.**

| Hypothesis | Verdict | Why |
|---|---|---|
| Mean reversion in ranges | **Rejected** | Negative on every timeframe and venue: −0.10R to −0.29R |
| Buying dips in a trend | **Rejected** | Zero or negative everywhere: −0.18R to +0.03R |
| Breakout from compression | **Rejected** | Negative on 1h; small on 4h and daily, and nowhere near the bar once deflated |
| Time-series momentum | **Rejected** | Large averages made by one trade: +670R, four-fifths of the total |
| Trend-following (N-bar breakout, trailing stop) | **Rejected** — narrowly | See below |
| *Anything on hourly bars* | **Rejected** | Every family negative or negligible after costs |

### The near miss

Trend-following on 4-hour bars — a close beyond the 100-bar extreme, a stop
four ATRs away that trails — on the research stretch, nine markets pooled:

| | Perpetual, long and short | Spot, long only |
|---|---|---|
| Trades | 1,021 | 588 |
| Expectancy after costs | **+0.254R** | **+0.371R** |
| 90% interval, clustered by week | +0.146R … +0.369R | +0.218R … +0.529R |
| Longs / shorts | +0.396R / +0.061R | +0.371R / — |
| Markets profitable | 8 of 9 | 8 of 9 |
| Settings profitable | 8 of 9 | 7 of 9 |
| Years profitable | 7 of 7 | 5 of 7 |
| Largest single trade | 6% of the profit | 8% of the profit |
| Costs it could bear | 6.3× those modelled | 5.9× |
| Its own trades at random bars (200 draws) | −0.003R; none did as well | +0.023R; none did as well |
| Annualised return at 1% risk a trade | 44% | 37% |
| Deepest drawdown | 25R | 25R |
| **Deflated Sharpe, 198 tries** | **0.87** — needed 0.95 | **0.91** — needed 0.95 |

It failed one test. The deflated Sharpe asks how good the best of N tries
would look by luck, and the protocol counted N as 198. Those 198 are five
ideas with neighbouring settings on overlapping timeframes; measured by how
much they actually differ they amount to about 15.5 independent tries, and
counted that way the same result scores 0.99 and 1.00
(`exp-20261012-130832-e11ca8`).

**That number has not been used to change the verdict.** Loosening a bar
after seeing a result just miss it is how a search becomes a finding. The
protocol said 198; the result is REJECTED.

Two cautions about the result itself, either way:

- **It is mostly being long.** The nine coins rose between 2.4× and 94× over
  the stretch. Buying and holding them returned far more, with drawdowns of
  76% to 96%. The strategy's case is the drawdown — 25% — not the return,
  and its shorts barely paid.
- **It is a research-stretch result.** It has seen nothing after June 2024.

### What was done instead

The three trend-following families that came closest were **registered for
forward watching** (`exp-20261012-140752-55154f`, `-140756-c38be3`,
`-140759-da0b7f`): parameters frozen, to be traded in simulation only on
bars that begin after 12 October 2026. That costs nothing of the unopened
history and cannot be tuned. `python -m app.research.run forward --refresh`
updates it; thirty forward trades are needed before it says anything, which
on nine markets is a few months.

**The one decision left open** is whether to also test it on the validation
and holdout stretches now, under the less conservative count of tries. It
is defensible — the confirmatory thresholds would be untouched, and counting
independent tries is what the method intends — and it is also a rule
changed after the fact. It consumes history that cannot be un-seen. It has
not been done, and is for whoever owns this research to decide.

## 7. Before and after: the live engine

Walk-forward on frozen snapshots, 4,000 bars out of sample (167 days),
parameters chosen on one stretch and traded on the next.

| Market | As reported — 0.05% a side, long and short | Corrected — the spot book it is: 0.236%, long only |
|---|---|---|
| BTCUSD 1h | 112 trades, 31% won, **−0.111R** (−0.314 … +0.096) | 16 trades, 25% won, **−0.728R** (−1.237 … −0.188) |
| ETHUSD 1h | 51 trades, 39% won, **+0.217R** (−0.137 … +0.584) | 35 trades, 34% won, **−0.479R** (−0.922 … −0.021) |
| SOLUSD 1h | 86 trades, 29% won, **−0.135R** (−0.358 … +0.103) | 31 trades, 39% won, **−0.112R** (−0.552 … +0.339) |

The left column reproduces the earlier report exactly. Charged as the
perpetual (0.059% taken, 0.024% resting, with funding) the three are −0.110R,
+0.226R and −0.132R: the futures venue is where the old numbers belonged.

Experiments, in the order generic / perpetual / spot — BTC:
`exp-20261012-140538-138bf2`, `-140556-e21cd2`, `-140604-8db980`; ETH:
`-140954-6b7513`, `-141005-0e7257`, `-141015-70e995`; SOL:
`-141335-13f389`, `-141346-506797`, `-141354-37904c`. Nine earlier runs at
3,200 bars out of sample are also on record (`-1308…` to `-1314…`); they
agree in verdict and differ in detail, which is itself a measure of how
little a 40-trade walk-forward pins down.

## 8. Walk-forward and out-of-sample results

Everything in §7 is out of sample by construction. Three further readings:

- **No thresholds at all** — every setup the engine produced, at the old
  costs: −0.171R on 234 trades (BTC), −0.233R on 275 (ETH; interval wholly
  below zero), −0.110R on 269 (SOL).
- **Against random entries** with the same stops, targets, ladder and costs:
  BTC p = 0.41, SOL p = 0.54, ETH p = 0.05 — not significant across three.
- **Parameter neighbourhood** — share of the nine threshold pairs that made
  money out of sample: BTC 11%, ETH 67%, SOL 44%.

For the candidate study there are **no** validation or holdout results:
nothing reached them.

## 9. Confidence intervals and sample sizes

Given beside every figure above. Three notes on how they were made:

- Engine results: a bootstrap of the out-of-sample trades, 90% two-sided,
  10,000 resamples, and no p-value under 30 trades.
- Study results: whole calendar weeks are resampled together. Nine coins in
  one week are one bet nine times, and resampling trades singly made the
  intervals about 2.5× too narrow on a test built to show it.
- A result's best configuration is deflated for every configuration tried,
  on weekly totals. Per-trade ratios are not reported under 30 trades, nor
  annual figures under 180 days.

## 10. Cost and execution assumptions

| Profile | Fee taken / resting | Other | Shorts | Source |
|---|---|---|---|---|
| `crypto_spot` — the default for the spot books | 0.236% / 0.236% | 0.02% slippage on a stop | no | CoinDCX spot base tier as reported; **excludes 1% TDS on every sale** |
| `crypto_perp` — only by name | 0.059% / 0.0236% | 0.01% funding per 8 hours held; 0.02% stop slippage | yes | CoinDCX USDT-M futures base tier as reported |
| `generic` — the old figure | 0.05% / 0.05% | none | yes | kept to reproduce earlier results |
| Indian segments | 0.036% – 0.053% a side | half a tick on a stop | intraday | statutory rates (unchanged) |

**Both crypto rates are from secondary sources.** CoinDCX's own fee page
refused automated reading. Reports of the spot base tier range from 0.10%
to 0.50% a side; 0.20% plus GST was used. Check your own statement — and
note that every candidate's result is given with the multiple of these
costs it could bear, which is the number that survives the uncertainty.

Execution: a decision at a close acts at the next open; a resting order
fills only when traded through; a stop triggers on a touch and fills at the
open on a gap; stop before target when one bar reaches both; one position
per market; no partial fills, no impact. The full list, with the direction
each errs in, is in [BASELINE.md](BASELINE.md).

## 11. Tests executed

| Command | Result |
|---|---|
| `python -m pytest tests` (Python 3.9.6) | **525 passed** in 42 s (362 before this work) |
| `ruff check app tests` | clean |
| `npm run typecheck` | clean |
| `npm run build` | built in 2.0 s |
| `python -m app.research.run study`, twice | identical records |
| Engine walk-forward on snapshots, old rules restored | three reported results reproduced exactly |

Not run this time: the suite on Python 3.11. It passed there before this
work; the second interpreter's environment was removed for disk space (the
machine has about 1 GB free) and was not rebuilt.

Each new rule was also checked the other way round in the tests that carry
it: a strategy that reads the next bar, one that normalises over the whole
history and one that under-declares its warm-up are each caught by the
audit; a planted trend is carried through every stage of the protocol and
pure noise is stopped at the first; each eligibility rule is shown to hold
a strategy back on its own.

## 12. Security and broker limitations

- **The live path has never run against a real Angel One account.** No
  credentials exist on this machine, and no order is ever sent to answer a
  question. Order parameters, status words, the order tag echo and the
  product field on positions are from documentation.
- **Currency and commodity futures stay blocked**; crypto has no live path
  at all. Live orders are intraday, one position per instrument, on cash
  equity and index futures.
- **`/metrics` and `/api/ready`** sit behind the same rule as every other
  route: this machine only, unless an access token is set. A test checks a
  remote caller is refused.
- **An approval** needs an explicit confirmation and a name, is audited, is
  tied to a code version, and arms nothing: the mode stays PAPER.
- Secrets are redacted from JSON logs as from text logs.
- The order-state table has not met the broker. It refuses only what cannot
  be legitimate — reviving a finished execution — and reports the rest.

## 13. Remaining risks and incomplete work

**Not done.**

- Alembic migrations, mypy, frontend tests, email alerts.
- An operator dashboard. The Backtest panel now shows eligibility and marks
  simulated data; experiments, datasets and the study are available from the
  API (`/api/research/*`) and the command line, not as panels.
- Portfolio-level sizing: correlated exposure across positions is limited by
  sector and count as before; nothing new was built.
- Machine learning, regime classification, cross-sectional strength, an
  ablation of the engine's components. None was started: there is no edge
  yet for any of them to refine, and each is a further set of tries.
- Shadow validation of the *live engine's* signals against its own replay.
  Forward watching exists for research strategies only.

**Someone else is working in this tree.** While this research ran, another
session was editing the same working directory: an account-balance source, a
live positions and history view, and an *unattended* live path ("auto-live",
which lets a signal place an order with nobody clicking). None of it is part
of this work, none of it was reviewed here, and it was not finished when
this was written. Three things follow, and the first is a mistake of this
work's own:

- **Two commits on this branch (`22d04f9`, `b0e73bb`) contain that session's
  files as they stood at the time**, swept in by `git add -A`. Their commit
  messages describe none of it. Separating them means rewriting the two
  commits; that was not done, and is for the owner of the repository to
  decide. Nothing of either session's work was lost or altered.
- The 525 tests ran with those files in the tree. The engine results above
  reproduced exactly with them present, and the change they make to the
  engine — which balance a live order is sized against — does not touch a
  signal. The engine fingerprint in the experiment records
  (`engine@c6f62452c4f8`) is of the tree as it was, that change included.
- **The unattended path goes through the same pre-trade gate as a manual
  order.** So the evidence and approval this work added stand in front of
  it too: with nothing eligible, it can send nothing. That holds only while
  `LIVE_REQUIRE_MEASURED_EDGE` is true. Turning it off, with that path
  armed, would remove the last check between a signal with no demonstrated
  edge and a real order.

**Known and unresolved.**

- **The confidence score is shown as if it meant something.** It does not
  (§2, item 7). The documentation now calls it a score; the interface still
  shows a percentage.
- **Indian markets are untested.** Everything here is nine crypto pairs.
  Equity and index-futures history needs a broker session.
- **The fee schedule is second-hand** (§10).
- **CoinDCX's candles for mid-2019 to late 2020 differ slightly from
  Binance's** — a few basis points on a close. From 2021 they are identical.
- **XRP is unusable** (a 98-day hole) and two markets have only six months.
- The research record ran with uncommitted files in the tree (the API
  router was being added). The files that decide a result are identified by
  fingerprint and are unchanged: `engine@c6f62452c4f8`, `exec@d94b9b60229a`,
  `costs@a0265176d91e`, `research@e1c89e70a1d9`.

## 14. Eligibility by instrument and timeframe

The five states are INELIGIBLE, INSUFFICIENT EVIDENCE, RESEARCH APPROVED,
PAPER VALIDATION and ELIGIBLE FOR HUMAN REVIEW. None trades.

| Strategy | Market | Timeframe | State | Reason |
|---|---|---|---|---|
| Live engine | BTCUSD | 1h | **INELIGIBLE** | −0.728R out of sample; and no live path for crypto |
| Live engine | ETHUSD | 1h | **INELIGIBLE** | −0.479R, interval below zero; no live path |
| Live engine | SOLUSD | 1h | **INELIGIBLE** | −0.112R; no live path |
| Live engine | any Indian equity or index future | any | **INSUFFICIENT EVIDENCE** | Never tested: no data without a broker session |
| Live engine | currency and commodity futures | any | **INSUFFICIENT EVIDENCE** | Never tested, and blocked in any case |
| Five plays | BTC, ETH, SOL | 1h, 15m | **INELIGIBLE** | Twenty walk-forwards, none with an edge; not what a live order follows |
| `donchian_trend` | nine crypto markets | 4h, 1d | **INELIGIBLE** — watched forward | Rejected by the protocol on one test; research only, no live implementation |
| `ts_momentum` | nine crypto markets | all | **INELIGIBLE** | One trade made the result |
| `range_reversion`, `trend_pullback`, `vol_expansion` | nine crypto markets | all | **INELIGIBLE** | No edge after costs |
| Any candidate | any | 1h | **INELIGIBLE** | Loses to costs |

`GET /api/research/eligibility?symbol=…&timeframe=…` gives the live engine's
state on any market with every reason for it.

## 15. Recommendation

**Remain in research. Do not paper-trade the live engine expecting it to
work, and do not consider a live-readiness review: there is nothing to
review.**

Specifically:

1. **Stop trading hourly crypto signals from this tool**, on paper or
   otherwise, as if they had an edge. Ten strategies say they do not, and on
   the spot book the fees alone are roughly half a percent a round trip.
2. **Let the forward watch run.** `python -m app.research.run forward
   --refresh`, weekly. It is the only test of the one promising idea that
   nobody can have fitted.
3. **Decide the open question in §6** — whether to spend the validation and
   holdout history on trend-following now — knowing it cannot be undone.
4. **Get real fee figures** from a CoinDCX statement and put them in
   `costs.PROFILES`. Every crypto conclusion here moves with them.
5. **If Indian markets are the goal, connect a broker for data first.**
   Nothing in this report is about them, and they are the only instruments
   Meridian can send an order for.
