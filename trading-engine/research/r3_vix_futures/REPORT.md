# R3, the volatility risk premium in VIX futures: report

Run on 2026-10-22 against HYPOTHESIS.md, fixed before any run. Nothing was
changed after the results; no deviation was needed. `python r3_vix_futures.py`
(with the repo's `.venv`) regenerates every number into `results/`, and a second
full run matched every file byte for byte. The prices, costs, lag and split are
S1's and R2's: S0 from 2008 scores 0.268481 here, exactly as in R2.

## Verdict

**The primary passes all 12 criteria, and the holdout doesn't kill it. By the
rule fixed beforehand it goes to shadow mode.** It is the first sleeve to pass
the gate on its own.

The rule: short VIX futures at 10% of NAV, rolled daily at a constant 30 days
to expiry, while their price is above the VIX (contango), else T-bills. Its
Sharpe was 0.79 from 2008 to 2023 with a worst drawdown of −7.3%. Half S0, half
this sleeve scored 0.48 against S0's 0.27, with S0's worst losses cut in every
stress window.

**Two things weigh against it, and the shadow period has to settle them:**

1. **The pass turns on one day.** On 2018-02-01 the curve closed inverted by
   1% (futures 13.35, VIX 13.47), so the rule was flat on 2018-02-05, when the
   futures rose 96% in a day. Had VIX closed 1% lower that Thursday, the
   sleeve would have been short. Re-scored that way:

   | | As run | Short on 2018-02-05 |
   |---|---|---|
   | Excess Sharpe | 0.79 | 0.62 |
   | Deflated Sharpe (bar 0.90) | 0.926 | 0.796, **fails** |
   | Max drawdown | −7.3% | −13.7% |
   | Worst day | −3.9% | −9.6% |
   | February 2018: sleeve; 50/50 with S0; S0 | −2.4%; −4.6%; −6.7% | −11.8%; −9.2%, **criterion 11b fails**; −6.7% |

   The rule's numbers are what it did; this table is what it is exposed to.
   The always-short book, which shows the exposure without the filter, lost
   9.6% that day.
2. **The holdout was poor.** From 2023-10 to 2026-10 its excess Sharpe was
   −0.29 (SE 0.59), where staying short all the time earned 0.35. The filter
   flipped 75 times in three years and kept missing the rebounds after brief
   spikes. In that period, adding it to S0 lowered S0's Sharpe from 0.65 to
   0.46. The kill rules (Sharpe below −0.5, a drawdown worse than −15%, a day
   worse than −12%) weren't met, so it stands, but no one should read the
   holdout as confirmation.

## Walk-forward, 2008-01 to 2023-10 (15.8 years)

Costs on, lag 1, Sharpe in excess of BIL; deflated Sharpe with 27 trials.

| Book | Excess Sharpe (SE) | Volatility | Max drawdown | Worst day | Deflated | Corr. with S0 |
|---|---|---|---|---|---|---|
| **Primary: short 10% in contango** | 0.79 (0.29) | 5.5% | −7.3% | −3.9% | 0.926 | 0.44 |
| V1: short only at a 5% premium | 0.81 | 4.6% | −5.4% | −2.8% | 0.933 | 0.33 |
| V2: size scaled down by volatility | 0.71 | 4.6% | −5.8% | −2.8% | 0.870 | 0.43 |
| V3: hedged with short SPY | 0.34 | 3.4% | −14.0% | −2.9% | 0.394 | 0.07 |
| Always short 10% (reference) | 0.52 | 7.4% | −16.7% | −9.6% | | 0.61 |
| S0, the live model | 0.27 | 13.0% | −38.0% | −9.1% | | 1.00 |
| S1 blend long/flat | 0.33 | 4.8% | −8.5% | −2.3% | | 0.60 |
| 50/50 S0 + primary | 0.48 | 8.1% | −20.8% | −4.6% | | 0.95 |

### The gate, criterion by criterion

| Criterion | Value | Pass |
|---|---|---|
| 1. Excess Sharpe ≥ 0.40 | 0.79 | yes |
| 2. Deflated Sharpe ≥ 0.90 (n = 27) | 0.926 | yes |
| 3. Bootstrap 5th percentile of the Sharpe > 0 | 0.44 | yes |
| 4. Every variant's Sharpe > 0 | worst 0.34 (V3) | yes |
| 5. Max drawdown no worse than −20% | −7.3% | yes |
| 6. Cost drag ≤ 1% a year | 0.32% | yes |
| 7. No day outside the mandate | 0 days (gross 99.5% at every close) | yes |
| 8. Worst day no worse than −12% | −3.9% (2020-06-11) | yes |
| 9. Worst calendar month no worse than −15% | −2.9% (2010-06) | yes |
| 10. Each stress window no worse than −15% | worst −6.8% (2010) | yes |
| 11a. 50/50 with S0 beats S0's Sharpe | +0.21 | yes |
| 11b. 50/50 with S0 loses no more than S0 in any stress window | smallest margin +1.0 point | yes |

### Stress windows

| Window | Primary | Always short | S0 | 50/50 S0 + primary |
|---|---|---|---|---|
| 2008 crisis (Sep 15 to Nov 20) | +0.1% | −13.7% | −26.9% | −14.2% |
| 2010 flash crash (Apr 23 to Jul 2) | −6.8% | −5.8% | −8.6% | −7.7% |
| 2011 (Jul 22 to Oct 3) | −3.4% | −11.0% | −10.3% | −6.8% |
| 2015–16 (Aug 17 to Feb 11) | −4.2% | −8.4% | −10.1% | −7.2% |
| February 2018 (Jan 26 to Feb 9) | −2.4% | −9.2% | −6.7% | −4.6% |
| Late 2018 (Sep 20 to Dec 24) | −3.7% | −6.1% | −7.8% | −5.7% |
| 2020 crash (Feb 19 to Mar 23) | −2.2% | −13.5% | −28.0% | −15.7% |
| 2022 | −2.3% | +1.3% | −9.8% | −5.9% |

The filter did its job in the long crashes: 2008, 2011 and 2020, where the
curve inverted early and stayed inverted. In 2010 it lost a little more than
always-short, and in 2022 it was out while staying short paid.

### By calendar year

| Year | Primary | Always short | S0 |
|---|---|---|---|
| 2008 | +1.0% | −10.0% | −23.1% |
| 2009 | +8.8% | +9.3% | +19.0% |
| 2010 | +9.8% | +11.0% | +14.7% |
| 2011 | −1.5% | −3.2% | +0.8% |
| 2012 | +11.2% | +12.6% | +4.4% |
| 2013 | +5.7% | +9.0% | +7.1% |
| 2014 | −0.4% | +0.7% | +3.8% |
| 2015 | +1.1% | +0.9% | −8.4% |
| 2016 | +11.3% | +8.8% | +10.2% |
| 2017 | +8.3% | +12.8% | +12.6% |
| 2018 | −2.4% | −9.1% | −4.5% |
| 2019 | +8.2% | +11.8% | +15.0% |
| 2020 | +0.2% | −6.3% | +4.6% |
| 2021 | +6.2% | +9.7% | +16.5% |
| 2022 | −2.3% | +1.3% | −9.8% |
| 2023 (to 10-11) | +14.3% | +12.2% | −0.8% |

In calm years with brief inversions (2013, 2017, 2019, 2021) the filter cost
3 to 4.5 points against always-short. It was short on 82% of decision days and
went short 170 times, about 11 a year.

## Holdout, run once (2023-10-12 to 2026-10-16)

| Book | Excess Sharpe (SE) | Max drawdown | Worst day |
|---|---|---|---|
| Primary | −0.29 (0.59) | −9.5% | −2.0% |
| Always short 10% | 0.35 (0.59) | −8.5% | −4.2% |
| S0 | 0.65 (0.64) | −10.9% | −5.8% |
| S1 blend long/flat | 1.03 (0.72) | −4.8% | −2.2% |
| 50/50 S0 + primary | 0.46 (0.61) | −6.2% | −2.9% |

No kill fired.

## Deflated Sharpe

27 trials so far: S1's 8, S3a's 6, R2's 9 and these 4. Their Sharpes' variance
doubled with this study (0.0215 to 0.0414 a year), because R3's sit well above
the earlier ones. That raises the threshold every later study has to clear
(0.41 here).

## The mandate between closes

The book is re-sized to a 10% short at every close, so gross is 99.5% after
each day's trades, and no close breached the mandate (criterion 7). Between
closes a rising VIX grows the short's notional while NAV falls. Before
re-sizing, gross was above 100% at 577 of 3,970 closes, at most 107.6%
(2020-06-11). Always-short's maximum was 120.7%, on 2018-02-05. Cash never
went below 0. Live, the runner's no-leverage check allows the reducing trade
that brings it back.

## What next

1. **Shadow mode from the next session**, for at least three months (PLAN §9,
   step 7): the rule run each evening on Cboe's settlements, nothing sent, its
   record beside the live S0 and the overlay's shadow book on the Strategies
   page. That needs the rule in production code (pinned to this study by a
   test, as S1's trend rule is) and a daily download of the VX settlements.
2. **What the shadow has to show** before anyone considers paper: the rule's
   decisions matching this code day for day, and its record against this
   backtest. A short-volatility record in calm markets says little about a
   spike; the exposure table above is the honest size of one.
3. **How it would be traded, later:**
   - VX or VXM futures at full notional in the no-leverage check. The runner
     has no futures path yet, VX has no market-on-close order, and the paper
     account needs CFE permissions. The owner decides.
   - Or a short VIXY position, through the existing ETF path at its borrow fee.

## Files

- `HYPOTHESIS.md`: the hypothesis, the 4 trials and the criteria, fixed
  before any run.
- `data.py`: fetches and caches Cboe's VX settlements (the archive for
  2004–2013, the current files since) and builds the 30-day series, into
  `research/_data/` (git-ignored).
- `r3_vix_futures.py`: one command reruns everything (`--stage walkforward`
  or `--stage holdout` for one stage).
- `results/`:
  - `verdict.csv` and the criteria for the walk-forward and the holdout
  - the books, stress windows and bootstrap
  - the daily returns and the primary's decisions
  - the gross before re-sizing (`*_drift.csv`)
  - the 27 trials and the deflated-Sharpe inputs
  - the data checks
