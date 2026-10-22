# R2, volatility and positioning data: hypotheses and test plan

Written on 2026-10-21, before any backtest in this folder ran. This file is
only appended to after the results (docs/PLAN.md §9, research gate, step 1).

Already read: research/s1_trend and research/s3a_vol_overlay, which used the
same prices and costs. A second study, R1 (calendar and macro timing), runs
alongside with its own trials.

## Data, all free, with no key or login (`data.py` fetches and caches it)

| Source | Series | Coverage on disk | Use |
|---|---|---|---|
| Cboe daily index histories | VIX, VIX3M | VIX from 1990, VIX3M from 2009-09-18 | H1, H1b |
| CFTC Commitments of Traders, legacy futures-only | commercial and non-commercial positions, open interest | 1986 to the report of 2026-10-13 | H2 |
| On disk | the ETF price master and IBKR's dividend-adjusted closes, as s1_trend loads them | — | every book |

- **VIX3M's free history starts in September 2009.** Its predecessor VXV
  (from December 2007) isn't on Cboe's site, and FRED's copy is behind a bot
  check. So H1 and H1b can't be tested on 2008. Their stress tests use 2011,
  2015–16, late 2018, the 2020 crash and 2022 instead.
- **A COT report is as of Tuesday and published on Friday at 15:30 ET.** Here
  it is used from the close of the first trading day at least 7 calendar days
  after its Tuesday: the Tuesday after the release, never before the release.
  Holiday weeks that delay the release by a day are covered by that margin.
- Before 2016-10 the prices leave out dividends, as in S1's study, for every
  book alike. BIL's return then is near zero, which the T-bill rate also was
  from 2009 to 2015.

## H1: the VIX term structure as risk timing

**Mechanism.**
- VIX is the market's 30-day expected volatility; VIX3M is its 3-month one.
  Normally the curve slopes up: VIX below VIX3M, contango.
- When near-term fear jumps above the longer-term level, the curve inverts.
  That happens in acute stress: forced selling, margin calls, liquidity
  spirals, when the equity premium's realized path is worst and losses
  cluster.
- Leaving equities for T-bills while the curve is inverted should cut the
  worst drawdowns at a modest cost in return.

**The literature.**
- Simon and Campasano (2014): the VIX futures basis (futures against spot)
  predicts VIX futures returns.
- Johnson (2017), "Risk premia and the VIX term structure": the term
  structure's slope predicts variance-asset returns and relates to equity
  risk.
- Practitioners widely report that inversion flags stress. The academic
  evidence for timing the S&P with it is thinner than for predicting
  volatility assets, so the bar here is high.

**Rule.**
- At each close t: hold SPY at 99.5% while the signal is "contango", otherwise
  BIL at 99.5%.
- Decided on t's closes (VIX and VIX3M close at 16:15 ET) and traded at t+1's
  close (lag 1). A day with no change holds.

**Variants (4 trials):**
- The ratio VIX / VIX3M is compared with a threshold of 1.00 or 0.95; below
  the threshold is contango.
- The ratio is either that day's, or its mean over the last 5 days.

**Primary, chosen now: threshold 1.00, no smoothing.** It is the literal
definition of an inverted curve.

## H1b: the same signal as an overlay on S0

**Rule.**
- S0's latest target (its rebalance dates, scaled to 99.5%, as in the S3a
  study), with every risky weight moved to BIL while the curve is inverted
  (the ratio at or above 1.00, no smoothing).
- It trades on S0's dates and when the signal flips.

**Trials:** 1. It is H1's primary signal applied to the live model, the use
the fund would make of it.

## H2: COT hedging pressure on ETF proxies

**Mechanism.**
- Hedgers (producers, consumers, dealers) pay a premium to lay off risk.
- When commercials are heavily net short, speculators must be net long to
  take the other side, and they are paid for it: the futures price is set
  below the expected future spot, so long positions earn a premium.
- Keynes's and Hicks's normal backwardation, with hedging pressure as the
  state variable.

**The literature.**
- Bessembinder (1992): returns relate to hedgers' net positions.
- de Roon, Nijman and Veld (2000): hedging pressure predicts returns in 20
  futures markets, including financials.
- Hirshleifer (1990).
- Basu and Miffre (2013): hedging-pressure portfolios earned premia in
  commodities from 1992 to 2011.
