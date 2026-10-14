# Prospective paper trading

**State, 13 October 2026: not started, and not startable for a candidate.**

## Why

Prospective paper trading tests a *frozen* strategy on bars that did not exist
when it was chosen. It needs (1) a strategy that survived history, and (2) a
live implementation that runs the same rules as the research code. Today
neither exists:

- No family passed the research stretch: all ten were rejected
  (`NSE_15M_RESEARCH_RESULTS.md`).
- The ten NSE candidates exist only as research code (`india_strategies.py`).
  `eligibility.evaluate` says so itself: *"exists only in research: there is
  no live implementation of it to review."*

## What does run

The running application's auto-trader paper-trades **the existing engine**'s
signals when the Paper panel is enabled. `paper_trades` has 0 rows on record
as of 12:00 — it has never traded in paper on this database. Starting it
would measure the existing engine, not a candidate, and the engine has no
measured edge anywhere it has been tested; the research report recommends
against running it expecting a result.

## What a valid prospective test needs

1. A candidate that cleared the research and validation stretches.
2. The candidate's live signal generator, run on completed bars only (as of
   today the live engine does this, `analysis/engine.py::frames_at_close`),
   verified against the research code on the same bars (the same shadow check
   the report lists as not built).
3. Every signal persisted — taken, skipped, rejected — with data timestamp
   and age, expected spread and costs, simulated fill and its assumptions,
   observed quote, stop/target behaviour, P&L before and after costs,
   exposure, drawdown, disconnections.
4. A minimum of 30 closed trades before it says anything (the eligibility
   rule asks 20 for agreement; 20 is a checkpoint, not proof), and more
   if the interval is still wide.
5. Backtest and paper results reported separately, never pooled with live.

## What was added toward it today

- Paper entries refuse a stale or missing quote (`ws.py`), so a paper fill
  now means a price that was current.
- Live bars and the analysis frames match the venue's grid and the
  backtest's completed-bar frames, which removes the largest known source of
  disagreement between "what the research did" and "what the running engine
  does".
- `live_executions` / `paper_trades` stay separate tables; the audit log
  records every mode change.

## Next action

When a family is `RESEARCH APPROVED` (or better), write its live signal
generator behind a flag, add the shadow comparison, enable Paper for that
family only, and record 30 trades. Until then: nothing to run.
