# Confidence calibration report

**Read 14 October 2026.** Phase 5 of the master engineering prompt asks
whether the fused score behaves like a probability and, if not, that the UI
stop implying it does. Both the measurement and most of the relabeling
already existed before this pass; this documents what's there and fixes
the one spot that didn't match it.

## 1. What the score is made of

Six weighted components (`backend/app/analysis/confidence.py`, `WEIGHTS`),
each a raw score in roughly [-1, 1] for how strongly it leans a direction,
combined as `score × weight × 100`:

| Component | Weight |
|---|---|
| Trend (MTF alignment, EMA stack, ADX, SuperTrend, Ichimoku) | 25% |
| Momentum (RSI, MACD, StochRSI, ROC) | 20% |
| Volume (relative volume, OBV slope, MFI, VP position) | 15% |
| Market structure (BOS/CHoCH, OB/FVG confluence, premium/discount) | 20% |
| Pattern (chart + candlestick pattern strength) | 10% |
| Claude / rule-engine fallback | 10% |

No two components read the same underlying signal directly, though trend
and momentum both key off price action and could move together in a clean
trend — not formally measured here, and not the thing the evidence below
actually turns on.

## 2. Does a higher score win more often? Already measured — no.

Two independent readings, both already on record, agree:

- **Crypto, hourly, 2025–2026** (`docs/research/BASELINE.md` §7): the
  confidence score did not separate winners from losers at all on any
  market tested.
- **NSE stocks, the live engine itself, 13 symbols**
  (`docs/NSE_15M_RESEARCH_RESULTS.md`, "Existing live engine, for
  comparison"): gross expectancy by confidence band —

  | Band | Gross expectancy |
  |---|---|
  | <55 | +0.014R |
  | 55–64 | −0.001R |
  | 65–74 | +0.003R |
  | 75+ | +0.064R |

  Essentially flat. A genuinely calibrated score would show a monotonic
  climb; this shows noise around zero with a mild, single-band uptick on a
  sample too thin to call a trend (consistent with
  `docs/research/BASELINE.md` §7's same finding on a different market).

No formal logistic/isotonic calibration was fit, because fitting a
calibration curve to a score that hasn't yet shown *any* separation between
bands would be calibrating noise — exactly the thing Phase 5.2 warns
against ("avoid overfitting small samples"). That step is only worth doing
once a band shows a real, sample-adequate gap to calibrate against.

## 3. The UI — already mostly honest, one spot fixed

Checked every frontend component that surfaces this number
(`grep -rl confidence frontend/src/components`):

- **`ConfidenceRing`** (`frontend/src/components/ui/index.tsx:120-162`) —
  the headline number on the Trade panel. Already labeled **"signal
  score"**, not "confidence" or a probability, with a tooltip stating
  outright: *"A heuristic — not a probability of winning, and not
  calibrated against results."* This is exactly what Phase 5.3 asks for,
  and predates this pass.
- **`LivePositionsPanel.tsx:353`** — already reads "signal score", not a
  percentage.
- **`JournalPanel.tsx`** — already frames the accuracy-by-confidence table
  as the open question it is: *"If high-confidence buckets do not win more
  often than low ones, the score is [not calibrated]."*
- **`Screener.tsx`** — the one gap. Every row showed a bare
  `{confidence}%` with no caveat at all, the one place in the whole app
  still presenting this as if it meant something on its own. Fixed: same
  "signal score" wording and tooltip as `ConfidenceRing`, and the trailing
  `%` removed (a percent sign reads as a probability even when the label
  next to it says otherwise).

## 4. Conclusion

The score is not calibrated to outcomes on any market measured so far, by
two independent studies that agree. The interface already said so almost
everywhere; it now says so everywhere. Re-running this check is worthwhile
once the NSE engine has enough live or paper trades logged in the Journal
to read a band split that isn't 13-stocks thin — not before.
