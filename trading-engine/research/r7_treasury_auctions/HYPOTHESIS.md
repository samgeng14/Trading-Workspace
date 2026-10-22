# R7, the Treasury auction cycle: hypotheses and test plan

Written on 2026-10-22, before any backtest in this folder ran. This file is
only appended to after the results (docs/PLAN.md §9, research gate, step 1;
research/README.md).

Already read:
- R6's report;
- R5's hypothesis and report, and R4's hypothesis;
- research/README.md;
- R6's trials.csv, which lists the 39 trials counted so far.

What was looked at before this was written:
- the papers below;
- Treasury's two auction sources;
- what `python data.py` prints:
  - the auctions' coverage, and the two sources against each other;
  - when each auction was announced and when its bids closed;
  - the auctions on early-close days, and the special auctions;
  - where each new issue sits against the ETFs' index ranges;
  - where each term's auctions fall in the month;
  - the sessions each rule would hold its ETF, from the calendar alone, and
    how many of them fall at the turn of a month;
  - the costs' inputs;
  - the arithmetic below, from published magnitudes.

No rule's return was computed, and no ETF return on or around any auction
date. The arithmetic uses only these:
- the papers' published magnitudes;
- each ETF's duration from its issuer (2026-10-20);
- each ETF's volatility from 2005 to 2007, before the first decision;
- R5's published walk-forward Sharpes for TLT, IEI and SHY held.

The period's market history is public.

## Before spending three trials: the recommendation is not to run this

**The bar.** 39 trials are counted, with a Sharpe variance of 0.0635 a year.
At n = 42 the expected best of 42 trials with no edge is a Sharpe of about
0.56. So a deflated Sharpe of 0.90 needs a walk-forward Sharpe of about 0.89.
Every failed trial raises the bar for the studies after it.

**The literature at full strength, in the ETFs' prices, cannot reach it**
(`data.py`, "the literature in each ETF's price"). That is before any decay
since publication, on the rules below:

| Rule | Runs a year | In the ETF | Cost a run | Gain a run (LYZ / FNR) | Sharpe gross | Sharpe net |
|---|---|---|---|---|---|---|
| Primary: IEI after 3-, 5- and 7-year auctions | 23.1 | 51% | 13.2 bps (5.3 of it impact) | 10.3 / 10.8 bps | 0.99 / 1.03 | 0.03 / 0.07 |
| V1: SHY after 2- and 3-year auctions | 22.5 | 45% | 7.2 bps | 4.2 / 3.6 bps | 1.11 / 0.97 | −0.50 / −0.63 |
| V2: TLT after 20- and 30-year auctions | 12.1 | 27% | 7.9 bps | 22.1 / 0.1 bps | 0.68 / 0.10 | 0.47 / −0.11 |

- **How the gain is read.** "LYZ" is half of Lou, Yan and Zhang's 5-day
  pre/post difference for the nearest tenor, turned into yield at that note's
  duration and back into the ETF's price at the ETF's own. "FNR" is Fleming,
  Nguyen and Rosenberg's day +5 yield change, adjusted for the day of the
  month.
- **How the Sharpe is read.** It includes each ETF's own premium, held at
  random for as many sessions: IEI 0.24, SHY 0.17, TLT 0.10.
- **A run's cost.** BIL is sold and the ETF bought, and back five sessions
  later. That is the half-spreads and commissions of four trades, plus the
  square-root impact of the two ETF trades on a $1M book. IEI traded only $7M
  to $23M a day from 2008 to 2014.
- **The best case is V2 at 0.47,** and it rests on the 10-year's effect in
  LYZ, their weakest (t = 1.78). FNR find that effect gone once yields are
  adjusted for the day of the month.

**After 2009 the effect largely went.**
- FNR (2024) find the post-auction gain on dealers' auction purchases
  significant to 2009, and insignificant in 2010–14 and 2015–20. It faded as
  investment funds replaced dealers at auctions.
- McLean and Pontiff (2016) put anomalies' returns 58% lower after
  publication. LYZ's working paper dates from 2011.
