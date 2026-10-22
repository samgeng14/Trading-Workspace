# R5, timing Treasury duration by the curve's carry: hypotheses and test plan

Written on 2026-10-22, before any backtest in this folder ran. This file is
only appended to after the results (docs/PLAN.md §9, research gate, step 1;
research/README.md).

Already read:
- the reports and hypotheses of research/r1_calendar_macro,
  research/r3_vix_futures and research/r4_overnight;
- research/README.md;
- R4's trials.csv, which lists the 33 trials counted so far.

What was looked at before this was written:
- the papers below, and the issuers' product pages for TLT, IEI, SHY and BIL;
- what `python data.py` prints:
  - the yields' coverage, their gaps, and a second source for them;
  - the ETFs' coverage, their coupons from two sources, each year's income
    against the yields, and each year's empirical duration;
  - the carry each rule reads on its decision days, from the yields alone.
    So the months each rule would sit in bills are known (below);
  - the 10-year yield's sell-offs, which set the bond stress windows;
  - prices, dollar volumes and daily ranges, for the costs;
  - every S0 ETF's two total-return sources.

No rule's return was computed, and no bond return on any signal. Two things
are disclosed:
- **Before `data.py` was written, an exploratory comparison of the two coupon
  sources printed each ETF's total return over the stretch both cover:** TLT
  −13.7% (2016-10-20 to 2026-03-06), IEI +14.7% and SHY +17.3% (2017-08-04 to
  2026-03-06), BIL +23.0% (2016-10-19 to 2026-03-06). These are the reference
  books' returns over the walk-forward's last seven years and most of the
  holdout, on no signal.
- **The period's market history is public.** Long Treasuries rallied for most
  of 2008 to 2020 and fell sharply in 2022, while the curve still sloped up.
  With the positions counted below, that tells much of the primary's record
  before any run (see "Known before the run").

## Data, all free and keyless (`data.py`)

| Source | What | Coverage |
|---|---|---|
| FRED (St. Louis Fed), keyless CSVs, cached in `research/_data/fred/` | Treasury constant-maturity (CMT) yields from H.15: 1 and 3 months; 1, 2, 3, 5, 7, 10, 20 and 30 years. Also the 3-month bill on a discount basis (DTB3) | every series from before 2005, to 2026-10-20 |
| Treasury's daily par yield curve CSVs (home.treasury.gov), `research/_data/treasury/` | the same yields: a second source | 2005 to 2026-10-21 |
| The ETF master (`etf_prices_master.parquet`) | closes, highs, lows, volumes | TLT and SHY from 2002-07-29, IEI from 2007-01-11, BIL from 2007-06-01, to 2026-10-16 |
| The vendor files the master was built from (`data/market/raw/etf/`) | a gross total-return index | to 2026-03-06 |
| IBKR's dividend-adjusted closes (`etf_total_return_closes.parquet`) | a second reading of the coupons, and the only one after 2026-03-06 | from 2016-10-18 (BIL), 2016-10-19 (TLT), 2017-08-03 (IEI, SHY) |
| The issuers' pages (iShares: TLT, IEI, SHY; State Street: BIL), read 2026-10-22 | yield to maturity, duration, 30-day median bid/ask spread, expense ratio | as of 2026-10-20 or 21 |
| S1's panel (`s1_trend.load_panel`) and S0's targets | S0's other ETFs, for S0 | from 2005-01-03 |

What the checks found:

**The yields.**
- **FRED and Treasury agree on all 54,261 yields compared,** 2005 to 2026,
  tenor by tenor. They differ only in which days they carry:
  - FRED shows 0.00 for the 3-month on three days in December 2008 when
    Treasury's file is blank;
  - FRED fills Treasury's 30-year gap (2002-02-19 to 2006-02-08, while no
    30-year bond was issued) with another series: 994 values, which jump from
    4.67 to the par curve's 4.51 when it resumes on 2006-02-09 (the 20-year
    moved 3 bps that day). So TLT's yield uses the 20-year alone before
    2006-02-09, two years before the first decision;
  - at about 03:30 ET on 10-22, Treasury's file had 2026-10-21's curve and FRED
    didn't. Treasury's methodology page: the New York Fed takes indicative bid
    quotes "at or near 3:30 PM each trading day", and the curve is "usually
    available ... by 6:00 PM". FRED posts it the next day.
