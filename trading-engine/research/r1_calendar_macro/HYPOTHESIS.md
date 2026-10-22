# R1, calendar and macro-announcement timing: hypotheses and test plan

Written on 2026-10-22, before any backtest in this folder ran. This file is
only appended to after the results (docs/PLAN.md §9, research gate, step 1).

Already read:
- the reports of research/s1_trend, research/s3a_vol_overlay,
  research/r2_vol_positioning and research/r3_vix_futures;
- R3's trials.csv, which lists the 27 trials counted so far. Their Sharpes'
  variance doubled with R3 (0.0215 to 0.0414 a year), which raises the
  deflated-Sharpe threshold this study has to clear.

What was looked at before this was written: only the data checks `fetch.py`
prints, which are coverage, the dates and how many days a year each rule
holds SPY. No rule's return, and no SPY return on any of these days.

## Data, all free, with no key or login (`fetch.py` fetches and caches it)

| Source | What | Coverage on disk |
|---|---|---|
| federalreserve.gov, the FOMC's historical pages and its calendar | each scheduled meeting's announcement day | 271 from 1994-02-04 to 2027-12-08 |
| ALFRED (St. Louis Fed), release 50 | every date the Employment Situation was published | 1955-05-06 to 2026-10-02 |
| ALFRED, release 10 | every date the Consumer Price Index was published | 1949-03-24 to 2026-10-14 |
| trading-engine/src/common/clock.py | the NYSE's sessions by its published rules | any date |
| On disk | SPY and BIL from S1's panel (`s1_trend.load_panel`), to 2026-10-16 | from 2005-01-03 |

What the checks found:
- **FOMC.** 8 scheduled announcements a year, and 7 in 2020: the March 17-18
  meeting was cancelled.
  - Left out: 46 headings for conference calls, unscheduled meetings and the
    cancelled one, including 2020-03-15's emergency cut and the inter-meeting
    moves of 2001 and 2008.
  - Two-day meetings count at their last day, when the statement is
    released. Every announcement day is an NYSE session.
- **One release a month.** ALFRED keeps other vintages besides each report,
  so one date a month is taken:
  - Employment Situation: the first date in each month. The later dates are
    corrections and benchmark revisions, such as 2024-08-21's preliminary
    benchmark: 2006-05-08, 2012-12-12, 2013-05-06, 2020-05-11, 2024-01-10 and
    2024-08-21.
  - CPI: the last date in each month. February's earlier date is the revision
    of the seasonal factors, published a few days before January's CPI.
- **Rescheduled after a lapse in appropriations.** These were announced only
  days before they fell, and the data doesn't say whether that was two
  sessions ahead, so the rules leave them out:
  - Employment Situation: 2013-10-22, 2025-11-20, 2025-12-16 and 2026-02-11,
    the only ones off the release's weekday (a Friday, or the Thursday before
    a closed Friday).
  - CPI: 2013-10-30, 2025-10-24 and 2025-12-18.
  - There was no release in October 2025 (jobs) or November 2025 (CPI).
  - Only 2013's two fall in the walk-forward; the rest are in the holdout.
- **Releases on a day NYSE was closed** count on the next session, the first
  whose close-to-close return includes them:
  - Employment Situation on Good Friday: 2007, 2010, 2012, 2015, 2021, 2023
    and 2026.
  - CPI: 2017-04-14 and 2020-04-10.
- **The calendar.** S1's panel has 5,482 sessions from 2005-01-03 to
  2026-10-16. clock.py's rules give 5,487. The panel lacks exactly the five
  unscheduled closures: 2007-01-02, 2012-10-29, 2012-10-30, 2018-12-05 and
  2025-01-09. None of those was known in advance except a few days ahead
  (2018-12-05 was announced on 2018-12-01). The rules use clock.py's
  calendar, not the panel's, so they can't foresee a closure (see Timing).
- **Days in SPY**, counts only, 2008-01-02 to 2026-10-16:
  - the primary: 39 to 48 a year, 19.1% of sessions;
  - V1: 45 to 56 a year, 21.7%;
  - V2: 61 to 77 a year, 29.2%.
  - Of the FOMC days from 2008, 27 of 151 fall inside a turn of the month;
    of the jobs days, 74 of 222; of the CPI days, none.

## The mechanisms, and what the literature says

**The turn of the month (TOM).**
- Lakonishok and Smidt (1988), on the Dow from 1897 to 1986: returns are
  far higher on the last trading day of the month and the first three
  trading days of the next.
- McConnell and Xu (2008), "Equity Returns at the Turn of the Month": over
  that four-day window from 1926 to 2005, investors in US stocks were paid
  for market risk only at the turn of the month. The effect appears in 31 of
  35 countries. Flows and volume didn't explain it: they called it a puzzle.
