# Meridian — current state audit

**Audited:** 13 October 2026, 09:50–10:40 IST, during the NSE session, on branch
`research/quant-pipeline` at commit `45ee41b` plus the uncommitted working tree.

**Evidence:** the code as read; the test suite as run; the running application
(read-only `GET` requests to `127.0.0.1:8000`); the application database opened
read-only; the SmartAPI SDK's own log files; the research records on disk.
Nothing here is taken from an earlier report without being checked — where
something could not be checked, it says so.

**In one paragraph.** Meridian is a FastAPI backend and a React front end that
take prices from Angel One, produce a BUY/SELL/WAIT reading, paper-trade it,
and can send a real order through a twelve-check gate. The engineering around
orders is careful and well tested. **No strategy in it has a demonstrated
edge, nothing is eligible for live trading, and the account (Rs 293.44) cannot
place a single trade inside its own risk rule.** The audit found six defects on
the live data and decision path that were not known; four are fixed and
landed, with tests. The mode is PAPER. No order has ever reached the broker.

Three Claude sessions were working in this directory at the same time. Who
did what is in §9.

---

## 1. Architecture as it actually is

```
Angel One SmartAPI ──► data/providers/angelone.py ──► data/feed.py ──► data/store.py
   (REST, 3 s poll)        quotes + history            ticks → bars     in-memory OHLCV
                                                            │
                     analysis/engine.py  ◄──────────────────┘
                     indicators · structure · patterns · MTF · confidence · plan
                            │
        ┌───────────────────┼─────────────────────────┐
        ▼                   ▼                         ▼
 routers/ws.py        services/paper.py        broker/risk_gate.py
 push to the UI       simulated account        12 checks, first failure wins
                                                      │
                                         broker/angelone/execution.py   (entry, LIMIT, intraday)
                                                      │
                                         broker/angelone/reconciler.py  (fills, broker-held stop,
                                                      │                   target, square-off, recovery)
                                         broker/angelone/client.py ──► Angel One
```

| Layer | Where | Notes |
|---|---|---|
| Entry point | `backend/app/serve.py` → `app/main.py` | Refuses a non-loopback bind without `APP_ACCESS_TOKEN` |
| Front end | `frontend/src` (React, Vite, Zustand, lightweight-charts) | No tests |
| Auth / session | `broker/angelone/auth.py` | TOTP login, one session, SDK imported lazily, SDK logs redacted |
| Instruments | `broker/angelone/instruments.py` | Token, lot and tick read from Angel One's scrip master, never typed in; nearest unexpired future for an index |
| History (charts) | `data/providers/angelone.py` | `getCandleData`, up to 8 windows |
| History (research) | `data/angel_history.py`, `research/india.py`, `research/datasets.py` | Window-by-window report, complete-or-refused, hashed snapshots |
| Live quotes | `data/providers/angelone.py` | **Polling every 3 s, not a websocket** |
| Bars | `data/store.py` | Built from ticks; now on the exchange's session grid (§6) |
| Signal engine | `analysis/engine.py` + `analysis/*` | One fused verdict and five named "plays" |
| Sizing | `analysis/plan.py` (recommendation), `broker/risk_gate.py::_check_quantity` (the order) | The order is re-sized from the broker's real funds and lot |
| Pre-trade risk | `broker/risk_gate.py`, `broker/live_limits.py` | §8 |
| Backtest | `backtest/engine.py`, `backtest/replay.py` | Replays the live engine bar by bar; walk-forward |
| Research | `research/` | Separate package; cannot place an order |
| Costs | `costs.py` (per-side rate), `costs_india.py` (order by order) | §7 |
| Orders | `broker/angelone/execution.py`, `client.py`, `orders.py` | Write-ahead intent, idempotency tag, no blind retry |
| Supervision | `broker/angelone/reconciler.py` (1,160 lines) | Broker-held stop behind every fill; restart recovery |
| State machine | `broker/order_states.py`, `schemas/execution.py::ExecutionStatus` | 15 states, illegal transitions refused |
| Mode and kill switch | `broker/trading_mode.py` | Mode is in memory and forgets; the kill switch is on disk and remembers |
| Database | SQLite by default (`db/models.py`, `db/session.py`) | `create_all` plus an additive-column step. **No Alembic** |
| Audit, alerts | `audit.py` (table `audit_log`), `alerts.py`, `notify.py` (Telegram) | No e-mail |
| Observability | `observability.py` | `/api/ready`, `/metrics`, JSON logs |
| CI | `.github/workflows/ci.yml` | ruff + pytest (3.11), typecheck + build |