- **No yields on 40 NYSE sessions from 2005: Columbus Day and Veterans Day,**
  when the bond market shuts and NYSE doesn't. The rules carry the last yield
  over them; no decision day falls on one. Ten days have yields and no NYSE
  session: Good Fridays when the bond market opened for a jobs report, and
  the closures of 2007-01-02, 2012-10-29 and 2025-01-09.
- **The CMT yields bottom at 0.00, never negative.** DTB3, on a discount
  basis, reached −0.05. The 1-month sat at 0.00 on 100 days and the 3-month
  on 21, between 2008-12-10 and 2021-06-03.
- **The 3-month CMT less DTB3:** a median of 0 bps from 2008, and 0 to +21
  bps from the 5th to the 95th percentile (the bond-equivalent conversion,
  larger at higher rates). The carry uses the CMT, on the same basis as the
  long yields.
- **The largest daily moves from 2008 are known events:**
  - the 3-month: −81 bps on 2008-09-17 (the flight to bills after Lehman);
  - TLT's segment: +32 on 2020-03-17 (the dash for cash) and −30 on
    2009-03-18 (the Fed's first Treasury purchases announced);
  - the 2-year: −57 on 2023-03-13 (Silicon Valley Bank).
- **Each segment yield is the ETF's own, within 3 bps.** The issuers' yields
  to maturity on 2026-10-20 against the segment yields that day:
  - TLT 5.48% against 5.45%, the mean of the 20-year (5.47) and the 30-year
    (5.42);
  - IEI 4.95% against the 5-year's 4.94%;
  - SHY 4.76% against the 2-year's 4.79%.

**The ETFs and their coupons.**
- **The master's closes equal the vendor's on every common session:** 5,940
  for TLT and SHY, 4,818 for IEI, 4,721 for BIL. No session is missing from
  either, and no close from the master. IEI's vendor file has 1,138 blank rows
  before its first close (2007-01-11); they are dropped.
- **The two coupon sources agree.** Where both run, the vendor's index and
  IBKR's adjusted closes:
  - differ by a daily standard deviation of 0.20 to 0.36 bps, the index's
    two-decimal rounding;
  - differ in growth by −0.11 to −0.01 bps a year for TLT, IEI and SHY;
  - put each distribution on the same date, the amounts within $0.0068.
- **Each disagreement is an IBKR error.**
  - TLT, 2023-08-09: IBKR's adjustment factor dips for one day (0.87457,
    0.87241, 0.87457) and reverses the next. R4 found the same kind of blip
    in SPY's.
  - **BIL, 2024-12-19: IBKR's adjusted closes miss a $0.39 distribution.**
    The close fell $0.37 that day, which a T-bill fund's price can't do
    otherwise, and the vendor's index has it. IBKR's series understates BIL's
    2024 return by 0.42%. S1's panel takes BIL from IBKR after 2016-10-18, so
    every earlier study's excess returns in 2024 (in the holdout) carry it.
  - Two other BIL dates differ only at the $0.02 floor used to count
    payments: about two cents each, which the vendor's rounding puts on the
    other side of it (2017-02-01, 2020-05-01).
  - All four funds pay their January distribution in mid-December, so
    December has two and January none, in both sources.
- **The index's own daily factor carries the coupons.**
  - TLT's and IEI's indices show 12 distributions every year from 2008 to
    2025.
  - SHY's fall below $0.02 in the years of zero rates: 10 in 2012, 4 in 2013,
    10 in 2014, 4 in 2021. BIL paid none above $0.02 from 2009 to 2015 or in
    2021: its income was about nil after its 0.135% fee.
  - Picking out each payment above a floor, as R4 did for SPY, would drop
    those small ones. So the series take the index's daily factor. Its
    rounding is noise of a fraction of a basis point a day that doesn't
    accumulate.
- **Each year's income matches the yields.** Income here is total-return
  growth over price growth: the coupons, not a total return.
  - TLT 4.41% in 2008 against its segment's average yield of 4.32%; 2.41
    against 2.41 in 2016; 1.49 against 1.45 in 2020.
  - The income lags when yields move fast, since a fund's coupons come from
    bonds bought at older rates: TLT 2.34% in 2022 against 3.21%, SHY 3.51%
    in 2008 against 2.01%.
  - BIL's is the bill yield less its fee: 0.00 to 0.09% from 2009 to 2015,
    against bill yields of 0.03 to 0.15%; 5.15% against 5.18% in 2024.
