# Experiment registry — unique runs, duplicates, overlap

Measured 14 October 2026 over the 37 records in `backend/data/research/experiments/`
by `app/research/dedupe.py` (new). Mode PAPER; no order was sent.

## What changed

The registry already stored every run and never edited one. It could not say
which runs asked the *same* question, so any total taken over runs counted a
repeat as new evidence. `dedupe.fingerprint` now names a question (strategy,
version, parameters, market, timeframe, venue, dataset content hashes, window,
cost profile, cost/execution/engine/research code versions, seeds).
`dedupe.summarise` groups records, counts each group once, and reports overlap.
Nothing is deleted or rewritten.

Deliberate choice: the git commit is **not** in the identity; the code-version
hashes are. A commit that touches none of the code that matters does not create
a new question. The commit stays on each record for audit. Notes
(`registration`, `diagnostic`) are never counted as evidence.

## Measured

| | Count |
|---|---|
| Records (all kinds) | 37 |
| Measurement records | 33 |
| Unique configurations | 32 |
| Exact duplicate runs | 1 |
| Re-runs whose trade count differs from their canonical run | 0 |
| Pairs of *different* configurations that examined overlapping bars | 67 |

Exact duplicates are rare because most repeats ran after the cost or engine
code changed, which correctly makes them new questions. The inflation is
overlap, not duplication:

| Market | History summed over configurations | History actually examined (overlap merged) |
|---|---|---|
| BTCUSD 1h | 899.8 days | 166.6 days |
| ETHUSD 1h | 899.8 days | 166.6 days |
| SOLUSD 1h | 899.8 days | 166.6 days |
| RELIANCE 15m | 490.9 days | 70.1 days |
| NIFTY 15m | 69.1 days | 69.1 days |

Consequence: the 506 trades summed over the 22 rows of the `backtests` table
are not 506 independent observations. The same bars were traded repeatedly under
different settings. The independent sample is bounded by the merged history
above, which is at most about 167 days per crypto market and 70 days per NSE
market. This is why the 200-independent-out-of-sample-trade bar has not been met
by anything, and why no configuration is eligible.

## Not done here

`backtests` (the older table the UI reads) is not linked to the registry, so its
22 rows cannot be fingerprinted; only `research_experiments` is. Reports should
cite this table, not the `backtests` totals.

Cost profile by record (measured): crypto engine walk-forwards, 6 under the
0.05%-a-side `generic` placeholder, 6 under `crypto_perp`, 6 under
`crypto_spot`; NSE walk-forwards, 7 under `india:stock` and 2 under
`india:index`; the NSE study under `india:stock:intraday`. The 6 `generic`
records are crypto, outside the approved Angel One universe, and must not be
cited as cost-realistic. The `india:index` profile prices index futures, and
the repo's own audit (D10) says futures STT may be stale, so those 2 are not
cost-verified either.

## Tests

`backend/tests/test_research_dedupe.py` (9): identical re-runs add no trades;
commit alone is not a new question; changed parameters, data, costs, seed,
window or code version each are; notes are not evidence; non-reproducible
re-runs are flagged; overlap is flagged and disjoint windows are not; merged
coverage counts overlapped history once. Full suite: 886 passed.
