# Meridian — AI Forex Trading Analysis Terminal

An institutional-grade **market analysis** workstation. A live candlestick chart is always on screen,
you draw your own structure on it (trendlines, S/R, zones, fibs, channels), and the platform
continuously fuses that with indicators, market structure, Smart Money Concepts, multi-timeframe
trend, volume and volatility — then asks Claude to reason over the evidence and return a
**BUY / SELL / WAIT** recommendation with confidence, entry, stop, targets and explicit reasoning.

> **By default this is not a trading bot.** Every output is analysis and probability, never a
> guarantee. When evidence conflicts, the analyst is required to say `WAIT`.

> **Two exceptions, both opt-in and both clearly labelled on screen.** A built-in demo account
> can auto-trade the platform's own signals on simulated money — useful for finding out whether
> the confidence score means anything before risking a rupee. And Angel One can be connected for
> **real** order execution, but it starts in `PAPER`, always returns to `PAPER` after a restart,
> and a live order only ever goes out after you explicitly confirm the exact trade on screen.
> Nothing executes silently.

> **Trading with it?** Read **[TRADING.md](TRADING.md)** — what you can legally trade from
> India, how to connect Zerodha or Angel One, how a signal becomes an order (manual or live),
> what it costs, and the honest path from paper trading to real size.

---

## What it does

| Layer | Capability |
| --- | --- |
| **Live data** | 154 instruments across 1m → 1W: 17 crypto pairs (CoinDCX, keyless), 122 NSE F&O stocks, NIFTY/BANKNIFTY, currency futures, MCX commodities and spot FX |
| **Drawing** | 15 drawing tools rendered on a Konva layer perfectly registered to the chart's price/time axes |
| **Drawing intelligence** | Every drawing is stored with price/time/angle/length and scored for touches, reactions, breakouts, fake breakouts, volume behaviour |
| **Indicators** | EMA, SMA, VWAP, ATR, RSI, MACD, ADX/DI, Stochastic RSI, Ichimoku, SuperTrend, Bollinger Bands, Volume Profile (POC/VAH/VAL), OBV, MFI, Pivot Points |
| **Market structure** | Swing highs/lows, HH/HL/LH/LL, BOS, CHoCH, liquidity sweeps, FVGs, order blocks, breaker blocks, mitigation blocks, equal highs/lows, premium/discount, supply/demand |
| **Patterns** | Triangles, wedges, flags, pennants, channels, H&S + inverse, double top/bottom, cup & handle, rectangles + **56 candlestick patterns** |
| **MTF** | 1m/5m/15m/1H/4H/D fused into a single alignment score with a conflict report |
| **Screener** | One click ("Best trades" in the top bar) scans every instrument and returns only the ones with a live BUY or SELL signal, sorted by confidence — instead of opening each chart by hand. Filtered to what's actually tradable on CoinDCX or Angel One (spot FX/metals excluded — analysis-only), and every row is tagged with the real trading name for that app (CoinDCX's own pair name, or Angel One's live-resolved contract — `NIFTY25AUG26FUT`, not a guess). Same rule engine as a normal analysis, minus Claude and pattern detection, so ~30 symbols come back in a few seconds (first scan of a session is slower — Angel One's contract list has to download once, then it's cached) |
| **Strategies** | Five named plays scored side by side — trend continuation, mean reversion, breakout, order-block retest, momentum — each showing every precondition met and missing |
| **Scenario paths** | Each engaged strategy draws its thesis forward on the chart, with the invalidation leg alongside it |
| **Futures & options** | Any call expressed as a listed contract: futures cash risk, or an ITM/ATM/OTM option ladder with premium, delta, theta as %/day, break-even and the stop converted into premium |
| **Risk management** | A protection ladder that ratchets the stop to breakeven then into profit as a trade runs, plus account-level guardrails — daily loss, losing streak, open risk, trade count |
| **Demo auto-trading** | A $10,000 simulated account that can act on the platform's own BUY/SELL signals automatically — real spreads and commission modelled, full trade history, no real money ever involved |
| **Live execution (Angel One)** | Optional, off by default. Submits a real order to your Angel One account only after you explicitly confirm the exact trade shown on screen — see [TRADING.md §2a/§3a](TRADING.md) for the full flow and its current limits |
| **AI** | Claude receives a structured evidence brief and returns strict JSON: decision, confidence, entry, SL, TP, R:R, probability, bull/bear points, summary |
| **Overlays** | The AI's levels are drawn back onto the chart — entry, SL, TP, profit/risk zones, order blocks, FVGs, liquidity pools, projected path arrows |
| **Journal** | Every recommendation persisted with full evidence snapshot, then marked win/loss and scored |
| **Backtest** | Replay history through the same engine: win rate, profit factor, max drawdown, avg R, Sharpe, expectancy |