- The walk-forward runs from 2008 to 2023, almost all after the change.

**A further confound.** The 5- and 7-year notes are auctioned at the end of
the month. So 47% of the primary's sessions in IEI fall in the last or first
three sessions of a month, against 29% of all sessions. That is where Etula
et al.'s month-end pattern sits, so a gain there could be the month's effect,
not the auction's.

**So no rule here plausibly clears 0.89.** Running it would spend three
trials and raise the bar for whatever comes next. The rest of this file is
the complete plan, in case the review decides otherwise.

## Data, all free and keyless (`data.py`)

| Source | What | Coverage |
|---|---|---|
| Fiscal Data's "Treasury Securities Auctions Data" (api.fiscaldata.treasury.gov), cached in `research/_data/treasury_auctions/` | one row an auction: the announcement, auction and issue dates; the original term and whether it reopens an issue; the offering amount; the results (bid-to-cover, high yield, the indirect bidders' award) | 1,907 note and bond auctions, 2005-01-12 to 2026-10-22 |
| TreasuryDirect's auction data (treasurydirect.gov, TA_WS), one file a year and type | the same auctions, a second copy, with the time competitive bids closed | the same |
| The ETF master and the vendor files, through R5's `data.py` | TLT, IEI, SHY and BIL with their income (`tr_closes`); prices, ranges and volumes for the costs | as R5's |

What the checks found:

**The auctions.**
- **1,512 nominal fixed-coupon auctions,** the securities the ETFs hold,
  after 240 TIPS and 155 floating-rate notes are left out:

  | Term | Auctions | Schedule |
  |---|---|---|
  | 2-year | 251 | monthly |
  | 3-year | 225 | monthly from November 2008 |
  | 5-year | 260, 7 of them reopenings | monthly |
  | 7-year | 223 | monthly from 2009-02-26 |
  | 10-year | 252, 165 of them reopenings | monthly |
  | 20-year | 79 | monthly from 2020-05-20 |
  | 30-year | 218, plus four bonds of 2007–08 first issued as 29 years 9 months and counted as 30-year | quarterly to 2008, monthly from 2009 |

- **The two sources agree on all 1,512,** matched on CUSIP and auction date:
  every announcement and issue date, term, reopening flag, offering amount,
  bid-to-cover ratio, high yield and indirect award.

**When each auction was known.**
- **Each auction is announced 0 to 6 sessions before it** (median 4): 1,497
  of 1,511 two or more sessions ahead, 10 one session ahead.
  - Treasury also publishes a tentative schedule at each quarterly refunding.
- **Four were announced on their own auction day:**
  - two 10-year reopenings on 2008-10-08, during the crisis;
  - two $25 million special reopenings, 10-year on 2019-06-21 and 20-year
    on 2021-12-02.
- **Competitive bids closed at 13:00 ET** for 1,321 auctions, earlier for
  70, and the time isn't given for 121 (2005–06). Every result was out before
  the 16:00 closing auction.
- **Five auctions fell on early-close days,** Christmas Eve's 13:00 close,
  before the 15:35 run: 2014-12-24 (7-year), 2018-12-24 (2-year), 2019-12-24
  and 2024-12-24 (5-year), 2025-12-24 (7-year).
- **So the rules use 1,502 of the 1,511 auctions to the panel's end:** each
  announced by the close of the session before it, at least $1bn, and not on
  an early-close day.

**Where new issues sit against the ETFs' ranges** (the indices rebalance at
month-end):
- A new 3-year note has 2.95 to 2.97 years left at its first month-end. That
  puts it in SHY's 1–3 year range, not IEI's 3–7.
- A new 20-year bond has 19.87 to 19.96 years, just under TLT's 20 or more.
- The rules take both as supply at the edge of the range. Dealers hedge an
  auction with nearby maturities, and Beetsma et al. find that unauctioned
  bonds near the auctioned maturity behave alike.

