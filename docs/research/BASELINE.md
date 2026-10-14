# Baseline — the repository as found, 12 October 2026

Phase A of the research brief: what was there, whether its reported results
could be reproduced, and what was wrong. Written before any strategy work;
the defects below were fixed first, and the fixes are in the commits named.

## State of the repository

| | |
|---|---|
| Branch | `main` at `876b83b`, 15 commits ahead of `origin/main`, working tree clean |
| Work done on | `research/quant-pipeline`, branched from that commit. Nothing was pushed, reset or discarded. |
| Backend tests | 362 passed (Python 3.9.6), 24 s |
| Backend lint | `ruff check app tests` — clean |
| Frontend | `tsc --noEmit` — clean |
| Running application | Left alone: the server started from `main` before this work kept running the code it had loaded |
| Trading mode | PAPER. No broker credentials on this machine, so nothing could reach a live account. |

## Could the reported results be reproduced?

The four results in the brief came from a walk-forward run against the live
feed. The feed's window moves every hour and nothing recorded which bars a
result was computed on — so as found, **they could not be reproduced at
all**. That is defect 1 below.

After history became a hashed snapshot (`research/datasets.py`), the same
test was run again on the frozen bars, with the fill rule and cost model put
back exactly as they had been:

| Market | Reported | Reproduced on the snapshot | Snapshot |
|---|---|---|---|
| BTCUSD 1h | 112 trades, −0.111R, interval −0.314R … +0.096R | **112 trades, −0.111R, −0.314R … +0.096R** | `e948e57c0e10685d` |
| ETHUSD 1h | 51 trades, +0.217R, interval −0.137R … +0.584R | **51 trades, +0.217R, −0.137R … +0.584R** | `d95c01eefe3ab56f` |
| SOLUSD 1h | 86 trades, −0.135R | **86 trades, −0.135R, −0.358R … +0.103R** | `a76b7d9cb5afa84a` |
| BTCUSD 15m | 9 trades, −0.640R | not re-run: nine trades is not a result to reproduce | — |

Identical to the last digit where it was run. The snapshots were taken a few
hours after the original runs, so the original window was still inside them.
The reported numbers are therefore a true account of what that code measured
— and, as defect 3 shows, of the wrong thing.

## Defects found

Each was found by reading the code or by running it, and each has a test
that fails without its fix.

### 1. A result did not say what it was computed on — fixed in `ffa2d27`

No dataset identity, no code version, no cost assumptions, no count of how
many times a test had been run. A walk-forward that passed could not have
been checked, and one that failed could have been re-run until it passed
with nothing to show for the attempts.

*Now:* history is a content-hashed snapshot; every run is an experiment
record with the snapshot's hash, fingerprints of the strategy, execution and
cost code, the git commit, every assumption, and the number of
configurations tried. The record is hashed too.

### 2. A test could run on simulated bars and still unlock a live order — fixed in `865ffbd`

A symbol with no venue connected is served bars from the simulator. So is a
symbol whose venue failed to answer. A walk-forward on those bars produced a
verdict like any other, and the live gate read the verdict without knowing
where the bars came from. Evidence about a random-number generator would
have cleared a real order.

The second half was worse in daily use: after one failed history request the
simulated stand-in was never replaced — a series with 200 bars was
considered loaded — and it was reported to the UI under the live venue's
name, with that venue's real quotes folded into it.

*Now:* every series records its source; a verdict reached on simulated bars
is withdrawn; the gate counts only a test that names a real venue; a
stand-in is labelled, is not mixed with real quotes, and is replaced as soon
as the venue answers.

### 3. Crypto was charged a fee from a different product — fixed in `ffa2d27`

The cost model charged crypto 0.05% a side, with the comment that this was
CoinDCX's fee on the USDT books. It is the reported taker fee on CoinDCX's
USDT-margined **futures**. The instruments in this application are the
**spot** books — that is what the candles are, what the paper account
models (no leverage, no shorting), and what TRADING.md tells you to trade.
Reported spot fees are 0.10% to 0.50% a side plus 18% GST, with 1% TDS
withheld on every sale. The backtest also took short trades on a book that
cannot be shorted.

*Now:* costs are filed under named venue profiles and a result records which
one charged it. The spot books default to the spot profile (0.20% plus GST,
long only); the perpetual has to be asked for by name. Both rates are from
secondary sources — CoinDCX's own fee page refused automated reading — and
the code says so beside each number.

What that correction does to the reported results:

