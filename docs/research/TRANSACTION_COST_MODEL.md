# What a trade in NSE cash equities costs — the model, its sources, and how far it was checked

13 October 2026. Branch `research/quant-pipeline`, uncommitted.

**The cost model for Indian equities was out of date and understated what a
small order pays by a factor of two and a half to six. It has been replaced
by one that prices each order as Angel One does, and that agrees with Angel
One's own calculator to the paisa on twelve test orders.** No strategy has
been tested with it yet.

---

## 1. Why a rate per side is not enough

`app/costs.py` priced a trade as a percentage of its value. Three of the
charges on an Indian equity order are not percentages:

- **Brokerage** is the lower of ₹20 and 0.1% of the order, with a minimum of
  ₹5 — per executed order. On a ₹2,000 order that is ₹5: a quarter of a
  percent, two and a half times the headline rate.
- **The depository (DP) charge** on a delivery sale is ₹20 plus GST per
  stock per day, whether ten shares were sold or ten thousand.
- **GST** is charged on both, so it carries the same floors.

What a trade costs therefore depends on how big it is, and that depends on
whose account it is. `app/costs_india.py` prices an *order* and returns
every charge by name; a round trip is two or more orders added up.

## 2. The schedule

Angel One, NSE cash equity, effective 17 November 2025, read 13 October 2026.

| Charge | Delivery | Intraday | On |
|---|---|---|---|
| Brokerage | 0.1%, max ₹20, min ₹5 | 0.1%, max ₹20, min ₹5 | each executed order |
| STT | 0.1% | 0.025% | delivery: both sides; intraday: the sale |
| Stamp duty | 0.015% | 0.003% | the purchase |
| Exchange transaction charge (NSE) | 0.0030699% | 0.0030699% | both sides |
| SEBI fee | ₹10 per crore | ₹10 per crore | both sides |
| IPFT | 0.0000001% | 0.0000001% | both sides |
| GST | 18% | 18% | brokerage, exchange, SEBI, IPFT and DP — not STT or stamp duty |
| DP charge | ₹20 + GST | none | per stock, per day sold |

Sources: Angel One's list of charges (`angelone.in/list-of-charges`) and its
pricing update of 17 November 2025, which raised intraday brokerage from
0.03% to 0.1% and the minimum to ₹5. Equity STT was not changed by the 2026
Budget, which raised futures and options only.

A schedule carries its id, its effective date, the date it was read and its
sources, and a result records which schedule charged it. Changing a rate
changes the cost fingerprint (`costs@…`), and evidence charged by the old
rate stops counting toward live eligibility.

## 3. How far it was checked

SmartAPI has a charge estimator (`estimateCharges`): give it an order and it
returns what that order would be charged, line by line. It places nothing.
`python -m app.data.angel_charges` asks it about a spread of orders and
compares each line with the engine.

RELIANCE on NSE, 13 October 2026 — total charges in rupees, DP excluded:

| Product | Side | ₹1,340 order | ₹13,400 order | ₹268,000 order |
|---|---|---|---|---|
| Delivery | Buy | 7.49 / 7.49 | 31.72 / 31.72 | 341.82 / 341.82 |
| Delivery | Sell | 7.29 / 7.29 | 29.71 / 29.71 | 301.62 / 301.62 |
| Intraday | Buy | 5.99 / 5.99 | 16.72 / 16.72 | 41.66 / 41.66 |
| Intraday | Sell | 6.29 / 6.29 | 19.66 / 19.66 | 100.62 / 100.62 |

*(engine / broker)* — the three sizes land on the ₹5 floor, the 0.1% rate
and the ₹20 cap. All twelve agree on every line.

**What this does not cover:**

- **The DP charge.** The estimator prices an order, and the DP charge is per
  stock per day, so no estimate includes it. The ₹20 + GST rests on the
  price list alone.
- **A contract note.** Nothing here has been reconciled with one. STT on a
  note is rounded to the rupee; the engine does not round.
- **SEBI's 2.5% ceiling on brokerage**, which would cap the ₹5 minimum on an
  order under ₹200. The engine charges the full ₹5 — slightly too much on
  orders that small.

## 4. What changed in the application

| Order (each way), intraday round trip | Old estimate | As Angel One charges it |
|---|---|---|
| ₹2,000 | ₹2.12 | ₹12.51 |
| ₹5,000 | ₹5.30 | ₹13.57 |
| ₹10,000 | ₹10.60 | ₹27.15 |
| ₹20,000 | ₹21.21 | ₹54.30 |
| ₹50,000 | ₹53.02 | ₹64.94 |
| ₹100,000 | ₹106.05 | ₹82.68 |
| ₹500,000 | ₹530.23 | ₹224.61 |