- **The durations behave.** Minus the slope of each year's daily total
  returns on the segment yield's changes:
  - TLT: 13.7 in 2008, rising to 19.5 in 2020 as yields fell, and 14.9 in
    2026, against iShares' 14.85 on 2026-10-20. R² 0.83 to 0.96;
  - IEI: 3.8 to 4.5 (iShares: 4.21);
  - SHY: 1.2 to 1.9 (1.79);
  - BIL: about 0.1 (State Street: 0.11), its R² near 0.

**What the rules would hold.** The carry (the segment's yield less the
3-month) on each decision day, from the yields alone:
- **TLT (the primary): at or below 0 on 12 of the walk-forward's 190
  decisions,** August 2019 and November 2022 to September 2023. Also on 14 of
  the holdout's 36, October 2023 to November 2024. That is 4 switches in all.
  Its median is 2.40 points, its 10th percentile 0.36, and it came within
  0.25 of 0 on 4 decisions.
- IEI (V1): 21 and 25 decisions, 10 switches.
  - Walk-forward: 2019-03 to 2019-10, 2020-01 and 02, 2022-11 to 2023-09.
  - Holdout: 2023-10 to 2024-11, 2025-02 to 2025-11, 2026-02.
- SHY (V2): 23 and 29 decisions, 11 switches.
  - Walk-forward: 2008-01 and 02, 2019-03 to 2019-10, 2020-01 and 02,
    2020-06, 2022-12 to 2023-09.
  - Holdout: 2023-10 to 2026-02.
- **So the primary is the same book as TLT held except in September 2019
  and from December 2022 to the walk-forward's end:** about 11 months of 190.
  The timing's test in the walk-forward rests on those two episodes; the
  holdout adds another 14 months.

**The costs' inputs** are in "Costs" below.