**Where the auctions fall in the month** (from 2008; sessions before the
month's last):

| Term | Median position | In the month's last 3 sessions |
|---|---|---|
| 2-year | 4 before | 5 of 215 |
| 5-year | 3 before | 83 of 224 |
| 7-year | 2 before | 145 of 223 |
| 3-, 10- and 30-year | 13 to 15 before, mid-month | none |
| 20-year | 7 before | none |

**What each rule would hold,** from the calendar alone, 5 sessions after each
auction, overlapping runs merged:

| Rule | Walk-forward sessions in the ETF | Holdout | Runs a year | In the turn of a month |
|---|---|---|---|---|
| Primary (IEI) | 2,030 of 3,971 (51%) | 53% | 23.1 | 47% |
| V1 (SHY) | 1,792 (45%) | 47% | 22.5 | 40% |
| V2 (TLT) | 1,071 (27%) | 44% | 12.1 (24 a year from 2020) | 3% |

"In the turn of a month" is the share of the rule's sessions in the ETF that
fall in a month's last three sessions or the next month's first three.
Those make up 29% of all sessions.

**Borrow fees.** No free source keeps their history: IBKR's public file of
shortable shares and fees is today's only, and R6 found iBorrowDesk refusing
connections. So no rule here is short, though LYZ's strategy was.

## The mechanism, and what the literature says

**Dealers' limited balance sheets.** Treasury auctions are large, regular and
announced in advance. Primary dealers must bid, and they absorb the new supply
until end investors take it over. If their risk-bearing capacity is limited
and investors' capital moves slowly, prices fall into the auction and recover
after it, even though everyone knows it is coming.
- **Lou, Yan and Zhang (2013),** "Anticipated and Repeated Shocks in Liquid
  Markets", Review of Financial Studies 26(8). Its working paper (LSE FMG
  discussion paper 684, June 2011) was read.
  - The sample is January 1980 to June 2008: 332 2-year, 210 5-year and 132
    10-year note auctions.
  - Prices fall in the 5 to 10 days before an auction and recover over the 5
    to 10 days after.
  - The 5-day return before an auction trails the 5-day return after it by
    8.89 bps for 2-year notes (t = 2.93), 22.54 for 5-year (t = 3.67) and
    23.84 for 10-year (t = 1.78).
  - Auction yields sit above the secondary market's by 4.90 bps for 2-year
    notes (at 5 days), 4.04 for 5-year and 2.66 for 10-year. That puts the
    issuance cost at 9 to 18 bps of the auction size.
  - A duration-hedged long/short around 2-year auctions (short before, long
    after, 10 days each) earned 8.62 bps a cycle, a Sharpe of 1.08. It was
    1.44 in 1998–2008, and about 0.95 net of 1997–2000 spreads (35 bps a
    year). It needs shorting and twice the gross the fund's mandate allows.
- **Fleming, Nguyen and Rosenberg (2024),** "How Do Treasury Dealers Manage
  Their Positions?", Journal of Financial Economics 158. Read in its NY Fed
  Staff Report 299 version, first issued in 2007 by Fleming and Rosenberg and
  revised in March 2024.
  - The sample runs from 1990 to 2020, with yields adjusted for day-of-month
    effects. The adjustment is needed because 2- and 5-year notes are
    auctioned near month end and 10-year notes early in the month (Table 8).
  - 2-year yields stand 1.77 bps below the auction day's 5 days before and
    2.00 bps below 5 days after. For the 5-year: 2.33 and 2.57.
  - **The 10-year shows no significant effect before its auctions.** Its
    yields rise 1.1 to 3.4 bps on days 1 to 3 after, and fall 2 to 4 bps only
    on days 6 to 10.
  - **The post-auction gain dissipates after the crisis** (Table 10). The
    price appreciation on dealers' auction purchases is significant before
    2007 and in 2007–09. It is insignificant in 2010–14 and in 2015–20, for
    2-, 5- and 10-year notes alike.
  - Dealers' share of coupon auctions fell from over 50% to about 30% after
    2015. Investment funds took it, and the more they buy at auction, the
    smaller the post-auction gain (Table 11).
