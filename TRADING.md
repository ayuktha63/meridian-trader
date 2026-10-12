# Trading with Meridian

This document is the honest version. It covers what you can legally trade from
India, how to get live data in, how a signal becomes an order in Kite, what it
costs, and what actually has to be true before any of this makes money.

**By default, Meridian never places an order.** It reads the market and hands
you a plan. Every order is typed by you, in your broker's terminal, with your
finger on the button. That is the deliberate default, not a missing feature.

**The one exception is Angel One, and only if you turn it on.** Meridian can
optionally submit a real order to your Angel One account — but it starts in
**PAPER** mode, always comes back to PAPER after a restart, and LIVE mode
requires you to explicitly flip a switch and confirm a dialog that shows the
exact order before anything is sent. See §2a and §3a. Nothing about this is
automatic, and nothing about it is subtle — a red **LIVE TRADING · REAL
MONEY** banner replaces the quiet default whenever it's armed.

**"Risk-free" is not on offer, here or anywhere.** What the live path does is
bound a loss and refuse to trade without evidence: a stop order rests at the
broker behind every fill, hard limits are checked before any order goes out,
and an order is refused outright unless a walk-forward test of that market
has shown an edge after costs (§7). When the honest answer is "no measured
edge", that is what it says — and that answer is the system working, not
failing.

---

## 1. What you can actually trade

Your broker determines this, and for an Indian resident so does the law.

### The legal position

Under **FEMA**, an Indian resident may trade currency derivatives only on a
**recognised Indian exchange** (NSE, BSE, MSEI), and only in the INR-paired
contracts those exchanges list. Trading **spot EURUSD / GBPUSD / XAUUSD through
an offshore broker is not permitted**, regardless of how the platform markets
itself. The RBI publishes an *Alert List* of unauthorised forex platforms, and
banks are instructed to refuse remittances to them.

So: the EURUSD chart in this app is for learning and for analysis. Do not fund
an offshore forex account on the strength of it.

### What Zerodha gives you

| Instrument | Symbol here | Venue | Lot | One tick |
| --- | --- | --- | --- | --- |
| USD/INR future | `USDINR` | NSE-CDS | 1,000 USD | ₹2.50 |
| EUR/INR future | `EURINR` | NSE-CDS | 1,000 EUR | ₹2.50 |
| GBP/INR future | `GBPINR` | NSE-CDS | 1,000 GBP | ₹2.50 |
| JPY/INR future | `JPYINR` | NSE-CDS | 100,000 JPY | ₹2.50 |
| NIFTY 50 | `NIFTY` | NSE-NFO | 75 | ₹3.75 |
| Bank NIFTY | `BANKNIFTY` | NSE-NFO | 30 | ₹1.50 |
| Gold | `MCXGOLD` | MCX | 100 g | ₹100 |
| Silver | `MCXSILVER` | MCX | 30 kg | ₹30 |
| Crude oil | `MCXCRUDE` | MCX | 100 bbl | ₹100 |

Plus **122 NSE F&O stocks** — every name with a listed futures and options
contract, from `RELIANCE` and `HDFCBANK` down to the mid-caps. Each can be
traded three ways: cash equity, the stock future, or an option. Search the
symbol picker by ticker, company name, sector or venue.

> **Verify every lot size before you trade it.** Exchanges revise them — NIFTY's
> lot size has changed several times, and stock lots change roughly twice a
> year. The contract page on the NSE/MCX site is the authority; the numbers
> here are defaults so the sizing maths has something to work with. When Kite
> is connected, Meridian adopts the broker's instrument dump instead, which is
> authoritative.

**Crypto** is legal in India but not available through Zerodha — use CoinDCX
(see §2). It is taxed at a flat **30% on gains, with 1% TDS on each sale and no
loss set-off** — the worst tax treatment of anything on this list.

### Futures or options?

The **F&O tab** turns any directional call into a contract you can place. Both
routes are shown side by side because they fail in opposite ways:

- **Futures** track the underlying one-for-one. No decay, no strike selection,
  no volatility guesswork. But the loss is uncapped and margin is 15–20% of
  notional, so a gap through your stop can cost more than you posted.
- **Options** cap the loss at the premium — but that premium decays every day
  whether you are right or not, and it is a *100% loss* far more often than a
  futures position is.

Three things on that tab are worth more than the rest:

1. **The stop is quoted in premium, not in the underlying.** A 100-point stop
   on NIFTY is not a 100-point stop on the option — through a delta of 0.54 it
   is about 54 points of premium. That converted number is what your broker
   fills, and getting it wrong is the most common way an option trade goes
   further wrong than intended.
2. **Theta is shown as a percentage of premium per day.** Above ~3%/day the
   decay, not your view, is the dominant term in the trade. The panel flags it.
3. **Break-even is the underlying level, not the premium.** The market has to
   travel past it before you make anything, which is always further than people
   expect.

Premiums and greeks are modelled with Black-Scholes on volatility inferred from
recent realised range. **Real implied volatility comes from the live option
chain and will differ**, sometimes by a lot around results and policy dates.
Treat the numbers as an order of magnitude and check the actual chain before
placing anything.

---

## 2. Getting live data in

Open **Settings** (gear icon, top right).

### Crypto — works immediately, via CoinDCX

Nothing to configure. Seventeen pairs stream live from CoinDCX's public
endpoints with no credentials: twelve USDT books (BTC, ETH, SOL, XRP, DOGE,
BNB, ADA, AVAX, LINK, DOT, LTC, TRX) and five INR books (BTC, ETH, SOL, XRP,
DOGE). This is the fastest way to see the platform working on real, moving
prices — and it is the venue you already hold an account on.

Meridian reads market data only. It has no order API wired to CoinDCX and
never will; you place every trade yourself in their app.

**Trade the USDT books, not the INR books.** Measured live, the difference is
brutal:

