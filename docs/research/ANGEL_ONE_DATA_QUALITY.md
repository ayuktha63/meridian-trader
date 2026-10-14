# Angel One history for NSE cash equities — what is on file and how far it can be trusted

13 October 2026. Branch `research/quant-pipeline`, uncommitted.

**Daily history of 49 of the 50 Nifty 50 stocks, October 2018 to October
2026, has been taken from Angel One, checked, and may be tested on. Nothing
has been tested on it yet.** Intraday history exists back to 2017 and has
not been taken for the universe.

This document is the record of how that history is taken, what the checks
are, what they found, and what they cannot see.

---

## 1. What was wrong before

Three defects, each of which would have invalidated any study of an Indian
stock. Each has a test that fails without its fix.

| Defect | Consequence | Where it is fixed |
|---|---|---|
| The quality check accepted a bar only if it opened on a multiple of its length **on the UTC clock**. NSE hourly bars open at 09:15, 10:15 … IST, and the venue stamps a daily bar at local midnight. | Every hourly and every daily bar of every Indian instrument was thrown away as "misaligned". A walk-forward of the live engine on BANKNIFTY 1h recorded this morning ran on **0 bars**. | `research/exchange_calendar.py`, `research/quality.py` |
| For any market with a session, missing bars were not measured at all. | A stock with a fortnight missing from its history passed as clean. | `quality._assess_session` |
| When a history request failed, the fetch kept what it had and returned it as if whole. | A truncated history was indistinguishable from a complete one. | `data/angel_history.py` |

A fourth was introduced and caught while building this: a snapshot was named
by its bars alone and is never rewritten, so the same bars judged again
under a corrected calendar kept their first verdict for ever. A snapshot's
name now includes what its bars were judged against.

## 2. How a history is taken

```
python -m app.research.india universe                  # what the universe is, and its limits
python -m app.research.india snapshot 1d --years 8     # take and check every member
python -m app.research.india snapshot 15m --years 3 --pause 1
python -m app.research.india accepted 1d               # which snapshots a study may use
```

1. **Which stocks** comes from a file with a version and a source
   (`research/universes/nifty50.json`), not from a command line.
2. **The token** for each is looked up in Angel One's scrip master at the
   time of the request, and recorded. It is never written down in code.
3. **The candles** are asked for window by window, in whole exchange days,
   at most 2.5 requests a second. A window that fails is tried up to four
   times (waiting 2, 4 and 8 seconds). Every window — asked, retried, empty,
   failed, not asked — is in the record.
4. **The trading days** are measured from the Nifty 50 index's own daily
   bars over the same stretch. A day the index has and the stock lacks is a
   missing day; a day neither has was a holiday.
5. **The result is stored whether it passes or not.** A rejected snapshot is
   evidence about the data. `india.accept(dataset)` is the one gate a study
   loads through.

Research cannot reach an order: nothing under `app/research` imports from
`app/broker`, and no file that fetches or studies history names an order
call. Two tests hold that.

## 3. What makes a snapshot unusable

| Check | Limit |
|---|---|
| Not from a venue (generated bars) | refused |
| Not held to an exchange session | refused |
| Trading days not checked against a calendar, or a calendar that does not cover the whole history | refused |
| Any request behind it failed, an empty window sits between two with bars, or the newest window is empty | refused as incomplete |
| Bars missing against the session and the calendar | more than 1% |
| Consecutive trading days missing | more than 3 |
| Bars with no volume | more than 1% |
| An overnight move of 20% or more that nobody has accounted for | any |
| Rows that arrived unusable (bad OHLC, off the grid, duplicates) | more than 1% |
| Fewer than 300 bars; 48 flat zero-volume bars in a row | refused |

Bars outside the regular session (an evening Diwali sitting) are left out
and counted, not treated as errors. A Saturday session is a traded day.

The 3-day rule is new and was set after seeing the data: Tata Motors' daily
series lacks ten consecutive days, which is 0.5% of eight years and passes
the 1% rule. It is a hole a daily strategy would trade straight across.

## 4. The universe

