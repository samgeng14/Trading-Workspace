# R3, the volatility risk premium in VIX futures: hypotheses and test plan

Written on 2026-10-22, before any backtest in this folder ran. This file is
only appended to after the results (docs/PLAN.md §9, research gate, step 1).
It is the plan's S3(b), with free data.

Already read: the reports of research/s1_trend, research/s3a_vol_overlay and
research/r2_vol_positioning. R2's report suggested this study: its VIX timing
of SPY failed, and the literature's evidence is stronger for the premium VIX
futures pay through the roll than for timing equities.

What was looked at before this was written: the data checks `data.py` prints
(coverage, the 2007 rescale, settlement errors, and the 30-day series' moves on
known stress days, which set the sizing below). No strategy's return, and not
how often the signal below is on.

## Data, all free, with no key or login (`data.py` fetches and caches it)

| Source | What | Coverage on disk |
|---|---|---|
| Cboe Futures Exchange, the archive (`cdn.cboe.com/resources/futures/archive/volume-and-price/`) | daily open, high, low, close, settlement, volume and open interest of every monthly VX contract | 112 contracts expiring 2004-05-19 to 2013-12-18 |
| Cboe Futures Exchange, the current files (listed at `www.cboe.com/us/futures/market_statistics/historical_data/product/list/VX/`) | the same | 162 contracts expiring 2014-01-22 to 2027-06-16, settlements to 2026-10-20 |
| Cboe daily index history | VIX's close | from 1990 (R2's cached file) |
| On disk | the ETF price master, as S1's study loads it | BIL, SPY and S0's ETFs |

What the checks found:
- **Prices before 2007-03-26 are divided by 10.** VX was quoted at ten times
  the VIX until Cboe rescaled it that day. After the division the front
  contract sits at 0.96 to 1.09 times VIX on either side of the date.
- **2013's contracts come from the archive.** The current files carry no
  settlements for them (all 0); their closes match the archive's exactly.
- **The early years are thin.** Consecutive monthly contracts are listed
  only from 2005-08. 100 days through 2006-08-21 have no roll return, because
  a newly listed second month had no settlement yet. From 2007 on, every
  Cboe Futures Exchange (CFE) day has one.
- **Two settlement errors**, in far contracts on 2008-04-21 and 2008-10-30
  (a move that fully reverses the next day). The series never uses those
  contracts.
- **Expiries** fall on the rule's Wednesday, 30 days before the third
  Friday of the next month, or the Tuesday before it on holidays. The
  exceptions are 2004-07-14 and 2004-10-13, a week early, from the product's
  first year.
- **Settlement against close on traded days:** a median gap of 0.16% and a
  99th percentile of 1.75%. Everything here uses settlements.
- **Calendars:** CFE traded on 2015-04-03 and 2018-12-05, when NYSE was
  closed. Cboe's VIX file has rows on some market holidays from 2022, which
  aren't trading days.

## The instrument: the 30-day series (`data.cm30()`)

- **The basis signal's price, CM30.** The two monthly contracts around 30
  calendar days to expiry, interpolated linearly in days to expiry. When the
  front is 30 days or more out, the front alone. VIX is itself a 30-day
  measure, so CM30 against VIX compares like with like.
- **What the sleeve trades: VXCM.** It is the S&P VIX Short-Term Futures
  Index's roll, rebuilt from the settlements:
  - A roll period runs from the trading day before one monthly settlement to
    the day before the trading day before the next.
  - In it the first month (the next settlement) starts at 100% and loses
    1/dt of the weight each day to the second month, dt being the period's
    trading days.
  - Each day's return is on the previous close's weights. It is an
    excess-return index, with no collateral and level 1.0 at the start.
  - Holding VXCM long is a position rolled daily from the first to the second
    contract; it never holds a contract into its final settlement. The
    sleeve is short it.
- **Checked against known events.** VXCM rose 96.1% on 2018-02-05: S&P's
  index rose 96% that day, the day XIV, a short version of it, lost 96% and
  was closed. It rose 42.4% on 2024-08-05, 34.9% on 2020-03-16 and 25.4% on
  2015-08-24. It rose 263.5% from 2020-02-19 to 2020-03-23 and 212.2% from
  2008-09-30 to 2008-11-20.
- **Timing.** VX settles at the end of its regular session (15:00 CT, 16:00
  ET, today) and VIX closes at 16:15 ET. A decision at the close of t uses
  t's settlements and t's VIX close, and trades at t+1's settlement (lag 1).
- **The panel.** Its sessions are S1's (NYSE). A session without a VX
  settlement carries VXCM's last level. A CFE day that isn't a session folds
  its move into the next session's.

## H1: short the VIX futures premium while the curve says it is there