| Market | As reported (0.05%, long and short) | As the perpetual (0.059%/0.024%, funding) | As the spot book it is (0.236%, long only) |
|---|---|---|---|
| BTCUSD 1h | −0.111R on 112 trades | −0.110R on 112 | **−0.728R on 16** (interval −1.24R … −0.19R) |
| ETHUSD 1h | +0.217R on 51 | +0.226R on 51 | **−0.479R on 35** (interval −0.92R … −0.02R) |
| SOLUSD 1h | −0.135R on 86 | −0.132R on 86 | **−0.112R on 31** (interval −0.55R … +0.34R) |

The one positive result in the original table belongs to a venue the test
was not about. On the spot book the engine is negative on every market, and
on two of them the whole interval is below zero.

Two more readings of the same replay, both at the old, flattering costs:

- **Every setup the engine produced, with no thresholds chosen at all:**
  −0.171R on 234 trades (BTC), −0.233R on 275 (ETH, interval wholly below
  zero), −0.110R on 269 (SOL). The walk-forward's choice of thresholds is
  what produced ETH's positive number; the signals underneath it lose.
- **Against its own trades moved to random bars** (same stops, targets,
  ladder and costs): BTC −0.111R against −0.144R for random (p = 0.41), SOL
  −0.135R against −0.110R (p = 0.54), ETH +0.217R against −0.117R (p = 0.05
  — alone, and not once three markets are corrected for). On two of three
  markets the engine's entries are indistinguishable from random ones.

### 4. History paging stopped at the first hole — fixed in `ffa2d27`

A page of candles with fewer than a thousand in it was read as the start of
the listing. Every exchange has holes where it was down. Bitcoin's hourly
history goes back to August 2018 — 71,220 bars — and the provider returned
30,996 of them; for most other markets it returned two years of eight.

*Now:* paging stops only at two consecutive empty windows.

### 5. A resting order filled on a touch — fixed in `ffa2d27`

A limit filled whenever a bar's low reached it. An order resting at a price
is behind everyone who was there first; the market has to trade through.
Both simulators now require that, for entries and targets. On the
reproduced runs above it changed nothing — real prices rarely stop exactly
on a level — so this is a correctness fix, not a result.

### 6. A passing backtest was the whole of the evidence — fixed in `22d04f9`

The gate asked: did a recent walk-forward pass, does it survive the number
of markets tested, does paper trading not contradict it. It did not ask
whether the code had changed since, whether the result was for this venue,
whether it beat random entries, whether it hung on one parameter setting,
or whether the same test had simply been run until it passed. And when all
of it held, nothing else stood between the result and an order.

*Now:* `research/eligibility.py` asks all of those, twenty agreeing paper
trades are required, and a person's approval for that code version on that
market has to be on record.

### 7. The confidence score is not a probability — reported, not fixed

Every setup the engine produced over the three replays, 778 of them, sorted
by the confidence it was given:

| Confidence | Setups | Won | Mean after costs |
|---|---|---|---|
| under 50 | 84 | 29.8% | −0.26R |
| 50 – 55 | 158 | 27.8% | −0.32R |
| 55 – 60 | 133 | 33.1% | −0.11R |
| 60 – 65 | 95 | 26.3% | −0.28R |
| 65 – 70 | 69 | 31.9% | −0.13R |
| 70 and over | 239 | 30.5% | −0.12R |

About three in ten win whatever the number says. Its correlation with
winning is +0.02 and with the result +0.06. Read as a win probability it
scores worse than ignoring it: a Brier score of 0.343 against 0.210 for
always guessing the base rate.

The score is a weighted count of agreeing indicators. Nothing has ever
fitted it to outcomes, and on this evidence it carries none. It is described
in the documentation as a score from now on. It has not been recalibrated:
with no discrimination there is nothing to calibrate — a calibrated version
would say "30%" every time.

### Smaller ones

- `backtest/metrics.py` computes "expectancy" as win rate × average win
  minus loss rate × average loss, counting a trade of exactly zero as a
  loss. It differs from the mean R only when a trade nets exactly zero. Left
  as it is; every verdict uses the mean.
- The random-entry control in the engine backtest used fixed ATR stops and
  its own entry frequency, so it was not a control for *this* strategy's
  exits. A matched version — the strategy's own stop and target distances
  and holding times, moved to random bars — now runs beside every
  walk-forward (`research/engine_evidence.py`).
- A snapshot's first coarse bar could be built from fewer fine bars than it
  spans; it is now dropped, like the last.

## What the simulator assumes that a broker does not

Stated so a result is not read as more than it is. None of these was
changed; they are limits of testing on bars.