## 2. What is implemented and what is not

| Area | State |
|---|---|
| Angel One login, TOTP, session reuse | Implemented. Verified connected today |
| Session expiry handling | **Was defective** — fixed today (§10, D1) |
| Scrip master, token / lot / tick resolution | Implemented. Verified against the live master today |
| Historical candles | Implemented, both paths |
| Streaming quotes | Polling only. No websocket, no tick-by-tick, no depth beyond best bid/ask |
| Bar construction | **Was defective** — fixed today (D2, D3) |
| Stale-quote detection | **Was missing** — added today (D5) |
| Signal engine | Implemented. No edge measured anywhere it has been tested |
| Modular, individually testable strategies | Five research candidates (`research/strategies.py`) on a clock grid; **no session-aware intraday candidates** (ORB, VWAP, gaps) — not built at the time of this audit |
| Backtest, walk-forward | Implemented. Look-ahead audit exists (`research/audit.py`) |
| Indian cost model | Order-level for NSE cash equity (`costs_india.py`), checked against Angel One's estimator. Futures and options: per-side rates only, **not verified** |
| Statistical gates | Implemented: study protocol (`research/study.py`) and live eligibility (`research/eligibility.py`) |
| Order state machine | Implemented, tested against a fake broker |
| Broker reconciliation, restart recovery | Implemented, tested against a fake broker. **Never run against the real account** |
| Kill switch | Implemented, persisted, survives restart |
| PAPER / LIVE separation | Enforced in the backend gate (§8). Paper has no code path to the broker's order calls |
| Portfolio risk (correlation) | Sector and count limits only |
| Database migrations | Additive only. No Alembic, no downgrade |
| Decimal arithmetic for money | **Not used**: money is binary float throughout |
| Front-end tests, mypy, e-mail alerts | Not built |
| Holiday calendar for the live path | **None.** The live gate's market-hours check is by the clock (§6) |

## 3. Tests — what was run, and the result

All runs were on a copy of the working tree with no `.env` in it, using the
project's own virtualenv (Python 3.9.6). The main checkout holds real broker
credentials and was not used to run tests.

| When (IST) | What | Result |
|---|---|---|
| 09:58 | Backend suite, tree as found | **630 passed**, 0 failed, 0 skipped, 44 s |
| 09:58 | `ruff check app tests` | clean |
| 10:08 | After D1, D2, D6 | 672 passed |
| 10:13 | After D3 | 689 passed |
| 10:19 | After D4, D5 and another session's cost changes | **699 passed**, ruff clean |
| 10:19 | `tsc --noEmit`, `vite build` | clean; built in 2.0 s |

The "538 passing" in the brief was true of an earlier tree; the difference is
tests added since by this and the other sessions. Not run: the suite on
Python 3.11 (the CI interpreter; no such environment on this machine and too
little disk to build one).

## 4. Broker integration — verified today

