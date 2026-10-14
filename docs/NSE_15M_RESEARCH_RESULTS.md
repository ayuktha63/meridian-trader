# NSE 15-minute study — results

**Run:** 13 October 2026, 12:37 IST, once. Experiment `exp-20261013-070740-268fc6`;
protocol `795454e2c335…` (`NSE_15M_RESEARCH_PROTOCOL.md`); files in
`research/nse_15m_universe_study/results/20261013T070645Z/`
(`results.json`, `configurations.csv`). Tests behind the code: 29 in
`test_india_strategies.py`. Costs: `costs_india` intraday schedule, order by
order, conservative execution scenario, Rs 1,00,000 account, 0.5% risk, 1×.

## What was and was not tested

| | |
|---|---|
| Stocks | **22 of the 25 declared.** TCS, ADANIPORTS, TECHM were left out: their history stayed incomplete after three attempts (Angel One rate limit) and `india.accept()` refuses it. **This is not a 25-stock study** |
| Data | 2024-10-14 to 2026-10-12, 15-minute, ~12,320 bars each; today's partial session excluded |
| Universe bias | Today's Nifty 50 members — **survivorship-biased**; flatters every result |
| Configurations | 40 declared, **40 run, all counted**; none dropped, none re-run |
| Stretch used | **Research only** (2024-10-14 → 2025-07-01). All ten families failed it, so validation and holdout were **never opened** |
| Not built | Cross-sectional momentum, daily mean reversion (see protocol). **Not completed** |
| Look-ahead / warm-up audit | Passed for all ten |

## Result

**All ten families REJECTED. No configuration of any of the 40 was
profitable after costs (0 of 40). Nothing is eligible.** Best net expectancy
per family (one trade = 1R planned risk):

| Family | Best config | Trades | Net R | Gross R | 90% interval (net) | Win rate | Profit factor |
|---|---|---|---|---|---|---|---|
| `supertrend15` | mult 3, ADX ≥ 20 | 700 | −0.297 | +0.010 | −0.346 … −0.251 | 27% | 0.28 |
| `gap_trend15` | gap 0.7%, rr 2.5 | 328 | −0.309 | +0.134 | −0.471 … −0.145 | 40% | 0.55 |
| `orb15` | 4 bars, rr 1.5 | 1,842 | −0.336 | +0.008 | −0.376 … −0.298 | 28% | 0.27 |
| `trend15_vol` | N 32, k 2.5 | 578 | −0.344 | +0.062 | −0.423 … −0.265 | 30% | 0.35 |
| `trend15` | N 32, k 2.5 | 1,041 | −0.391 | +0.048 | −0.453 … −0.327 | 27% | 0.30 |
| `rsi_momentum15` | level 60, k 2.5 | 2,646 | −0.394 | +0.047 | −0.427 … −0.360 | 27% | 0.27 |
| `channel15` | N 40, k 2.5 | 1,765 | −0.396 | +0.044 | −0.443 … −0.347 | 28% | 0.29 |
| `bb_squeeze15` | pct 0.30, k 2.5 | 876 | −0.450 | +0.028 | −0.498 … −0.399 | 25% | 0.26 |
| `gap_reversal15` | gap 0.8%, decide bar 1 | 307 | −0.495 | −0.011 | −0.622 … −0.372 | 36% | 0.26 |
| `vwap_reversion15` | z 1.5, ADX ≤ 35 | 2,261 | −0.637 | −0.027 | −0.700 … −0.569 | 32% | 0.25 |

Gross (before costs) is within ±0.13R of zero in every family; the largest,
`gap_trend15` at +0.134R, is on 328 trades. The costs are what decide the
result: **0.19–0.24% of turnover, Rs 40,000–Rs 3,62,000 per family over the
stretch, 0.3–0.7R per trade.** The break-even cost multiple (how many times
the modelled costs a result could bear) is 0.00–0.33: every family would need
costs to be *one third or less* of the modelled ones to reach zero. Deflated
Sharpe 0.00 everywhere (need 0.95); breadth 0–25% of stocks profitable (need
60%); 0% of every grid profitable (need 60%); every family also loses at 1.5×
costs. Random-entry baselines were not run, because the gate is sequential
and every family had already failed.

## Is it the costs, or the account?

Fixed charges (two Rs 5-minimum brokerages plus GST) are large against a
Rs 500 risk budget. Re-costing the same chosen configurations:

| Account | `orb15` | `supertrend15` | `gap_trend15` |
|---|---|---|---|
| Rs 50,000 | −0.393 R | −0.331 R | −0.395 R |
| Rs 1,00,000 (primary) | −0.336 R | −0.297 R | −0.309 R |
| Rs 5,00,000 | −0.224 R | −0.191 R | −0.191 R |

A larger account lowers the loss and does not remove it: at Rs 5,00,000 the
best families still lose 0.19R a trade net. The result is "no gross edge to
pay for costs of any size", not "costs of a small account". The first
sensible conclusion — a bigger account fixes it — is false on this data.
Rs 293.44 trades nothing at all (every signal is un-sizeable).

## Reading it

1. **Nothing here supports trading any of these rules**, on paper or live.
2. **It does not prove the hypotheses false in general.** It says: on 22
   survivor stocks, two years, 15-minute bars, assumed execution, one
   predeclared grid each, no net edge appears. A different timeframe,
   instrument, holding period or an actually-measured execution could
   differ; that is a new study with a new protocol, not a rerun of this one.
3. **The one place with a faint gross signal** — gap continuation (+0.08 to
   +0.13R, n=328–813) — is one of 40 configurations, on a few hundred trades, and is nowhere near
   paying 0.4R of costs.
4. The number of times this protocol was run is **one**. A second run on the
   same stretch would be a search.

## Existing live engine, for comparison (offline, a different session)

Not part of the protocol and not on the shared record. Run by session
`meridian-trader-0e` on the first **13** accepted stocks, engine
`engine@8d01d6ffd3ae`, 8,000 out-of-sample bars each; file
`/private/tmp/claude-501/-Users-krishnaprasad-Downloads-meridian-trader/b77affed-030d-449d-a980-47ad7acc262b/scratchpad/nse-engine-15m-13stocks-2026-10-13.json`
(scratch, not in the repository). Reported by that session, **not
independently reproduced here**:

| Reading | Result |
|---|---|
| Walk-forward | 59 trades, 10 of 13 stocks sat out, 28.8% won, −0.38R net (90% CI −0.60 … −0.16), 0 passes |
| Defaults (confidence 55, RR 2) | 3,137 trades, 29.8% won, −0.507R net, −0.012R gross; 0 of 13 stocks positive |
| Every setup, no thresholds | 5,480 trades, −0.472R net, +0.016R gross |
| Gross by confidence band | <55 +0.014R; 55–64 −0.001R; 65–74 +0.003R; 75+ +0.064R |

The same shape as the candidates: gross about zero, costs decisive. The
score bands show essentially no separation, consistent with
`docs/research/BASELINE.md` §7 (the earlier crypto result) — the score is
not a probability.

## Not done / next

- TCS, ADANIPORTS, TECHM: refetch after the rate limit eases; a run with them
  is a different run and must say so.
- Validation and holdout: unopened and should stay so.
- Random-entry baselines, parameter-neighbourhood plots: moot until a family
  passes research.
- Execution assumptions are unmeasured; prospective paper trading could
  measure spread and slippage, but only for a rule worth running (none).
