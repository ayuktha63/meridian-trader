# Execution and order lifecycle

**Evidence:** `broker/angelone/{execution,reconciler,client,orders}.py`,
`broker/order_states.py`, `schemas/execution.py`, read 13 October 2026; tests
`test_execution.py`, `test_reconciler.py` (against `tests/fake_angel.py`).
**Nothing here has run against the real broker.** Order parameters, status
strings, whether the order tag is echoed in the order book, and whether a
resting target beside a resting stop is accepted are from documentation
(`reconciler.py` docstring says the same).

## Pipeline

```
completed bars → analysis (verdict, plan) → recommendation row
   → POST /api/execution/{id}/execute (confirmed)
   → PreTradeRiskGate (12 checks)           — a REJECTED row is written; nothing is sent
   → write-ahead: LiveExecutionRow(SUBMITTING, order_tag) committed
   → client.place_order (LIMIT, INTRADAY, DAY, tag)
   → reconciler: follow to fill → rest broker-held STOP (+ TARGET) → ladder → square-off → close
```

## States

The brief's list, mapped to what exists (`ExecutionStatus`):

| Brief | Meridian | Notes |
|---|---|---|
| CREATED | RECOMMENDED, PENDING_CONFIRMATION, VALIDATING | before anything is sent |
| RISK_REJECTED | REJECTED (error code = the failed check) | a retry of the same recommendation is allowed |
| APPROVED | — | implicit: the gate passed and SUBMITTING is written |
| SUBMITTING | SUBMITTING | committed *before* the broker is called |
| ACKNOWLEDGED | SUBMITTED | broker returned an order id |
| OPEN | OPEN | |
| PARTIALLY_FILLED | PARTIALLY_FILLED | held quantity is counted from fills, never assumed |
| FILLED | FILLED | |
| CANCEL_PENDING / CANCELLED | CANCEL_PENDING / CANCELLED | |
| REJECTED | REJECTED | broker-side rejection |
| EXPIRED | — | not distinguished; an unfilled DAY order is CANCELLED by the entry timeout (300 s) or by the broker |
| UNKNOWN | UNKNOWN | ambiguous submission |
| — | CLOSING, CLOSED, ERROR | exit in flight; finished; reserved |

`order_states.TRANSITIONS` is the lifecycle as one table. CLOSED and
CANCELLED are final and never become anything else (refused outright, because
supervision places orders). Any other unexpected change raises a critical
alert and proceeds — refusing could leave a position without its stop.

## Ambiguity and duplicates

- A **timeout or malformed response** raises `AngelOneAmbiguousError`, never
  a retry. The service looks the order up in the order book by tag (and by
  symbol, side, quantity, time); if found it is adopted, otherwise the row is
  UNKNOWN and the reconciler keeps looking for `UNKNOWN_GRACE_SECONDS` (120 s)
  before concluding it never arrived.
- The **intent is durable first** (write-ahead), with an idempotency tag
  derived from the recommendation id (`orders.order_tag`).
- **One execution per recommendation** (unique key; SUBMITTING and UNKNOWN
  block a second); **one live position per instrument** (`_activity` check),
  because Angel One nets orders into one position.
- Protective legs have their own tags (`S`, `T`, `X`, `R`); a timed-out stop
  waits `AMBIGUOUS_GRACE_SECONDS` (8 s) and is searched for before another is
  placed.
- A clean broker rejection (`AngelOneOrderError`) is final; the row is
  REJECTED with the broker's text.

## Protection — and which kind

| Protection | Where it lives | Counts as protected? |
|---|---|---|
| Stop order at the broker (`STOPLOSS` variety) | Angel One | **Yes** — `protection_status` is set only when a stop order id exists and is seen in the book |
| Target order at the broker | Angel One | An extra, not protection. A rejected target is watched and exited in software |
| Trailing / ladder moves | the reconciler modifies the broker stop | Yes while the stop exists |
| Anything held only in Meridian's memory or database | local | **No.** It is a note, not a stop |

If the stop cannot be placed the position is **flattened**, the kill switch
is tripped and a critical alert is raised. If positions cannot be read the
reconciler treats that as "unknown", not "flat", and cancels nothing.

## Restart, reconciliation, kill switch

- On start the reconciler loads every execution in an active state and
  re-derives its status from the order book and positions; an entry that was
  in flight is found by tag. The pass repeats every 3 s, with backoff
  (to 300 s) while the broker is unreachable, **and since today the login
  itself backs off** (`auth.py`) instead of retrying every poll.
- What is held is *counted*: fills of every exit ever placed are summed and
  subtracted from the entry's fills. Over-exit (two exits that both filled)
  is corrected with a reverse order.
- Positions closed outside Meridian are noticed (after `FLAT_CONFIRMATIONS` =
  3 passes) and the orphan stop is pulled so it cannot open a new position.
- A new entry is blocked while any execution is non-final for the same
  instrument; account-wide, open count, daily loss and exposure are read from
  the same table.
- **Kill switch:** blocks new entries at the gate (check 4), cancels working
  entries, leaves protective stops in place, persists across restart.
  `flatten-all` is the separate, confirmed action that closes positions.
  Behaviour with the *database* unavailable is not tested: the gate needs the
  ledger and fails closed (`"Live trade history was not loaded"`), the
  kill-switch file is independent of the database.

## Not tested, honestly

Cancel/replace races at the real broker; partial fills of a stop; broker
status strings other than `complete/filled/rejected/cancelled`; MCX and CDS
(blocked); options (no path); the reconciler's behaviour across an Angel One
daily session expiry while a position is open (the session now re-logs in,
with backoff — mocked, not observed); a dropped connection during the
last-twenty-minutes square-off.