| Check | Result | Evidence |
|---|---|---|
| Session | Connected | `GET /api/broker/angel/status` at 10:20:42 — connected, 1,628 s old |
| Mode | **PAPER**, auto-live off, kill switch off | `GET /api/trading/mode` at 09:53 and 10:19 |
| Available funds | **Rs 293.44** cash, no collateral, no margin used | `GET /api/broker/angel/account` at 10:20:42 |
| Positions and orders at the broker | 0 and 0 | same call |
| Instrument resolution | RELIANCE → NSE `RELIANCE-EQ`, lot 1, tick 0.10. NIFTY → NFO `NIFTY27OCT26FUT`, lot 65, tick 0.10. BANKNIFTY → `BANKNIFTY27OCT26FUT`, lot 30, tick 0.20 | capability endpoint |
| Live quotes | Arriving for the streamed symbols | candles and quotes endpoints |
| Real orders ever sent | **None.** 14 execution rows, all `REJECTED` before submission, none with a broker order id | `live_executions` table, read-only |
| Order path against the real broker | **Never exercised.** Order parameters, status strings, the tag echo are from documentation | `reconciler.py` docstring; `LIVE_BROKER_VALIDATED=false` |
| CDS (currency) | Blocked: tick-size scale unverified | `risk_gate.UNTRUSTED_TICK_EXCHANGES` |
| MCX (commodity) | Blocked: quantity unit (lots or units) unverified | `risk_gate.UNVERIFIED_QUANTITY_EXCHANGES` |
| Options | No execution path at all | — |

**Something the owner should know.** The audit log shows the mode switched 40
times and unattended live trading ("auto-live") armed on 12 and 13 October —
most recently LIVE with auto-live on from 01:13 to 01:35 on the 13th. Six
automatic BANKNIFTY attempts on the 12th got as far as position sizing and
were refused there: *"One lot (30) risks 3654.00 between entry and stop, but
the recommendation's risk budget is 2.93."* Eight BTCUSD attempts were
refused as unsupported. So the gate held, and the first thing it tripped on
was the size of the account. Behind that check stood the market-hours,
price-band, account-limit and measured-edge checks; with no experiment
passing, the measured-edge check would also have refused. That last statement
is read from the code, not observed: the edge check is the **last** of the
twelve, so no rejected row shows it firing.

## 5. Capital and what it can trade

At Rs 293.44, using the order-level cost model
(`costs_india`, schedule `angelone-nse-equity-intraday-2025-11-17`):

| One round trip | Charges | With assumed spread and slippage | As a share of the position |
|---|---|---|---|
| RELIANCE, 1 share at 1,162.10, intraday | Rs 12.21 (brokerage 10.00 = two Rs 5 minimums; GST 1.81; STT 0.29) | Rs 14.54 | 1.25% |
| RELIANCE, 1 share, delivery | Rs 37.99 (adds DP Rs 20 + GST) | Rs 40.31 | 3.47% |
| ITC, 1 share at 260.30, intraday | Rs 11.89 | Rs 12.41 | 4.77% |
| ITC, 5 shares, intraday | Rs 12.26 | Rs 14.86 | 1.14% |

The brief's "about Rs 12" for one RELIANCE share is confirmed to the paisa
range. The consequence: a 1% risk budget on this account is **Rs 2.93**, and
the charges on the smallest possible order are four times that. `size_position`
returns zero shares for every stock tried, at 1× and at 5× intraday leverage,
with the reason. One NIFTY future is Rs 14.71 lakh of contract value (65 ×
22,638.2) and one BANKNIFTY future Rs 16.64 lakh (30 × 55,480.2) — about
5,000 times the account.

**Research eligibility and account eligibility are separate questions.**
Nothing is research-eligible. Independently of that, nothing is tradable on
this account, and no setting should be changed to make it so. The cost of one
trade in units of risk falls from "cannot trade" at Rs 10,000 to 0.21R at
Rs 50,000 and 0.11R at Rs 10 lakh (Rs 1,000 share, 5% stop, 0.5% risk,
delivery) — that table is what decides feasibility, not a wish to trade.

## 6. Market data — defects known

Found today on the running application and in the SDK's logs. Each has a test
that fails without its fix.

