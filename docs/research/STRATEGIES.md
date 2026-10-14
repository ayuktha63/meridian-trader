# Strategies: what each one claims, and what happened when it was tested

Ten strategies are described here. Five are the plays the application has
always shown (the **Plays** tab); five are candidates written for the study
in [REPORT.md](REPORT.md). None of them is assumed to work. Each is stated
as a hypothesis that a result could contradict, and for each the result is
given.

**None has a demonstrated edge.** Skip to the last column of the two tables
if that is all you came for.

---

## The five plays the application shows

These come out of the live engine's own analysis pass
(`backend/app/analysis/strategies.py`). Each lists five or six weighted
conditions, scores the share of weight that holds, and **takes a side only
at 60% or more**; below that it reports what it is waiting for. In a
backtest the entry is a limit at the signal price that is missed if the next
bar does not trade through it, and every play gets the protection ladder.

| Play | What it claims | Should work when | Should fail when | Entry | Stop | Target |
|---|---|---|---|---|---|---|
| **Trend Continuation** | A trend that higher timeframes agree on resumes after a pullback | Persistent, orderly trends | The pullback is the reversal; the higher timeframes lag it | At the price when conditions hold | 0.4 ATR behind the nearest structural level, or 1.5 ATR if there is none | 2.2× the risk |
| **Mean Reversion** | In a range, a stretch from the mean snaps back | ADX low, price at a range extreme | A break of structure: the range is over and this is a knife-catch | At the price | 1.2 ATR | The mean (Bollinger mid, else VWAP), or 1.6 ATR if that is too close |
| **Range Breakout** | A close out of a compressed range on volume travels the range's height | After compression, with participation | No volume: a stop hunt that reverses | At the price | Just inside the range, or 1.5 ATR if that is more than 3 ATR away | 0.9× the range height |
| **Order Block Retest** | After a break of structure, price returns to where the move began and continues | A clean impulse leaving an untested zone | Price closes through the block | The edge of the nearest unmitigated block | 0.3 ATR beyond the block | The nearest resting liquidity, else 2.5× the risk |
| **Momentum Expansion** | Expanding MACD, rising ADX and volume mean the move has further to go | The early part of an impulse | The first stall — and thin volume | At the price | The SuperTrend line, else 1.2 ATR | 2× the risk |

**What these depend on that has never been shown to predict anything.** Every
one leans on concepts the code turns into exact rules — a "break of
structure" is a close beyond a confirmed swing, an "order block" is the last
opposing candle before an impulse — and those rules are causal: a swing is
only known once the bars after it exist, and the replay never hands a
decision a bar it could not have had (a test checks the replay against the
live engine on the same bar). Being computable without look-ahead is not the
same as being predictive. Whether a fair value gap, an order block or
agreement across timeframes carries information is exactly what the tests
below measure, in aggregate, and the answer so far is no.

### What happened

Walk-forward, 4,000 bars out of sample per market, on CoinDCX history, run
on 12 October 2026 *before* the cost model was corrected — so at 0.05% a
side, which flatters them (see [BASELINE.md](BASELINE.md), defect 3):

| Play | BTCUSD 1h | ETHUSD 1h | SOLUSD 1h | BTCUSD 15m | Verdict |
|---|---|---|---|---|---|
| Trend Continuation | −0.16R (39) | −0.10R (66) | −0.45R (32) | −0.64R (28) | No edge |
| Mean Reversion | — (0) | −0.38R (8) | +0.08R (27) | −0.76R (7) | No edge; rarely trades |
| Range Breakout | −0.17R (27) | −0.40R (51) | −0.11R (41) | +0.09R (65) | No edge |
| Order Block Retest | −1.03R (2) | −0.07R (41) | −0.44R (6) | −0.38R (6) | No edge; rarely trades |
| Momentum Expansion | −0.36R (40) | −0.01R (21) | +0.06R (71) | −0.34R (46) | No edge |

Expectancy per trade after costs, with the number of trades. Twenty tests,
none with an interval clear of zero; the three positive averages are the
size a no-edge strategy produces by chance. These were run against the live
feed and were not recorded as experiments — the registry did not exist yet.
They are quoted from the session that ran them and can be re-run with
`python -m app.research.run engine BTCUSD 1h --strategy breakout`.

The **engine's fused verdict** — what the trade panel shows and the only
thing a live order can follow — is reported in [REPORT.md](REPORT.md) §7.