- **Fleming and Rosenberg's 2007 version,** as Beetsma et al. summarize it:
  yields rise 1 to 1.5 bps before auctions and fall 1 to 2.5 bps after.
- **Beetsma, Giuliodori, de Jong and Widijanto (2016),** "Price Effects of
  Sovereign Debt Auctions in the Euro-zone: The Role of the Crisis", Journal
  of Financial Intermediation 25. Read in its working paper version
  (Tinbergen Institute 13-150, 2013).
  - The sample runs from 1999 to 2012.
  - Italian yields rise before auctions by up to 3.5 bps for 5- and 10-year
    debt and almost 3 bps for 2-year, and fall after.
  - German yields rise by up to about 2.5 bps (5-year).
  - The Italian pattern is largely confined to the crisis from mid-2007,
    with little before.
- **Sigaux (2024),** "Trading Ahead of Treasury Auctions", Journal of Banking
  and Finance 158 (ECB working paper 2208, 2018). In Italian Treasuries,
  meetings with primary dealers and auction announcements explain a 2.4 bps
  rise in yields (its abstract).
- **The turn of the month.** Etula, Rinne, Suominen and Vaittinen (2020),
  "Dash for Cash: Monthly Market Impact of Institutional Liquidity Needs",
  Review of Financial Studies 33(1).
  - Treasury yields rise into the month's end, stay elevated until T−2 (the
    last day selling settles in the month) and reverse from T−1.
  - They left the 2- and 5-year notes out of that analysis because those
    auctions fall near month end.
- **McLean and Pontiff (2016):** anomaly returns are about 58% lower after
  publication.

**What the window holds.** The walk-forward (2008 to 2023) lies almost wholly
after the change FNR find. The Fed was a large buyer of Treasuries through
much of it, and from 2015 investment funds took the auction share dealers
gave up.

## The instruments

- **IEI_TR, SHY_TR, TLT_TR and BIL_TR,** R5's total-return closes (the
  vendor's index to 2026-03-06, then IBKR's adjusted closes). Every excess
  return is over BIL_TR, as in R5 and R6.
- **S0_TR,** exactly as R5 built it. S0 on the usual basis runs too, to check
  that the harness reproduces 0.268623.

## The rules

**When every rule decides and trades.**
- **The auction must be announced by the decision.** At the close of the
  session before an auction of one of its terms, a rule targets its ETF. The
  harness's lag 1 buys it in the auction day's closing auction, after the
  auction's 13:00 result.
- **It holds the ETF for the 5 sessions after the auction.** It earns the
  close-to-close returns of sessions a+1 to a+5, then goes back to BIL at the
  close of a+5.
- **Runs that overlap merge:** an auction of the rule's terms inside a run
  extends it to 5 sessions after that auction.