**S0's ETFs (for criterion 11).**
- The vendor's index agrees with IBKR's for every ETF S0 holds that pays
  distributions. The gap is within ±1.34 bps a year (VNQ's), BIL's missed
  payment aside.
- IBKR's adjusted closes have one more blip, 2017-03-20, reversed the next
  day, in SLV, VNQ, VWO and VIXY.
- **For ETFs that pay nothing, the price is the total return:** GLD, SLV,
  USO, UNG, VIXY, IBIT and ETHA. IBKR's adjusted closes equal each one's
  price.
  - The vendor's "total return" column for IBIT and ETHA runs 3.0% and 1.8% a
    year above it, which no payment explains.
  - The master's ETHA closes are 3.00 times the vendor's, a split the vendor
    file doesn't carry.
  - Those columns aren't used.
- **The price-only basis leaves out each ETF's income** before IBKR's series
  begins. A year:

  | ETF | Income left out | ETF | Income left out |
  |---|---|---|---|
  | VNQ | 4.65% | IEI | 1.96% |
  | TLT | 3.93% | SPY | 1.89% |
  | VPU | 3.46% | SHY | 1.76% |
  | VEA | 3.12% | VIS | 1.61% |
  | VWO | 2.49% | VHT | 1.36% |
  | VFH | 2.17% | INDA | 0.75% |
  | | | QQQ | 0.58% |
  | | | BIL | 0.48% |

## The mechanism, and what the literature says

**The slope forecasts bond excess returns.** If the expectations hypothesis
held, the extra yield a long bond pays over a bill would be offset, on
average, by the capital losses from the rises in short rates the curve
forecasts. Holding duration would earn the bill rate and nothing more. The
evidence says otherwise:
- **Fama and Bliss (1987),** "The Information in Long-Maturity Forward
  Rates", American Economic Review 77(4): the spread of the forward rate over
  the spot rate forecasts one-year excess returns on 2- to 5-year bonds.
- **Campbell and Shiller (1991),** "Yield Spreads and Interest Rate
  Movements: A Bird's Eye View", Review of Economic Studies 58(3). When the
  curve is steep, long yields tend to fall afterwards, not rise as the
  expectations hypothesis requires. So the slope forecasts long bonds'
  excess returns.
- **Cochrane and Piazzesi (2005),** "Bond Risk Premia", American Economic
  Review 95(1): one combination of forward rates forecasts excess returns on
  bonds of every maturity, with in-sample R² up to about 0.44 (1964 to 2003).
- **Koijen, Moskowitz, Pedersen and Vrugt (2018),** "Carry", Journal of
  Financial Economics 127(2).
  - An asset's carry is its return if prices don't change: measured in
    advance, without a model.
  - Carry predicts returns in the cross-section and over time in global
    bonds and US Treasuries, among their asset classes.
  - Carry strategies lose together, in global recessions.
  - For a Treasury fund held against bills, the carry is the yield its bonds
    pay over the bill's, plus the roll-down as the bonds age along the curve.

**The doubts, out of sample.**
- **Thornton and Valente (2012),** "Out-of-Sample Predictions of Bond Excess
  Returns and Forward Rates: An Asset Allocation Perspective", Review of
  Financial Studies 25(10). Forward-rate forecasts gave an investor no
  systematic economic value out of sample, against assuming no
  predictability, and did worse over time.
- **Bauer and Hamilton (2018),** "Robust Bond Risk Premia", Review of
  Financial Studies 31(2). The evidence that anything beyond the curve's
  level, slope and curvature forecasts bond returns suffers from small-sample
  distortions, and is much weaker than it looked. The rules here use the
  slope's simplest form: the long yield less the bill.
- McLean and Pontiff (2016): anomaly returns are about 58% lower after
  publication (as R1 and R4 cite).
- Fama and Bliss's, Campbell and Shiller's, and Cochrane and Piazzesi's
  samples end before 2008. Koijen et al.'s working paper dates from 2013, so
  its sample overlaps the window's first years. The holdout (from 2023-10)
  is after all of them.

**Maturity.** Frazzini and Pedersen (2014), "Betting Against Beta", Journal
of Financial Economics 111(1): in Treasuries, Sharpe ratios fall almost
monotonically with maturity, so the short end pays more per unit of risk.
That is why the variants run the same rule on IEI (3 to 7 years) and SHY
(1 to 3).

**What the window holds.**
- The 3-month sat near 0 from December 2008 to December 2015 and from March
  2020 to March 2022. The carry then was the whole long yield, and every rule
  here held its bond.
- The Fed bought long Treasuries in several programs over the window, and
  its purchases are thought to have pushed term premia down. So in these
  years the slope may say more about the expected policy path than about a
  premium.
- The test therefore asks whether the slope still timed duration in a
  market where the Fed was a large buyer.

## The instrument: total returns (`data.tr_closes`)

The C++ walk-forward prices books close to close from one price panel. A bond
fund's return is mostly its coupon, so the bond ETFs enter as total-return
series.
- **TLT_TR, IEI_TR, SHY_TR and BIL_TR:** the vendor's total-return index to
  its last row (2026-03-06), then IBKR's adjusted closes, chained there.
  - Each level starts at the master's close on the panel's first date
    (2005-01-03; IEI 2007-01-11, BIL 2007-06-01), so the per-share
    commission starts at the right size.
  - The level climbs above the price as the coupons accrue. TLT's ends near
    twice its price, so the harness's per-share commission falls to about
    half its true size by then. At $0.005 a share on $77 to $172 that is
    under 0.33 bps a trade, for a rule that switches four times.
  - The volumes are rescaled by close over level, so the square-root impact
    sees the true dollar volume.
- **Every book is on this basis.** The studies so far have left out every
  ETF's distributions before IBKR's adjusted closes begin (S1's panel). For
  a bond fund that is most of the return: TLT's averaged 3.93% a year there.
  - **Excess returns are over BIL_TR,** BIL with its income, not S1's
    price-only BIL. The two differ by BIL's income from 2007 to 2016 (about
    1.9% in 2008, under 0.1% a year after) and by the payment IBKR misses in
    2024.
  - **S0 is valued the same way (S0_TR).** Each ETF that pays distributions
    is on the vendor's index to 2026-03-06 and IBKR's adjusted closes after.
    Those that pay none keep S1's panel's closes. S0's targets don't change.
  - S0 on the studies' usual basis is run too, and reported. It is the check
    that the harness reproduces R4's figure, 0.268623.

## The rules

**When every rule decides and trades.**
- Each rule decides on the walk-forward's first decision day (2008-01-02)
  and on each month's last NYSE session, by the exchange's calendar (known in
  advance).
- It reads that session's yields, quoted at 3:30 pm and published by 6 pm,
  before the next session's run.
- The harness's lag 1 trades at the next session's close. Between decisions
  the book is left alone.
- **Each rule holds 99.5% of NAV in one ETF,** the rest a cash buffer, as in
  the earlier studies.

**The primary, chosen now: TLT while its carry is positive.**
- On each decision day, carry = TLT's yield (the mean of the 20- and 30-year
  CMT yields) less the 3-month CMT yield.
