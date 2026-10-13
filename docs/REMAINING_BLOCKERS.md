# Remaining blockers

**As of 13 October 2026, ~12:45 IST.** Evidence: the audit, the study run and
the tests in this branch. Mode PAPER; no order was ever sent to the broker;
no gate was bypassed or loosened.

## Blockers to any live trading, in order

| # | Blocker | Why it blocks | Next action |
|---|---|---|---|
| 1 | **No strategy has an edge.** 0 of 40 NSE configurations and the existing engine (0 passes, ≈0 gross) | The gate would refuse every order, correctly | A new, differently-framed hypothesis and a new frozen protocol. Do not re-run or loosen this one |
| 2 | **The account cannot trade.** Rs 293.44; the 1% rule is Rs 2.93; charges on one round trip are ≥ Rs 12; every stock sizes to zero | Not a tuning problem | Owner decision. Research does not support adding capital or leverage as a remedy |
| 3 | **The order path has never run against the real broker** (`LIVE_BROKER_VALIDATED=false`) | Status strings, tag echo, stop+target acceptance are from documentation | Only the owner can run one minimum-size order, watched in Angel One's app (TRADING.md §3a). Not done here |
| 4 | **No live implementation of any candidate; no prospective paper record** | The live gate requires both | Only worth writing for a rule that survives research |
| 5 | **Execution costs are assumed, not measured** | Every net figure rests on a spread/slippage scenario | Paper-trade something minimal and record quotes vs fills |

## Defects still open (details in the audit)

D9 broker path unvalidated · D10 futures/options/commodity charges
unverified (futures STT may be stale) · D11 no holiday calendar on the live
path · D12 NIFTY/BANKNIFTY history is one contract's token of unknown
construction · D13 live eligibility bar (30 trades) far below the study's
(200) — owner's call · D14 money is float · D15 no Alembic · D16 assumed
spread unmarked · D17 backtest scores no sub-chart timeframes · D18 manual
paper close fills at a stale quote · D19 no front-end tests. Also: no
peak-to-trough drawdown limit and limits set to 0 are *off* rather than
refused (`RISK_CONTROL_AUDIT.md`); LIVE can be entered with one confirmed
call and no installation-level lock (`PAPER_LIVE_ISOLATION.md`).

## Work not completed

- Cross-sectional momentum and daily mean reversion: not built.
- TCS, ADANIPORTS, TECHM: incomplete history (rate limit); study ran on 22.
- Open-source review: metadata and licences only; no third-party source read.
- Futures, options, currency, commodities: no research; CDS and MCX blocked.
- Mid-session behaviour of the new login backoff, bar grid and stale-quote
  logic is tested on fakes and was seen on the running app only before it was
  restarted: **restart the backend** (it does not reload) and watch the first
  session.
- Python 3.11 (CI) suite, Alembic, mypy, front-end tests.
- The commit `d00e751` is not self-contained: it builds on other sessions'
  uncommitted work. This session's later documents and results are
  uncommitted until committed by path.
