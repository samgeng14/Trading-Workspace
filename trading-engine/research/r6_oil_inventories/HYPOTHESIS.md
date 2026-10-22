# R6, crude oil timed by US inventories: hypotheses and test plan

Written on 2026-10-22, before any backtest in this folder ran. This file is
only appended to after the results (docs/PLAN.md §9, research gate, step 1;
research/README.md).

Already read:
- R5's hypothesis and report, and R3's and R4's hypotheses;
- research/README.md;
- R5's trials.csv, which lists the 36 trials counted so far.

What was looked at before this was written:
- the papers below; EIA's archive pages; USO's quarterly report for the
  quarter to 2020-06-30 (its 10-Q, on SEC EDGAR) and etf.com's account of
  USO's changes in April 2020;
- what `python data.py` prints:
  - the series' coverage, a second copy of them, and when each week's
    figures came out;
  - each week as first published against today's figures;
  - this study's five-year norm against EIA's own published comparisons;
  - the futures curve against the inventories, from prices and inventories
    alone;
  - what each rule would hold on its decision days, and in each stress
    window, from the inventories alone;
  - USO's split, volumes, distributions, premium to NAV and tracking of the
    curve;
  - USO's volatility before the first decision and its worst days, for the
    size;
  - oil's crashes in WTI's spot price, which set the crash windows;
  - USO's actual share prices, ranges and dollar volumes, for the costs.

No rule's return was computed, and no USO return on any signal. Disclosed:
- **USO's prices across the whole panel, the holdout included.** For the
  costs, `data.py` prints each year's lowest and highest share price; 2026's
  run from $67.79 to $161.86. Before `data.py` was written, a check of the
  master's split basis printed USO's year-end closes from 2006 to 2026.
- **Three return statistics, none conditioned on a signal:**
  - USO's betas and R² against contracts 1 and 2, before and after
    April 2020;
  - its volatility from 2006-04 to 2007-12;
  - its five worst days since 2008.
- **The period's market history is public,** oil's and USO's both (see
  "Known before the run").

## Data, all free and keyless (`data.py`)

| Source | What | Coverage |
|---|---|---|
| EIA's bulk petroleum file (`opendata/bulk/PET.zip`, JSON lines, read with the standard library), cached in `research/_data/eia/` | weekly: US ending stocks of crude oil excluding the Strategic Petroleum Reserve (WCESTUS1), the same at Cushing, Oklahoma (W_EPC0_SAX_YCUOK_MBBL), refiners' net crude input (WCRRIUS2); daily: WTI's spot price at Cushing (RWTC) and NYMEX contracts 1 to 4 (RCLC1 to RCLC4) | stocks and inputs from 1982-08-20, Cushing from 2004-04-09, each to 2026-10-09; the spot to 2026-10-14; the futures to 2024-04-05 |
| EIA's series pages (`dnav/pet/hist/LeafHandler.ashx`) | the three weekly series as HTML tables: a second copy | to 2026-10-16 |
| EIA's archive of the Weekly Petroleum Status Report (`petroleum/supply/weekly/archive/`), `research/_data/eia/wpsr/` | 795 reports: each one's release date (its folder's name) and its table 4 (stocks, CSV) as published; for the first report of each quarter, its table 2 (refiners' input) and its highlights (PDF) | 2011-08-03 to 2026-10-21 |
| The ETF master (`etf_prices_master.parquet`) and the vendor files | USO's bars and its NAV; BIL with its income through R5's `tr_closes` | USO from 2006-04-11, to 2026-10-16 |
| R5's panel and S0's targets | S0_TR, as R5 built it | from 2005-01-03 |

What the checks found:

**EIA's series.**
- **The bulk file and EIA's series pages agree on every value:** 2,298 weeks
  of US stocks and of refiners' input, 1,175 of Cushing's. The pages also
  have the week ending 2026-10-16, which the bulk file (updated 2026-10-15)
  doesn't yet.
- **No week is missing from 2000,** and every week ends on a Friday.
- **WTI's spot price settled below 0 once, at −$36.98 on 2020-04-20,** with
  contract 1 at −$37.63 (the May contract, a day before its expiry).
  Contracts 2 to 4 never did.

