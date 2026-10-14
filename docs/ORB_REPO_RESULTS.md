# ORB (arshadakl/nse-orb-backtest) — 5-minute results

**Run:** 14 October 2026, once, against the frozen protocol
(`ORB_REPO_PROTOCOL.md`, hash `2dbc83f88a3b…`). Files:
`research/orb_repo_5m_study/results/20261014T102257Z/` (`results.json`,
`orb_repo_baseline_research_trades.csv`, itemised costs per trade).
Data: Angel One 5-minute, 14 stocks, 2024-10-14 → 2026-10-14, all 14 accepted
(37,023 bars each, 0.476% missing). Survivorship-biased (today's members).

## Verdict

| Variant | Stretch | Trades | Net expectancy | Profit factor | Deflated Sharpe | 1.5× costs | Status |
|---|---|---:|---:|---:|---:|---:|---|
| `orb_repo_baseline` | research (to 2025-07-01) | 1,190 | **−0.401 R** (−Rs 125/trade) | 0.28 | 0.00 | −0.590 R | **FAILED_OUT_OF_SAMPLE** |
| `orb_repo_v3_default` | — | — | NOT MEASURED | NOT MEASURED | NOT MEASURED | NOT MEASURED | **NOT MEASURED** |

Later stretches (validation, holdout) were never opened: the first failed.

## Why v3 is not measured

Its NIFTY-alignment filter needs NIFTY 5-minute bars. The NIFTY spot series was
fetched and **refused by the acceptance check: 100% of its bars have zero
volume** (spot indices carry none; EDGE's research log reports the same). The
protocol says a refused series is not used, and it was not loosened. The v3
filters that need only the stock (breakout strength, volume, no Friday) were not
run as a substitute: that would be a third, unregistered variant.

## Baseline, in numbers

* Source's own fills, no costs: 1,191 trades, 45.5% win rate, profit factor 0.90,
  mean −0.03 R. **The README's 57% win rate is not reproduced.**
* Executable fills: gross −0.024 R per trade (≈ zero); costs Rs 1,40,382 on 1,190
  trades (≈ Rs 118 each) against gross P&L of −Rs 8,493; net −Rs 1,48,875.
  Win rate 25.6%, average win +0.60 R, average loss −0.74 R. Max drawdown Rs 1,49,866.
* 95% week-clustered interval on expectancy: −0.463 … −0.340 R (38 weeks).
* Random entries with the same stops, targets and costs: mean −0.373 R
  (95th percentile −0.347). The strategy, at −0.401 R, is **not better than
  random** (p = 0.94).
* All 14 stocks negative (−0.29 … −0.49 R). Every weekday negative. One bar of
  execution delay: −0.385 R. At 2× costs: −0.778 R.
* The benchmark (long at 09:45, range-low stop, no target) printed −2.53 R over
  2,437 trades. Its planned risk is the width of the range tail, which is tiny
  against a day's move, so its R is not comparable in size; it is negative
  either way and the strategy was judged against it only as a direction.

## Reading it

The entry has no edge before costs (gross ≈ 0). Costs, driven by fixed charges
on a Rs 1,00,000 account risking 0.5% (Rs 500) per trade, turn zero into
−0.40 R. This is the same result as the 15-minute study (`orb15`, best −0.34 R).
Account size is not the explanation: that study re-costed 50,000 to 5,00,000
and every family stayed negative.

## Limits

Spread/slippage are assumed, not observed. Survivorship-biased. Research stretch
only (nothing fitted, so also out-of-sample for the rules). 9 trades fall on
Saturdays (special NSE sessions). The author's original filter tuning is
unknown, so the 2-trial Deflated Sharpe is a floor on trials, which only helps
the strategy and it still scores 0.00.