- Above 0, hold TLT_TR at 99.5%. At or below 0, hold BIL_TR at 99.5%.
- **Why:**
  - It is Koijen et al.'s time-series carry signal for the long end, long or
    in bills. The trade earns the long yield over the bill if yields don't
    move, and the rule holds it only while that is positive.
  - It has no parameter: 0 is the breakeven at which an unmoved TLT earns
    what the bill does.
- **The roll-down is left out.** From 2008, the 30-year less the 20-year ran
  from −40 to +48 bps. At a duration of 16 that is worth at most about 0.8
  points a year, and the data can't pin down the 20-to-30-year curve without
  an interpolation.
- **Long or in bills, not short.** The mandate allows a short. But the claim
  is about the premium's presence, and the harness's idle cash earns nothing,
  which would charge a short the bill rate.

**Variants (2 trials):**
- **V1, IEI on its own carry:** the 5-year less the 3-month, holding IEI_TR
  or BIL_TR. The middle of the curve: steeper, and paying more per unit of
  risk than the long end (Frazzini and Pedersen).
- **V2, SHY on its own carry:** the 2-year less the 3-month, holding SHY_TR
  or BIL_TR. The short end, where the carry turns with the Fed's expected
  path.

No variant can replace a failed primary.

## Trials and the deflated Sharpe

- This study's trials: the primary and 2 variants, 3. With the 33 already
  spent (S1's 8, S3a's 6, R2's 9, R3's 4, R1's 3, R4's 3), **n = 36**.
- The trials' Sharpe variance comes from all 36: the 33 in R4's trials.csv
  and these three.
- The reference books (TLT, IEI and SHY held; S0) and the cost runs below
  are not trials.

## Costs (through the C++ walk-forward)

S1's CostModel:
- IBKR Pro Fixed commission ($0.005 a share, $1 minimum, 1% cap);
- square-root impact (k = 0.5) on the master's volumes, rescaled as above;
- no borrow (nothing is short);
- idle cash earning nothing;
- with these half-spreads:

