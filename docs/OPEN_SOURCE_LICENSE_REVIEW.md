# Open-source licence review

**Evidence:** GitHub's repository metadata API, read 13 October 2026 ~11:55 IST.
That gives identity, licence tag, last push and archive status. **It is not
an inspection of any source file.**

| Repository | Licence (as tagged) | Last push | Archived | What follows |
|---|---|---|---|---|
| nautechsystems/nautilus_trader | LGPL-3.0 | 2026-10-13 | no | Linking is permitted; copying code into a non-LGPL project is not. Concepts only |
| freqtrade/freqtrade | GPL-3.0 | 2026-10-13 | no | Copying any code would make Meridian GPL-3.0. Concepts and published method only |
| mementum/backtrader | GPL-3.0 | 2024-08-19 | no | Same; also not maintained for two years |
| polakowo/vectorbt | `NOASSERTION` (a non-standard licence; its text was **not read today**) | 2026-10-09 | no | Treat as non-open for reuse; no dependency |
| vijaxx/algo-trading-system | MIT | 2026-09-21 | no | Reusable with notice; 0 stars, unreviewed |
| Gaurav1112/nse-trading-lab | MIT | 2026-10-12 | no | Same |
| builditwithgk/dhan-nifty-algo-trading-lab | `NOASSERTION` | 2026-08-17 | no | No licence granted → all rights reserved; **do not copy** |
| Ayush1906saxena/TradingBot | none | 2026-03-05 | no | No licence → do not copy |
| WallStreetPete/awesome-open-source-trading | CC0-1.0 | 2026-09-21 | no | A list; nothing to reuse but pointers |

Not found/checked: DeltaForge, EDGE, Options-Trading-Bot-India and
Algo-Trading, named in the brief as in Meridian's research notes — they are
not in `docs/research/REFERENCES.md`, and no URL for them was given.

## Decision

**No code from any of these has been copied, vendored or added as a
dependency, and none is recommended.** Meridian is not itself under a stated
licence in the repository (no `LICENSE` file), so combining GPL or LGPL code
into it is a legal question the owner has not answered; clean-room
re-implementation of published *methods* (look-ahead re-run checks; an order
lifecycle as one table; fill rules for resting orders) is what the earlier
session did and what this one did. Ideas and algorithms are not
copyrightable; code is. Where a method is attributed in code or docs, the
attribution names the project and the idea, never a file copied.

## Dependencies already in the project

Not reviewed here beyond `requirements.txt`; the SmartAPI SDK (`smartapi-python`,
MIT per its package metadata — **not re-checked today**) is the only broker
dependency. Three libraries were removed with the old providers
(`requirements.lock` −11 lines, from the 13 Oct 01:32 change by another
session).
