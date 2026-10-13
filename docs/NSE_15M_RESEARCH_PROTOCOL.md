# NSE 15-minute research protocol

**Frozen:** 13 October 2026, 12:00 IST, before any strategy result existed.
**Hash:** `795454e2c33543278776e9846ae64521e760f36d3c6e37510d752de67bbf8b4b`
(`research/nse_15m_universe_study/protocol.json`). The runner recomputes the
protocol from the code and refuses to run if it differs from that file, and
refuses if the file was edited. The hash is recorded in every experiment
record. Code: `backend/app/research/india_strategies.py`, `india_study.py`.

## Question

Does any of ten predeclared intraday rules, on 15-minute bars of liquid NSE
cash equities, make money **after** an order-by-order Indian cost model, by
more than chance, across parameters, across stocks, and on history it was not
chosen on?

It does not ask whether any rule can be made to pass. Nothing is tuned on
more than the research stretch; a rule that fails a stretch never sees the
next.

## Data

| | |
|---|---|
| Source | Angel One `getCandleData`, 15-minute |
| Universe | 25 Nifty 50 members, the most traded over the 60 sessions before the study, **fixed by session `meridian-trader-0e` before any result** (list in `protocol.json`) |
| Membership | **Current constituents only. Survivorship-biased** — stated on every result |
| Eligibility of a series | `india.accept()` returns nothing: real venue, session-checked, calendar-checked (Nifty 50 index daily bars), every request succeeded, quality check passed |
| Used | only the series that pass at run time; every one left out is listed with the reason. *Not a "25-stock study" unless 25 pass* |
| Depth | 2024-10-14 to 2026-10-12 (two years). Today's partial session is excluded |

## Stretches (local midnights, IST)

| Stretch | From | To (excl.) | Used for |
|---|---|---|---|
| Research | 2024-10-14 | 2025-07-01 | choosing the best configuration of each family; all 40 configurations are run here |
| Validation | 2025-07-01 | 2026-03-01 | the chosen configuration, frozen, only if research passed |
| Holdout | 2026-03-01 | 2026-10-13 | opened once, only for what survived validation |

Plans are built on the history up to the **end of the stretch being traded**
and no further. Prospective paper trading is a fourth stretch that does not
exist yet (`PROSPECTIVE_PAPER_TRADING.md`).

## Hypotheses (declared; 40 configurations)

| Family | Idea | Grid |
|---|---|---|
| `orb15` | first close beyond the opening range | or_bars {2,4} × rr {1.5,2.5} |
| `trend15` | break of N-bar extreme with the daily EMA(20) trend | N {16,32} × k {1.5,2.5} |
| `trend15_vol` | same, only when ATR/close ≥ its 500-bar median (the volatility filter, tested beside the baseline) | N × k |
| `supertrend15` | direction flip, optional ADX filter | mult {2,3} × adx_min {0,20} |
| `vwap_reversion15` | stretch from session VWAP in a low-ADX market | z {1.5,2.5} × adx_max {25,35} |
| `bb_squeeze15` | breakout after a narrow Bollinger width | pct {.15,.30} × k {1.5,2.5} |
| `rsi_momentum15` | RSI cross of a high level above the 50-EMA | level {55,60} × k {1.5,2.5} |
| `channel15` | N-bar high/low breakout (shifted window) | N {20,40} × k {1.5,2.5} |
| `gap_trend15` | confirmed gap continuation | gap {.3%,.7%} × rr {1.5,2.5} |
| `gap_reversal15` | gap fade toward the prior close | gap {.4%,.8%} × decide bar {0,1} |

Each has a written hypothesis, the conditions it works and fails in, and what
would falsify it (`Strategy` fields; copied into `protocol.json`).

**Declared but not built:** cross-sectional momentum (needs a ranking across
stocks and a point-in-time universe the data does not have) and daily mean
reversion (a delivery-product study of its own). They are in the register as
**not completed**. Index and stock futures, options, currency and commodities
are not part of this study.

## Execution model

- Decision at a bar's close; **market order at the next bar's open.**
- Stops trigger on a touch and fill at the open on a gap; a bar that reaches
  both stop and target takes the stop; targets fill only when traded through.
- One position per stock; no partial fills; no latency beyond the one bar.
- **Flat by the close of the 15:00 bar**; no entry decided after 13:30. A
  trade whose bars would span two sessions (a data hole) is dropped and
  counted.
- Intraday product, shorts allowed (Angel One's intraday schedule permits).

## Costs

`costs_india` schedule `angelone-nse-equity-intraday-2025-11-17`, order by
order, checked against Angel One's own charge estimator to the paisa on
12 orders (`docs/research/TRANSACTION_COST_MODEL.md`): brokerage with its Rs 5
floor per order, STT, stamp, exchange, SEBI, IPFT, GST. Spread and slippage
are the **conservative** scenario (assumed, not measured). Shares are sized
whole against a Rs 1,00,000 account at 0.5% risk, 1× (no leverage); a
signal that cannot buy one share is counted as skipped. Also reported: Rs
50,000 and Rs 5,00,000 accounts; optimistic / base / conservative scenarios
at 1.0×, 1.5×, 2.0× costs.

## Judgement (thresholds unchanged from `study.THRESHOLDS`)

To leave the research stretch a family needs **all** of: 200 trades pooled;
a 90% interval (clustered by exit week) wholly above zero; deflated Sharpe
≥ 0.95 after all 40 configurations, on weekly totals; ≥ 60% of its own grid
profitable; ≥ 60% of stocks with 20+ trades profitable; positive at 1.5×
costs; better than matched random entries (p ≤ 0.05, 200 draws that keep the
strategy's own stop and target distances, trail and holding time); and a
passed look-ahead/warm-up audit. Validation: 100 trades, interval above zero,
≥ 50% of stocks. Holdout: 100 trades, interval above zero. **A threshold that
cannot be computed is reported NOT EVALUABLE and counts against the family.**

## What a pass would still not be

Evidence from one two-year stretch of survivors, with assumed execution
costs, never run live. It would make a family RESEARCH APPROVED at most; the
live gate additionally needs paper trading, broker validation and a person's
approval, and no family has a live implementation (`STRATEGY_ELIGIBILITY.md`).

## Reproducing

```bash
cd backend
.venv/bin/python -m app.research.india accepted 15m        # what data a study may use
.venv/bin/python -m app.research.india_study show          # frozen? which members usable?
.venv/bin/python -m app.research.india_study run           # writes research/nse_15m_universe_study/results/<utc stamp>/
```

Each run writes `results.json`, `configurations.csv` and an experiment record
(hashed) under `data/research/experiments/`. It writes nothing to the
application database and cannot send an order. A rerun makes a new folder; the
number of runs is part of the evidence and is stated in the results.
