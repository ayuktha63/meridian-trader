# ORB (arshadakl/nse-orb-backtest) — 5-minute protocol

**Frozen:** 14 October 2026, ~15:17 IST, before any 5-minute history was fetched
and before any result existed.
**Hash:** `2dbc83f88a3b1bdfe426847e675fbdd53a8441d10c73a6a019462ff4eca934f3`
(`research/orb_repo_5m_study/protocol.json`). `orb_repo_study run` recomputes
the protocol from the code and refuses to run if the code or the file differs.
Code: `backend/app/research/orb_repo.py` (rules), `orb_repo_study.py` (study).
Tests: `backend/tests/test_orb_repo.py`.

## Question

Do the opening-range-breakout rules published in `nse-orb-backtest` make money
on 5-minute bars of liquid NSE cash equities **after** an order-by-order
Indian cost model, by more than chance, with fills a market could have given?

## What was taken from the repository, and how

| | |
|---|---|
| Repository | https://github.com/arshadakl/nse-orb-backtest, `main`, commit `6130c7a2526235870830b45c052c86586582c5a6` (3 May 2026) |
| Rules read from | `angel_orb_backtest.py` — `simulate_day()` and the constants at the top |
| Licence | **No LICENSE file** in the repository; the README says "MIT". Unverifiable, so **no code was copied**: the rules were written again from the source |
| Fidelity check | `simulate_day()` was run from a scratch checkout on the same 2,642 trades (3 filter sets × 40 random 30-day histories) and compared with `orb_repo.play_published`: **identical entry, exit, reason and R on every one**. The scratch script is not committed (it would import the unlicensed code); `test_orb_repo.py` instead holds a row-by-row oracle written from the rules, 24 parameterised histories |
| Its own evidence | The README's "57% win rate" is attributed to "a published Nifty backtest (Jul–Oct 2025)", not to this code. The two committed result files hold 5 trades each. Neither is evidence |

## Rules (unchanged), long only

Range: the six bars 09:15–09:45; skipped if narrower than 0.20% of the first open
or if any of the six is missing. Breakout: first bar opening 09:45–14:30 that
**closes** above the range high (and passes the variant's filters). Entry: next
bar's open. Stop: range low. Target: entry + 1.5 × (entry − range low). Out: stop,
target, or the 15:15 bar. One trade per stock per day.

## Variants (the only two; nothing is tuned)

| Name | Filters | Source flag |
|---|---|---|
| `orb_repo_baseline` | none | `--no-filters` |
| `orb_repo_v3_default` | close ≥ 0.15% above the high; breakout volume ≥ 1.5× mean range-bar volume; no Friday; NIFTY above its own range high | defaults |

Any other parameter value would be a third, separately named experiment. The
number of trials entering the Deflated Sharpe is 2; how many the author tried
before settling on the v3 filters is unknown, so that is a floor.

## Two ways to play the same entries

* **published** — the source's fills (costless; stop and target at their price
  even through a gap; the entry bar's range not examined; the 15:15 exit at that
  bar's close). Diagnostic only: it reproduces the source.
* **executable** — the same levels with a market's behaviour: stop live from the
  entry bar; a gap through the stop fills at the open; the target fills only when
  traded through (at the open on a gap up); stop wins when a bar reaches both;
  sold at the 15:15 bar's open; sized to 0.5% of Rs 1,00,000 (1×, not compounded)
  **without** the source's `max(1, …)`; every trade charged order by order
  (`costs_india`, intraday, conservative execution scenario). **This is the
  one judged.**

## Data

Angel One `getCandleData`, 5-minute, 2024-10-14 to 2026-10-13 (the day itself
excluded), fetched with `python -m app.research.india snapshot 5m`. Fourteen
stocks: the source's fifteen less TATAMOTORS (not a Nifty 50 member under that
name since the demerger): HDFCBANK ICICIBANK SBIN AXISBANK BAJFINANCE INFY TCS
RELIANCE LT ADANIPORTS MARUTI HINDALCO TITAN DRREDDY. NIFTY 50 spot 5-minute
bars for the alignment filter. A series is used only if `india.accept` returns
nothing (real venue, session- and calendar-checked, every request succeeded,
quality passed); anything else is listed with its reason. **Survivorship-biased:
today's members.**

## Stretches and order

| Stretch | From | To (excl.) | Opened |
|---|---|---|---|
| research | 2024-10-14 | 2025-07-01 | first. Nothing is fitted, so this is out-of-sample for the rules (which cite Jul–Oct 2025) |
| oos | 2025-07-01 | 2026-10-13 | validation and holdout pooled; **only if research passed every test** |

## Gate (the brief's, and in this order)

1. ≥ 200 trades, else `INSUFFICIENT_DATA`.
2. Net expectancy > 0; profit factor > 1; the 95% week-clustered bootstrap
   interval on expectancy has a lower end > 0; Deflated Sharpe ≥ 0.95 (weekly
   R, 2 trials); beats 200 matched random-entry draws (p ≤ 0.05) and the
   benchmark — else `FAILED_OUT_OF_SAMPLE`.
3. Positive expectancy at 1.5× all costs — else `FAILED_COST_STRESS`.
4. Positive with one bar of execution delay, and ≥ 60% of stocks with ≥ 20 trades
   positive — else `FAILED_ROBUSTNESS`.
5. Otherwise `PAPER_VALIDATION_REQUIRED`. Never `ELIGIBLE_FOR_HUMAN_REVIEW`:
   that needs ≥ 20 agreeing paper trades and a person, which no backtest supplies.

Controls. *Random:* each real trade's stop distance (fraction of price) and
target multiple, moved to a random bar 09:50–14:25 of a random full-range day of
the same stock, long, same exits and costs. *Benchmark:* long at the 09:45 open
every full-range day, range-low stop, no target, sold at the 15:15 open.

## Not done, said now

Parameter-sensitivity grid, walk-forward re-fitting and a Monte Carlo of trade
order are not run: there are no parameters being fitted (two fixed variants), and
the block bootstrap already resamples trade order. Real-time execution costs are
assumed, not observed. Index futures were not tested.