- **The rules use only:**
  - auctions announced by the close of the session before;
  - offerings of at least $1bn, which leaves out the two $25 million special
    reopenings;
  - auctions not on an early-close day: the 15:35 run can't buy in a 13:00
    close (R4's rule).
- **A run due to end on an early-close day ends at the next session's
  close.**
- **In a run, 99.5% of NAV in the ETF; otherwise 99.5% in BIL,** as in the
  earlier studies.

**The primary, chosen now: IEI for the 5 sessions after each 3-, 5- or 7-year
note auction.**
- **The 5-year's effect is the literature's strongest:** LYZ's t of 3.67,
  and FNR's largest after the day-of-month adjustment.
- **The 5 sessions are LYZ's headline window,** set before the window:
  their sample ends in 2008-06. FNR's paper uses data from 2008 to 2020, so
  nothing here is chosen from it.
- **Long or in bills, not short:** no free source has the borrow fees, and a
  short side would also need the hedge LYZ used.

**Variants (2 trials):**
- **V1, SHY** after each 2- or 3-year note auction: the most frequent
  auctions, LYZ's longest series.
- **V2, TLT** after each 20- or 30-year bond auction: the long end, where a
  basis point of yield is worth the most. No paper above measures the 20- or
  30-year auction cycle, and the 10-year's effect is the weakest in LYZ and
  absent in FNR's first five days.

No variant can replace a failed primary.

## Trials and the deflated Sharpe

- **This study's trials:** the primary and 2 variants, 3. With the 39 already
  spent (S1's 8, S3a's 6, R2's 9, R3's 4, R1's 3, R4's 3, R5's 3, R6's 3),
  **n = 42**.
- **The trials' Sharpe variance** comes from all 42: the 39 in R6's
  trials.csv and these three.
- **The bar:** with the variance at 0.0635, a deflated Sharpe of 0.90 needs a
  walk-forward Sharpe of about 0.89.
- The reference books (IEI, SHY and TLT held; S0_TR; S0 on the usual basis)
  and the cost runs are not trials.

## Costs (through the C++ walk-forward)

S1's CostModel:
- IBKR Pro Fixed commission ($0.005 a share, $1 minimum, 1% cap);
- square-root impact (k = 0.5) on the master's volumes, rescaled as R5's;
- no borrow (nothing is short);
- idle cash earning nothing;
- R5's half-spreads: IEI 2 bps, SHY 1, TLT 1, BIL 1, S0's other ETFs 2.

**A rule that trades often pays these often.**
- A run sells BIL and buys the ETF, then reverses five sessions later:
  - IEI: about 8.0 bps of NAV in spreads and commissions, plus about 5.3 of
    impact on a $1M book;
  - SHY: 6.3 plus 0.9;
  - TLT: 6.0 plus 1.9.
- At 12 to 23 runs a year, that is 1% to 3% of NAV a year.
- IEI's impact is large because it traded only $7M to $23M a day from 2008
  to 2014.
- **Costs at 0, 1, 2 and 3 times these half-spreads and impact** are reported
  (the commission at 1×, which the CostModel can't scale). Criterion 6 judges
  the 2× run.
- **Sources:** the master's prices and volumes (`data.py`); IBKR's published
  Fixed schedule; R5's sources for the half-spreads.

## Split and measures (as R5's)

- **Walk-forward:** decisions from 2008-01-02, in yearly test blocks on an
  expanding window, up to the holdout. The rules have nothing to fit.
- **Holdout:** the last 756 sessions of the panel ending 2026-10-16, so from
  2023-10-12. Run once, after the walk-forward is written down.
- **Measures:**
  - excess returns over BIL_TR, with Sharpe SE sqrt((1 + SR² / 2) / T);
  - the block bootstrap of 63-day blocks, 2,000 resamples, seed 21;
  - the deflated Sharpe with n = 42;
  - cost drag: the costs a year over the average value.
- **Reference books,** through the same harness, window and costs, not
  counted as trials:
  - IEI, SHY and TLT held, each at 99.5%;
  - S0_TR, and S0 on the usual basis.
- **Reported for every book:**
  - the annual return and volatility, the max drawdown, the worst day and
    the worst month, the cost drag;
  - the sessions in the ETF, the runs and the switches;
  - the correlations with S0_TR and with the ETF held;
  - the stress windows below.

## Pass criteria (walk-forward, the primary)

**Standalone:**
1. Excess Sharpe at least 0.40.
1b. **Excess Sharpe above IEI held's** (IEI_TR at 99.5% throughout, the same
    harness and window). If the timing doesn't beat holding the ETF, it buys
    nothing.
2. Deflated Sharpe at least 0.90 (n = 42).
3. Block-bootstrap 5th percentile of the excess Sharpe above 0.
4. Every variant's excess Sharpe above 0.
5. **Max drawdown no deeper than 60% of IEI held's,** as S3a, R4, R5 and R6
    judged timing against the asset held, and no deeper than R6's −25%.
6. **Cost robustness** (R4's, in place of the 1% cap on cost drag, which a
    rule trading this often fails by arithmetic): with the half-spreads and
    impact doubled, an excess Sharpe still at least 0.20.
7. No day outside the mandate.

**Crashes:**
8. Worst day no worse than −12% of NAV (R3's tolerance).
9. Worst calendar month no worse than IEI held's.
10. **In each stress window where IEI held lost, a loss no deeper than IEI
    held's.**
    - The windows are R3's eight (calendar 2022 included) and R5's bond
      sell-offs: every rise of 100 bps or more in the 10-year yield, from
      `research/r5_bond_carry/data.py`'s `sell_offs`.
    - A window where IEI rallied is no crash for a bond sleeve, and a rule in
      the ETF half the time can't keep up with it there.
    - So those windows are reported, not judged.

**Against the fund's book:**

11. The 50/50 book of S0_TR and the primary, with daily returns mixed as in
    the earlier studies, meets both:
    - a higher excess Sharpe than S0_TR's;
    - in each of R3's eight windows, a loss no worse than S0_TR's own.
12. **Daily return correlation with S0_TR at most 0.50** (S1's limit).

Reported, not judged:
- each variant against its own ETF held, V2 against TLT held;
- the correlation with the ETF held;
- the cost runs;
- the stress windows where the ETF held rallied;
- **the primary against random timing** (R6's measure): 2,000 draws (seed
  21), each placing as many runs of the same lengths at random in the
  walk-forward, from the daily returns of IEI_TR and BIL_TR before costs.
  Reported: the share of draws whose excess Sharpe is below the primary's at
  0× costs.
- **The month-end confound:** the primary's excess return on its sessions in
  IEI at the turn of a month (the last 3 and first 3 sessions), against its
  other sessions in IEI.

**Holdout kills:** an excess Sharpe below −0.5, a drawdown deeper than IEI
held's over the holdout, or a day worse than −12%.

**Verdict.**
- **Pass:** every criterion and no holdout kill. Then shadow mode (PLAN §9,
  step 7).
- **Fail:** the report says which criteria failed, and the primary isn't
  taken further. No variant replaces a failed primary.

## Known before the run

So the reader can weigh the result:
- **The bar is a Sharpe of about 0.89,** and the literature at full strength
  gives these rules at most 0.03 to 0.07 net (the primary) and 0.47 (V2). See
  the arithmetic at the top.
- **The effect's own literature says it faded after 2009,** almost the whole
  walk-forward: FNR's Table 10.
- **The primary's sessions in IEI cluster at the turn of the month** (47%,
  against 29% of all sessions). Its result can't be read as the auction's
  alone without the month-end split reported above.
- **Duration's 2022 is public.** The primary holds IEI on about half of the
  sessions, so it takes about half of IEI's fall that year at random.

## How it would be traded (not tested here)

- **On the session before an auction of the rule's terms,** the daily run
  (15:35 ET) reads Treasury's announcements. Treasury publishes them 2 to 6
  sessions ahead, and a tentative schedule each quarter.
- **On the auction day,** the run sends a market-on-close switch from BIL to
  the ETF by 15:45 ET (`auction.py`'s cutoff), after the 13:00 result. Five
  sessions later it switches back.
- **On early-close days** nothing is sent: a run ends at the next session.
- **Both legs go through the C++ check** (`dv02_engine.risk.check`), sells
  first; gross stays at the 99.5% held, and cash at or above 0.
- **Beside S0** the sleeve gets its share of the one gross budget, the 50/50
  case criterion 11 tests.
- **The auction calendar would be recorded point in time** in the data lake
  (`src/data/lake.py`), from Treasury's announcements as they come.

## Decision at review: not run (2026-10-22)

The review accepted the recommendation above. Phase 2 was not run, so no
trial is counted: the protocol's count stays at 39, with the variance R6 left
(0.0635 a year). This file and `data.py` stay as the record: the literature's
magnitudes, the auction calendar's checks, and the arithmetic that rules the
study out. Revisit only if new evidence shows the effect returned after 2009
at several times the size Fleming, Nguyen and Rosenberg found.
