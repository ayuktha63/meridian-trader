# Meridian — release readiness assessment

**14 October 2026.** Produced against the master engineering prompt's
Phase 12. Scores are 0–100, each with the evidence behind it and what it
does not cover. Where a score rests on judgment rather than a measured
number, that is said outright rather than dressed up as one.

## Scores

| # | Category | Score | Evidence | What it does not cover |
|---|---|---|---|---|
| 1 | Data integrity | **85** | Single real source (Angel One); synthetic data removed from every production path; explicit "no data" states; NIFTY/BANKNIFTY now read a continuous spot-index token instead of a few-week-old futures contract (confirmed live: 0 → 1,500 real bars). | The 8 symbols that still return no 15m data after repeated retries (CDS pairs, a handful of stocks) haven't been individually root-caused — likely genuine Angel One gaps, not confirmed. |
| 2 | Risk-control effectiveness | **80** | Independent server-side gate, re-derives everything, never trusts frontend input. Daily loss, now a peak-to-trough drawdown lock (new this session, persisted, tested), a hard-coded block on two segments with unverified tick/lot conversion, kill switch, measured-edge requirement. Three critical limits can no longer be silently disabled by a `0`. | No correlation-based exposure limit beyond sector grouping. The account's own size (₹420) is currently the dominant real control, not any configured limit. |
| 3 | Execution correctness | **70** | A real, tested state machine (`order_states.py`) enforced at the ORM layer, not just in a service function — every state has a table entry, finished states can't be revived, duplicate/ambiguous-response handling and restart recovery are all tested against an in-memory broker. | **Never run against the real broker.** Status words, the tag-echo behaviour, and the product field on positions are from Angel One's documentation, not observation. This is the one gap no amount of further engineering here closes — it needs a real, small, owner-authorized order. |
| 4 | Test coverage and quality | **85** | 463 backend tests, offline, ~25s, green. Adversarial coverage exists for most of Phase 8.2's named scenarios: zero/missing limits, stale quotes, duplicate orders, submission timeouts, partial fills, restarts, drawdown breaches (new). Safety rules were historically broken on purpose, one at a time, to confirm each one actually catches its own failure. | No frontend test suite (Python 3.11/CI parity also unverified this session — not re-checked). Test count is not itself proof of safety, per the prompt's own instruction — see the line-by-line defect fixes in this session's commits for what the number actually represents. |
| 5 | Research methodology | **80** | Train/validation/holdout splits, walk-forward with parameter selection on one stretch and testing on the next only, random-entry controls, multiple-testing correction (deflated Sharpe), 30+ trade minimums before any verdict. Costs charged from a shared, named model — the same one paper trading and live limits use. | The NSE study ran once, on 22 of 25 declared stocks (3 excluded for incomplete history) — a second run on the same stretch would itself be the kind of search the protocol exists to prevent, so it hasn't been re-run to raise that count. |
| 6 | Demonstrated trading profitability | **0** | Measured, not assumed: 0 of 40 tested NSE strategies profitable after real costs; the live engine itself measured −0.38R to −0.51R net expectancy on 13 real stocks; the one real trade placed (INFY, this session) lost ₹12.50 — consistent with, not contrary to, every other finding. | Nothing. This is the actual, current, measured answer, and no amount of engineering elsewhere changes it. |
| 7 | Confidence calibration | **10** | Measured twice, independently, on two different markets (crypto hourly, NSE stocks): the score does not separate winners from losers. The UI already labels it "signal score" with an explicit "not a probability" tooltip almost everywhere; the one place that didn't (the Screener) was fixed this session. | The 10, not 0, because the *honesty* of the presentation is good — the score itself still carries zero demonstrated predictive value. |
| 8 | UX transparency | **75** | Mode (PAPER/LIVE) is now a segmented control plus a full-width "LIVE TRADING · REAL MONEY" banner, not a single color-changing pill. Screener rows now show "as of" with a staleness tint. Chart overlays (both views) now visibly dim and say so once their own stop or target has already been reached, instead of looking identically "live" forever. | Orders/fills/P&L transparency (submitted vs acknowledged vs filled, paper vs live P&L separation) was not independently re-audited this session beyond what the state-machine review in §3 already covers. |
| 9 | Operational reliability | **70** | PAPER-on-restart, kill-switch persistence, drawdown persistence (new), holiday-aware market hours (new — was previously "by the clock only"), reconciliation against the broker's own book rather than this app's assumptions. | No peak-to-trough equity dashboard exposed yet beyond the new `/api/trading/drawdown` read endpoint — recovery is a single POST, not a guided procedure. |
| 10 | Overall live-execution readiness | **BLOCKED** (see below) | — | — |

## Release status

**BLOCKED.**

Not RESEARCH ONLY, and not PAPER VALIDATION — those would both undersell
how much real engineering rigor is already in place. But the prompt is
explicit that no status "automatically authorizes live execution," and two
separate, independent facts each independently block it on their own:

1. **No strategy tested on this account's actual instruments has a
   measured edge.** Category 6 is 0, not low. Executing live against that
   is not a risk being managed; it is trading on an answer that has
   already come back "no."
2. **The order path has never met the real broker.** Category 3's one real
   gap. Even a system with a demonstrated edge should not go live before
   this closes.

Either one alone would block. Both apply.

## What changes this

- For (1): a different, pre-registered hypothesis, tested honestly, that
  clears the bars in `docs/STRATEGY_ELIGIBILITY.md` — or patience while the
  one surviving research idea (4-hour trend-following, forward-watched,
  crypto) accumulates the real elapsed time it needs. Not more tuning of
  what already failed.
- For (2): the owner placing one minimum-size, closely-watched order
  through Angel One, per TRADING.md §3a — something this session will not
  do on its own initiative, and should not be asked to.

## What this session actually did, plainly

Fixed, tested, and verified against the real account: a drawdown lock that
didn't exist, a holiday calendar that didn't exist, a stale-quote paper
close that silently faked a flat exit, an NSE index feed that was reading
the wrong instrument, and a config footgun that let three critical limits
be silently switched off. Audited, and found already solid rather than
rebuilt: the order state machine, duplicate-order handling, and most of
the confidence-score honesty work. Found nothing that moves category 6
or 10 — because nothing legitimately can, except real evidence or a real
order, neither of which this document pretends to have.