**Mechanism.**
- VIX futures trade above the VIX they converge to, on average. Buyers pay
  for protection against volatility spikes, and those spikes come in equity
  selloffs, when protection is worth most.
  - It is the variance risk premium (Carr and Wu 2009) seen through the
    futures.
  - Eraker and Wu (2017): VIX futures and the ETNs that hold them earn large
    negative average returns, as an equilibrium volatility premium.
- In contango the futures roll down toward spot as their maturity shortens. A
  short earns that roll whenever VIX doesn't rise by more.
- Simon and Campasano (2014): the VIX futures basis predicts the futures'
  returns, not spot VIX's changes. Shorting in contango and buying in
  backwardation, hedged with S&P futures, was profitable from 2006 to 2011.
- Cheng (2019), "The VIX Premium": the premium, measured ex ante, falls and
  can turn negative when risk jumps, so the futures then underprice the
  coming volatility. The short is least paid exactly when it is most
  dangerous. That is the case for standing aside while the curve is inverted.
- Johnson (2017): the slope of the VIX term structure predicts the returns
  of variance assets.

**The risk.** Short volatility loses in sudden spikes: October 2008,
2015-08-24, 2018-02-05, March 2020, 2024-08-05.
- The premium is widely harvested. Short-volatility products held billions
  before February 2018, and its size since then is untested here; the
  holdout covers 2023-10 to 2026-10.
- The signal can't take the sleeve out before a spike that starts overnight.
  An inversion seen at Friday's close is traded at Monday's settlement.

**Rule (the primary, chosen now).**
- At each close t: short VXCM at **10% of NAV** (notional) while
  **CM30_t > VIX_t** (contango). Otherwise flat.
- Re-sized to 10% of NAV at every close, a constant-exposure short like a
  −0.1x daily product. The backtester checks the mandate at each close after
  that day's trades, so gross is 99.5% at every close.
- The rest of the 99.5% budget is in BIL: 89.5% when short, 99.5% when flat.
  Gross = |VX notional| + BIL ≤ 99.5%, so the mandate holds with futures at
  full notional. Cash is at least 0.5%.
- Decided every trading day, lag 1.

**Why 10%.** VXCM's largest day since 2004 was +96.1% (2018-02-05). A 10%
short loses about 9.6% of NAV on a repeat of it, and lag 1 can't take it out
first. The fund's whole book targets 8–10% a year of volatility (PLAN §9).
The sleeve is sized to survive the worst known day, not to avoid it.

**Variants (3 trials).**
- **V1, a stronger premium:** short only while CM30_t / VIX_t ≥ 1.05, a
  premium of at least 5% over spot; else flat. It tests whether a larger
  basis earns more, Simon and Campasano's threshold idea.
- **V2, volatility-scaled size:** notional = min(10%, 5% / σ_t) of NAV, with
  the primary's condition.
  - σ_t is the annualized standard deviation (ddof 1, × √252) of VXCM's daily
    returns over the last 21 sessions up to t.
  - It only scales down from the primary, cutting the short when the series
    is most volatile, as spikes begin (Moreira and Muir 2017's scaling,
    applied to short volatility).
