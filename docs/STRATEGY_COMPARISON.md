# Strategy comparison

**Evidence:** `NSE_15M_RESEARCH_RESULTS.md` (run `exp-20261013-070740-268fc6`)
and the baseline it quotes. Same data window, costs, account and exits for
every row; the existing engine row is a different session's offline run on 13
stocks and is not strictly comparable.

| Strategy | Trades | Gross R | Net R | Verdict |
|---|---|---|---|---|
| No trade (baseline) | 0 | 0 | 0 | beats all of the below |
| `supertrend15` | 700 | +0.010 | −0.297 | REJECTED |
| `gap_trend15` | 328 | +0.134 | −0.309 | REJECTED |
| `orb15` | 1,842 | +0.008 | −0.336 | REJECTED |
| `trend15_vol` | 578 | +0.062 | −0.344 | REJECTED |
| `trend15` | 1,041 | +0.048 | −0.391 | REJECTED |
| `rsi_momentum15` | 2,646 | +0.047 | −0.394 | REJECTED |
| `channel15` | 1,765 | +0.044 | −0.396 | REJECTED |
| `bb_squeeze15` | 876 | +0.028 | −0.450 | REJECTED |
| `gap_reversal15` | 307 | −0.011 | −0.495 | REJECTED |
| `vwap_reversion15` | 2,261 | −0.027 | −0.637 | REJECTED |
| Existing engine, defaults (13 stocks, offline) | 3,137 | −0.012 | −0.507 | no edge |
| Existing engine, walk-forward (13 stocks, offline) | 59 | — | −0.378 | no edge |

Within-design comparisons the protocol asked for:

- **Volatility filter (K) vs plain trend:** at the same N and k the filter
  raised net R slightly (N32,k2.5: −0.344 vs −0.391; N16,k1.5: −0.621 vs
  −0.710) and cut trades roughly in half. Both lose. A filter that loses less
  by trading less is not an edge.
- **Gap continuation vs gap fade:** continuation lost 0.31R, the fade 0.50R;
  the direction of the gross signal favours continuation, and neither pays.
- **Mean reversion (VWAP, gap fade) vs trend/breakout:** the reversion
  families have the worst gross (−0.03 to −0.01R) and the worst net.
- **Across families, the ranking is the ranking of trade count and of stop
  distance, not of idea:** wide stops (k=2.5) lose about half as much as
  tight ones (k=1.5) in every trailing family, because fewer, larger stops
  mean fewer small-risk, cost-heavy trades.

Buy-and-hold is not a meaningful baseline for a flat-by-close rule and was not
computed. Random-entry baselines were not run (the gate stops at the first
failure).