- Kang, Rouwenhorst and Tang (2020) find the level premium weak once they
  control for a short-term liquidity premium earned by taking the other side
  of hedgers' recent trades. That is the reason for the change variant below.

**Markets and their ETF proxies** (CFTC codes, which stay continuous through
the 2022 renaming):

| ETF | Futures | Code |
|---|---|---|
| GLD | Gold, COMEX | 088691 |
| SLV | Silver, COMEX | 084691 |
| USO | WTI crude, NYMEX | 067651 |
| UNG | Henry Hub natural gas, NYMEX | 023651 |
| TLT | Treasury bonds, CBOT | 020601 |
| IEI | 5-year Treasury notes, CBOT | 044601 |
| SPY | E-mini S&P 500, CME | 13874A |
| QQQ | E-mini Nasdaq-100, CME | 209742 |
| VEA | MSCI EAFE, ICE US | 244041 (from 2011-06) |
| VWO | MSCI Emerging Markets, ICE US | 244042 (from 2011-06) |

**Hedging pressure** of a report: HP = (commercial short − commercial long) /
(commercial short + commercial long), between −1 and 1. A high HP means
hedgers are net short.

**Eligibility, the same in every variant.** An asset trades on a decision date
when both hold:
- it has 253 closes up to that date (S1's rule, with S1's UNG start after its
  2010 gap);
- it has 156 COT reports (3 years) usable by then.
VEA and VWO enter around 2014.

**Sizing (S1's long/flat conventions).**
- Each asset's signal is 0 or 1.
- Weight = signal / its 63-day volatility, scaled so every eligible asset at
  full signal would fill 99.5% of NAV.
- A 20% cap per name, and a 10% a year ex-ante volatility ceiling that only
  scales down.
- BIL holds the rest.
- Decisions every 21 trading days (monthly), lag 1. Long/flat, so gross can't
  drift over the mandate.

**Variants (4 trials):**
- **TS-156, the primary, chosen now:** long when the latest HP is above its
  median over the last 156 reports. It compares each market with its own
  history, which keeps financial and commodity markets comparable.
- **TS-52:** the same against the last 52 reports.
- **XS-level:** long the top half (rounded up) of eligible assets ranked by
  their latest HP level, as Basu and Miffre rank.
- **Change-4w:** long when commercials' net short position as a share of open
  interest rose over the last 4 reports (the liquidity premium of Kang et al.).

## Trials and the deflated Sharpe

- This study's trials: H1's 4, plus H1b's 1, plus H2's 4, is 9.
- With the 14 already spent (S1's 8, S3a's 6), n = 23 for the deflated
  Sharpe. R1's trials, running alongside, aren't known here: the real total is
  higher, so these deflated Sharpes are if anything generous.
- The trials' Sharpe variance comes from the 23: S1's and S3a's from their
  results files, and these nine.

## Costs and split (as S1's study)

- Costs: S1's CostModel through the C++ walk-forward (IBKR Pro commissions,
  2 bps half-spread, square-root impact k = 0.5), lag 1, the mandate checked
  every day.
- Excess returns over BIL; Sharpe SE sqrt((1 + SR² / 2) / T).
- **Walk-forward:** yearly test blocks on an expanding window, up to the
  holdout.
  - H2's decisions start 2008-01-02, as S1's.
  - H1's and H1b's start 2009-10-01, when VIX3M exists.
- **Holdout:** the last 756 trading days (from 2023-10-12), run once, after the
  walk-forward is written down.
- **Benchmarks** run through the same harness and window: SPY bought and held
  at 99.5% for H1, S0 for H1b, and S0 plus S1 blend long/flat for H2's
  correlations.

## Pass criteria (walk-forward, each hypothesis's primary)