---

## Running it

### Requirements

| Need | Version | Check with |
| --- | --- | --- |
| Python | 3.9 or newer | `python3 --version` |
| Node.js | 18 or newer | `node --version` |
| make | any | `make --version` |

Nothing else. No database server, no Redis, no API key — see [Zero-config by
design](#zero-config-by-design).

### First time — two commands

```bash
cd /Users/aashishs/Documents/trading

# 1. Creates backend/.venv, installs Python deps, runs npm install.
#    Takes a few minutes. Only ever needed once.
make setup

# 2. Starts the API and the UI together.
make dev
```

That is the whole install. `make dev` prints the UI address in its own banner:

```
  VITE v5.4.21  ready in 249 ms
  ➜  Local:   http://localhost:5173/     ← open this
```

> **The port is not always 5173.** If another project already holds it, Vite
> silently moves to 5174, 5175 and so on. **Read the URL in the banner** rather
> than assuming — opening a stale 5173 that belongs to a different project is
> the most common "it's broken" that isn't.

| What | Where |
| --- | --- |
| Trading terminal | the `Local:` URL from the banner |
| API docs (Swagger) | http://localhost:8000/docs |
| Health check | http://localhost:8000/api/health |

The API port is fixed at 8000 and does not fall forward — if it is taken, see
[Troubleshooting](#troubleshooting).

### Every time after that

```bash
make dev
```

Then open the URL from the banner. You should see live BTC candles moving
within a few seconds — that is CoinDCX, streaming without any credentials.

### Stopping it

Press **Ctrl-C** once in the terminal running `make dev`. It stops both servers.

If a server was started some other way and is still holding a port:

```bash
pkill -f "uvicorn app.main"    # the API
pkill -f vite                  # the UI
```

Confirm both are down:

```bash
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8000/api/health
# 000 = stopped.  200 = still running.
```

### Running the halves separately

Useful when you want the API logs in one terminal and the UI in another.

```bash
make backend     # API only, on :8000, with --reload for auto-restart on edits
make frontend    # UI only — it proxies /api and /ws to :8000
```

The UI needs the API. Started alone it shows **"Cannot reach the analysis
engine"** with a Retry button.

### As a desktop app

```bash
make desktop     # Electron shell wrapping the same UI
```

### Other commands

```bash
make test        # 122 backend tests (pytest)
make lint        # frontend typecheck (tsc --noEmit)
make clean       # remove venv, node_modules, build output
```

> `make clean` also deletes `backend/data/meridian.db`, which holds your
> journal, trade history and account-limit state. Your settings and API keys
> live separately in `backend/data/settings.json` and survive it.

### First things to do in the app

1. **Nothing** — it already works. BTC and 16 other crypto pairs are live from
   CoinDCX with no setup, and the deterministic analyst produces verdicts
   without an API key.
2. **Add your Claude key** (optional): gear icon → *Analyst* → paste → *Test
   connection*. Applies immediately, no restart.
3. **Set your account balance** (recommended): gear icon → *Risk*. Every lot
   size and every account guardrail is computed from it, and the defaults are
   placeholders.

### Troubleshooting

| Symptom | Cause | Fix |
| --- | --- | --- |
| "Cannot reach the analysis engine" | API not running, or died | Check the `make dev` terminal for a traceback; `make backend` alone shows its logs clearly |
| Browser shows a *different* app | You opened 5173 but Vite moved to 5174 | Use the URL from the banner |
| `Address already in use` on 8000 | An old API is still alive | `pkill -f "uvicorn app.main"`, then `make dev` |
| `make: command not found` | No build tools | macOS: `xcode-select --install` |
| `make setup` fails on `python3 -m venv` | Python missing or too old | Install Python 3.9+ and re-run |
| Chart shows **SIM**, not **LIVE** | That symbol has no keyless source | Expected for NSE/MCX symbols — they need Zerodha (see [TRADING.md](TRADING.md)). Crypto should always read LIVE |
| Everything is stale after a code change | The API does not hot-reload under `make dev` | Ctrl-C and `make dev` again, or use `make backend`, which does reload |

### Everything is configured in the app

Open the **gear icon** in the top bar. No env editing, no restarts:

| Tab | What you set |
| --- | --- |
| **Analyst** | Anthropic API key (with a live *Test connection*), model, effort, re-analysis interval |
| **Market data** | Which provider serves prices, plus a per-symbol LIVE / SIM table |
| **Zerodha** | Kite Connect key + secret, and the daily login that mints an access token |
| **Risk** | Account balance, currency, risk-per-trade — the inputs behind every lot size |
| **Automatic protection** | Whether the stop climbs the protection ladder on its own as a trade runs |

Settings persist to `backend/data/settings.json` (0600, gitignored) and apply immediately.

**Angel One is the one exception, deliberately.** Its credentials (`ANGEL_API_KEY`,
`ANGEL_CLIENT_CODE`, `ANGEL_PASSWORD`, `ANGEL_TOTP_SECRET`) are boot-time environment
variables only — never accepted through Settings, never returned by any API response, not
even masked, because they can place a real order. What *is* in the app: the **DEMO / LIVE**
indicator and Angel One connection status in the top bar, and the confirmation dialog that
gates every live order. See [TRADING.md §2a](TRADING.md) for setup.

### Zero-config by design

Nothing external is required to boot, and **17 crypto pairs stream genuinely live from
CoinDCX with no credentials at all** — so a fresh clone shows real, moving prices and
analysis that changes with them.

| Dependency | Present | Absent |
| --- | --- | --- |
| Anthropic API key | Claude Opus 5 analyst | Deterministic rule-based analyst (same JSON contract) |
| CoinDCX / Binance keys | — | Not used. Market data is public; Meridian places no orders |
| Kite Connect | Live NSE-CDS / NFO / MCX data | Those symbols run simulated |
| Angel One credentials | LIVE mode can be armed (still requires manual confirmation per trade) | Trading mode stays `PAPER`; demo auto-trading works fully either way |
| PostgreSQL | Async Postgres via asyncpg | SQLite at `backend/data/meridian.db` |
| Redis | Shared cache / pub-sub | In-process LRU cache |

### Instrument coverage

| Group | Symbols | Live source |
| --- | --- | --- |
| Crypto (USDT) | BTC, ETH, SOL, XRP, DOGE, BNB, ADA, AVAX, LINK, DOT, LTC, TRX | CoinDCX — keyless |
| Crypto (INR) | BTCINRC, ETHINRC, SOLINRC, XRPINRC, DOGEINRC | CoinDCX — keyless (wide spreads, see TRADING.md) |
| NSE F&O stocks | 122 names — RELIANCE, HDFCBANK, INFY, TCS, … | Zerodha Kite Connect |
| NSE currency futures | USDINR, EURINR, GBPINR, JPYINR | Zerodha Kite Connect |
| NSE indices | NIFTY, BANKNIFTY | Zerodha Kite Connect |
| MCX commodities | MCXGOLD, MCXSILVER, MCXCRUDE | Zerodha Kite Connect |
| Spot FX & gold | EURUSD, GBPUSD, USDJPY, AUDUSD, USDCAD, XAUUSD | Twelve Data (analysis only for Indian residents — see TRADING.md) |

---

## Architecture

```
┌────────────────────────── frontend (React + TS + Vite) ──────────────────────────┐
│  lightweight-charts ── candles/indicators      Konva layer ── user drawings       │
│           ▲                                            │                          │
│           └── AI overlay renderer (entry/SL/TP/OB/FVG/liquidity/path)             │
│  Zustand store  ·  WS client (candles, ticks, analysis)  ·  REST client           │
└───────────────────────────────────┬──────────────────────────────────────────────┘
                                    │  WebSocket /ws/stream  +  REST /api/*
┌───────────────────────────────────┴──────────────────────────────────────────────┐
│                              backend (FastAPI, async)                             │
│                                                                                   │
│  providers ─► feed (tick→candle aggregation, all TFs) ─► candle store (Redis/mem) │
│                                    │                                              │
│                                    ▼                                              │
│  analysis/engine.py  ── indicators · structure · patterns · candlesticks          │
│                      ── volume profile · drawings · MTF · confidence              │
│                                    │                                              │
│                     MarketContext (structured evidence)                           │
│                                    ▼                                              │
│  ai/narrative.py ─► ai/prompts.py ─► ai/claude_client.py ─► strict JSON verdict   │
│                                    │                    └─► ai/fallback.py        │
│                                    ▼                                              │
│  overlays/builder.py ─► journal (Postgres/SQLite) ─► backtest/engine.py           │
└───────────────────────────────────────────────────────────────────────────────────┘
```

### Confidence model

The final 0–100 score is a weighted fusion, not a model opinion:

| Component | Weight |
| --- | --- |
| Trend (MTF alignment, EMA stack, ADX, SuperTrend, Ichimoku) | 25% |
| Momentum (RSI, MACD, StochRSI, ROC) | 20% |
| Volume (relative volume, OBV slope, MFI, VP position) | 15% |
| Market structure (BOS/CHoCH, OB/FVG confluence, premium-discount) | 20% |
| Pattern (chart + candlestick pattern strength) | 10% |
| Claude reasoning | 10% |

Each component is computed independently and reported with its own sub-score, so the UI can show
*why* the number is what it is. `WAIT` is emitted whenever directional components disagree beyond
the conflict threshold — see `backend/app/analysis/confidence.py`.

---

## Layout

```
backend/app
├── main.py               FastAPI app, lifespan, feed bootstrap
├── config.py             settings (env-driven, safe defaults)
├── instruments.py        instrument reference data (pip size, lots, venues)
├── stocks.py             the NSE F&O stock universe
├── data/                 providers (coindcx, binance, zerodha, twelvedata,
│                         synthetic), tick→candle feed, candle store
├── analysis/             indicators, structure, patterns, candlesticks,
│                         volume profile, drawings, mtf, confidence, engine,
│                         strategies, derivatives, live tier, manage,
│                         risk (protection ladder), guardrails
├── ai/                   prompts, narrative brief, Claude client, fallback analyst
├── overlays/             analysis → chart overlay primitives
├── backtest/             replay engine + performance metrics
├── broker/               live-execution layer
│   ├── angelone/         auth (TOTP session), client (SmartAPI wrapper),
│   │                     instruments (scrip-master resolution), execution
│   │                     (recommendation → order orchestration)
│   ├── risk_gate.py      PreTradeRiskGate — mode/confirmation/connection/
│   │                     kill-switch/instrument/quantity/funds/risk checks
│   └── trading_mode.py   PAPER/LIVE flag + kill switch, restart-safe
├── services/              paper (demo auto-trade account), journal, positions
├── db/                   SQLAlchemy models + async session
├── routers/              market, drawings, analysis, journal, backtest,
│                         positions, paper, broker_angel, execution, ws
└── tests/                pytest suite — analytical core, paper trading,
                          Angel One safety/instrument resolution, execution

frontend/src
├── components/chart/     ChartCanvas, DrawingLayer, AIOverlay, Toolbar, Replay
├── components/panels/    Trade (+ ExecuteLiveButton), Analysis, Strategies,
│                         Derivatives, MTF, Structure, Journal, Backtest,
│                         Protection, AccountRisk, Management
├── components/layout/    TopBar, AutoTradeBar (demo account), TradingModeControl
│                         (DEMO/LIVE + Angel One status), Screener (best trades now)
├── components/ExitAlert  full-screen stop/target alert on the 1s tier
├── components/PaperFillToast   screen flash + toast when a demo/live trade opens or closes
├── lib/theme.ts          dark/light palette + canvas re-theming
├── components/ui/        design-system primitives
├── store/                zustand slices (chart, drawings, analysis, journal)
└── lib/                  api, ws, geometry, types, formatting
```

---

## Testing

```bash
make test          # 190+ pytest cases across the analytical core and execution
make lint          # frontend typecheck (tsc --noEmit)
```

Covered: indicators, market structure, patterns, drawing analytics, confidence
fusion, backtest metrics, the five strategies, option pricing and expiry
arithmetic, the protection ladder, the account guardrails, demo auto-trading
(symbol/timeframe gating, R:R floor), and the Angel One live-execution path
— PAPER-mode isolation, the pre-trade risk gate's ten checks, instrument
resolution (including a regression test for a real bug: matching a
non-tradable index-quote row instead of an actual futures contract),
broker rejection, ambiguous/timeout handling, and duplicate-order
protection.

Tests run against a throwaway SQLite file in the system temp directory, never
`backend/data/meridian.db` — otherwise fabricated test trades would appear in
your real journal and account limits.

---

## Risk notice

Forex, metals and crypto trading carries substantial risk of loss. This software produces
probabilistic analysis for research and education and makes no claim of accuracy. By default it
does not place orders or manage capital. The one exception — Angel One live execution — is
off by default, requires explicit setup and per-trade confirmation, and is documented in full,
limitations included, in [TRADING.md](TRADING.md). Every recommendation includes its own
invalidation level precisely because the analysis can be wrong. Size positions accordingly.
