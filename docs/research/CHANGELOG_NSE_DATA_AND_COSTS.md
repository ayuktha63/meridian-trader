# Change log — NSE data pipeline and cost model

13–14 October 2026. Branch `research/quant-pipeline`.

**Where it landed.** All of this was committed in `31f8cda` ("Angel One only…"),
an 86-file commit made by another session that swept these files in with its own
work. Its message does not itemise them, and history was not rewritten; this
file is the itemised record. Nothing here was staged or committed separately.

**Status after the change: RESEARCH ONLY.** No strategy has an edge. No order
was sent to Angel One by any of this work.

## 1. Defects fixed

| # | Defect | Effect | Fixed in |
|---|---|---|---|
| 1 | Quality check accepted a bar only if it opened on a multiple of its length on the UTC clock. NSE hourly bars open 09:15, 10:15 …; daily bars are stamped at local midnight. | Every hourly and daily bar of every Indian instrument was dropped. A BANKNIFTY 1h walk-forward ran on 0 bars. | `research/exchange_calendar.py`, `quality.py` |
| 2 | Missing bars were not measured for a market with a session. | A stock missing a fortnight passed as clean. | `quality._assess_session` |
| 3 | A failed history request kept what had arrived and returned it as whole. | A truncated history looked complete. | `data/angel_history.py` |
| 4 | A snapshot was named by its bars alone and never rewritten. | A corrected judgement of the same bars kept its first verdict for ever. | `datasets.digest` / `_context` |
| 5 | Indian equity charges were a rate per side (0.053%). Brokerage has a ₹5 floor and ₹20 cap, and the depository charge is flat. | A ₹10,000 round trip was ₹10.60; Angel One's calculator says ₹27.15. The daily-loss limit was enforced on the low number. | `costs_india.py`, `costs.charges()` |
| 6 | Equity brokerage was 0.03% a side; Angel One has charged 0.1% since 17 Nov 2025. | Backtests and paper trades undercharged. | `costs.EQUITY_BROKERAGE_RATE` |
| 7 | On a network error the SDK wrote this machine's public IP, local IP and MAC address to the log, and the full client code was logged at login. | Local log file only (gitignored); no credentials. | `broker/angelone/auth.py` |
| 8 | A scrip-master download failure read as "symbol not listed". | Misleading error. | `angel_history.AngelHistorySource.scrip` |

Reported and **not** fixed: the rate table for index futures still carries STT
of 0.02% on the sale (0.05% since 1 April 2026) and percentage brokerage where
Angel One charges a flat ₹20. See `TRANSACTION_COST_MODEL.md` §4.

## 2. What was added

New files:

- `backend/app/costs_india.py` — order-level NSE equity charges (delivery and
  intraday), three execution scenarios, position sizing, a record of its own
  fingerprint.
- `backend/app/data/angel_history.py` — historical candles window by window,
  five tries per window (3/6/12/24 s waits), a report of every request, and
  an explicit complete/incomplete verdict. Reads only.
- `backend/app/data/angel_charges.py` — compares the cost engine with Angel
  One's own `estimateCharges`. Agreed on 12 of 12 orders, every line.
- `backend/app/research/exchange_calendar.py` — the NSE session, bar grid and a
  trading calendar measured from the index's own daily bars.
- `backend/app/research/india.py` — the universe, `snapshot`, and `accept()`,
  the one gate a study loads data through.
- `backend/app/research/india_costing.py` — sizes and charges simulated trades
  for a stated account; `from_engine` adapts the live engine's trades.
- `backend/app/research/universes/nifty50.json` (NSE list of 13 Oct 2026, hash
  recorded) and `corporate_actions.json`.
- Tests: `test_exchange_calendar`, `test_india_data`, `test_costs_india`,
  `test_india_costing`, `test_log_redaction`, and the fixtures in `india_fixtures.py`.
- Docs: `ANGEL_ONE_DATA_QUALITY.md`, `TRANSACTION_COST_MODEL.md`.

Changed (small, additive):

- `research/quality.py` (rules version 3): session grid, missing days against a
  calendar, longest missing run (limit 3 days), zero-volume (1%), unexplained
  overnight moves ≥20%, the session's closing bar (warning), and evening
  sittings not counted as missing days.
- `research/datasets.py`: session, calendar and provenance on a snapshot;
  `fetch()` holds an NSE cash equity to its session.
- `research/versions.py`: `costs_india.py` is in the cost fingerprint, so
  evidence charged by an old rate stops counting.
- `costs.py`: stock charges are order-level in `charges()`; 0.1% brokerage in
  `per_side_rate()`.
- Seven tests that pinned the old charge figures were restated by hand from the
  published rule (`test_execution`, `test_reconciler`, `test_backtest_honesty`).

## 3. Data on file

- Daily bars, Nifty 50, 12 Oct 2018 – 12 Oct 2026: 96,660 bars; **49 of 50
  accepted**; Tata Motors rejected (ten consecutive days missing, Nov 2021).
- Intraday history exists back to July 2017 (15/5/1-minute) and 8+ years (1h).
- 15-minute bars: another session took 25 stocks over two years; those that
  completed passed the gate. The 15:15 bar is missing on 13 sessions
  (19 Aug – 4 Sep 2026) in every stock — a venue hole, reported as a warning.
- The universe is today's index. Results on its past are survivorship-biased.

## 4. Verification

| Check | Result |
|---|---|
| `python -m pytest tests` | **752 passed** (14 Oct 10:02 IST) |
| `ruff check app tests` | clean |
| `npm run typecheck`, `npm run build` | clean (13 Oct) |
| Cost engine vs Angel One `estimateCharges` | 12 of 12 orders agree to the paisa; DP charge not covered |
| Live order sent | **none** — all 14 live records are rejections with no broker order id |

Not run: the suite on Python 3.11; any real-order validation.

## 5. Results this work supports

- The existing engine, 13 liquid stocks, 15-minute bars, ~2 years, 3,137
  trades (run by another session, not re-run here): **−0.51R per trade after
  costs, −0.01R before.** No stock passed walk-forward.
- At ₹10,000 a ₹1,000 share with a 5% stop sizes to zero shares.

## 6. Open

1. Strategy candidates are being studied by another session; none has passed.
2. Intraday data for the full universe has not been taken.
3. The live order path has never been validated with a real order.
4. The ₹20 depository charge rests on the price list only; nothing is reconciled
   with a contract note.
5. Index-futures cost rates (above).
6. Historical index membership is not on file.
7. Execution assumptions (spread and slippage) are assumed, not measured.