| Book | Spread to cross | Round trip |
|---|---|---|
| BTCUSDT | ~0.00% | negligible |
| SOLUSDT | 0.013% | 0.03% |
| BTCINR | 0.37% | **0.74%** |
| SOLINR | 0.55% | **1.10%** |
| DOGEINR | 0.63% | **1.26%** |

An INR-book scalp needs the market to move more than 1% in your favour before
you break even. The same trade on the USDT book needs 0.03%. That is a
30–60× handicap, and it is invisible until you count it. Use the INR pairs
only for multi-day positions where a 1% cost is small against the move, or
when you specifically need rupee settlement.

Meridian encodes these spreads per instrument, so the cost model and the
sizing already reflect them.

**The spread is the small part. The fee is the large one.** CoinDCX's spot
fee is reported at 0.20% a side at the base tier — some sources say 0.10%,
some 0.50% for small INR volumes — plus 18% GST, and 1% TDS is withheld on
every sale. That is close to **half a percent a round trip before the
market has moved**, on the USDT books too. Meridian used to charge crypto
0.05% a side, which is the fee on the exchange's *futures*; it now charges
these books as the spot books they are (§5), and no hourly strategy tested
survives it (§7). Check the figure against your own statement — it could
not be read from CoinDCX's fee page directly — and treat any intraday crypto
signal accordingly.

### Zerodha — for everything Indian