- **V3, equity-hedged** (Simon and Campasano's hedge): the primary plus a
  short SPY position.
  - The hedge is |β_t| × the VX notional, capped at 40% of NAV. β_t is the
    OLS slope of VXCM's daily returns on SPY's over the last 63 sessions up
    to t. When β_t ≥ 0, there is no hedge.
  - BIL is 99.5% less the VX notional and the hedge.
  - It strips out the equity beta a short-volatility position carries, to
    test whether the premium survives beyond that beta.

## Trials and the deflated Sharpe

- This study's trials: the primary and 3 variants, 4. With the 23 already
  spent (S1's 8, S3a's 6, R2's 9; R1 was stopped before any hypothesis, so it
  spent none), **n = 27**.
- The trials' Sharpe variance comes from the 27: S1's, S3a's and R2's from
  their results files (R2's `trials.csv` lists all 23), and these four.

## Costs (through the C++ walk-forward)

**VXCM** is a futures instrument: no borrow, no cash moved, multiplier 1,
priced at its level.
- **Each trade pays 6 bps a side** of the dollars traded
  (`half_spread_bps_by_symbol={"VXCM": 6.0}`), costed at a VX price of 12,
  where a contract is $12,000:
  - commission and fees: IBKR's commission ($0.85), CFE's VX fee and the NFA
    fee, taken together as $2.50 a contract a side, above their sum as best
    known (check IBKR's and CFE's fee pages before trading): 2.1 bps;
  - the spread: a full tick (0.05 points, $50) to cross it: 4.2 bps.
  - Most of the sample has VX above 12, where the same dollars are fewer
    bps, so this is conservative. The futures table's commission for VXCM is
    0, because the per-contract cost is already in the 6 bps.
- **The roll: 8 bps of the notional once a month.** A constant-maturity
  position moves its whole notional from the first contract to the second
  over a month. That is charged as one calendar-spread trade on the full
  notional on one roll date a month (`FuturesRoot(commission=0, roll_bps=8,
  roll_months=1-12, roll_day=1)`): a full outright tick on one leg (4.2 bps)
  plus both legs' commission (4.2 bps).
- **Daily re-sizing** trades the day's drift of the short, and of BIL. It
  pays the costs above and S1's.
- **No impact.** VX trades about 200,000 contracts a day, and 10% of a $1M
  book is about 7 contracts at a VX price of 15. The trades are counted as
  unmeasured and reported.

**BIL and SPY** take S1's CostModel: IBKR Pro commissions, a 2 bps
half-spread, square-root impact k = 0.5 on the master's volumes, and 25 bps a
year of borrow on the SPY short. As in S1's study, cash earns nothing.

## Split and measures (as R2's)

- **Walk-forward:** decisions from 2008-01-02 (S1's and R2's start), yearly
  test blocks on an expanding window, up to the holdout.
- **Holdout:** the last 756 sessions of the panel ending 2026-10-16 (R2's
  panel), so from 2023-10-12. Later sessions are not used. It runs once,
  after the walk-forward is written down.
- **Measures:**
  - excess returns over BIL, with Sharpe SE sqrt((1 + SR² / 2) / T);
  - the block bootstrap of 63-day blocks, 2,000 resamples, seed 21;
  - cost drag as R2's: total cost a year over the average value.
- **Reference books** run through the same harness and window, reported and
  not counted as trials:
  - S0;
  - S1 blend long/flat;
  - VXCM shorted at 10% every day, with no signal, to show what the
    condition adds.

## Pass criteria (walk-forward, the primary)

**Standalone:**
1. Excess Sharpe at least 0.40.
2. Deflated Sharpe at least 0.90 (n = 27).
3. Block-bootstrap 5th percentile of the excess Sharpe above 0.
4. Every variant's excess Sharpe above 0.
5. Max drawdown no worse than −20%.
6. Cost drag at most 1% a year.
7. No day outside the mandate.

**Crashes:** this replaces S1's "positive in 2008 and 2022", which doesn't fit
short volatility.
8. Worst day no worse than −12% of the sleeve's NAV.
9. Worst calendar month no worse than −15%.
10. In each stress window, a loss no worse than −15%:
    - 2008 crisis: 2008-09-15 to 2008-11-20
    - 2010 flash crash: 2010-04-23 to 2010-07-02
    - 2011: 2011-07-22 to 2011-10-03
    - 2015–16: 2015-08-17 to 2016-02-11
    - February 2018: 2018-01-26 to 2018-02-09
    - late 2018: 2018-09-20 to 2018-12-24
    - 2020 crash: 2020-02-19 to 2020-03-23
    - calendar 2022

**Against the fund's book:**
11. The 50/50 book of S0 and the sleeve, with daily returns mixed as in
    S3a's and R2's studies, meets both:
    - a higher excess Sharpe than S0's;
    - a loss in each stress window of criterion 10 no worse than S0's own.
      Adding the sleeve must not deepen S0's crashes.

**Correlation with S0 and with S1 is reported, not judged.** Short volatility
is long equity risk by construction, so some overlap with an equity book is
expected. Criterion 11 judges the overlap where it matters: whether the
combined book does better, and whether its crashes are worse.

**Holdout kills:** an excess Sharpe below −0.5, a drawdown worse than −15%,
or a worst day worse than −12%. The holdout includes 2024-08-05, a +42% day
for VXCM.

**Verdict.**
- **Pass:** every criterion and no holdout kill. Then shadow mode (PLAN §9,
  step 7).
- **Fail:** the report says which criteria failed, and the primary isn't
  taken further. No variant replaces a failed primary.

## How it would be traded (not tested here)

- **VX futures**, or VXM minis (multiplier 100, listed since 2020-08), at
  full notional in the no-leverage check (`risk.check` counts futures at
  notional). The two nearest monthly contracts would be held in VXCM's
  weights and re-sized and rolled daily near the 16:00 ET settlement. VX has
  no market-on-close order, so the runner would need a futures path that
  isn't built yet, and the paper account needs CFE permissions.
- **The ETF route:** a short VIXY position, or long SVXY (−0.5x daily) at
  twice the exposure. VIXY tracks the same S&P index for a 0.85% expense
  ratio, and its borrow fee varies. The research models the futures.
