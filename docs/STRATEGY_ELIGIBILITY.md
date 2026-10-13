# Strategy eligibility

**Evidence:** `research/eligibility.py`, `research/study.py`,
`research/india_study.py`, `services/edge.py`, `broker/risk_gate.py`, read
13 October 2026. **No threshold was changed.**

## Two questions, kept apart

| | Research eligibility | Account eligibility |
|---|---|---|
| Asks | does the rule have an edge after costs, on history it was not chosen on? | can *this* account take the trade inside its risk rule? |
| Answered by | `study.py` / `india_study.py` thresholds; `eligibility.evaluate` | `risk_gate`, `costs_india.size_position` |
| Today | **Nothing is eligible** (below) | **Nothing is tradable**: Rs 293.44; zero shares for every stock tried |

A research-eligible rule is not a tradable one; a tradable instrument is not
an eligible rule. Neither is allowed to stand in for the other.

## The two bars

| | Study (candidates) | Live gate (`eligibility.THRESHOLDS`) |
|---|---|---|
| Trades | 200 pooled (research); 100 later | 30 out of sample |
| Interval | 90%, clustered by week, above 0 | 90% above 0 |
| Multiple testing | deflated Sharpe ≥ 0.95 over all configurations | Holm across markets tested in the window |
| Parameter stability | ≥ 60% of own grid profitable | ≥ 50% |
| Breadth | ≥ 60% of stocks profitable | — |
| Costs | positive at 1.5× | cost profile must match the venue |
| Random baseline | p ≤ 0.05 | p ≤ 0.05 |
| Data | audit + accepted real snapshot | real venue, quality passed |
| Staleness | — | test ≤ 30 days old; code fingerprints unchanged |
| Paper | — | 20 closed paper trades agreeing within 0.30R |
| Broker | — | `LIVE_BROKER_VALIDATED` true |
| People | — | an approval on record for the code version and market |

The live gate is the weaker on sample size (30 vs 200) and breadth. **It is
the one that leads to an order.** Not changed in either direction; flagged as
defect D13 in the audit for the owner.

States: INELIGIBLE · INSUFFICIENT EVIDENCE · RESEARCH APPROVED · PAPER
VALIDATION · ELIGIBLE FOR HUMAN REVIEW. None trades. A threshold that cannot
be computed is NOT EVALUABLE and holds the family back.

## Current position

| Subject | State | Why |
|---|---|---|
| Live engine, any NSE stock or index future | **INSUFFICIENT EVIDENCE** at best | the walk-forwards on record: RELIANCE 19 trades (+0.65R, interval +0.21…+1.07R, `NO MEASURED EDGE`), NIFTY 0 trades; six RELIANCE named-play walk-forwards, none passing; all before the 13 Oct engine/cost changes, so stale by fingerprint. A fresh baseline on 25 stocks is running offline in another session |
| Live engine, BANKNIFTY 1h | no evidence | a walk-forward at 09:54 recorded 0 bars |
| Live engine, crypto | INELIGIBLE, archived | `docs/research/REPORT.md`; venue removed |
| The ten NSE candidates | **not yet judged** — see `NSE_15M_RESEARCH_RESULTS.md` | protocol frozen at 12:00 |
| Any of them as a *live* strategy | **cannot be eligible** | no live implementation exists (`live_capable=False`) |
| Currency, commodity futures | blocked | CDS tick scale and MCX quantity unit unverified |
| Options | unsupported | no execution path |

`GET /api/research/eligibility?symbol=…&timeframe=…` returns the live
engine's state and every reason.

## Hard rules held

Thresholds are constants with a version, not arguments. Evidence goes stale
when the strategy, execution or cost code changes — it did today, on purpose.
A result on simulated data is withdrawn. A winner is never chosen and then
re-tested on the stretch it was chosen on.