**H1 (a standalone sleeve that must beat holding SPY):**
1. Excess Sharpe at least 0.40.
2. Deflated Sharpe at least 0.90 (n = 23).
3. Paired block bootstrap against SPY (63-day blocks, 2,000 resamples, seed 21,
   the same blocks for both): P(excess Sharpe ≥ SPY's) at least 80%.
4. Max drawdown at most 60% of SPY's (as depth).
5. All four variants with an excess Sharpe at least SPY's.
6. Cost drag at most 1% a year.
7. Loses less than SPY in each stress window:
   - 2011-07-22 to 2011-10-03
   - 2015-08-17 to 2016-02-11
   - 2018-09-20 to 2018-12-24
   - 2020-02-19 to 2020-03-23
   - calendar 2022
8. No day outside the mandate.

**H1b (an overlay that must improve S0), as S3a's criteria:**
1. Max drawdown at most 60% of S0's.
2. Excess Sharpe at least S0's minus 0.05.
3. Bootstrap P(drawdown shallower than S0's) at least 80%.
4. Bootstrap P(excess Sharpe ≥ S0's − 0.05) at least 80%.
5. Cost drag at most 0.5% a year.
6. Loses less than S0 in each of H1's stress windows.
7. No day outside the mandate.

**H2 (a standalone sleeve), as S1's criteria:**
1. Excess Sharpe at least 0.40.
2. Deflated Sharpe at least 0.90 (n = 23).
3. Block-bootstrap 5th percentile of the excess Sharpe above 0.
4. All four variants with a positive excess Sharpe.
5. Max drawdown no worse than −20%.
6. Positive in 2008 and in 2022.
7. Correlation with S0 at most 0.50, and the 50/50 book of S0 and H2 with a
   higher excess Sharpe than S0's.
8. Cost drag at most 1% a year.

Correlation with the S1 trend sleeve is reported for every book, not judged.

**Holdout kills:**
- H1: an excess Sharpe more than 0.5 below SPY's, or a drawdown deeper than
  SPY's.
- H1b: a drawdown deeper than S0's, or an excess Sharpe more than 0.5 below
  S0's.
- H2: an excess Sharpe below −0.5, or a drawdown worse than −15%.

**Verdict.**
- Pass: all criteria and no holdout kill. Then shadow mode (PLAN §9, step 7).
- Fail: the report says which criteria failed, and the primary isn't taken
  further. No variant replaces a failed primary.

## Deviations (appended 2026-10-21, before any backtest in this folder ran)

**COT reports the CFTC published late.** The 7-day rule above would use some
reports before their release, which the same paragraph rules out. Publication
stopped in three lapses in appropriations and after the ION Markets cyber
incident. Those reports are used from the first trading day at least 4
calendar days after their actual release: the margin the rule gives a Friday
release, which is used on the Tuesday. The release dates are the CFTC's own
announcements:

- 2013 shutdown (press release 6745-13): the report as of Oct 1 published on
  Oct 25. Oct 8 and Oct 15 "during the week of October 28", taken as its
  Friday, Nov 1. Oct 22 and Oct 29 during the week of Nov 4, taken as Nov 8.
- 2018–19 shutdown (press release 7864-19): the report as of Dec 24 published
  on Feb 1, 2019, then one each Tuesday and Friday, in order, through the
  report as of Feb 26, published on Mar 5.
- 2023, the ION Markets incident (the COT page's special announcements): as of
  Jan 31 published on Feb 24, Feb 7 on Mar 3, Feb 14 on Mar 8, Feb 21 on
  Mar 10, Feb 28 on Mar 14, Mar 7 on Mar 16, Mar 14 on Mar 21.
- 2025 shutdown (the CFTC's schedule of Dec 9, 2025): as of Sep 30 published
  on Nov 19, through the report as of Dec 16, published on Dec 23.

Every other report follows the 7-day rule. Market-specific delays (the MSCI
minis in November 2011, three natural gas markets in December 2008) are left
alone: those markets weren't eligible then. H2's primary is also run with the
7-day rule for every report, as a check. It is reported, not judged.

**Readings of the text, fixed before the runs:**

- "Its median over the last 156 (or 52) reports": the window includes the
  latest report, and "above" means strictly above.
- Change-4w: the latest report's commercial net short, as a share of open
  interest, against the report four before it. "Rose" means strictly higher.
- VIX and VIX3M are Cboe's daily closes. At the close of t, the latest Cboe
  close on or before t is used. The 5-day mean is the mean of the daily ratio
  over the last 5 Cboe closes up to t.
- SPY "bought and held at 99.5%": bought at the first trade, never rebalanced.
- H1b's inverted book moves S0's whole book, its own BIL included, to BIL
  (99.5%).
- S1 blend long/flat, the reference for the correlations, runs over each
  hypothesis's own window: from 2009-10-01 for H1 and H1b, from 2008-01-02
  for H2.
- H1's and H1b's yearly test blocks start on 2009-10-01, so their folds don't
  line up with H2's. The holdout is the same for all: the last 756 rows of the
  panel, which ends on 2026-10-16, so from 2023-10-12.