| ETF | Half-spread charged | One cent of spread at its prices from 2008 | Its issuer's 30-day median spread | Median daily dollar volume |
|---|---|---|---|---|
| TLT | 1 bp | 0.58 to 1.30 bps ($171.57 on 2020-08-04, $77.11 on 2026-10-05) | 0.01% | $234M in 2008; $0.6B to $3.5B since 2010 |
| IEI | 2 bps (S1's default) | 0.75 to 0.95 bps | 0.01% | $7M to $23M a year from 2008 to 2014; $76M to $205M since 2019 |
| SHY | 1 bp | 1.15 to 1.24 bps | 0.01% | $62M to $420M |
| BIL | 1 bp (R1's) | 1.08 to 1.10 bps | 0.01% | $6M in 2008, $14M to $39M to 2016; $69M to $776M since 2018 |

- **The half-spreads are at least twice half a one-cent spread,** which is
  each ETF's tick and the issuers' current median.
- **IEI keeps S1's 2 bps** for its thin early years, when quotes wider than a
  cent are likely. Its rule's first switch is in 2019, by when it traded
  $76M a day.
- **BIL's daily range shows its quotes:** a median high-low range of 1 cent a
  day from 2018 and 2 cents from 2010 to 2017. It was 10 cents in 2008, when
  it traded $6M a day. Only V2 trades BIL in 2008: it starts in BIL in
  January and switches to SHY at the start of April. At a 10-bp half-spread
  those two trades would cost about 0.2%, once.
- **The commission,** at $0.005 a share: TLT 0.29 to 0.65 bps, IEI 0.37 to
  0.48, SHY 0.58 to 0.62, BIL 0.54 to 0.55.
- **The SEC's fee on sales isn't charged separately:** under 0.3 bps at
  every rate R4 checked for the window. It sits inside each half-spread's
  margin over half a cent.
- **The fund trades in the closing auction,** where no spread is paid. The
  half-spread stands for the auction's impact and the uncertainty of its
  price.
- A switch from TLT to BIL costs about 3 to 4 bps of NAV, both sides. The
  monthly re-sizing trims add a little.
- **Costs at 0 and 3 times these half-spreads and impact,** the commission at
  1× (the CostModel can't scale it), are reported, not judged.
- **Sources:**
  - the issuers' product pages (iShares, State Street), read 2026-10-22;
  - IBKR's published Fixed schedule, as R4 checked it;
  - the volumes, prices and ranges from the master (`data.py`);
  - R4's sources for the SEC's fee rates.

## Split and measures (as R1's, R3's and R4's)

- **Walk-forward:** decisions from 2008-01-02, in yearly test blocks on an
  expanding window, up to the holdout. The rules have nothing to fit; the
  harness is S1's so the numbers compare.
- **Holdout:** the last 756 sessions of the panel ending 2026-10-16, so from
  2023-10-12. Run once, after the walk-forward is written down.
- **Measures:**
  - excess returns over BIL_TR, with Sharpe SE sqrt((1 + SR² / 2) / T);
  - the block bootstrap of 63-day blocks, 2,000 resamples, seed 21;
  - the deflated Sharpe with n = 36;
  - cost drag: the costs a year over the average value.
- **Reference books,** through the same harness, window and costs, not
  counted as trials:
  - TLT, IEI and SHY held: each at 99.5%, re-sized on the same decision
    days;
  - S0_TR, and S0 on the usual basis.
- **Reported for every book:**
  - the annual return and volatility, the max drawdown, the worst day and
    the worst month, the cost drag;
  - the months in the bond, and the switches;
  - the correlations with S0_TR and with TLT held;
  - the stress windows below.

## Pass criteria (walk-forward, the primary)

*Reviewed on 2026-10-22, before any run.* The review settled three of the
draft's choices and amended criterion 5 (below):
- **The primary stays the sign of the carry,** although only 11 months of the
  walk-forward and 14 of the holdout test the timing. A rule scaled by the
  carry would use more of the signal. But it needs a scale, and any scale
  chosen now would be chosen knowing how 2022 went: the yields put TLT's
  carry at 1.86 points at the end of 2021, 2.20 in April 2022 and 0.11 in
  October, while long Treasuries fell sharply. The sign has no parameter. The
  report leads with how little of the record the timing decides.
- **Every book stays on the total-return basis.** A bond fund's coupon is the
  carry the rule bets on, so a price-only series would leave out the thing
  being tested. The 33 earlier trials' Sharpes were on the price basis; they
  set the deflated Sharpe's variance with these three all the same.
- **Criterion 11's 2022 window stays,** though "Known before the run"
  expects it to bind. A duration sleeve that would have deepened the fund's
  worst year is what the gate is there to stop.

**Standalone:**
1. Excess Sharpe at least 0.40.
1b. **Excess Sharpe above TLT held's** (TLT_TR at 99.5%, the same harness
    and window). If the timing doesn't beat holding TLT, it buys nothing.
    This turns on the 11 months the primary sits in bills.
2. Deflated Sharpe at least 0.90 (n = 36).
3. Block-bootstrap 5th percentile of the excess Sharpe above 0.
4. Every variant's excess Sharpe above 0.
5. **Max drawdown no deeper than 60% of TLT held's** over the same window,
   as S3a judged its overlays against S0 and R4 the night against SPY.
   (*Amended at review, before any run:* the draft asked only for a drawdown
   no deeper than TLT held's. A rule in TLT on 178 of its 190 decisions meets
   that unless it sits in bills at the wrong time, so it set no bar. The
   primary held TLT until its 2022-11-30 decision, through most of long
   Treasuries' fall from 2020, so this criterion will likely bind too.)
6. Cost drag at most 1% a year.
7. No day outside the mandate.

**Crashes, against holding TLT:**
8. Worst day no worse than −12% of NAV (R3's tolerance).
9. Worst calendar month no worse than TLT held's.
10. **In each stress window, a return no more than 0.25 points below TLT
    held's.** The 0.25 points allows for a switch's costs; where the primary
    holds TLT through a window, it is TLT held. The windows:
    - **R3's eight, the fund's crashes:**
      - 2008 crisis: 2008-09-15 to 2008-11-20
      - 2010 flash crash: 2010-04-23 to 2010-07-02
      - 2011: 2011-07-22 to 2011-10-03
      - 2015–16: 2015-08-17 to 2016-02-11
      - February 2018: 2018-01-26 to 2018-02-09
      - late 2018: 2018-09-20 to 2018-12-24
      - 2020 crash: 2020-02-19 to 2020-03-23
      - **calendar 2022**
    - **The bond sell-offs:** every rise of 100 bps or more in the 10-year
      yield, from its closing low to its closing high, the swings split at
      50-bp reversals (`data.sell_offs`):
      - 2009-03-18 to 2009-06-10 (+147 bps)
      - 2010-10-06 to 2011-02-08 (+134)
      - 2012-07-25 to 2013-12-31 (+161; the taper tantrum)
      - 2016-07-05 to 2017-03-13 (+125)
      - 2017-09-07 to 2018-11-08 (+119)
      - 2020-08-04 to 2021-03-19 (+122)
      - **2021-07-19 to 2022-06-14 (+230) and 2022-08-01 to 2022-10-24
        (+165)**
      - 2023-04-05 to 2023-10-19 (+168), judged to the walk-forward's end,
        2023-10-11
    - The holdout's sell-off (2024-09-16 to 2025-01-13, +116) is reported
      with the holdout.

**Against the fund's book:**

11. The 50/50 book of S0_TR and the primary, with daily returns mixed as in
    the earlier studies, meets both:
    - a higher excess Sharpe than S0_TR's;
    - in each of R3's eight windows, calendar 2022 among them, a loss no
      worse than S0_TR's own. Adding the sleeve must not deepen the fund's
      crashes.
12. **Daily return correlation with S0_TR at most 0.50** (S1's limit). A
    duration sleeve is worth adding to the fund only as a diversifier.

Reported, not judged:
- the correlation with TLT held;
- each variant against its own ETF held;
- the 50/50 book in the bond sell-offs;
- the cost runs.

**Holdout kills:** an excess Sharpe below −0.5, a drawdown deeper than TLT
held's over the holdout, or a day worse than −12%.

**Verdict.**
- **Pass:** every criterion and no holdout kill. Then shadow mode (PLAN §9,
  step 7).
- **Fail:** the report says which criteria failed, and the primary isn't
  taken further. No variant replaces a failed primary.

## Known before the run

So the reader can weigh the result:
- **The primary's standalone record is mostly TLT's.** The checks above
  count it in TLT on 178 of 190 walk-forward decisions. So criteria 1, 2, 3,
  5, 9 and 11 largely measure holding TLT from 2008 to 2023, which public
  market history describes.
- **Only criterion 1b isolates the timing,** and the walk-forward gives it
  two episodes: September 2019, and December 2022 to October 2023.
- **2022's sell-off came while TLT's carry was still positive.** The primary
  moves to bills only after its 2022-11-30 decision, so it holds TLT through
  nearly all of 2022. Criterion 11's 2022 window asks a 50/50 book with that
  sleeve to have lost no more than S0.

## How it would be traded (not tested here)

- **On the first session of each month,** the daily run (15:35 ET) reads the
  previous session's par yields from Treasury's file. Treasury publishes it
  by 6 pm the evening before; FRED posts it a day later.
  - If the carry's sign changed, the run sends a market-on-close switch
    between TLT and BIL by 15:45 ET (`auction.py`'s cutoff).
  - Otherwise it trims to 99.5%.
- **Both legs go through the C++ check** (`dv02_engine.risk.check`).
  - Gross stays at most the 99.5% held.
  - The sale and the purchase fill at the same close, and the basket puts
    sells first. The check counts cash on the trade date, so the purchase
    sized from the sale keeps cash at or above 0, with no borrowing.
- **S0 and the sleeve can't both hold 99.5% of the same NAV.** Together each
  gets its share, which is the 50/50 case criterion 11 tests.
- **What the shadow would show:** its month-end decisions against the yields
  Treasury published by the run's time, and the closing-auction fills on the
  switch days.