- Etula, Rinne, Suominen and Vaittinen (2020), "Dash for Cash" (RFS): the
  mechanism. Payments fall due around month end, so institutions sell to
  raise cash just before it. Short-term funding rates and equity and bond
  yields rise temporarily into the month's end, then revert. The payment
  cycle is institutional plumbing, not a mistake that trading removes, so
  there is a case that the effect outlives its publication.

**Scheduled FOMC announcements.**
- Lucca and Moench (2015), "The Pre-FOMC Announcement Drift" (JF): from 1994
  to 2011, the S&P 500 rose on average about 49 bps in the 24 hours before
  scheduled announcements, a large share of the equity premium in that
  sample.
- Kurov, Wolfe and Gilbert (2021), "The Disappearing Pre-FOMC Announcement
  Drift" (FRL): the drift largely vanished after 2015, with or without a
  press conference. The test period here, 2008 to 2023, holds both regimes.

**Macro announcement days.**
- Savor and Wilson (2013, JFQA): from 1958 to 2009, the stock market's
  average excess return on days with scheduled inflation, employment and
  FOMC news was about ten times that on other days. The pay is for bearing
  announcement risk, which resolves on those days.
- Later work concentrates that premium on FOMC days, and the post-2015 fade
  above applies to it.

**Decay.** McLean and Pontiff (2016): anomaly returns are about 58% lower
after publication. All three effects were published before this test's
window, so the walk-forward is entirely post-publication and may find them
weaker than the papers did.

## Timing: which day's return each rule earns, and when it trades

- In S1's harness, a decision at session t's close is traded at session
  t+1's close (lag 1) and held to t+2's close, so it earns session t+2's
  close-to-close return.
- So a rule that wants day D's return holds SPY in the decision two sessions
  before D, and is in SPY from the close of D−1 to the close of D. Live,
  that is a market-on-close buy on the evening of D−1.
- "Two sessions ahead" counts on clock.py's calendar, which a trader had in
  advance. Around an unscheduled closure the rule acts as it would have live:
  - 2012-10-29 and 30 were closed, so the decisions for 2012-10-31 and
    11-01 fell on closed days: the sleeve misses those two returns.
  - 2018-12-05, the third session of December 2018 by the rules, was closed:
    the sleeve bought for it and holds through the next session.
- Every date is known well before D−2:
  - the turn of the month comes from the exchange calendar;
  - the Fed publishes each year's FOMC schedule a year or more ahead
    (fomccalendars.htm already lists 2027);
  - BLS publishes each year's release schedule ahead of the year, and the
    rescheduled releases are left out (above).
- What each day's return holds:
  - FOMC: the statement comes in the afternoon: 14:00 ET since 2013, and
    before that 14:15, or 12:30 on press-conference days in 2011-12. So day
    D's return holds most of the pre-announcement window and the reaction.
  - Jobs and CPI come at 08:30 ET, before the open, so day D's return holds
    the reaction.

## The primary and its variants

**The primary, chosen now: the turn of the month.**
- On each month's last session and the next month's first three, hold SPY at
  99.5% of NAV, else BIL at 99.5%. Decided every session, lag 1.
- It is held over the close of session −2 to the close of session +3: one
  round trip a month.
- Why the primary: it has the longest record of the three, it shows up
  across countries, it has a mechanism that trading shouldn't remove (the
  payment cycle), and its dates need no data.

**Variants (2 trials).** Each adds known-in-advance announcement days to the
turn of the month: the same family, holding SPY on more days that the
literature says pay more than the rest.
- **V1: the turn of the month and scheduled FOMC announcement days.** It
  tests whether the FOMC days add anything, which after 2015 the literature
  says they shouldn't.