**When each week was published** (EIA's archive: 795 reports, 2011-08-03 to
2026-10-21).
- **Regular reports came out on a Wednesday 675 times,** a Thursday 106, a
  Friday 10 and a Monday 2.
- **From a week's end to its first release:** 5 days for 674 weeks, 6 for
  106, 7 for 10 (nine of them around Christmas and the New Year), 10 for 2
  (2013-10-11, in the federal shutdown, and 2025-12-19).
- **Three weeks have no regular report of their own.**
  - The weeks ending 2022-06-17 and 2023-11-03 came out late, as data-only
    releases, and in the next regular reports 12 days after they ended.
  - The archive's table for 2019-07-03 repeats the week before's (the week
    ending 2019-06-21), so the week ending 2019-06-28 shows first in the
    report of 2019-07-10.
- **On all 182 decision days from 2011-08, the week the rule reads (one that
  ended at least 7 days before) was out by the close.** On 52 of them a later
  week was out too, so the rule then reads a week behind. Before 2011-08 the
  archive has no reports, and the 7 days are taken to cover the same
  schedule.

**Revisions.**
- **EIA's policy:** it revises weekly figures only if the revision would
  substantively affect understanding of US petroleum supply, and before 2004
  they had been revised about once every five years (Federal Register,
  69 FR 54140, 2004-09-07).
- **From the week ending 2016-10-07 (522 weeks), every first publication in
  the archive equals today's figure.** So do Cushing's on all 793 weeks, and
  refiners' input on all 62 weeks sampled (the first report each quarter).
- **Before that, EIA's weekly figure included lease stocks,** oil in tanks at
  producing leases. The report of 2016-10-13 dropped them (about 31 million
  barrels then) and restated the history; WCESTUS1 kept its name.
  - Every first publication from 2011-07-29 to 2016-09-30 (271 weeks) equals
    EIA's lease-inclusive weekly series (W_EPC0_SAX_NUS_MBBL), and sits 21.9
    to 34.1 million barrels above today's restated series.
  - That report's own year-ago figure (2015-10-09, 436.6 million barrels)
    was already on the new basis: the restated history came out with the
    change.
  - The only week whose next report restated it is 2016-09-30, in that
    report.
  - Lease stocks, the gap between the two series, averaged 16 to 21 million
    barrels from 1985 to 2010, and 31 million by 2014–16.
- **So the primary and V2 read the stocks as published** ("The rules").

**EIA's own five-year comparison.**
- 33 of the 62 highlights sampled state where crude stocks stood against
  "the five year average for this time of year": the first report of every
  quarter from 2018-07 to 2026-07. The earlier ones compare with "the
  average range", and 2026-10-07's words it otherwise. EIA's level matches
  the week's figure in all 33.
- **This study's norm puts stocks on EIA's side of its average in all 33,**
  within 1 point of EIA's figure (which EIA rounds) in 31. The largest gaps:
  1.4 points on 2026-03-27 (EIA "about 0.1% above", this study +1.5%) and 1.3
  on 2019-06-28 ("about 5% above", +6.3%).
- The PDFs' two columns interleave in the text, which `data.highlight`
  allows for. Each report is dated by its release, since the archive's table
  for 2019-07-03 repeats the week before's.

**The curve and the inventories: the mechanism's premise.** On the 196
decision days to EIA's last futures price (2024-03-28):
- **The curve was backwardated on only 63** (contract 1 above contract 2).
  The median slope was −0.32%; the lowest −16.4% (2020-03-31) and the
  highest +2.6% (2022-06-30).
- **Each gauge moves against the slope, as the theory says.** Their
  correlations with it are −0.37 (US stocks), −0.40 (Cushing) and −0.45
  (days of supply).
- **When the primary's gauge was below its norm, the curve was backwardated
  on 36 of 51 days; when above, on 27 of 145.** Cushing's: 56 of 85 against
  7 of 111. Days of supply: 45 of 66 against 18 of 130.

