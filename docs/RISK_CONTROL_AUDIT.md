# Risk control audit

**Evidence:** `broker/risk_gate.py`, `broker/live_limits.py`, `config.py`,
`analysis/guardrails.py`, `analysis/autotrader.py` read 13 October 2026;
tests `test_execution.py`, `test_risk.py`, `test_reconciler.py`; database
`live_executions` (14 rows, all refused before submission).

## The independent gate

`PreTradeRiskGate` is separate from the strategy and from the submission
code. A signal is a `Recommendation` built **on the server** from the stored
analysis (never from front-end input); the gate re-derives prices on the tick
grid and re-sizes the quantity itself. A rejection cannot be overridden by a
signal.

| Brief item | Gate check | Fails closed when… |
|---|---|---|
| Trading mode | 1 `trading_mode` | not LIVE |
| Account/session | 3 `angel_connection` | no session |
| Instrument eligibility | 6, 7, `unverified_quantity_unit` | unresolved; CDS; MCX |
| Market-data freshness | `price_band` (broker LTP within 1%) | LTP unreadable |
| Quantity and lot size | `quantity` | funds unreadable; less than one lot affordable |
| Available funds and margin | `funds` | funds unreadable; margin (or notional) above cash |
| Max notional | `max_order_value`, `symbol_exposure`, `sector_exposure` | over Rs 50,000 / 50,000 / 1,00,000 |
| Max risk per trade | `quantity` (≤ 1% of broker cash) + `risk_amount` re-assertion | — |
| Max daily loss | `daily_loss` (realised **and** open stop-out risk, charges included); trips the kill switch | — |
| Max drawdown | **not implemented in the gate.** Paper has `DAILY_LOSS_LIMIT_R`; live has no peak-to-trough limit | — |
| Position limits | `open_positions` (2), `trades_per_day` (5), `instrument_open` | — |
| Correlated exposure | sector only; no correlation measure | — |
| Pending orders | `instrument_open` counts working orders | — |
| Stop validity | `recommendation` shape + `price_tick` (stop at least one tick from entry after rounding) | — |
| Session restrictions | `market_hours`, no entry in last 45 min, square-off 20 min before close | **by the clock — no holiday calendar** |
| Strategy eligibility | `measured_edge` ← `services/edge.py` ← `research/eligibility.py` | any missing evidence |
| Risk-reward | enforced in `measured_edge` against the walk-forward's minimum | — |
| Missing configuration | every limit has a default in `config.py`; a limit of 0 disables it | **a limit set to 0 is off, not closed** |

## Findings

1. **No peak-to-trough drawdown limit on live orders.** Daily loss is bound;
   a week of small losses is not. *Open.*
2. **A limit of `0` disables that check** (`if not limit`). That is how they
   are written; a mistyped `.env` value of `0` silently removes a control.
   `LIVE_*` are read once at boot, so it cannot be set from the UI — but it
   is not "fail closed on missing configuration". *Open; recommend rejecting 0
   at boot for the loss, notional and risk limits.*
3. **Market-hours has no holiday calendar** (`data/sessions.py`). On an NSE
   holiday the check passes. *Open.*
4. **The measured-edge check filters on the confidence score**, which carries
   no information where tested (`docs/research/BASELINE.md` §7). It is a
   restriction, so it can only refuse more; but its stated meaning ("an edge
   measured on setups above 65% says nothing about 50%") rests on a score
   that does not rank setups. *Left in place: removing a check from the live
   gate is the owner's call.*
5. **Position sizing used a stored risk budget** (`risk_amount`) taken from
   the balance typed in Settings, capped at 1% of the broker's cash. The cap
   is correct; at Rs 293.44 it is Rs 2.93 and the gate refuses every
   instrument (14 of 14 attempts on record).
6. **Money is `float`.** Rounding error is far below a paisa on these sums;
   it is a hygiene gap, not a measured defect.
7. **The gate is evaluated at the click, not at the fill.** A LIMIT entry may
   fill up to 300 s later (timeout), at the limit or better; the stop is
   placed by the reconciler on the fill. The window is bounded and the price
   band is checked at submission only.

## Daily loss, in numbers

Limit Rs 2,000 against an account of Rs 293.44: the limit is 6.8× the
account. It cannot bind before the account is gone. The 1%-of-cash risk cap
is the control that actually applies at this size.

## Paper guardrails (`analysis/guardrails.py`)

3R daily loss, 3R open risk, 3 consecutive losses, 6 trades a day — for the
paper account only. They do not bind live orders.

## Tested here

Zero shares at Rs 293.44 for every stock tried at 1× and 5×
(`costs_india.size_position`); stale-quote refusal for paper entries
(`test_market_state.py`). Existing gate tests cover excessive size, daily
loss, insufficient funds, invalid stops, ineligible strategy, duplicates and
invalid instruments (`test_execution.py`).