| Assumption | Reality | Direction of the error |
|---|---|---|
| One fill per order, at one price | Partial fills, in pieces | Flatters, slightly, at small size |
| A stop fills at its price unless the bar gaps | It fills where the book is | Flatters; a slippage allowance is charged (0.02% of price on crypto) |
| When a bar reaches stop and target, the stop went first | Unknown without finer data | Understates |
| A limit filled inside a bar is not credited its target on that bar | It may have been reached | Understates |
| No latency beyond one bar | Seconds | Negligible at these timeframes |
| No market impact | None at retail size on these books | — |
| Signals on spot bars, traded as the perpetual (that profile only) | Basis moves | Unknown, small |
| The deterministic analyst supplies the verdict | Claude, when a key is set | Unknown — the replay cannot tell |
| Timeframes below the chart's are not scored | They carry about a fifth of the trend weight live | Unknown |

## Data, as found and as it is now

Nine markets have usable history; three do not, and say why.

| Market | Snapshot | Bars (1h) | From | Missing | Largest gap | Use |
|---|---|---|---|---|---|---|
| BTCUSD | `e948e57c0e10685d` | 71,220 | 2018-08-23 | 0.125% | 10 bars | yes |
| ETHUSD | `d95c01eefe3ab56f` | 70,522 | 2018-09-21 | 0.146% | 15 | yes |
| SOLUSD | `a76b7d9cb5afa84a` | 49,587 | 2021-02-11 | 0.107% | 17 | yes |
| DOGEUSD | `efc567c492850536` | 63,640 | 2019-07-05 | 0.141% | 18 | yes |
| BNBUSD | `4bc755976096f8ce` | 67,938 | 2019-01-06 | 0.148% | 21 | yes |
| ADAUSD | `9f9940dfde940ddb` | 68,748 | 2018-12-03 | 0.171% | 20 | yes |
| LINKUSD | `da894c6a2b46ade8` | 67,700 | 2019-01-16 | 0.165% | 18 | yes |
| LTCUSD | `0a29c47bc340973b` | 68,748 | 2018-12-03 | 0.171% | 21 | yes |
| TRXUSD | `175f00b99385c745` | 68,869 | 2018-11-28 | 0.165% | 17 | yes |
| XRPUSD | `bed3a0be69ed4e7f` | 71,504 | 2018-05-04 | **3.349%** | **2,357** | **no** — fails the quality check |
| AVAXUSD | `a8c83822420993e2` | 4,380 | 2026-04-12 | 0% | 0 | **no** — six months is not a research stretch |
| DOTUSD | `f0a2f0219062c001` | 4,381 | 2026-04-12 | 0% | 0 | **no** — same |

No row was dropped as a duplicate, an impossible candle or an off-grid
timestamp in any of them. Gaps were left as gaps.

**Checked against a second source.** Twenty-four windows of 200 hourly bars,
spread across each history, were compared with Binance's own endpoint for
the same pair. From February 2021 onward the two agree bar for bar (95% to
100% of bars identical in every window). Between roughly mid-2019 and late
2020 almost every bar differs slightly — a median of 0.01% to 0.05% on the
close, at most 1.5% on one bar's extreme: CoinDCX's candles for that period
evidently come from somewhere else. A few basis points on hourly closes does
not move a result on 4-hour or daily bars, but the two years are not the
same data, and a result that depended on them would deserve a second look.

**Not available at all:** Indian equities, index futures, currency and
commodity futures. Their history needs an authenticated broker session and
there are no credentials on this machine. Nothing in this work says anything
about them.

## Missing, as found

| Missing | Now |
|---|---|
| Dataset versioning, experiment tracking, strategy and cost versioning | Built (`research/datasets.py`, `registry.py`, `versions.py`) |
| Data validation | Built (`research/quality.py`) |
| Baselines beyond a fixed random control | Matched random entries; buy and hold |
| Multiple-testing control beyond markets | Trial counts per experiment; deflated Sharpe; repeat-run limit |
| Locked holdout | Built into the study; never opened |
| Structured logs, readiness, metrics | Built (`observability.py`) |
| Order-state table | Built (`broker/order_states.py`) |
| Forward / shadow validation | Built for research strategies (`research/forward.py`) |
| Alembic migrations | **Not built.** New tables are created on start; new columns by the existing additive step. |
| mypy | **Not built.** |
| Frontend tests | **Not built.** |
| Email alerts | **Not built.** Telegram only. |
| A live run against a real Angel One account | **Not done, and not attempted** — no credentials, and no order is ever sent to settle a question. |