**USO.**
- **The master's closes and volumes equal the vendor's on every session,**
  both split-adjusted. USO's 1-for-8 reverse split took effect after the
  close of 2020-04-28 (its 10-Q), and the master's closes up to then are 8
  times what a share traded at: $17.04 on 2020-04-28 was $2.13 a share, and
  $18.00 the next day was $18.00. Close times volume is the dollars traded
  on both sides of the split.
- **USO pays nothing:** the vendor's total-return index equals its price on
  all 5,007 sessions to 2026-03-06. IBKR's adjusted closes are within 0.05%
  of the price.
- **Its close sat near its NAV,** a median of −1.2 bps from 2008 (the 1st to
  99th percentile −116 to +191 bps). **Except in April 2020:** +36.5% on
  2020-04-21, +8.5% the next day, and +9.1% on 2020-03-18.
- **What it held changed in April 2020.** It had held the front month,
  rolled over four days about two weeks before expiry. In April 2020 it
  moved 20% of its holdings to the second month, then spread them over
  contracts out to June 2021, and stretched its roll to ten days (etf.com,
  April 2020). Its futures broker barred it from the June 2020 contract,
  and NYMEX capped its positions. Its quarterly report to 2020-06-30 lists
  contracts from July 2020 to May 2021 (its 10-Q).
- **Its daily tracking of the curve changed little.** Against contracts 1
  and 2 (roll days left out), from 2008-01 to 2020-03: betas 0.85 and 0.91,
  R² 0.881 and 0.905. From 2020-07 to 2024-04: 0.85 and 0.88, R² 0.905 and
  0.913. The betas below 1 likely come from timing: USO closes at 16:00 ET,
  the futures settle at 14:30. The change shows in the roll, not the daily
  moves.

**What the rules would hold** (from the inventories alone, as R5 counted
from the yields):
- **The primary: below its norm on 45 of the walk-forward's 190
  decisions,** in 2008-01, 2008-04 to 2008-09, 2011-11, 2018-03 to 2018-09,
  2019-03, 2020-01 to 2020-03, 2021-05 to 2022-12, 2023-04 to 2023-06 and
  2023-08 to 2023-09. **And on 32 of the holdout's 36,** 2023-10 to
  2026-02 and 2026-05 to 2026-07. 19 switches in all.
  - Its gauge over the walk-forward: a median of +6.2%, the 10th to 90th
    percentile −3.7% to +29.3%. It came within 1% of the norm on 11
    decisions.
- **V1 (Cushing): 79 of 190, and all 36 of the holdout's;** 18 switches.
  Its gauge swings far more: the 10th to 90th percentile −41% to +63%.
- **V2 (days of supply): 62 of 190, and 33 of 36;** 36 switches.
- The primary agrees with V1 on 81% of the decisions and with V2 on 87%.
- **The first decision (2008-01-02) reads the week ending 2007-12-21.**
  Cushing's norm then averages 3 years.

**Oil's crashes,** found mechanically in WTI's spot price (see criterion 10):
seven from 2008, all in the walk-forward, none in the holdout.

**The costs' inputs** are in "Costs" below.

## The mechanism, and what the literature says

**The theory of storage.** A commodity held in storage earns its owner a
convenience yield: the value of having it on hand when it runs short.
- Kaldor (1939), "Speculation and Economic Stability", Review of Economic
  Studies 7(1), introduced the convenience yield. Working (1949), "The Theory
  of Price of Storage", American Economic Review 39(6), and Brennan (1958),
  "The Supply of Storage", American Economic Review 48(1), built the theory
  on it. These three are cited as the papers below cite them; they weren't
  read here.
- When inventories are scarce, the convenience yield is high, the spot price
  trades above the futures and the curve is backwardated. A long futures
  position then rolls up the curve as it nears delivery. When inventories
  are ample, the curve is in contango and a long position pays the carry.
- **Fama and French (1987),** "Commodity Futures Prices: Some Evidence on
  Forecast Power, Premiums, and the Theory of Storage", Journal of Business
  60(1). The basis (futures less spot) moves with interest rates and with
  the seasonals of convenience yields, as the theory says. But evidence of
  time-varying expected premiums was found for only 5 of 21 commodities.