The old figure is the one the live safeguards used: the pre-trade check that
an order's loss at its stop fits the risk budget, and the day's running loss
that trips the kill switch. For any order up to ₹50,000 — the largest a
live order is allowed to be — it was too low.

- `costs.charges()`, which those safeguards call, now prices a stock's round
  trip order by order. It is not told which leg was the sale, so it returns
  the dearer reading (a few paise).
- `costs.per_side_rate()`, which backtests and paper trading are charged by,
  uses 0.1% brokerage for stocks. As a rate that is exact between ₹5,000
  and ₹20,000, too little below (the floor) and too much above (the cap).
- Futures, currency and commodity rates are unchanged.

Seven tests that pinned the old figures were restated from the published
rule, by hand: ₹56.06 for a ₹25,000 trade stopped at ₹24,800, where they had
said ₹26.41.

**Reported, not changed:** the rate table for index futures still carries
STT of 0.02% on the sale. The 2026 Budget raised it to 0.05% from 1 April
2026. Brokerage on a future is over-stated by the same table (0.03% where
Angel One charges a flat ₹20), and the two errors roughly offset on a large
contract — but neither is right, and futures need their own order-level
schedule before anything is concluded about them.

## 5. Execution: what is given up to get filled

Spread and slippage are not charges and nobody publishes them. They are
**assumptions**, labelled as such in every record, to be replaced by fills
from paper trading.

| Scenario | Half-spread, each side | Slippage, each side | Extra on a stop | Round trip, stopped |
|---|---|---|---|---|
| Optimistic | 0.01% | 0 | 0.02% | 0.04% |
| Base | 0.02% | 0.02% | 0.05% | 0.13% |
| Conservative | 0.05% | 0.05% | 0.15% | 0.35% |

Eligibility is judged on the conservative row. A resting limit order that
the market came to pays neither; a breakout entry on a stop slips as a
protective stop does. A gap through a stop is the simulator's to model — it
fills at the open — and is not in these figures.

## 6. What this means for a small account

One delivery trade: a ₹1,000 share, a stop 5% away, half a percent of the
account at risk, conservative execution. "Cost in R" is what the trade must
make, in multiples of its risk, before it has made anything.

| Account | At risk | Shares | Costs if stopped | Cost in R |
|---|---|---|---|---|
| ₹10,000 | ₹50 | **0** | — | cannot trade |
| ₹15,000 | ₹75 | **0** | — | cannot trade |
| ₹25,000 | ₹125 | 1 | ₹40.95 | 0.33 |
| ₹50,000 | ₹250 | 3 | ₹52.04 | 0.21 |
| ₹100,000 | ₹500 | 8 | ₹86.39 | 0.17 |
| ₹250,000 | ₹1,250 | 21 | ₹187.25 | 0.15 |
| ₹1,000,000 | ₹5,000 | 88 | ₹559.02 | 0.11 |

At ₹10,000, one share loses ₹90.95 at the stop — ₹40.95 of it charges and
slippage — against a budget of ₹50. The position is never rounded up to
fit: the answer is no shares, with that reason.

Intraday, with a stop 1% away and five times leverage, a ₹10,000 account
can take two shares and pays 0.39R; a ₹1,000,000 account pays 0.28R. A
tight stop makes every cost large against the risk. An intraday strategy
has to earn roughly a third of its risk on the average trade, before costs,
merely to break even under these assumptions.

## 7. How a backtest is charged

`research/india_costing.py` takes trades the simulator produced without
costs and, for a stated account:

1. sizes each at the decision price — whole shares, the loss at the stop
   *including costs* inside the risk budget, never rounded up;
2. counts a signal the account cannot afford one share of as **skipped**,
   with the reason, rather than shrinking or forcing it;
3. charges the entry and each exit as orders, by the schedule and one
   execution scenario;
4. reports gross P&L, brokerage, each statutory charge, the DP charge, the
   execution estimate, net P&L, turnover, cost per trade, expectancy in
   rupees and in R, and the multiple of the modelled costs the result could
   bear before nothing was left.

`sensitivity()` gives the same trades under each scenario at 1×, 1.5× and
2× costs; `by_account()` gives them for several account sizes. It refuses
trades the simulator already charged, and intraday trades held overnight.

Two limits. The account does not compound: every trade is sized against the
same equity, so a result is a rate per trade, not a growth curve. And sizing
uses back-adjusted prices, which understates the smallest account that
could have traded a stock that has since split.