1. **Get a Kite Connect app.** Sign in at
   [developers.kite.trade](https://developers.kite.trade), create an app, and
   set the redirect URL to `http://localhost:5173`.
2. **Subscribe.** Live quotes and historical candles require the **paid** Kite
   Connect plan. The free personal tier can place orders but returns no market
   data, which makes it useless for this platform. Check current pricing on
   [zerodha.com/products/api](https://zerodha.com/products/api/).
3. **Save your key and secret** in Settings → Zerodha.
4. **Log in daily.** Click *Open Kite login*, approve, then copy the
   `request_token=…` value out of the redirect URL and paste it back. Meridian
   exchanges it for the day's access token and reconnects the feed.

Kite access tokens **expire every morning around 06:00 IST**. That is a broker
rule. Step 4 is a daily ritual, roughly fifteen seconds.

### Adding your Claude key

Settings → Analyst → paste your key from
[console.anthropic.com](https://console.anthropic.com) → **Test connection**.

Without a key the platform still produces the full verdict from its
deterministic confluence engine. The key buys you Claude's reasoning over the
evidence — the bull/bear argument, the invalidation logic, the uncertainty.

Watch the cost: analysis runs on every bar close *and* on the interval you set.
On a 1-minute chart at a 15-second interval that is ~240 calls an hour. Set the
interval to match how fast you actually trade — on a 15m chart, 120s is plenty.

### 2a. Angel One — optional, real orders, off by default

Everything above is analysis. This is the one integration that can touch real
money, so read this section fully before you connect it.

**What it is.** Angel One SmartAPI lets Meridian submit a real order — after
you review and confirm it — instead of you retyping the plan into a broker's
terminal. It does not replace the manual Kite/CoinDCX workflow in §3; it's an
alternative for the same recommendation, gated behind explicit confirmation
at every step.

**Setup.**

1. Create a SmartAPI app at [smartapi.angelone.in](https://smartapi.angelone.in)
   and enable TOTP-based login on your Angel One account.
2. Add four values to `.env` (never the Settings UI — see why below):
   ```
   TRADING_MODE=PAPER
   ANGEL_API_KEY=
   ANGEL_CLIENT_CODE=
   ANGEL_PASSWORD=
   ANGEL_TOTP_SECRET=
   ```
3. Restart the backend. The DEMO/LIVE control in the top bar (TradingModeControl)
   shows Angel One's connection status once it can authenticate.
4. **Set the limits to your account, not to the defaults.** They are in
   `.env` too (table below), sized for a small account, and they are what
   stands between a bug — or a bad afternoon — and your balance.
5. Optional but worth it: `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`, so a
   stop that could not be placed reaches your phone and not just the screen.
6. Run a **walk-forward test** (Backtest tab) on each symbol and timeframe
   you mean to trade. Without a passing one, Execute Live stays refused —
   and a passing one is no longer enough by itself (§3a, *What the evidence
   has to be*).
7. When you have placed one minimum-size order and watched it end to end in
   Angel One's own app, set `LIVE_BROKER_VALIDATED=true`. Until then nothing
   becomes eligible for live review: the order path itself is unverified.

**The limits.** Read once at startup, never editable from the UI — a limit
you can raise with a click while you are losing is not a limit. Changing one
means editing `.env` and restarting, which also drops you back to PAPER.
Setting one to `0` switches that check off.

| Setting | Default | What it stops |
|---|---|---|
| `LIVE_MAX_RISK_PCT_OF_FUNDS` | 1.0 | An order risking more than this share of the cash Angel One reports — whatever balance is typed into Settings |
| `LIVE_MAX_ORDER_VALUE` | 50,000 | One order bigger than this |
| `LIVE_MAX_QUANTITY` | 5,000 | A fat-fingered size |
| `LIVE_MAX_OPEN_POSITIONS` | 2 | A third position (working orders count) |
| `LIVE_MAX_TRADES_PER_DAY` | 5 | Overtrading |
| `LIVE_DAILY_LOSS_LIMIT` | 2,000 | A new order that could take the day past this if it and every open stop were hit; reaching it trips the kill switch. Counted **after charges** — see below |
| `LIVE_MAX_SYMBOL_EXPOSURE` | 50,000 | Too much in one name |
| `LIVE_MAX_SECTOR_EXPOSURE` | 1,00,000 | Stacking one sector |
| `LIVE_PRICE_BAND_PCT` | 1.0 | An entry far from the last traded price — a stale recommendation |
| `LIVE_ENTRY_TIMEOUT_SECONDS` | 300 | An unfilled entry left resting to fill hours later |
| `LIVE_NO_ENTRY_MINUTES_BEFORE_CLOSE` | 45 | An entry that would be squared off almost at once |
| `LIVE_SQUAREOFF_MINUTES_BEFORE_CLOSE` | 20 | Leaving the exit to the broker's own square-off |
| `LIVE_REQUIRE_MEASURED_EDGE` | true | Trading a market with no evidence behind it |

Two rules have no setting, because there is no safe way to switch them off:

- **One live position per instrument.** Angel One nets every order in an
  instrument into a single position. A second entry in the same name —
  adding to it, or the other way round — could not be stopped, sized, or
  even told apart on its own, so it is refused until the first is closed.
- **The day's loss is counted after charges.** A trade stopped exactly at its
  stop has lost the distance to it *and* about 0.1% of its turnover in
  brokerage and statutory charges (§5). Angel One's order book reports fills,
  not charges, so Meridian estimates them from the same cost model the
  backtest is charged by — both for what the day has lost and for what the
  open stops could still lose. The contract note is the record; the limit
  errs on the side of stopping early.

**Why credentials live in `.env` and not Settings, unlike Zerodha's.**
Zerodha's keys in this app are read-only market data. Angel One's can place
an order, so they're boot-time environment variables only — never accepted
over the API, never returned by any endpoint even masked, never touched by
the frontend. If you've read `backend/app/broker/angelone/`, that boundary is
enforced in code, not just in this paragraph.

**PAPER and LIVE.** The account always starts in PAPER — real broker
connection, zero orders sent. Switching to LIVE is a deliberate two-part act:
flip the toggle in the DEMO/LIVE control in the top bar, then confirm the
dialog that follows. **A backend restart always drops back to PAPER**, on
purpose — a crash mid-session must never wake back up still armed. There's
also a **kill switch** (the same top-bar control → Emergency Stop). It blocks
new live orders and cancels every entry order still working; protective
stops are left exactly where they are. It stays on — through a switch to
PAPER and back, and through a restart — until you clear it yourself. Closing
open positions is a separate, explicit button beside it (**Flatten N live
positions**), which asks first. The kill switch also trips itself: when the
daily loss limit is reached, and when a stop order cannot be placed.

**Instrument support is checked, not assumed.** Angel One does not support
crypto at all — BTCUSD, ETHUSD and every other CoinDCX pair simply cannot be
live-traded through it, full stop, and the UI says so rather than silently
falling back to something else. For everything else, Meridian resolves the
real, current contract (exchange, trading symbol, token, lot size, expiry)
from Angel One's own instrument master before ever showing you an
**Execute Live** button — never a hard-coded guess.

**Two segments are deliberately blocked**, because each depends on a number
nobody has checked against the broker, and a wrong guess is a wrong order:

- **Currency futures** (USDINR, EURINR, GBPINR, JPYINR): the tick-size
  conversion from Angel One's contract file doesn't match the known real
  value.
- **Commodity futures** (MCX gold, silver, crude and the rest): orders are
  sized in contract units — one lot of crude is 100 — which is what Angel One
  takes for equity and for index futures. Commodity quantities are widely
  reported to be taken in *lots* instead. If that is right, an order sized
  here would be a hundred times too large, so none is sent until a real
  one-lot order has settled the question.

Cash equity and index futures aren't affected. Both blocks are a line each in
`backend/app/broker/risk_gate.py`, with the reason beside it.

---

## 3. From signal to order

The whole loop, in the order you do it.

### Before the session

- **Use the screener to pick the instrument, not gut feel.** Click **Best trades**
  in the top bar — it scans every instrument in a few seconds and shows only
  the ones with a live BUY or SELL right now, sorted by confidence, instead
  of you opening 30 charts one at a time to find out the same thing. Click a
  row to jump straight to that chart and timeframe.
- **It's filtered to what you can actually trade.** By default the screener
  drops spot FX and metals (EURUSD, XAUUSD, …) — analysis-only for Indian
  residents, see §1 — and shows only instruments live on CoinDCX or Angel
  One. Every row is tagged with exactly which one and the real trading name
  to search for there: CoinDCX's own pair (`BTCUSDT`), or Angel One's
  currently-resolved contract (`NIFTY25AUG26FUT`, not a guessed name — the
  same resolution the live-execution path uses, see §2a). No translation
  step between "the screener says X" and "what do I type into the app."
- Once you're on a chart, **stay on it for the session**. Flipping charts
  after that point, looking for a *better* signal than the one you have, is
  how you find one that isn't there — the screener is for the "which
  instrument" decision, not for re-litigating it every five minutes.
- Mark your own levels. Draw the swing highs/lows, the ranges, the zones you
  care about. Meridian scores every drawing for touches, reactions and breaks,
  and feeds that to the analyst as evidence — your read is an input, not
  decoration.
- Check **Backtest** on this instrument and timeframe. If the confluence engine
  has no edge on it historically, today is unlikely to be the exception.

**What the screener is and isn't.** It's the same deterministic rule engine
behind every analysis — no Claude, no pattern detection, which is exactly
what keeps a full scan fast. It is not a stronger signal than what you'd get
opening that chart directly; it's the same confidence number, just computed
for everything at once so you know where to look. A high-confidence row here
still deserves the same four checks in "When a signal appears" below before
you act on it — the screener narrows the search, it doesn't replace the read.

### When a signal appears

The trade panel gives you: decision, confidence, entry, stop, target, R:R, and
a suggested lot size. Before you act on it, run four checks yourself.

1. **Is the R:R real?** Below 1.5 it usually isn't worth it after costs.
2. **Is the stop behind something?** The panel says *why* it chose that level.
   "Behind structural level 83.1450" is a reason. "Volatility stop, no clean
   structure in range" means the market has not given you a place to hide.
3. **Do the timeframes agree?** The MTF panel is at the bottom right. A BUY
   against a bearish daily is a counter-trend trade — take it smaller or not at
   all.
4. **What is the analyst uncertain about?** That field exists to be read.

If any check fails, the answer is no trade. Passing on a marginal setup costs
you nothing.

### Reading the Plays tab

The headline verdict is one blended number, and blending hides the shape of the
market. A chart can be hostile to breakouts and generous to mean reversion at
the same moment. The **Plays** tab runs five strategies independently and shows
every precondition each one tests:

| Strategy | Fires when | Dies when |
|---|---|---|
| Trend Continuation | Higher timeframes aligned, pullback into structure | The trend breaks |
| Mean Reversion | Ranging, ADX < 22, price stretched | A break of structure prints |
| Range Breakout | Compression, price at the edge, **volume confirming** | The break has no volume |
| Order Block Retest | BOS, then price returns to the block | Price closes through the block |
| Momentum Expansion | MACD expanding, ADX rising, RVOL up | The first stall |

A strategy takes a side only when 60% of its weighted conditions hold; below
that it reports what it is *waiting for*, which is often more useful than a
signal. Expand any row to see each condition ticked or crossed with its current
reading.

The **agreement** figure matters as much as the direction. When strategies
disagree, the market is usually between regimes — the worst possible time to
size up.

### The scenario paths on the chart

Each engaged strategy draws its thesis forward from the last bar: a dashed path
to its target, and for the leading one a second path to its stop. Several of
them disagree at once, and that is deliberate.

**These are not predictions.** They are conditionals — where price goes *if*
that strategy's thesis is correct. Nothing in this platform knows the future.
The paths exist so you can see what each read implies and how far apart they
are, not so you can pick the prettiest line.

### Placing the order in Kite

Meridian gives you levels; Kite needs an order. For a long USDINR future:

1. Search **USDINR** in Kite and pick the **current-month contract** — the
   nearest expiry, which is where the liquidity is. Meridian resolves this for
   you (Settings → Zerodha shows the resolved contract), but confirm it.
2. **Buy → NRML** for a positional trade, **MIS** for intraday. MIS gives more
   leverage and is auto-squared-off in the afternoon; NRML needs full margin
   and can be held to expiry.
3. **Quantity in lots**, not units. If Meridian says 3 lots, enter `3` in the
   lots field.
4. Order type **Limit** at the entry price, or **Market** if you're taking the
   signal immediately and the spread is tight.
5. **Place the stop-loss immediately after the fill.** Use a separate **SL-M**
   order in the opposite direction at the stop price. Not a mental stop. Not
   "I'll watch it." An order in the book.
6. Set the target as a **Limit** order, or use a **GTT OCO** so the stop and
   target cancel each other.

If you skip step 5, nothing else in this document matters.

### 3a. Placing the order via Angel One — the alternative to the above

If you've connected Angel One (§2a) and switched to LIVE, the trade panel
shows an **Execute Live** button next to **Log to journal** instead of making
you retype the plan into Kite. It's only enabled when Angel One is connected,
LIVE mode is confirmed, the kill switch is off, and the instrument is
actually supported — otherwise it's disabled with the exact reason shown, not
just greyed out.

Clicking it shows a confirmation dialog — and what it shows is not the
recommendation's numbers but the **server's own preview of the order**: the
real quantity in shares or lots, the prices rounded to the exchange tick, the
order value, what a stop-out would cost and the estimated charges on top of
it, the margin it blocks, and the pre-trade check's verdict. If the order would be refused, the dialog says why
and **Confirm & Execute** is disabled. What you confirm is exactly what is
sent; the browser contributes nothing but the click.

**How the order is sized.** Not from the lot figure on the trade panel:

```
budget    = the smaller of   the recommendation's risk amount (Settings → Risk)
                             LIVE_MAX_RISK_PCT_OF_FUNDS × cash Angel One reports
quantity  = budget ÷ (entry − stop),  rounded DOWN to a whole broker lot
```

So a ₹2,00,000 balance in Settings does nothing for you if the account holds
₹40,000: the order risks 1% of ₹40,000. If the budget cannot buy one lot the
order is refused — it is never rounded up.

**Before the order is sent** the pre-trade check has to pass every test, and
any one of them refuses the order with its reason:

- LIVE mode, confirmed, Angel One connected, kill switch off, and this
  recommendation not already executed;
- the instrument resolves to a real contract with a plausible tick size, and
  the stop and target are on the right sides of the entry;
- the order fits the funds: Angel One's own margin figure where it gives
  one, the full notional where it does not;
- the market is open, it is not the last 45 minutes of the session, and the
  entry is within `LIVE_PRICE_BAND_PCT` of the last traded price — with no
  price at all, no order;
- order value, quantity, open positions, trades today, and exposure per
  symbol and per sector are inside their limits;
- nothing is already live in this instrument — no position, no working
  order (one at a time per instrument, §2a);
- if this order *and every stop already open* were hit, the day would still
  be inside `LIVE_DAILY_LOSS_LIMIT`, charges included;
- the evidence for this symbol and timeframe is in order, and a person has
  approved it (below) — and this recommendation is the kind of trade that
  was tested: at or above the confidence and reward:risk it traded.

**What the evidence has to be.** A passing walk-forward test used to be the
whole of it. It is now the first of several, and each of them refuses on
its own:

- the test is on record as an experiment whose hash still matches, less
  than 30 days old, with at least 30 out-of-sample trades and an interval
  above zero;
- it ran on a **real venue's data** that passed its quality checks — never
  on simulated bars, which is what a symbol with no venue connected shows;
- it was run by **the code that is running now**. Change the engine, the
  execution model or the cost model and the test is about something that no
  longer exists;
- it was charged as the venue this market trades on;
- it did better than its own trades moved to **random bars**, and does not
  hang on one parameter setting;
- it is not the seventh attempt at the same question in a month;
- **twenty closed paper trades** on this market agree with it;
- the broker path has been validated (`LIVE_BROKER_VALIDATED`).

When all of that holds, the state is **ELIGIBLE FOR HUMAN REVIEW** — and the
order is still refused. A person has to record an approval
(`POST /api/research/approvals`, with a name and an explicit confirmation)
for that version of the code on that market. Edit the engine afterwards and
the approval is for code that no longer runs. The Backtest panel shows the
current state and every reason for it; so does
`/api/research/eligibility?symbol=…&timeframe=…`.

**After the order is sent** Meridian supervises it — every few seconds, and
again from scratch after a restart:

- The entry is a **LIMIT at the recommended price**. Not filled in
  `LIVE_ENTRY_TIMEOUT_SECONDS` (five minutes), it is cancelled as stale. A
  partial fill keeps what filled and cancels the rest.
- On a fill, a **stop order is placed at Angel One** for the filled quantity
  — SL-M, or a stop-limit where SL-M is refused — and a target limit beside
  it. When one fills the other is cancelled. The result card says **"Stop
  resting at Angel One"**, or in red that it is not.
- **If no stop can be placed, the position is closed at market, the kill
  switch trips, and a critical alert is raised.** A live position is never
  left open without a stop at the broker.
- As the trade runs, the protection ladder moves that real stop — only ever
  tighter (§ *The protection ladder*).
- If Angel One will not rest a target beside the stop (it may want extra
  margin for a second exit order), the stop stays and Meridian takes the
  target itself when price gets there — which needs Meridian running.
- Twenty minutes before the session ends, whatever is still open is closed.
  Orders are intraday (MIS): the broker would square them off anyway, later
  and on its terms.
- What is held is **counted from the fills** every pass, not assumed: a
  target that fills for part of the position shrinks the stop to what is
  left, and if both exits ever fill, the excess is bought straight back.
- If you close the position yourself in Angel One's app, Meridian sees the
  order that did it, records the exit, and cancels its own stop so it cannot
  open a new position later. It will **not** cancel a stop just because the
  positions screen came back empty — it needs an order that explains it.
- If you sell **part** of it yourself, the stop and the target are cut down
  to what Angel One still shows. Left at the full size, the stop would sell
  shares that are no longer there and leave you short.
- **No order is ever placed for a position Angel One does not show.** In the
  first seconds after a fill the positions screen can lag the order book, and
  the stop does not wait for it. After that, if the broker says nothing is
  held, Meridian places nothing and changes nothing until the two agree —
  and says so. (This is also what makes an upgrade safe: a fill recorded by
  an older version, long since squared off, is closed out in the records
  rather than given a stop.)
- A position that cannot be checked at all raises a critical alert rather
  than a line in a log.
- **UNKNOWN** still means a submission timed out. Meridian looks for that
  exact order by the tag it carries and adopts it if it exists; until that
  is settled a second click is refused.

Every order, stop, stop move, close, mode change and refused order is in the
audit log at `/api/audit`.

**What can still go wrong, plainly:**

- **None of this has been run against a real Angel One account.** It is
  tested against a simulated broker, thoroughly, and the simulator is not
  the broker. The order parameters, the status words Angel One uses, whether
  it echoes the order tag, and how it reports an intraday position are all
  taken from its documentation, not from watching it. Make the first live
  order the smallest the exchange allows, on a liquid stock, and watch Angel
  One's own app while it goes: confirm the stop appears there, at the right
  price, for the right quantity.
- **A stop is not a guarantee of price.** Once triggered it is a market
  order. On a gap it fills where the market is, which can be well past the
  level — the loss was *sized* at 1R, not capped at it.
- **Supervision needs the program running.** The stop at the broker protects
  you if Meridian dies. The ladder, a software-watched target, and the
  square-off before the close do not happen while it is down — the broker's
  own square-off is then the backstop. On restart it picks up where the
  broker says things stand.

### After the trade

Hit **Log to journal** on the trade panel when you take a signal. When it
closes, mark it **win** or **loss**. This is the only mechanism that will ever
tell you whether the confidence number means anything for *your* market and
*your* timeframe — the Journal tab breaks accuracy down by confidence bucket.

If 80%-confidence signals don't win more often than 50%-confidence ones after
40+ trades, the score is not calibrated for what you're trading, and you should
believe your journal over the number.

### Managing the trade — the exit alert

Record the position in the **Trade** tab the moment you are filled: side, entry,
stop, optional target, lots. The panel shows risk in ticks, cash at risk and
R:R as you type, and refuses a stop on the wrong side of the entry.

Once a position is tracked, Meridian runs **two loops at different speeds**:

- **Every tick (~1s)** — pure price arithmetic against the levels you set. Live
  R, unrealised pips, distance to stop and target. No analysis, no API call, so
  it can run continuously.
- **Every bar close, or on the timer** — the full structural pass: has the trend
  broken, is momentum fading, should the stop trail, is the thesis still alive.

The split matters. A stop being hit is arithmetic; waiting for the next
structural pass to notice would be exactly the wrong moment to be slow.

When price reaches your stop or target, a **full-screen alert** fires
immediately — a red or green wash across the whole window, a five-second
countdown, and a beep. Terminal alerts stay until you acknowledge them.
Proximity warnings (price approaching a level) clear themselves after fourteen
seconds so they never become wallpaper.

**Act in your broker first, then acknowledge here.** Meridian cannot see your
broker and cannot close anything. If your stop was resting in the order book
you are already out; if it was a mental stop, close it now and do not re-enter
to "get it back".

The same applies to adjusting a stop: moving it in this panel updates the
tracker only. Move it in Kite or CoinDCX too, or the protection is imaginary.
Stops may only ever be **tightened** through the API — widening a stop on a
losing trade is the single most reliable way to turn a small loss into a large
one, so it is not permitted.

### The protection ladder

A trade starts fully exposed: a stop-out costs 1R. As it runs in your favour
the stop climbs a ladder behind it, and the card at the top of the rail answers
one question before anything else — **if this reverses right now, what does it
cost me?**

| Rung | Arms at | Stop moves to | State |
|---|---|---|---|
| Breakeven | +1.0R | entry + round-trip cost | **SAFE** — the trade is free |
| Lock ½R | +1.6R | +0.5R | **LOCKED** |
| Lock 1R | +2.5R | +1.0R | **LOCKED** |
| Lock 2R | +4.0R | +2.0R | **LOCKED** |

With **auto-protect** on (Settings → Automatic protection, on by default) each
rung is taken for you the moment it arms, on the one-second tier. With it off,
each rung is offered as a one-click action instead.

Three details in there are doing real work:

**The gap between "arms at" and "moves to" is deliberate.** At +1.6R the stop
goes to +0.5R, not to +1.6R. A stop parked directly under the price gets hit by
ordinary noise, and a trade stopped at +1.5R that was going to +4R is a worse
outcome than one stopped at +0.5R.

**Breakeven is not the entry price.** You paid the spread getting in and pay it
again getting out, so a stop at exactly your entry books a small *loss*. The
breakeven rung sits at entry plus the measured round-trip cost. On a CoinDCX
INR pair that gap is substantial — see §2.

**Rungs are gated on ATR as well as R.** Price has to be a full ATR clear of the
stop a rung would set before it arms. Moving to breakeven too early does not
make a trade safer; it converts winners into scratches, and over a year that
costs more than the losses it avoids.

The card also tracks the **peak** and the **worst** point of the trade from
ticks rather than bar closes. A position that ran to -0.9R before working was
nearly a loss, and the journal should record that rather than only the outcome.

**On an Angel One live position the ladder moves the real order.** With
auto-protect on, each rung modifies the stop resting at the broker — the
trigger price, and for a stop-limit the limit with it. A move is only ever
tighter and never through the market, and if Angel One refuses the change the
previous stop simply stays where it was.

### ⚠ The one thing that can go badly wrong

**For a position you placed by hand — in Kite, in CoinDCX, or in Angel One's
own app — Meridian moves its own tracked stop. It cannot move yours.** There
is no order connection to Kite or CoinDCX, deliberately, and a position
Meridian did not open is not one it will touch.

When the tracked stop and your broker's stop drift apart, the card turns red
and says so, because a screen reading **SAFE** while the original stop is still
resting in the market is more dangerous than no tool at all — you have stopped
watching a trade that is still fully exposed. Confirm each move with the
"I've moved it in my broker" button, and only after you actually have.

**A position opened with Execute Live is the exception**: its stop is a real
order at Angel One, placed and moved by Meridian (§3a). The equivalent
failure there is narrower — Meridian not running — and the stop already at
the broker is what covers it.

### Account limits — the circuit breaker

Managing one trade stops it becoming a disaster. It is not what kills accounts;
a *sequence* is — three losses, then a bigger position to win it back, then a
bigger one still. The bar above the trade panel tracks four limits:

| Limit | Default | Why |
|---|---|---|
| Daily loss | 3R | The trades taken after a bad day are the worst of the month |
| Losing streak | 3 in a row | Usually means the regime changed, not that you were unlucky |
| Open risk | 3R total | Correlated positions stop out together — treat them as one trade |
| Trades per day | 6 | Frequency is the strongest predictor of a losing month in every published retail dataset, SEBI's included |

On a breach the trade panel stops pre-filling entries and explains why. It
cannot stop you from trading and does not pretend to — it refuses to help,
which is the only honest lever a tool has here. Limits reset at 00:00 UTC, or
when a winner breaks the streak.

Open risk is measured **from your entry, clamped at zero**: a position with its
stop at breakeven contributes nothing, because it cannot lose. Unrealised
profit is not capital and is never counted as risk.

**Live orders are different: there the limits are not advice.** The bar above
is computed from the positions you log and from the demo account, and it can
only decline to help. Real orders sent through Angel One are checked against
their own ledger — what is actually open at the broker, what was traded
today, what today has cost after charges — and an order outside a limit is
refused before it is sent (§2a has the list). That day begins at midnight
IST, not UTC.

---

## 4. Position sizing

The suggested lot size comes from exactly two inputs you control in
Settings → Risk, plus the distance to the stop:

```
risk amount = account balance × risk %
lots        = risk amount ÷ (stop distance in ticks × tick value)
```

**Worked example.** ₹2,00,000 account, 1% risk, USDINR long:

- Risk budget: ₹2,000
- Entry 83.2000, stop 83.1500 → 50 paise = **20 ticks** (tick = 0.0025)
- Cost per lot if stopped: 20 × ₹2.50 = **₹50**
- Lots: 2,000 ÷ 50 = **40 lots**

Now sanity-check that against margin. 40 lots of USDINR is a notional of
40,000 USD ≈ ₹33 lakh; NRML margin on that is far more than a ₹2 lakh account
has. **When the risk-based size exceeds what your margin allows, margin wins** —
trade the smaller number, or don't trade it.

That tension is the single most common way retail accounts die: the position
that "only risks 1%" is one the account cannot actually carry.

**For an Angel One live order, both halves of that are enforced for you.**
The quantity is recomputed on the server from the risk budget and the
broker's real lot size, the budget is capped at a share of the cash the
account actually holds, and the order is checked against the margin Angel One
says it needs (§3a). The lot figure on the trade panel is still arithmetic on
the numbers in Settings; the confirmation dialog shows the real one.

### On risk percentage

At 2% per trade, ten losses in a row costs ~18% of the account. Ten-loss
streaks happen to strategies that win 55% of the time — roughly once every few
hundred trades. Professionals sit at 1–2% for this reason. Above 3%, an
ordinary drawdown becomes an account-ending one. The Risk tab shows you the
ten-loss number live as you move the slider; look at it.

---

## 5. What it costs to trade

This is where most retail edges die, and it is arithmetic, not opinion.

Zerodha charges roughly **₹20 or 0.03% per executed order, whichever is lower**
on intraday equity, F&O, currency and commodity. On top: exchange transaction
charges, SEBI fees, GST at 18% on (brokerage + transaction charges), stamp duty,
and STT/CTT where applicable. Run your exact numbers through the
[Zerodha brokerage calculator](https://zerodha.com/brokerage-calculator) — rates
change and I am not going to quote stale ones at you.

**The number that matters.** A round trip on a small currency-futures position
costs on the order of ₹50–70 all-in. At ₹2.50 per tick per lot, that is
**roughly 20 ticks on a single lot** before you see a rupee. So:

- A 15-tick target on 1 lot is a losing trade even when it wins.
- Costs scale per *order*, not per lot, so they matter far more on small
  positions than large ones.
- The more you trade, the more of your edge you hand over. Six good trades a
  month beats sixty marginal ones, and it isn't close.

Meridian shows spread and ATR on the trade panel and flags when the spread is
large relative to your stop. The trade panel does **not** add brokerage to a
single recommendation — put that in yourself before deciding a setup is worth
taking.

**The demo account, the backtest and the live limits do charge it, from one
model** (`backend/app/costs.py`), so none of them can flatter the others. The
Angel One confirmation dialog shows the estimated charges beside the loss at
the stop, and a closed live trade reports its profit or loss both before and
after them:

| Segment | Per side | Round trip |
|---|---|---|
| NSE equity, intraday | 0.053% | **≈ 0.106%** of turnover |
| Index futures | 0.049% | ≈ 0.097% |
| Commodity futures | 0.044% | ≈ 0.088% |
| Currency futures | 0.036% | ≈ 0.072% |
| Crypto, CoinDCX **spot** — the default for these books | 0.236% | **≈ 0.47%** |
| Crypto, CoinDCX USDT **perpetual** — only when a test asks for it | 0.059% taken, 0.024% resting | ≈ 0.08% – 0.12%, plus 0.01% funding per 8 hours held |

Plus the spread for each side that crosses it, and slippage on a stop. Those
figures are brokerage at 0.03% (the flat ₹20 cap is ignored, which overstates
a large order), STT/CTT, exchange and SEBI charges, stamp duty, and GST on the
brokerage and exchange charges. They are averages to keep current, not a
contract note — check them against yours.

Put that against a stop. A ₹2,500 share with a ₹10 stop: the round trip costs
about ₹2.65 a share, **over a quarter of what the trade risks**, before it has
moved. That single ratio — cost over stop distance — decides more intraday
strategies than any indicator, and it is why tight stops on short timeframes
so rarely survive a backtest that charges honestly.

---

## 6. Tax (India)

- **F&O (currency, index, commodity)** — non-speculative business income, taxed
  at your slab rate. Losses carry forward 8 years and can offset other business
  income. A tax audit may apply above certain turnover thresholds.
- **Intraday equity** — speculative business income; losses only offset
  speculative gains, carry forward 4 years.
- **Crypto** — flat 30% on gains, 1% TDS per sale, **no loss set-off against
  anything**, not even other crypto losses.

Keep the journal. It doubles as your trade register at filing time. Talk to a
CA before your first filing season as a trader — the audit rules are genuinely
fiddly.

---

## 7. So — will this make money?

The truthful answer: **I don't know, and neither does anyone who tells you they
do.** What I can tell you is what has to be true, and how to find out cheaply.

Meridian gives you a repeatable process, sized risk, and a record. Those are
necessary. They are not sufficient. Whether there's an edge depends on the
instrument, the timeframe, the conditions, and mostly on whether you follow the
process when it's inconvenient.

Here is the sequence that finds out without paying for the lesson:

**Stage 0 — Ask the history first. It takes a few minutes.** Backtest tab →
**Walk-forward test**, on the symbol and timeframe you mean to trade. This is
not the kind of backtest that tells you what you want to hear:

- It replays the **live engine itself** — the same two functions that
  produce the call on the trade panel, run once per historical bar — not a
  faster imitation of it. A test in the repository checks that the replay
  and the live engine reach the same verdict on the same bar.
- It trades each signal the way a live order trades: a limit at the signal
  price that is *missed* if the market does not come back to it, a stop that
  fills at the open when the market gaps through it, a stop that wins when
  one bar touches both stop and target, the protection ladder, and — on
  Indian instruments — a square-off at the end of every session.
- It charges every cost in §5.
- It fits its two thresholds (minimum confidence, minimum reward:risk) on
  one stretch of history and trades them on the **next**, four times over,
  and reports only the stretches it did not fit on. A stretch is only traded
  at all if something made money on the one before it.
- The verdict needs at least 30 of those out-of-sample trades and a 90%
  interval on expectancy that sits wholly above zero. A positive average on
  a handful of trades is what luck looks like.
- **The bar rises with every market you test.** Run it on twenty symbols and
  one will pass at the 5% level by chance; the gate counts every test from
  the last 30 days and corrects for them all.

What it cannot replay, and says so in every result: Claude's verdict (the
deterministic analyst stands in, as it does live whenever Claude is off), your
own drawings, and the timeframes below the chart's in the multi-timeframe
read.

**What it said when it was run — 12 October 2026.** Four crypto markets on
CoinDCX history, 4,000 bars out of sample each (167 days of hourly bars, 42
days of 15-minute bars), every cost charged. First the engine — the thing
Execute Live would trade:

| Market | Trades | Won | Expectancy | 90% interval | Verdict |
|---|---|---|---|---|---|
| BTCUSD 1h | 112 | 31% | **−0.11R** | −0.31R … +0.10R | No measured edge |
| ETHUSD 1h | 51 | 39% | **+0.22R** | −0.14R … +0.58R | No measured edge — the interval spans zero |
| SOLUSD 1h | 86 | 29% | **−0.14R** | −0.36R … +0.10R | No measured edge |
| BTCUSD 15m | 9 | 11% | −0.64R | — | Too few trades to say anything |

Then two things to read that against:

- **Entries chosen at random**, same bars, same exits, same costs, about 800
  trades a market: −0.16R, −0.10R, −0.18R and −0.27R. That is roughly what
  the costs alone come to — and on Bitcoin and Solana the engine's result is
  not distinguishable from it.
- **The five named plays** (Plays tab), walk-forward on the same four
  markets: twenty tests, none passed. The best was +0.09R on 65 trades, with
  an interval from −0.36R to +0.59R.

Twenty-four tests, no measured edge in any. One positive average out of
four, on 51 trades, with an interval that reaches well below zero, is what
luck looks like — and it is exactly the result the multiple-test correction
exists to stop you trading.

Read it for what it is. It is six months of four crypto pairs with the
deterministic analyst — not Indian equities or F&O, which need a broker
connection for their history and were not tested, and not Claude's verdicts.
It does not say nothing here can work. It says **nothing here has been shown
to**, and that on these markets the signals as they stand do not beat their
own costs. The live gate was built to refuse on that answer, and it does.

**A correction to that table, made the same day.** Those runs charged 0.05%
a side — the futures fee — and simulated short sales on books that cannot be
shorted. They reproduce exactly from frozen data, so they are a true account
of what was measured; it was the wrong venue. Charged as the spot books they
are, long only:

| Market | As above | As the spot book |
|---|---|---|
| BTCUSD 1h | −0.11R on 112 trades | **−0.73R on 16** — interval wholly below zero |
| ETHUSD 1h | +0.22R on 51 | **−0.48R on 35** — interval wholly below zero |
| SOLUSD 1h | −0.14R on 86 | **−0.11R on 31** |

The one positive number is gone. And the confidence figure beside every
signal turned out to say nothing: across 778 setups, about three in ten won
whether it read 50 or 70.

**A wider search, also 12 October 2026.** Five simpler ideas — trend
following, momentum, fading a range, buying dips, breaking out of a squeeze
— on nine coins with up to eight years of hourly history, 198 configurations
declared before any was run, and the last two years of history set aside
unseen. Everything on hourly bars lost to its costs. Nothing passed. One
idea came close: a slow trend-follower on 4-hour bars (+0.25R to +0.37R on
600 to 1,000 trades, profitable on eight coins of nine), which cleared every
bar but the one that discounts a result for the number of things tried. It
was rejected, the unseen history was left unseen, and it is now being
watched on new bars instead. The whole account is in
[docs/research/REPORT.md](docs/research/REPORT.md).

Run it yourself before believing either this table or a better one: Backtest
tab → **Walk-forward test**. The first pass over a market takes three or four
minutes, because it really does run the engine once per bar. It now fetches
its own snapshot of history and records the run as an experiment, so the
same test can be repeated on the same bars; `python -m app.research.run
experiments` lists them.

If the verdict is "no measured edge", stop here. That is the cheapest this
lesson will ever be, and changing the instrument or the timeframe and testing
again is free — as long as you remember that every extra test raises the bar.
Execute Live is refused without a passing test; that is deliberate.

**Stage 1 — Paper trade, 4 to 6 weeks.** No real money. Take every signal the
process says to take, log every one, mark every outcome. You need **40+ trades**
before the numbers mean anything; below that you are reading noise. The point
isn't the P&L, it's finding out whether *you* can follow a process.

**Stage 2 — Read your journal, not your feelings.** Look at expectancy in R,
profit factor, max losing streak, and accuracy by confidence bucket. If
expectancy is negative, do not proceed to real money and do not increase size
to "make it back". Change the instrument, the timeframe, or the filters, and
run stage 1 again.

**Stage 3 — Minimum size, 2 to 3 months.** One lot. The goal is not profit; it
is finding out what changes when the money is real. It always changes
something. If the demo account has taken ten or more trades on that market and
lost, the gate refuses the live order whatever the backtest said: when the two
disagree, the backtest is the one that is wrong.

**Stage 4 — Scale slowly.** Increase risk % only after a profitable quarter at
the current size, and only in small steps. Cut size immediately during a
drawdown.

**Things that will lose you money regardless of how good the analysis is:**
overtrading, moving a stop away from price, revenge trading after a loss,
sizing up to recover a drawdown, trading instruments you don't understand,
and taking a signal you wouldn't have taken if the AI had said WAIT.

The tool cannot fix any of those. It can only make them visible in the journal
afterwards — which, if you actually read it, is worth more than the signals.

---

## 8. What Meridian cannot do

Stated plainly so there's no confusion:

- It **cannot place, modify, or cancel an order on Kite or CoinDCX.** No
  broker order API is wired to either, deliberately.
- **On Angel One it trades only what you confirm, one order at a time, and
  one position per instrument.** It places the entry you approved and then
  manages that position — stop, target, ladder, square-off, flatten — but it
  never opens a position on its own: there is no live auto-trader, and the
  demo account's auto-trading never touches the broker. It trades **intraday only** and cannot carry a
  position overnight; it cannot trade options, currency or commodity
  futures (both blocked until a number each depends on is verified, §2a) or
  crypto; and none of it has been run against a real account yet (§3a).
- It **cannot predict price.** It weighs evidence and reports a confidence
  *score*, capped at 72. That number is a weighted count of indicators that
  agree. It is not a probability, and it has been measured: over 778 setups
  on three markets, about 30% won whether the score was under 50 or over 70.
  Do not size a trade by it.
- It **has no news, no fundamentals, no order book, no positioning data.** It
  sees candles and what you drew.
- Its **backtest is not a forecast.** It replays the live engine over the
  history a data provider gives you — a few thousand bars at most — with
  pessimistic fills and real costs. A passing walk-forward says the edge was
  there, recently, after costs; it does not say it will be there tomorrow,
  which is why the evidence expires after 30 days.
- It **cannot make a losing strategy win.** Stops, limits and a kill switch
  bound what a bad idea can cost. They do not turn it into a good one.
- **Outside an Angel One live order, it does not know your margin, your open
  positions, or your account.** The lot size on the trade panel is arithmetic
  on the numbers you typed into Settings.

---

*Trading currency futures, index derivatives, commodities and crypto involves
substantial risk of loss. This software is an analysis tool for research and
education. It is not investment advice, it is not a recommendation to trade,
and nothing in it is a guarantee of any outcome.*