**Inventories and the premium.**
- **Gorton, Hayashi and Rouwenhorst (2013),** "The Fundamentals of Commodity
  Futures Returns", Review of Finance 17(1); NBER working paper 13249 (2007).
  31 commodity futures from 1969 to 2006.
  - Futures risk premiums vary across commodities and over time with the
    level of inventories, as the theory of storage says.
  - The convenience yield falls, non-linearly, as inventories rise.
  - The basis and past returns reflect inventories and carry information
    about the premiums. Backwardation strategies earn partly by picking
    commodities whose inventories are low.
  - CXO Advisory's summary of the 2007 version: a portfolio long the
    highest-basis futures and short the lowest earned 10.2% a year. That is
    a cross-sectional result.
- **Szymanowska, de Roon, Nijman and van den Goorbergh (2014),** "An Anatomy
  of Commodity Futures Risk Premia", Journal of Finance 69(1), 21 commodity
  futures. Sorting on the basis and other characteristics gives spot premia
  of 5% to 14% a year and term premia of 1% to 3%. One basis factor explains
  the cross-section of spot premia.
- **Koijen, Moskowitz, Pedersen and Vrugt (2018),** "Carry", Journal of
  Financial Economics 127(2). Carry, the return if prices don't move, is
  measured in advance. It predicts returns in the cross-section and over
  time in commodities, among other assets. Carry strategies lose together,
  in global recessions.
- **Gorton and Rouwenhorst (2006),** "Facts and Fantasies about Commodity
  Futures", Financial Analysts Journal 62(2). An index of fully collateralized
  commodity futures from 1959 to 2004 matched equities' return and Sharpe
  ratio, and was negatively correlated with stocks and bonds and positively
  with inflation.
- **Erb and Harvey (2006),** "The Strategic and Tactical Value of Commodity
  Futures", Financial Analysts Journal 62(2). The average single commodity
  future earned about no excess return, and their returns were largely
  uncorrelated. The term structure has historically earned above-average
  returns.

**The doubts.**
- **The evidence is mostly cross-sectional.** Gorton, Hayashi and
  Rouwenhorst, and Szymanowska et al., sort many commodities against each
  other. This rule times one, and Fama and French found time-varying premiums
  in only 5 of 21.
- **Crude's premium changed after 2005.** Hamilton and Wu (2014), "Risk
  Premia in Crude Oil Futures Prices", Journal of International Money and
  Finance 42 (NBER working paper 19056, 2013): since 2005 the compensation to
  the long side has been smaller on average and more volatile, consistent
  with index investors having become more important than commercial
  hedgers. The window starts in 2008.
- McLean and Pontiff (2016): anomaly returns are about 58% lower after
  publication.
- **The inventory papers' samples end by 2006.** Hamilton and Wu's and
  Koijen et al.'s working papers (both 2013) reach into the window's first
  years. The holdout (from 2023-10) is after all of them.
- **USO isn't a constant instrument** (see "USO" above): from April 2020 it
  held contracts across many months, so its returns depend less on the
  front spread the inventories best explain.

## The instruments

- **USO,** on the master's split-adjusted closes. It pays no distributions,
  so its price is its total return. Its actual share price, for the
  per-share costs, is the master's close divided by 8 up to 2020-04-28.