---

## The five research candidates

Written for the study (`backend/app/research/strategies.py`). Simpler than
the plays on purpose: each is one idea with two or three parameters, so that
a result is about the idea. All enter at the next bar's open after a
decision at a close. None is used by the live engine, and none can be.

| Candidate | What it claims | Falsified by | Entry | Exit | Grid |
|---|---|---|---|---|---|
| `donchian_trend` | A close beyond the last N bars' extreme marks a move that continues further than it reverses | No net edge on unseen data, or no better than random entries with the same stops and holding times | Close above the highest high (below the lowest low) of the previous N bars | Stop k ATR; trail k ATR behind the best price; no target | N ∈ {20, 55, 100} × k ∈ {2, 3, 4} |
| `ts_momentum` | The sign of the return over a lookback predicts the next | Same | Lookback return turns positive (negative) | Leave when it is no longer on the trade's side; stop k ATR | lookback ∈ {24, 72, 168, 336} × k ∈ {3, 5} |
| `range_reversion` | Without a trend, a close outside the Bollinger band returns to the mean | No net edge; a win rate that does not pay for the stops | ADX(14) under a ceiling and a close outside the band | Target the band's mid-line; stop k ATR; out after 24 bars | width ∈ {2, 2.5} × k ∈ {1.5, 2.5} × ADX ∈ {20, 25} |
| `trend_pullback` | In an established trend, a sharp dip is bought back to the recent extreme | No net edge, or no better than random entries in the trend's direction | EMA50 above EMA200, price above both, RSI(3) under a threshold | Target the 20-bar extreme; stop k ATR; out after 48 bars | RSI ∈ {15, 25} × k ∈ {1.5, 2.5} |
| `vol_expansion` | A break out of an unusually narrow range starts a larger move | No net edge, or no better than the plain breakout it filters | Band width in its lowest share of 200 bars, then a close beyond the 20-bar extreme | Stop k ATR; trail k ATR | share ∈ {0.15, 0.30} × k ∈ {2, 3} |

Thirty-three combinations, on three timeframes, charged as two venues: 198
configurations, all declared before the first was run and all counted.

### What happened

Nine markets, research stretch only (to 30 June 2024), pooled. Net
expectancy per trade for the configuration each family chose there:

| Candidate | 1h, perpetual | 4h, perpetual | 1d, perpetual | 1h, spot | 4h, spot | 1d, spot |
|---|---|---|---|---|---|---|
| `donchian_trend` | −0.01R | **+0.25R** | +0.43R | −0.04R | **+0.37R** | +0.90R |
| `ts_momentum` | +0.05R | +0.53R | +2.20R | +0.08R | +0.91R | +2.82R |
| `range_reversion` | −0.13R | −0.10R | −0.23R | −0.29R | −0.14R | too few |
| `trend_pullback` | −0.07R | −0.00R | −0.03R | −0.18R | +0.03R | too few |
| `vol_expansion` | −0.05R | +0.06R | +0.20R | −0.15R | +0.11R | too few |

**Every one was rejected by the protocol, on the research stretch, and none
was taken further.** The reasons differ and are worth reading separately:

- **Everything on the hourly timeframe loses to its costs.** Fading the
  band, buying the dip and trading the breakout all have negative or
  negligible expectancy on 2,300 to 11,500 trades each. This agrees with the
  engine's own results, and it is the most robust finding in the whole study.
- **Mean reversion and pullback lose on every timeframe.** Not marginally:
  there is nothing there to refine.
- **Time-series momentum's large averages are one trade.** On daily bars its
  best single trade was +670R — four-fifths of everything it made — and 2021
  alone is nine-tenths of the result. Taken as weekly totals its Sharpe is
  0.07. That is a lottery ticket that paid once.
- **Trend-following on 4-hour bars is the near miss.** +0.25R to +0.37R on
  588 to 1,021 trades, an interval well clear of zero, profitable on eight
  of nine markets and on seven to eight of its nine settings, positive in
  five of seven calendar years on the spot book and in all seven on the
  perpetual, no single trade more than 8% of the profit — and none of 200
  versions with its entries moved to random bars did as well. It failed one test: after deflating for all 198 configurations as
  if each were an independent try, its Sharpe clears the bar with
  probability 0.87 to 0.91, and the protocol asks for 0.95.

What that last result does and does not mean, and what has been done about
it, is in [REPORT.md](REPORT.md) §6.