- **V2: the turn of the month, FOMC days, and Employment Situation and CPI
  days** (Savor and Wilson's announcement days).

Every variant holds SPY at 99.5% on its days, else BIL at 99.5%, decided
every session with lag 1. No variant can replace a failed primary.

## Trials and the deflated Sharpe

- This study's trials: the primary and 2 variants, 3. With the 27 already
  spent (S1's 8, S3a's 6, R2's 9, R3's 4), **n = 30**.
- The trials' Sharpe variance comes from all 30: the 27 in R3's trials.csv
  and these three.

## Costs

*Amended at review on 2026-10-22, before any run.* The draft took S1's
CostModel unchanged, with its 2 bps half-spread for every ETF. For SPY and BIL
that is about ten times their real spread. At 12 round trips a year it would
put the primary's cost drag at about 1% a year, right at criterion 6's limit,
so the verdict could turn on a cost the trades don't pay. The spreads below
were set from the instruments' quotes, not from any result.

- S1's CostModel through the C++ walk-forward, with SPY's and BIL's
  half-spreads at **1.0 bps** (`half_spread_bps_by_symbol={"SPY": 1.0,
  "BIL": 1.0}`):
  - SPY quotes a one-cent spread. With its price between about $68 (March
    2009) and $770 from 2008 to 2026, half of it is 0.07 to 0.74 bps.
  - BIL quotes one cent on about $91.5, a 1.1 bps spread, so half of it is
    about 0.55 bps.
  - The fund trades both in the closing auction, where there is no spread to
    cross.
  So 1.0 bps errs high for both, throughout.
- The rest is S1's: IBKR Pro commissions, square-root impact k = 0.5 on the
  master's volumes, and the mandate checked every day. Cash earns nothing (BIL
  holds the idle money).
- A round trip moves 99.5% of NAV from BIL to SPY and back: about 4 bps of NAV
  in spread (1 bp on each of four legs), plus commissions and impact.
- The primary makes 12 separate SPY holdings a year (one round trip each),
  V1 18 and V2 33. The books are re-sized to their targets at every close,
  which trades the day's drift.
- These costs are lower than R2's VIX timing of SPY paid (S1's 2 bps), so the
  two studies' cost drags don't compare.

## Split and measures (as R2's and R3's)

- **Walk-forward:** decisions from 2008-01-02, yearly test blocks on an
  expanding window, up to the holdout. The rules have nothing to fit; the
  harness is S1's so the numbers compare.
- **Holdout:** the last 756 sessions of the panel ending 2026-10-16, so from
  2023-10-12. Run once, after the walk-forward is written down.
- **Measures:**
  - excess returns over BIL, with Sharpe SE sqrt((1 + SR² / 2) / T);
  - the block bootstrap of 63-day blocks, 2,000 resamples, seed 21;
  - cost drag as R2's and R3's: total cost a year over the average value.
- **Reference books**, run through the same harness and window, reported and
  not counted as trials:
  - SPY held at 99.5%, bought once;
  - S0;
  - S1 blend long/flat;
  - the complement: SPY on every session the primary is in BIL, to show what
    the calendar adds over SPY on any other days.

## Pass criteria (walk-forward, the primary)

**Standalone:**
1. Excess Sharpe at least 0.40.
2. Deflated Sharpe at least 0.90 (n = 30).
3. Block-bootstrap 5th percentile of the excess Sharpe above 0.
4. Every variant's excess Sharpe above 0.
5. Max drawdown no worse than −20%.
6. Cost drag at most 1% a year.
7. No day outside the mandate.

A sleeve in SPY on a fifth of the sessions, on days no better than average,
would have about √0.19 ≈ 0.44 of SPY's Sharpe. So criteria 1 and 2 ask the
chosen days to pay several times SPY's average day. That is the claim being
tested.

**Crashes,** for a sleeve that holds SPY only some days. They replace S1's
"positive in 2008 and 2022", which doesn't fit a part-time equity book:
8. Worst calendar month no worse than −10%.
9. In each of R3's stress windows, a return above SPY held's over the same
   window. Its days are a subset of SPY's, so doing worse would mean the
   chosen days are where the crashes come. The windows:
   - 2008 crisis: 2008-09-15 to 2008-11-20
   - 2010 flash crash: 2010-04-23 to 2010-07-02
   - 2011: 2011-07-22 to 2011-10-03
   - 2015–16: 2015-08-17 to 2016-02-11
   - February 2018: 2018-01-26 to 2018-02-09
   - late 2018: 2018-09-20 to 2018-12-24
   - 2020 crash: 2020-02-19 to 2020-03-23
   - calendar 2022

**Against the fund's book:**
10. The 50/50 book of S0 and the sleeve, with daily returns mixed as in S3a's,
    R2's and R3's studies, has a higher excess Sharpe than S0's.

Correlations with S0 and S1, and the complement book, are reported, not
judged.

**Holdout kills:** an excess Sharpe below −0.5, or a drawdown worse than −15%.

**Verdict.**
- **Pass:** every criterion and no holdout kill. Then shadow mode (PLAN §9,
  step 7).
- **Fail:** the report says which criteria failed, and the primary isn't
  taken further. No variant replaces a failed primary.

## How it would be traded (not tested here)

The fund's own path:
- a market-on-close buy of SPY and sell of BIL on the evening of D−1, and the
  reverse after the last chosen day;
- the daily run at 15:35 ET already sends market-on-close orders, inside the
  no-leverage check: SPY and BIL are both in the model universe.