- **BIL_TR,** BIL with its income (R5's `tr_closes`): the vendor's
  total-return index to 2026-03-06, then IBKR's adjusted closes. Every
  excess return is over it, as in R5.
- **S0_TR,** S0 with its ETFs' distributions, exactly as R5 built it. S0 on
  the usual basis runs too, to check that the harness reproduces R4's and
  R5's 0.268623.

## The rules

**When every rule decides and trades.**
- Each rule decides on the walk-forward's first decision day (2008-01-02)
  and on each month's last NYSE session, as R5's do.
- **It reads the latest week that ended at least 7 days before the decision
  day.** EIA publishes a week's figures on the Wednesday after it ends, at
  10:30 ET, or a day or two later in holiday weeks. The 7 days make the rule
  independent of each release's exact date; "When each week was published"
  above checks it against the archive.
- The harness's lag 1 trades at the next session's close. Between decisions
  the book is left alone.
- **In oil, a rule holds USO at 37% of NAV and BIL at 62.5%; out of it, BIL
  at 99.5%.** The 0.5% is the cash buffer of the earlier studies.

**The gauge, and the five-year norm.**
- Each rule's gauge is an inventory measure against its five-year norm: the
  measure divided by the norm, less 1. Below 0, inventories are scarcer than
  usual for the time of year.
- **The norm** for a week is the mean of the measure on the same calendar
  day in each of the previous five years. Each of those values is linearly
  interpolated between the weeks around it, and 29 February is read as the
  28th.
  - That is EIA's documented method for its natural gas storage
    comparisons. Its crude oil method isn't published, so "EIA's own
    five-year comparison" above checks this one against EIA's statements.
  - Five years is the span EIA's own report uses ("the five year average
    for this time of year"). It is a convention fixed by the report, not
    fitted here.

**The primary, chosen now: USO while US crude stocks are below their
five-year norm.**
- On each decision day, the gauge of US commercial crude stocks (excluding
  the SPR) in the week it reads, **as EIA published them then.**
  - Until the report of 2016-10-13, EIA's weekly figure included lease
    stocks, oil in tanks at producing leases (about 31 million barrels then).
    That report dropped them and restated the history, so today's series
    (WCESTUS1) excludes them throughout.
  - The figure as published is EIA's lease-inclusive weekly series
    (W_EPC0_SAX_NUS_MBBL), which runs to the week ending 2016-09-30. The
    archive's reports match it from 2011-07-29 (above); before that, with no
    archive, it is taken as what was published.
  - So weeks to 2016-09-30 are gauged on the lease-inclusive series against
    a norm on the same basis, and later weeks on today's series. On today's
    series alone, 2 decisions would differ (2014-08-29 and 2014-09-30).
- Below 0, hold USO at 37% and BIL at 62.5%. At or above 0, hold BIL at
  99.5%.
- **Why:**
  - It is the theory of storage's signal, as Gorton, Hayashi and Rouwenhorst
    tested it: low inventories, high convenience yield, a backwardated curve
    and a higher premium for the long side.
  - The measure and the norm are EIA's headline comparison, which the market
    reads every Wednesday.
  - It has no fitted parameter. Its fixed choices are the five years (EIA's)
    and the 7-day lag (for safety).
  - Long or in bills, not short. A short USO position needs a borrow fee,
    and none could be sourced (below).

**Why 37%.**
- The sleeve, while in oil, is sized to the fund's volatility target. The
  book targets 8% to 10% a year (PLAN §9), and its sleeves share the one
  gross budget by inverse volatility.
- USO's volatility before the first decision, 2006-04-12 to 2007-12-31
  (433 days), was 26.9% a year. 10% over 26.9% is 37.1%, rounded down to
  37%. Only data from before the window sets the size.
- **The worst day must be survivable.** USO's worst day since 2008 was
  −25.3% (2020-03-09). At 37% that is −9.4% of NAV, inside the −12% limit of
  criterion 8. If USO's volatility in the window runs higher than 2006–07's,
  as oil's crashes suggest, the sleeve runs above 10% while in oil; that is
  accepted rather than tuned away.

**Variants (2 trials):**
- **V1, Cushing's stocks:** the same rule on the stocks at Cushing,
  Oklahoma, where NYMEX's WTI contract delivers. The front spread is set
  there, so it is the theory's most direct gauge for WTI. Cushing's series
  starts on 2004-04-09: before 2009-04 its norm averages the 3 or 4 years
  the data have (3 at the first decision).
- **V2, days of supply:** US stocks (as published, as for the primary) over
  refiners' weekly crude input, the same week, against its own five-year
  norm. On today's restated series alone, 5 decisions would differ.
  - Stocks in barrels grew with the shale boom (an average of 289 million
    barrels in 2008 and 489 million in 2017, on today's series) while
    refining barely did (14.65 and 16.57 million barrels a day).
  - Days of supply measure the stock against what refiners use.
- **Not a long/short version.** Shorting USO when stocks are above the norm
  would test the theory's other half. But the cost of borrowing USO couldn't
  be sourced for free: iBorrowDesk refused the connection, IBKR publishes its
  fees only to clients, and S1's 100 bps for USO was a guess.

No variant can replace a failed primary.

## Trials and the deflated Sharpe

- This study's trials: the primary and 2 variants, 3. With the 36 already
  spent (S1's 8, S3a's 6, R2's 9, R3's 4, R1's 3, R4's 3, R5's 3),
  **n = 39**.
- The trials' Sharpe variance comes from all 39: the 36 in R5's trials.csv
  and these three.
- The reference books (USO held at 37%; S0_TR; S0 on the usual basis) and
  the cost runs below are not trials.

## Costs (through the C++ walk-forward)

S1's CostModel, with:
- IBKR Pro Fixed commission ($0.005 a share, $1 minimum, 1% cap), as the
  harness computes it;
- square-root impact (k = 0.5) on the master's volumes;
- no borrow (nothing is short);
- idle cash earning nothing;
- and these half-spreads:

| ETF | Half-spread charged | Why |
|---|---|---|
| USO | **10 bps** | Half a one-cent spread plus the $0.005 commission, at the actual share price, is 2.8 bps at the median session from 2008 and 9.0 at the 90th percentile; 12.6 at 2016's low of $7.96. The harness charges the commission on the split-adjusted price, an eighth of the true one before the split, so the 10 bps carries the commission too. |
| BIL | 1 bp | R1's and R5's |
| S0_TR's other ETFs | 2 bps (S1's default) | as R5's S0_TR |

- **USO's actual price ran from $2.13 (2020-04-28) to $161.86
  (2026-09-15).** Its median price was $10.36 to $16.86 in 2015–2019, when half
  a cent was 3 to 5 bps; it was $35 to $84 in 2008–2014 and $47 to $126 since
  2021.
- **The 10 bps overcharges most years** and undercharges the days in April
  2020 when USO traded at $2 to $4 (up to 46.9 bps with the commission). A
  constant is what the harness takes. A switch trades 37% of NAV in USO and
  as much in BIL, about 0.04% of NAV.
- **USO is deep:** a median of $133M to $807M traded a day in each year from
  2008. The square-root impact on a few hundred thousand dollars is under a
  basis point.
- **The SEC's fee on sales isn't charged separately:** under 0.3 bps at every
  rate R4 checked, inside the margin.
- **USO's price strayed from its NAV in April 2020.** Its close was 36.5%
  above NAV on 2020-04-21 (the vendor's NAV). The closing auction would have
  paid that premium; the backtest, on the close, does too.
- **Costs at 0 and 3 times these half-spreads and impact,** the commission at
  1×, are reported, not judged.
- **Sources:** the master's prices, ranges and volumes (`data.py`); IBKR's
  published Fixed schedule, as R4 checked it; R4's sources for the SEC's fee
  rates.

## Split and measures (as R5's)

- **Walk-forward:** decisions from 2008-01-02, in yearly test blocks on an
  expanding window, up to the holdout. The rules have nothing to fit.
- **Holdout:** the last 756 sessions of the panel ending 2026-10-16, so from
  2023-10-12. Run once, after the walk-forward is written down.
- **Measures:**
  - excess returns over BIL_TR, with Sharpe SE sqrt((1 + SR² / 2) / T);
  - the block bootstrap of 63-day blocks, 2,000 resamples, seed 21;
  - the deflated Sharpe with n = 39;
  - cost drag: the costs a year over the average value.
- **Reference books,** through the same harness, window and costs, not
  counted as trials:
  - **USO held:** USO at 37% and BIL at 62.5%, re-sized on the same decision
    days;
  - S0_TR, and S0 on the usual basis.
- **Reported for every book:**
  - the annual return and volatility, the max drawdown, the worst day and
    the worst month, the cost drag;
  - the months in oil, and the switches;
  - the correlations with S0_TR and with USO held;
  - the stress windows below.

## Pass criteria (walk-forward, the primary)

*Reviewed on 2026-10-22, before any run.* The review settled the draft's open
points, amended criterion 5 and added one reported measure (below):
- **The stocks as published stand:** lease-inclusive to the week ending
  2016-09-30, today's restated series after. That is what the market read
  each Wednesday; the restated history wasn't out before 2016-10-13.
- **Long or in bills stands.** A short side needs a borrow fee for USO from
  2008 to 2023. No free source has one, and a guessed fee would be a
  parameter.
- **Criteria 1b, 9 and 10 stay as minimums,** though USO lost most of its
  value and its held book is easy to beat. Criteria 1 to 3 judge the sleeve
  on its own, and criterion 5 now has an absolute floor.
- **The holdout stays as written,** though the rules are in oil on about 90%
  of it, so it can hardly kill. The report says so first.
- **One measure is added, reported and not judged:** the primary against
  2,000 random timings (seed 21), each in oil on the same number of the
  walk-forward's decisions, all before costs. With USO held so weak a
  benchmark, it isolates the timing.

**Standalone:**
1. Excess Sharpe at least 0.40.
1b. **Excess Sharpe above USO held's** (USO at 37% throughout, the same
    harness and window). If the timing doesn't beat holding the same
    position, it buys nothing.
2. Deflated Sharpe at least 0.90 (n = 39).
3. Block-bootstrap 5th percentile of the excess Sharpe above 0.
4. Every variant's excess Sharpe above 0.
5. **Max drawdown no deeper than 60% of USO held's** over the same window,
   as S3a, R4 and R5 judged timing against the asset held, **and no deeper
   than −25%.** (*Amended at review, before any run:* USO lost most of its
   value, so 60% of its held book's drawdown is a loose bar. −25% is the
   median worst drawdown, over the window's 15.8 years, of a book at the
   size's 10% volatility that just meets criterion 1's Sharpe: −25.8% in
   4,000 simulated paths.)
6. Cost drag at most 1% a year.
7. No day outside the mandate.

**Crashes, against holding USO:**
8. Worst day no worse than −12% of NAV (R3's tolerance).
9. Worst calendar month no worse than USO held's.
10. **In each stress window, a return no more than 0.25 points below USO
    held's** (R5's tolerance for a switch's costs). The windows:
    - **R3's eight, the fund's crashes:**
      - 2008 crisis: 2008-09-15 to 2008-11-20
      - 2010 flash crash: 2010-04-23 to 2010-07-02
      - 2011: 2011-07-22 to 2011-10-03
      - 2015–16: 2015-08-17 to 2016-02-11
      - February 2018: 2018-01-26 to 2018-02-09
      - late 2018: 2018-09-20 to 2018-12-24
      - 2020 crash: 2020-02-19 to 2020-03-23
      - calendar 2022
    - **Oil's crashes,** every fall of 40% or more in WTI's spot price from a
      closing high to a closing low, the swings split where the price
      rebounds 25% from its low (`data.crashes`; the spot is floored at $1,
      since it settled at −$36.98 on 2020-04-20):
      - 2008-09-22 to 2008-12-23 (−75%)
      - 2013-09-06 to 2015-03-17 (−61%; the high stood from 2013 until
        the 2014 fall began)
      - 2015-10-09 to 2016-01-20 (−46%)
      - 2018-06-27 to 2018-12-27 (−43%)
      - 2019-04-23 to 2020-03-30 (−79%)
      - 2020-04-03 to 2020-04-20 (to the $1 floor)
      - 2022-03-08 to 2023-03-17 (−46%)
    - None falls in the holdout.

**Against the fund's book:**

11. The 50/50 book of S0_TR and the primary, with daily returns mixed as in
    the earlier studies, meets both:
    - a higher excess Sharpe than S0_TR's;
    - in each of R3's eight windows, a loss no worse than S0_TR's own.
12. **Daily return correlation with S0_TR at most 0.50** (S1's limit).

Reported, not judged:
- the correlation with USO held;
- each variant against USO held;
- the 50/50 book in oil's crashes;
- the cost runs;
- the primary against random timing (*added at review, before any run*):
  2,000 draws (seed 21), each holding USO at 37% on as many of the
  walk-forward's decisions as the primary did, chosen at random, from the
  daily returns of USO and BIL_TR before costs, each draw's positions held
  over the same sessions as the primary's would be. Reported: the share of
  draws whose excess Sharpe is below the primary's at 0× costs.

**Holdout kills:** an excess Sharpe below −0.5, a drawdown deeper than USO
held's over the holdout, or a day worse than −12%.

**Verdict.**
- **Pass:** every criterion and no holdout kill. Then shadow mode (PLAN §9,
  step 7).
- **Fail:** the report says which criteria failed, and the primary isn't
  taken further. No variant replaces a failed primary.

## Known before the run

So the reader can weigh the result:
- **Oil's history from 2008 to 2023 is public,** and so is USO's: a share
  that traded at $117.48 in 2008 traded at $2.13 on 2020-04-28, before its
  split. Holding it lost most of its value to the falls and to contango.
  USO held at 37% is therefore a weak benchmark for criterion 1b.
- **The rules sit out most of the walk-forward.** The primary is in oil on
  24% of its sessions, V1 on 41% and V2 on 33%. US stocks grew with the
  shale boom faster than their trailing five-year norm, so the gauge sat
  above it for most of 2010 to 2017.
- **The gauge lags demand shocks.** Inventories build only after demand
  falls, so the primary held USO through much of oil's crashes of 2008,
  2018, 2020 and 2022:

  | Window | Primary | V1 (Cushing) | V2 (days of supply) |
  |---|---|---|---|
  | 2008 crisis (R3's) | 73% | 100% | 0% |
  | Oil crash 2008-09-22 to 2008-12-23 | 47% | 76% | 0% |
  | Oil crash 2013-09-06 to 2015-03-17 | 0% | 60% | 55% |
  | Oil crash 2015-10-09 to 2016-01-20 | 0% | 0% | 0% |
  | February 2018 (R3's) | 0% | 55% | 55% |
  | Late 2018 (R3's) | 47% | 100% | 100% |
  | Oil crash 2018-06-27 to 2018-12-27 | 71% | 100% | 100% |
  | Oil crash 2019-04-23 to 2020-03-30 | 19% | 81% | 55% |
  | 2020 crash (R3's) | 100% | 100% | 100% |
  | Oil crash 2020-04-03 to 2020-04-20 | 100% | 100% | 100% |
  | 2022 (R3's) | 100% | 100% | 100% |
  | Oil crash 2022-03-08 to 2023-03-17 | 88% | 95% | 88% |

  The share of each window's sessions in oil, from the inventories alone.
  The rules were out of oil in R3's 2010, 2011 and 2015–16 windows.
- **The norm trails the level.** It averages five past years, so it lags a
  trend: the shale years kept stocks above it, and the glut of 2020–21 keeps
  it high until 2025–26, which puts later stocks below it.
- **The holdout is mostly USO held.** Stocks were below their norm on 32 of
  the holdout's 36 decisions (V1 36, V2 33), so the rules are in oil on
  about 90% of its sessions. The holdout's price ranges, printed for the
  costs, show USO's rise in 2026. The holdout can only kill, and it is
  unlikely to.

## How it would be traded (not tested here)

- **On the first session of each month,** the daily run (15:35 ET) reads
  EIA's weekly figures. The latest week that ended at least 7 days before
  the month's last session was published the Wednesday before, at 10:30 ET,
  or a day or two later in holiday weeks.
  - If the gauge's sign changed, the run sends market-on-close orders for
    USO and BIL by 15:45 ET (`auction.py`'s cutoff), sells first.
  - Otherwise it trims to the targets.
- **Both legs go through the C++ check** (`dv02_engine.risk.check`). USO is
  an ETF, counted at market value; gross stays at the 99.5% held, and the
  sale funds the purchase in the same auction, so cash stays at or above 0.
- **Beside S0** the sleeve gets its share of the one gross budget, the 50/50
  case criterion 11 tests.
- **EIA's figures would be recorded point in time:** the weekly table as
  published, into the data lake (`src/data/lake.py`), with when it was read.
- **USO's premium to NAV** reached 36.5% on 2020-04-21. Production should
  check the premium before buying in the auction; the test doesn't.
