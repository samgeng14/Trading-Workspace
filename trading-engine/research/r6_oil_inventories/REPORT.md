# R6, crude oil timed by US inventories: report

Run on 2026-10-22 against HYPOTHESIS.md. It was fixed before any run and
reviewed. The review kept stocks as EIA published them (lease-inclusive to
2016-09-30), the long-or-bills rule and the criteria as minimums. It added an
absolute floor of −25% to criterion 5 and a random-timing comparison, reported
and not judged. No deviation was needed.

`python r6_oil_inventories.py` (with the repo's `.venv`) regenerates every
number into `results/` in about four seconds, and three full runs matched all
20 files byte for byte. S0 on the usual basis scores 0.268623 and S0_TR
0.330121, R5's exact figures.

## Verdict

**USO while US crude stocks are below their five-year norm fails the gate,
on 7 of its 14 criteria.** Its Sharpe was −0.26 from 2008 to 2023, below USO
held's −0.16: timing the position did worse than holding it. The holdout
fired no kill. The primary isn't taken further.

**Two episodes decided it, and the plan said they might.**
- **The gauge lags demand shocks.** Inventories build only after demand
  falls, so stocks were still below their norm when oil collapsed in 2008
  and in 2020. HYPOTHESIS.md's "Known before the run" counted this from the
  inventories alone.
- The sleeve lost 15.6% while in oil from May to November 2008, and 35.5%
  from February to April 2020.
- **Random timing did better.** Choosing the same number of months in oil at
  random beat the inventory months in 80% of 2,000 draws. That isn't
  significant, but it leaves the timing nothing.

## Walk-forward, 2008-01 to 2023-10 (15.8 years)

Costs on, lag 1, total returns, Sharpe in excess of BIL with its income. In
oil, a rule holds USO at 37% of NAV and BIL at 62.5%.

| Book | Excess Sharpe (SE) | Return a year | Volatility | Max drawdown | Cost drag a year |
|---|---|---|---|---|---|
| **Primary: USO while US stocks are below their norm** | −0.26 (0.26) | −1.7% | 8.1% | −47.5% | 0.07% |
| V1: Cushing's stocks | −0.38 | −3.3% | 9.6% | −62.0% | 0.08% |
| V2: days of supply | −0.38 | −2.8% | 8.5% | −54.2% | 0.12% |
| USO held at 37% | −0.16 | −2.4% | 13.6% | −68.4% | 0.04% |
| S0_TR, the live model with its distributions | 0.33 | 4.3% | 13.0% | −36.7% | 0.09% |
| S0 on the usual basis | 0.27 | 3.3% | 13.0% | −38.0% | 0.10% |
| 50/50 S0_TR + primary | 0.12 | 1.5% | 8.7% | −29.0% | 0.08% |

The primary was in oil on 45 of its 190 decisions, 934 of 3,971 sessions,
with 16 switches. Its drawdown ran from 2008-07-14 to 2020-04-28 and hadn't
recovered by the walk-forward's end.

### The gate, criterion by criterion

| Criterion | Value | Pass |
|---|---|---|
| 1. Excess Sharpe ≥ 0.40 | −0.26 | **no** |
| 1b. Excess Sharpe above USO held's | −0.26 against −0.16 | **no** |
| 2. Deflated Sharpe ≥ 0.90 (n = 39) | 0.00 | **no** |
| 3. Bootstrap 5th percentile of the Sharpe > 0 | −0.73 | **no** |
| 4. Every variant's Sharpe > 0 | worst −0.38 (V1) | **no** |
| 5. Max drawdown no deeper than 60% of USO held's (−41.1%) and −25% | −47.5% | **no** |
| 6. Cost drag ≤ 1% a year | 0.07% | yes |
| 7. No day outside the mandate | 0 days | yes |
| 8. Worst day no worse than −12% | −8.6% (2020-03-09) | yes |
| 9. Worst month no worse than USO held's (−19.8%, March 2020) | −19.8%, the same month | yes |
| 10. In each of 15 stress windows, no more than 0.25 points below USO held | smallest margin 0.00 | yes |
| 11a. 50/50 with S0_TR beats S0_TR's Sharpe | 0.12 against 0.33 | **no** |
| 11b. 50/50 with S0_TR loses no more than S0_TR in each of R3's windows | smallest margin +2.4 points (late 2018) | yes |
| 12. Daily correlation with S0_TR ≤ 0.50 | 0.30 | yes |

### The timing

**Each stretch the primary spent in oil:**

| In oil | Sessions | The sleeve | USO | Deepest fall inside |
|---|---|---|---|---|
| 2008-01-04 to 2008-03-03 | 40 | +2.0% | +3.8% | −4.2% |
| 2008-05-02 to 2008-11-03 | 129 | −15.6% | −41.9% | −24.4% |
| 2011-12-02 to 2012-01-03 | 21 | +1.0% | +2.8% | −2.8% |
| 2018-04-03 to 2018-11-01 | 150 | +3.4% | +5.7% | −6.0% |
| 2019-04-02 to 2019-05-01 | 21 | +1.2% | +3.1% | −1.9% |
| 2020-02-04 to 2020-05-01 | 62 | −35.5% | −77.5% | −38.7% |
| 2021-06-02 to 2023-02-01 | 421 | +18.1% | +45.4% | −12.5% |
| 2023-05-02 to 2023-08-01 | 63 | +4.5% | +9.6% | −3.5% |
| 2023-09-05 to 2023-10-11, the walk-forward's end | 27 | −0.2% | −1.6% | −4.0% |

- **Six of the nine stretches made money.** The 2008 and 2020 stretches lost
  more than those six made together.
- **The monthly re-sizing deepened 2020.** Each month-end decision put USO
  back to 37% of a smaller NAV, buying into the fall, so the sleeve lost
  35.5% where 37% of USO's fall is 28.7%.

**Against random timing** (2,000 draws, seed 21, before costs):
- Each draw holds oil on 45 decisions chosen at random.
- The primary scored −0.26. The median draw scored −0.05, the 5th to 95th
  percentile −0.45 to +0.33.
- 20.0% of the draws scored below the primary.

### Stress windows

R3's eight windows, the fund's crashes:

| Window | Primary | USO held at 37% | S0_TR | 50/50 S0_TR + primary |
|---|---|---|---|---|
| 2008 crisis (Sep 15 to Nov 20) | −13.1% | −21.1% | −26.6% | −20.0% |
| 2010 flash crash (Apr 23 to Jul 2) | 0.0% | −7.0% | −8.4% | −4.2% |
| 2011 (Jul 22 to Oct 3) | 0.0% | −9.0% | −10.0% | −5.1% |
| 2015–16 (Aug 17 to Feb 11) | 0.0% | −17.9% | −9.3% | −4.7% |
| February 2018 (Jan 26 to Feb 9) | +0.1% | −3.3% | −6.7% | −3.4% |
| Late 2018 (Sep 20 to Dec 24) | −3.0% | −14.8% | −7.8% | −5.3% |
| 2020 crash (Feb 19 to Mar 23) | −21.6% | −21.6% | −28.0% | −24.7% |
| 2022 | +11.7% | +11.7% | −9.8% | +0.7% |

Oil's own crashes, every fall of 40% or more in WTI's spot price:

| Crash | Primary | USO held at 37% | S0_TR | 50/50 S0_TR + primary |
|---|---|---|---|---|
| 2008-09-22 to 2008-12-23 (−75%) | −13.6% | −28.5% | −19.8% | −16.5% |
| 2013-09-06 to 2015-03-17 (−61%) | −0.2% | −26.7% | +12.5% | +6.1% |
| 2015-10-09 to 2016-01-20 (−46%) | 0.0% | −20.8% | −7.7% | −3.9% |
| 2018-06-27 to 2018-12-27 (−43%) | −0.9% | −12.0% | −3.7% | −2.2% |
| 2019-04-23 to 2020-03-30 (−79%) | −22.6% | −28.4% | −11.7% | −17.1% |
| 2020-04-03 to 2020-04-20 (−96%, to the $1 floor) | −10.8% | −10.8% | +5.4% | −2.9% |
| 2022-03-08 to 2023-03-17 (−46%) | −5.5% | −10.1% | −13.8% | −9.5% |

- **The gauge kept the sleeve out of the supply gluts and in for the demand
  crashes.**
  - It was out of oil for the gluts of 2014–16, when US stocks were high.
  - It was in oil for the demand collapses of 2008 and 2020, which came
    while stocks were still low.
- **Beside S0, oil was no hedge.** In the three equity crashes the sleeve was
  in oil for (2008, late 2018, 2020) it lost too; it gained only in 2022. USO
  held correlated 0.53 with S0_TR.

### Costs

| Costs | Primary | V1 (Cushing) | V2 (days of supply) | USO held at 37% |
|---|---|---|---|---|
| 0× | −0.256 | −0.368 | −0.363 | −0.158 |
| 1× | −0.264 | −0.376 | −0.375 | −0.160 |
| 3× | −0.279 | −0.391 | −0.400 | −0.165 |

Excess Sharpe at each multiple of the half-spreads and impact; the commission
stays at 1×. Costs aren't why it failed.

## Holdout, run once (2023-10-12 to 2026-10-16)

| Book | Excess Sharpe (SE) | Return a year | Max drawdown | Worst day |
|---|---|---|---|---|
| Primary | 0.57 (0.62) | 11.5% | −10.1% | −4.0% (2026-03-23) |
| USO held at 37% | 0.72 (0.65) | 14.7% | −12.6% | −4.0% (2026-03-23) |
| S0_TR | 0.64 (0.63) | 11.6% | −10.9% | −5.8% (2026-01-30) |
| 50/50 S0_TR + primary | 0.76 (0.66) | 11.9% | −8.5% | −2.8% (2026-01-30) |

- **No kill fired,** and none could have been expected. The plan said the
  rules would be in oil on about 90% of the holdout. The primary was, on 683
  of its 756 sessions, so the holdout mostly graded USO held.
- **It trailed USO held.** It was out of oil in April and May 2026 and from
  September 2026, and missed part of that year's rally.

## Deflated Sharpe

39 trials so far: S1's 8, S3a's 6, R2's 9, R3's 4, R1's 3, R4's 3, R5's 3 and
these 3. Their Sharpes' variance is 0.0635 a year, up from 0.0335 after R5.
These three are the only trials below −0.02 so far, and they widen the
trials' spread.
The primary's deflated Sharpe is 0.00. **The bar for the next study rises
with the variance.**

## After the verdict

These were found after the results, so they aren't hypotheses to test on this
data:
- **The premise held; the premium didn't.**
  - Below the norm, the curve was backwardated on 36 of 51 decision days;
    above it, on 27 of 145. So the gauge did read the curve.
  - But the roll yield backwardation pays is a few percent a year, and
    crude's price moved by tens of percent. The two demand crashes swamped
    it.
- **The variants fared worse.** Cushing's stocks kept V1 in oil through the
  2014 fall (−21.8% while in oil from March 2014 to February 2015). Days of
  supply switched 30 times.

## What this says

- **The theory of storage described the curve, and that was all the gauge
  told.** Low stocks meant backwardation, not that oil would rise. Over 2008
  to 2023 crude's price moves swamped its roll, and its two great falls came
  from demand, which inventories see only afterwards. Hamilton and Wu's
  finding fits: after 2005 the long side's compensation was smaller and more
  volatile.
- **Timing one commodity gives few episodes to learn from.** Nine stretches
  in oil, and two decided the result.
- **For the research queue:**
  - The literature finds commodity carry in the cross-section: many
    commodities, long the backwardated and short those in contango.
  - That needs futures curves for many commodities. The fund's archive has
    them from 2026-10-21 only, and EIA's free curve, crude alone, stops in
    2024.
  - So a cross-sectional test waits on the archive, like S1's futures
    version.

## Files

- `HYPOTHESIS.md`: the rules, the 3 trials and the criteria, fixed before any
  run, with the review's note and its amendments.
- `data.py`: the data checks:
  - EIA's series against a second copy;
  - each week as first published, from EIA's archive of 795 reports;
  - the five-year norm against EIA's own statements;
  - the curve against the inventories;
  - USO's split, distributions, premium and tracking;
  - the costs' inputs and oil's crashes.
  `python data.py` fetches what's missing into `research/_data/eia/` and
  prints every check.
- `r6_oil_inventories.py`: one command reruns everything (`--stage
  walkforward` or `--stage holdout` for one stage).
- `results/`:
  - `verdict.csv` and the criteria for the walk-forward and the holdout
  - the books, the stress windows and the bootstrap
  - the stretches in oil, and the random-timing comparison and its draws
  - the cost runs and the correlations
  - each rule's decisions, with the week and gauge it read
  - the daily returns
  - the 39 trials and the deflated-Sharpe inputs
  - the data checks
