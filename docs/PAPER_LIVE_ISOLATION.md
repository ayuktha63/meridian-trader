# PAPER / LIVE isolation

**Evidence:** source read and tested, 13 October 2026. Tests:
`backend/tests/test_mode_isolation.py` (13), `test_angelone.py`,
`test_execution.py`. The running application was read at 09:53, 10:19 and
12:00: PAPER, auto-live off, kill switch off.

## The property

PAPER mode must be unable to send an order to a broker, as a fact about the
code, not about a label or a button.

## How it is held

| Layer | Mechanism | Where |
|---|---|---|
| Paper trades are a separate system | `services/paper.py` and `routers/paper.py` keep a simulated account in the database. They import no broker module | `test_paper_analysis_backtest_and_research_do_not_import_the_order_client` |
| Only two modules can send | `place_order`, `modify_order`, `cancel_order` are called from `broker/angelone/execution.py` and `reconciler.py` and nowhere else in `app/` | `test_only_the_execution_path_calls_an_order_method` (AST scan of every module) |
| The execution service asks the gate first | `risk_gate.evaluate` precedes `place_order`; a rejection returns before any broker call | `test_the_execution_service_sends_an_order_only_after_the_gate` |
| The gate's first check is the mode | `_check_trading_mode` refuses unless `trading_mode.is_live` | `test_the_gate_refuses_every_order_unless_the_mode_is_live` |
| The mode forgets | In memory only; seeded from `TRADING_MODE`; anything other than exactly `LIVE` is PAPER; a new process is PAPER | `test_a_restart_comes_back_in_paper…`, `test_a_missing_or_garbled_mode_setting_fails_to_paper` |
| Unattended trading forgets too | `auto_live` is never seeded true, drops on any switch away from LIVE, and on a kill-switch trip | `trading_mode.py` |
| The kill switch remembers | Persisted to `data/kill_switch.json`; an unreadable file is treated as ACTIVE | `test_angelone.py` |
| The backend decides, not the UI | LIVE needs `confirmed=true` on the request, the broker connected, and then every order is gated again. The frontend toggle only calls this | `routers/broker_angel.py` |
| Research cannot reach the broker | `research/`, `backtest/`, `analysis/` import neither the client, the execution service, the reconciler nor the gate | same AST test |
| Safe defaults | `live_require_measured_edge=True`, `live_broker_validated=False`, `trading_mode=PAPER` | `test_the_live_edge_requirement_and_broker_validation_default_to_the_safe_side` |

## What paper does not claim

Paper fills use the quote's bid and ask and the cost model; they are labelled
simulated and are never broker-confirmed. A paper fill is now refused when
there is no current quote (older than 20 s, or none) — before this change it
filled at whatever price the feed had last seen. Paper performance is
recorded in `paper_trades` and is never combined with live executions
(`live_executions`).

## What is not isolated, stated plainly

1. **Switching to LIVE is a single confirmed API call** once the broker is
   connected. There is no installation-level lock. The control is the
   per-order gate, which is why the audit log can show 40 mode changes. If the
   owner wants "this machine can never go LIVE", it needs a boot-time flag
   that `set_mode` checks; it does not exist. *Not added: this is the owner's
   decision, not a defect fix.*
2. **The reconciler sends orders without checking the mode.** By design: it
   only places stops, cancels entries and closes positions that an earlier
   LIVE order opened, and "a tripped kill switch must never be the reason a
   position has no stop". It acts only on rows in `live_executions`. In PAPER
   with no such rows it sends nothing. With a row left from an earlier LIVE
   session it would still protect that position. That is intended; it is also
   the one place a PAPER process can place an order, and it is the reason the
   tests name the reconciler as an allowed sender.
3. **Evidence from the database, not the code:** the 14 execution rows on
   record were all refused before submission (none has a broker order id).
   The path to the real broker has never carried an order.
4. A person with the API open on this machine can switch mode; the API answers
   this machine only unless `APP_ACCESS_TOKEN` is set, and `serve.py` refuses
   to bind a network address without it.