**D1. A failed login was retried every three seconds, forever; an expired
token was never replaced.** The quote poll asks for a session every 3 s.
With no backoff it logged in every 3 s: 154 `loginByPassword` attempts in
four minutes on 13 Oct (09:47–09:51) while a DNS lookup failed. Against a
wrong PIN that locks an account. The opposite failure the night before: from
23:59 on 12 Oct the broker answered **"Invalid Token" (AG8001) 559 times in
half an hour** and the poll kept presenting the same token, because a session
was judged only by its age. *Fixed:* exponential backoff (5 s doubling; 120 s
ceiling when unreachable, 900 s when the broker refuses), a session the
broker disowns is dropped and replaced, two callers make one login.
`tests/test_auth_backoff.py`.

**D2. Live bars were not the exchange's bars.** The store cut bars on a UTC
clock. Angel One stamps a daily bar at midnight IST and counts intraday bars
from 09:15. Seen on the running app at 09:56: NIFTY's daily series held **two
bars for 13 October** (history's, and one built from quotes and opened at
whatever the price was when history loaded), so day change, day high and day
low were read from the wrong bar; RELIANCE's hourly series had a bar opening
at 09:30 after history's 09:15; "4h" bars split the session 1 + 4 + 2 hours.
Three more follow from the same cause and were read from the code, then
proved by test: the poll runs all night and **opened a flat bar every fifteen
minutes at the last price**; a holiday would do the same all day; and the
first bar of every session opened at yesterday's close, drawing the overnight
gap into its range. *Fixed:* bars are cut on the venue's session grid
(`data/sessions.py::BarGrid`), nothing is built outside the session or by a
tick that shows no trading, a new bar opens at its first price.
`tests/test_live_bars_session.py`.

**D6. Index-future volume was in two units.** Angel One's candles count a
future's volume in contracts; the quote's running total is in units. On
NIFTY27OCT26FUT (lot 65) history's 15-minute bars held 5,085 and 7,872 and
the two built from quotes straight after held 79,930 and 63,830 — every live
bar an apparent volume spike. *Fixed for NFO* (divide by the lot). MCX and
CDS were not measured and are not changed.

**D5. Nothing knew a quote was old.** A quote has a timestamp and nothing
read it. When the feed stopped the last price sat on screen under a green
"LIVE" badge and the paper account would fill at it. *Fixed:* every quote
read carries its age; older than 20 s is stale; the auto-trader opens no
paper trade on a stale or missing quote; the badge says STALE.
`tests/test_market_state.py`.

