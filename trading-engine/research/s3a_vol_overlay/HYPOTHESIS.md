# S3a, a volatility-managed overlay on S0, and S0 with a trend overlay: hypotheses and test plan

Written on 2026-10-21, before any backtest of either hypothesis ran. Results go
in REPORT.md; this file is only appended to after that (docs/PLAN.md §9,
research gate, step 1).

S1's report (research/s1_trend/REPORT.md) had already been read when this was
written. It found S1 failed alone but halved S0's drawdown when held 50/50 with
it. H2 below comes from that suggestion, so it isn't independent of those
results. The trial count carries S1's eight trials for that reason.

## What is being tested

Both hypotheses are overlays on S0, the live model, and they compete with it,
not with cash. The claim to test: does the overlay cut S0's drawdowns a lot
without costing its return per unit of risk? A standalone Sharpe above zero is
not the claim here (S0's own isn't distinguishable from zero).

## H1 (S3a): S0 scaled down when its volatility is high

**Mechanism.**
- Volatility clusters: this month's realized volatility predicts next month's
  well, while returns aren't predictably higher when volatility is high.
- So cutting exposure when volatility is high cuts risk more than it cuts
  return (Moreira and Muir 2017).
- Momentum, which S0 is, crashes in high-volatility rebounds. Scaling it by its
  own recent volatility removed most of its crash risk and nearly doubled its
  Sharpe (Barroso and Santa-Clara 2015).

**Rule.** On every trading day t, using closes up to t only:
- The base book is S0's latest target, scaled to the 99.5% budget as in S1's
  study. It changes only on S0's own rebalance dates.
- σ is the annualized standard deviation of the base book's daily returns over
  the last W days: today's base weights, applied to each asset's daily
  returns in the window.
- The exposure scale is min(1, target / σ). It only scales down, never up: no
  leverage.
- Every risky weight is multiplied by the scale, and BIL takes the rest of the
  99.5% (BIL in S0's own target counts as cash, not as risk).
- Trades happen on S0's rebalance dates, and on any day the scale has moved by
  0.10 or more since the last trade. Otherwise the book is held.
- Gross stays at 99.5% or below and cash at 0.5% or above: long only, so it
  can't drift over the mandate.

**Variants (4 trials):** W of 21 or 63 days × a target of 10% or 8% a year.
- 10% and 8% are the plan's book target range (PLAN §9, allocation).
- 21 days is Moreira and Muir's month; 63 is the quarter S1 used.
- **Primary, chosen now: W = 21, target 10%.** This is the plan's own
  volatility rule (scale down only, 10% ceiling) applied to S0, with the
  literature's window.

## H2: S0 with a trend overlay

**Mechanism:** S1's. Time-series trend is flat or short when an asset class is
falling, so it holds up when S0, long-only momentum, loses most. In S1's study
its daily correlation with S0 was −0.08.

**Primary: the 50/50 book of S0 and S1's blend long/flat**, chosen now.
- S1's blend rule (3, 6 and 12-month signs, inverse vol, 20% name cap, 10% vol
  ceiling, monthly), with negative signs set to 0. It runs exactly as in
  research/s1_trend, unchanged.
- Long/flat rather than long/short because a long/short book drifts over the
  no-leverage mandate between rebalances (S1's report: up to 111.5% gross).
  The 50/50 long/short book has already been measured; it is in S1's report
  and isn't retested here.
- Each sleeve gets half the capital. Daily returns are mixed 50/50, as in S1's
  study, with no netting of the two sleeves' trades.

**Variant (1 trial): S0 with a trend filter.**
- On S0's rebalance dates, each name in S0's target keeps its weight only if
  at least half of its available lookback returns (63, 126 and 252 days) are
  positive.
- The sign mean over those lookbacks must be ≥ 0: with three lookbacks, at
  least two up.
- A name with fewer than 64 closes keeps its weight (no trend to read).
- The weight a name loses goes to BIL. The book trades only on S0's dates.

That makes **2 trials** for H2.

## Trials and the deflated Sharpe

- Trials so far: S1's 8, plus H1's 4, plus H2's 2, is **14**.
- The deflated Sharpe of each book is reported with n_trials = 14 and the
  variance of the 14 Sharpe ratios (S1's eight from its results file and these
  six).
- **It is not a pass criterion here.** It tests a standalone Sharpe against
  zero, and these overlays claim to be better than S0, whose own deflated
  Sharpe is far below 0.90.
- Multiple testing is handled instead by resting each verdict on its primary
  only, chosen above, never on the best variant.

## Data, costs and split (all as in S1's study, for comparison)

- Prices: s1_trend.load_panel. These are raw closes chained to IBKR's
  dividend-adjusted closes from 2016-10, so earlier P&L leaves out dividends,
  for S0 and the overlays alike.
- Costs: S1's CostModel (IBKR Pro commissions, 2 bps half-spread, square-root
  impact k = 0.5 on the master's volumes, borrow as there).
- Lag 1: decided on the close of t, traded at the close of t+1. The mandate
  check runs every day.
- Walk-forward: decisions from 2008-01-02, yearly test blocks, the last 756
  trading days held out (from 2023-10-12). It is the same span as S1's.
- Excess returns over BIL. The Sharpe SE is sqrt((1 + SR² / 2) / T).

## Pass criteria (walk-forward, each hypothesis's primary against S0)

1. Max drawdown at most 60% of S0's, measured as depth.
2. Excess Sharpe at least S0's minus 0.05.
3. Paired block bootstrap (63-day blocks, 2,000 resamples, seed 21; the same
   blocks drawn for both books): the probability that the overlay's max
   drawdown is shallower than S0's is at least 80%.
4. The same bootstrap: the probability that the overlay's excess Sharpe is at
   least S0's minus 0.05 is at least 80%.
5. Cost drag at most 0.5% a year. For the 50/50 book this is half of each
   sleeve's drag, added.
6. It loses less than S0 in each of 2008, the 2020 crash (Feb 19 to Mar 23)
   and 2022.
7. No day outside the no-leverage mandate.

Criteria 3 and 4 split the example "beats S0 on Sharpe or on drawdown" into
two tests that must both hold, which is stricter.

**Holdout (run once, after the walk-forward is written down).** A
hypothesis's primary is killed if its holdout max drawdown is deeper than
S0's, or its holdout excess Sharpe is more than 0.5 below S0's. Otherwise the
holdout changes nothing: three years can't confirm a Sharpe.

**Verdict.**
- **Pass:** all seven, and the holdout doesn't kill it. Then shadow mode as
  S0's risk manager: the daily scale, or the filtered names, and the would-be
  orders, logged for at least three months beside the live S0 (PLAN §9, step
  7).
- **Fail any:** the report says which, and that primary isn't taken further.