Nifty 50 as published by NSE Indices on 13 October 2026
(`ind_nifty50list.csv`, SHA-256 recorded in the file). Fifty stocks, all
series EQ, all present in Angel One's scrip master.

**It is today's index, and that biases any result on its past.** The stocks
that were dropped — mostly after doing badly — are not in it; the ones that
are, are the ones that grew into it. No history of membership is on file.
This limitation is recorded in every snapshot taken from the universe and
has to be stated beside any result. A study on this universe cannot be
called free of survivorship bias, and a long-only one is flattered by it.

## 5. What was measured

### Daily, all 50 members

| | |
|---|---|
| Requested | 12 Oct 2018 → 12 Oct 2026 |
| Requests | 250, all answered; 3 automatic retries |
| Bars stored | 96,660 |
| Trading calendar | 1,981 days, from the index; every year within the normal range |
| **Accepted** | **49 of 50** |
| Rejected | TMPV (Tata Motors): 15–29 Nov 2021 missing, ten days in a row |
| Shorter histories | ETERNAL from 23 Jul 2021, JIOFIN from 21 Aug 2023, MAXHEALTH from 21 Aug 2020 — their listings |
| Most common missing day | 14 Nov 2020, the Saturday-evening Diwali session, absent for 32 stocks |
| Bars with no volume | none |
| Overnight moves of 20% or more | none, in any stock |

**Angel One's history is adjusted for corporate actions.** Reliance's 1:1
bonus of October 2024 and Tata Motors' demerger of October 2025 show no
break in price. So the prices in the past are not the prices that traded
then; returns are right, rupee prices are not. Dividends are not added back:
these are price returns.

### Intraday, one stock (RELIANCE)

| Interval | How far back | Note |
|---|---|---|
| 1 hour | at least October 2018 | 13,780 bars taken and accepted; 9 days missing, 16 short days, 0.63% |
| 15, 5 and 1 minute | **July 2017** (July 2016 is empty) | measured with single-window requests |

The Tata Motors days missing from the daily series are **present** in its
15-minute series. A daily bar can therefore be rebuilt from complete
sessions of finer bars; `datasets.resample` does this and refuses a partial
day. Doing it automatically for a hole is not built.

### The rate limit, in practice

`getCandleData` is limited to 3 a second and 180 a minute, per API key. This
morning three sessions and the running application shared one key, and
requests failed with *"Access denied because of exceeding access rate"*. The
fetch reported those histories incomplete and the gate refused them, which
is the designed behaviour — but a universe-wide intraday run (about 950
requests for three years of 15-minute bars, 2,800 for nine) needs the key to
itself: after 15:30, `--pause 1`, nothing else polling.

## 6. What these checks cannot see

- **A hole the whole venue shares.** The calendar is measured from Angel
  One's own index series. A day missing from every series would not be
  noticed, except as a year with too few trading days. An exchange holiday
  list from NSE itself would close this; none is on file.
- **A corporate action smaller than 20%.** A demerger that moved a stock 8%
  looks like a day's trading. The adjustment is the vendor's and is taken
  on trust beyond what the 20% check can confirm.
- **Whether the adjusted prices could have been traded.** Sizing in whole
  shares on back-adjusted prices lets a small account "afford" a share that
  cost several times more at the time.
- **Intraday quality across the universe.** Not measured. Another session
  observed, on two 15-minute snapshots, a flat zero-volume 15:15 bar on
  about one day in five and no 15:15 bar on about one in ten; both show up
  in the quality report (`zero_volume_pct`, `short_days`) and may well put a
  15-minute series over the 1% limits. This has to be looked at before any
  intraday study, not assumed.
- **Index and stock futures.** A future's token is one contract. No session
  or continuous series is defined for them, and research on them still
  yields no usable history.

## 7. What to do next

1. Take the 15-minute universe with the key to itself, and read its quality
   report before designing anything on it.
2. Decide what to do about a missing or dead closing bar: it is a data fact
   about the last fifteen minutes, which is when an intraday position exits.
3. Rebuild Tata Motors' missing daily bars from its 15-minute sessions, with
   the repair recorded in the snapshot.
4. Get historical index membership, or state plainly in every result that
   the universe is today's.