**The cumulative-volume defect in the brief** (every poll adding the whole
day's volume) was already fixed, with a regression test
(`test_history.py::test_angel_never_reports_the_days_volume_as_a_ticks`).
Confirmed present and passing.

**Known and not fixed:**

- **Angel One's own 15-minute history has flat, zero-volume closing bars.**
  In the stored RELIANCE series the 15:15 bar is a single price with no
  volume — the official close — on 20 of 110 days, and absent on 13 of 123.
  NIFTY-future history has a 15:30 bar on 17 of 324 days. A backtest that
  reads the last bar's high and low is reading a constant. The research
  quality check counts these (short days, zero-volume share).
- **"NIFTY" history is one futures contract's token**, not a continuous
  series: `NIFTY27OCT26FUT` returns bars back to June 2025, sixteen months
  before a monthly contract of that expiry could have been listed. What that
  series is has not been established. Every NIFTY or BANKNIFTY backtest on
  record rests on it.
- **No holiday calendar on the live path.** `is_market_open` is the clock.
  On a holiday the gate's market-hours check passes. (Research measures its
  calendar from the index's own daily bars and does not have this problem.)
- **Quotes without a book are given an assumed spread** (0.05% of price) with
  nothing in the quote saying so.
- **Polling at 3 s**, shared with history requests on one API key. Three
  sessions on the key today produced "Access denied because of exceeding
  access rate".
- **The SDK log** (`backend/logs/<date>/app.log`) had every credential field
  redacted — checked: 0 unredacted of 1,163 occurrences across both days —
  but until today it recorded the machine's public IP and MAC address.
  Another session added those to the redaction this morning.

Full detail on research data: `docs/research/ANGEL_ONE_DATA_QUALITY.md`.

## 7. Strategy and cost-model defects

**D3. The live engine decided on the unfinished bar; the backtest never did.**
`engine.run` read the store as it stood — the chart's forming bar, and every
other timeframe's forming bar, fed by quotes up to the second — and it ran
every 45 seconds. The backtest (`backtest/replay.py`) builds the same frames
at each close from bars that had finished. So most live conclusions were
about a bar no test had seen, and "paper trading agrees with the backtest",
which the eligibility rules ask for, compared two different engines. *Fixed:*
the live engine decides on completed bars at the last completed close, using
the backtest's own frame builder; the quote is what is displayed and what an
open position is managed against. `tests/test_completed_bars.py` holds the
two to identical frames.

**D4. A heuristic score was labelled a probability.** The trade panel read
"*N*% est. hit probability". The number is the confidence score squeezed into
30–72; nothing has ever fitted it to outcomes. Where it was measured (778
replayed setups, `docs/research/BASELINE.md` §7) about three in ten won
whether it said under 50 or over 70, and as a probability it scored worse
than always guessing the base rate (Brier 0.343 against 0.210). *Fixed:*
relabelled "heuristic score … not a win probability" and "signal score", with
the reason in the tooltip. The number is still shown; it is not removed from
the gate's inputs (see below).

**Costs.** Before today the engine's backtest and the research simulator
charged an NSE stock 0.03% brokerage a side with no minimum — Rs 0.35 on one
RELIANCE share where the broker charges Rs 5. Another session corrected the
rate to 0.1% and made the live limits price a stock order by order. Still
true:

- The **engine backtest and `research/simulator.py` charge per unit of
  price**, so the Rs 5 floor and Rs 20 cap cannot be represented there. The
  order-level model (`research/india_costing.py`) has to be applied to a
  study's trades separately, with an account size.
- **Futures, currency and commodity rates are unverified** per-side figures.
  The 2026 Budget raised STT on futures and options from 1 April 2026;
  `costs.STATUTORY_PER_SIDE["index"]` has not been checked against it.
- Spread and slippage are **assumed**, in three named scenarios. No fill has
  ever been observed.

Full detail: `docs/research/TRANSACTION_COST_MODEL.md`.

**Things that are not defects but should be known:**

- **Two gates, two bars.** The study protocol asks for 200 trades, a
  week-clustered 90% interval above zero, a deflated Sharpe of 0.95, 60% of
  the grid and 60% of markets profitable, survival at 1.5× costs, and better
  than random. The *live* gate (`research/eligibility.py::THRESHOLDS`) asks
  for 30 out-of-sample trades, p < 0.05 against random, and 50% of the grid.
  The live one is the one that leads to an order, and it is the weaker.
  Neither was changed.
- The live gate still filters on the confidence score
  (`risk_gate._check_measured_edge` compares it with the walk-forward's
  minimum). Given D4 that filter is of unknown value. Left as it is: removing
  a check from the live gate is the owner's decision.
- The engine does not score timeframes below the chart's in the backtest and
  does live. They are now cut at the last close, so they no longer carry the
  forming bar, but the two still differ.

## 8. Safety gates, and where each is enforced

| Gate | Enforced at | Default |
|---|---|---|
| Mode is LIVE | `broker/risk_gate.py:140` (check 1 of 12, every order path) | PAPER. In memory only — a restart is always PAPER unless `.env` says otherwise |
| Explicit confirmation | `risk_gate.py::_check_confirmation`; `routers/execution.py:89` | required |
| Broker connected | `_check_angel_connection` | — |
| Kill switch | `_check_kill_switch`; persisted in `data/kill_switch.json`; trips auto-live off | off |
| One execution per recommendation | `_check_duplicate`; unique `recommendation_id`; SUBMITTING and UNKNOWN block too | — |
| Stop on the right side, risk and size present | `_check_recommendation_shape` | — |
| Instrument resolves to a real contract | `_check_instrument_capability` | — |
| CDS and MCX blocked | `_check_untrusted_tick_conversion`, `_check_unverified_quantity_unit` | blocked, hard-coded |
| Prices on the tick grid | `_check_price_tick` | — |
| Size from the risk budget, capped by broker funds | `_check_quantity` (`LIVE_MAX_RISK_PCT_OF_FUNDS`, 1%) | fails closed if funds unreadable |
| Margin or notional within funds | `_check_funds` | — |
| Market hours, no entry near the close | `_check_market_hours` | on (by the clock) |
| Entry within 1% of the broker's last price | `_check_price_band` | on; refuses blind |
| Order value and quantity limits | `_check_order_limits` | Rs 50,000; 5,000 |
| Daily loss, trades per day, open positions, exposure | `_check_account_limits` ← `broker/live_limits.py` | Rs 2,000; 5; 2; Rs 50,000 / 1,00,000 |
| **Measured edge** | `_check_measured_edge` ← `services/edge.py` ← `research/eligibility.py` | `LIVE_REQUIRE_MEASURED_EDGE=true` |
| Broker path validated with a real order | `services/edge.py:144` → eligibility holds at PAPER VALIDATION | `LIVE_BROKER_VALIDATED=false` |
| A person's approval for this code version and market | `research/approvals.py`, table `strategy_approvals` (0 rows) | none on record |
| Unattended live trading | `routers/ws.py:449` `can_auto_trade_live()` | off; never survives a restart or a kill-switch trip |
| Limits not editable from the UI | `config.py` — read once at boot | — |
| Network exposure | `security.py`, `serve.py` | loopback only without a token |

**Not gated by anything in the backend:** switching to LIVE itself. With the
broker connected, `POST /api/trading/mode {"mode":"LIVE","confirmed":true}`
succeeds — there is no boot-time flag that forbids it. That is the design
(the per-order gate is the control), and it is why the audit log shows 40
mode changes. An environment-level lock ("this installation may never go
LIVE") does not exist; whether to add one is for the owner.

The test suite runs with `LIVE_REQUIRE_MEASURED_EDGE=false` set in
`tests/conftest.py` so that the other checks can be tested in isolation; the
tests of the edge check switch it back on. It is an environment variable in
the test process only.

## 9. Uncommitted work, and whose it is

`git status` at 09:50 showed 86 modified, 5 deleted and 14 untracked files,
all uncommitted, nothing staged. **None of it has been reset, stashed,
staged or committed by this audit.** By origin:

| Work | Files | From |
|---|---|---|
| Angel One as the only data source; CoinDCX, Binance, Zerodha, TwelveData and the synthetic feed removed from `app/` (the simulator moved to `tests/simulated.py`) | most of the 86 modified files; the 5 deletions | another session, landed 01:32 on 13 Oct |
| Live positions and history panels, live-mode banner | `LivePositionsPanel.tsx`, `LiveHistoryPanel.tsx`, `LiveModeBanner.tsx`, `TopBar.tsx`, `TradingModeControl.tsx` | earlier sessions. Read before being touched; the only edit made to `LivePositionsPanel.tsx` is one label |
| NSE research data and costs | `research/india.py`, `exchange_calendar.py`, `india_costing.py`, `universes/`, `data/angel_history.py`, `angel_charges.py`, `costs_india.py`, their tests; `docs/research/ANGEL_ONE_DATA_QUALITY.md`, `TRANSACTION_COST_MODEL.md`; the 0.1% brokerage in `costs.py`; session-aware `datasets.fetch` | session `meridian-trader-8f`, working alongside this audit |
| A baseline walk-forward of the existing engine on 25 NSE stocks, offline in its own copy | nothing in the tree | session `meridian-trader-0e` |
| `requirements.lock` | 11 lines removed (the deleted providers' dependencies) | the 01:32 change. Not touched here |
| This audit's fixes (D1–D6) | `broker/angelone/auth.py`, `data/sessions.py`, `store.py`, `feed.py`, `providers/base.py`, `providers/angelone.py`, `analysis/engine.py`, `analysis/confidence.py` (docstring), `ai/narrative.py`, `backtest/replay.py`, `backtest/engine.py`, `routers/ws.py`, `schemas/market.py`, `schemas/broker.py`, `main.py`, `__init__.py`; four front-end files; five new test files | this session |

Two earlier commits on this branch (`22d04f9`, `b0e73bb`) contain another
session's files under messages that do not describe them — recorded in
`docs/research/REPORT.md` §13 and still true.

The historical crypto research (27 experiment records, the report) is kept
and marked archived. It cannot be re-run from the current code; commit
`45ee41b` is the last that can.

## 10. Defects, in order of consequence

| # | Severity | Defect | Evidence | Status |
|---|---|---|---|---|
| D1 | **High** | Login retried every 3 s without limit; expired token never replaced | SDK logs: 154 logins in 4 min; 559 "Invalid Token" in 30 min | **Fixed**, 10 tests |
| D3 | **High** | Live engine decided on the forming bar; backtest on completed bars | code; `test_completed_bars.py` | **Fixed**, 9 tests |
| D2 | **High** | Live bars on a UTC grid: duplicate daily bar, misaligned hourly, flat bars all night, gap drawn into the first bar | running app, 09:56 and 09:59 | **Fixed**, 17 tests |
| D5 | **High** | No stale-quote detection; paper fills at a stopped price | code; no reader of `updated_at` | **Fixed**, 9 tests |
| D6 | Medium | Index-future tick volume ~65× candle volume | running app, 10:06 | **Fixed for NFO** |
| D4 | Medium | Heuristic score labelled "est. hit probability" | `TradePanel.tsx:120` | **Fixed** (relabelled) |
| D7 | **High** | No strategy has an edge; nothing eligible | every experiment on record | Open — this is the research question, not a bug |
| D8 | **High** | Account cannot place one trade inside its risk rule | §5 | Open — a fact about the account |
| D9 | **High** | Order path never run against the real broker | `LIVE_BROKER_VALIDATED=false` | Open — needs the owner, and a real order |
| D10 | Medium | Futures / options / commodity charges unverified; futures STT may be stale | `costs.STATUTORY_PER_SIDE` | Open |
| D11 | Medium | No holiday calendar on the live path | `data/sessions.py` | Open |
| D12 | Medium | NIFTY / BANKNIFTY history is a single contract token of unknown construction | dataset `3346da7b90bf33ee` | Open |
| D13 | Medium | The live eligibility bar (30 trades) is far below the study's (200) | `eligibility.THRESHOLDS` | Open — owner's decision; not weakened, not strengthened |
| D14 | Medium | Money is binary float; no Decimal | throughout | Open |
| D15 | Low | No Alembic; schema changes are additive only | `db/session.py` | Open |
| D16 | Low | Assumed spread on a quote with no book is not marked as assumed | `providers/angelone.py::_best_bid_ask` | Open |
| D17 | Low | Engine backtest still scores no timeframe below the chart's; live does | `backtest/replay.py` | Open, documented |
| D18 | Low | Manual paper close fills at the last quote even when it is stale | `routers/paper.py::close` | Open |
| D19 | Low | No front-end tests | — | Open |

## 11. The results in the brief — what could be verified

| Reported | Verdict | Basis |
|---|---|---|
| RELIANCE walk-forward, 1,500 bars: 19 trades, +0.65R | **Reproducible record.** `exp-20261012-195606-74c3ab`: 19 trades, 63% won, +0.650R, interval +0.208R…+1.072R, verdict NO MEASURED EDGE. Charged at the old 0.03% brokerage with no minimum, and on the engine before D3 — so it is a record of what that code measured, not evidence about today's | experiment record, dataset `7d8ddd5d5cbc95d0` |
| NIFTY engine walk-forward: zero trades | **Reproducible record** (`exp-20261012-195614-c5f19e`), 1,500 bars | experiment record |
| RELIANCE walk-forward, 2,000 bars: zero trades | **Not on record.** No 2,000-bar run exists in the database or the experiment files | — |
| NIFTY 15m, six months: 139 trades, 31% won, −0.41R, PF 0.46, drawdown 46%, 12 losses in a row | **Not on record. Cannot be reproduced.** No backtest row or experiment holds it | manually supplied |
| NIFTY, engine-selected thresholds: 14 trades, 14% won, −0.82R | **Not on record** | manually supplied |
| RELIANCE 15m, three months: 70 trades, 40% won, +0.06R | **Not on record** | manually supplied |
| Confidence 65+ won 27%, under 55 won 32% (NIFTY) | **Not on record.** The one calibration artefact on disk is the crypto one (778 setups), which shows the same thing — no relation | `artifacts/engine_calibration.json` |
| Crypto: BTC −0.73R, ETH −0.48R, SOL −0.11R | **Reproducible records** at commit `45ee41b`; archived, venue removed | `docs/research/REPORT.md` §7 |
| Successful live orders: zero | **Confirmed** | §4 |
| 538 backend tests | Superseded: 630 as found, 699 now | §3 |
| Balance Rs 293.44 | **Confirmed** from the broker session at 10:20:42 | §4 |
| Costs modelled at 0.03% | **Confirmed as it was**; corrected today by another session | §7 |

A 19-trade sample says nothing either way. The zero-trade runs were not
investigated further here: session `meridian-trader-0e` is re-running the
existing engine on 25 stocks with the corrected engine and costs, which is
the answer to that question and is not finished.

## 12. Reproducing this

```bash
# Tests — never in the main checkout (it has real credentials). From a copy with no .env:
cd backend
<main>/backend/.venv/bin/python -m pytest -p no:cacheprovider tests -o addopts="--tb=short" -q
<main>/backend/.venv/bin/ruff check app tests --no-cache
cd ../frontend && npx tsc --noEmit -p . && npx vite build --outDir <scratch>

# State of the running application (read-only)
curl -s http://127.0.0.1:8000/api/trading/mode
curl -s http://127.0.0.1:8000/api/broker/angel/status
curl -s "http://127.0.0.1:8000/api/market/candles?symbol=NIFTY&timeframe=1d&limit=50"   # D2: one bar per day

# Costs on one order, and sizing at an account size
python -c "from app import costs_india as c; print(c.trade_cost(1, 1162.10, 1162.10, product=c.INTRADAY).to_dict())"
python -c "from app import costs_india as c; print(c.size_position(293.44, 0.01, 1162.10, 1150.48, product=c.INTRADAY, leverage=5))"

# What research data exists and whether a study may use it
python -m app.research.india accepted 1d
python -m app.research.india accepted 15m

# The records behind §11
ls backend/data/research/experiments/        # exp-20261012-1956* are the NSE walk-forwards
sqlite3 "file:backend/data/meridian.db?mode=ro" "select status, count(*) from live_executions group by 1"
sqlite3 "file:backend/data/meridian.db?mode=ro" "select event, count(*) from audit_log group by 1"
```

**After pulling these changes the backend must be restarted** (it does not
reload). It will start in PAPER. Until it is restarted the status bar shows
"NSE —", because the running server still serves the old clock.
