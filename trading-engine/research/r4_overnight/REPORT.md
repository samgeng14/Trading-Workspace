# R4, the overnight drift: report

Run on 2026-10-22 against HYPOTHESIS.md. It was fixed before any run and
reviewed. Before any run, the review:
- made the crash limits relative to holding SPY;
- added "beat SPY held" as a criterion;
- cut the buy side's cost allowance to 0.1 bps;
- set cost robustness at a Sharpe of 0.20 at doubled costs;
- sat the rules out of the nights after early closes.

The file marks each change. Its Deviations section, appended after the runs,
records two small readings that change no result.

`python r4_overnight.py` (with the repo's `.venv`) regenerates every number
into `results/`, and two full runs matched all 17 files byte for byte. S0
scores 0.268623, R1's exact figure.

## Verdict

**SPY held every night fails the gate, on 8 of its 13 criteria.** Its Sharpe
was 0.15 from 2008 to 2023, against 0.40 required and 0.51 for holding SPY.
The holdout fired no kill. The primary isn't taken further.

**Two reasons, either enough:**
- **The day wasn't empty.** Before costs, the night earned 5.4% a year (Sharpe
  0.44) and the day 3.5% (0.26). The papers' pattern, the whole return
  overnight and nothing in the day, doesn't hold for SPY's daily bars from
  2008 to 2023. Even before costs the night's Sharpe was below holding SPY's.
- **Costs take most of what the night earns.** A round trip every session
  costs about 1.4 bps (a median 1.38 for SPY), 3.5% of NAV a year. At 1× the
  night keeps 1.7% a year; at 2× it loses money.

## Walk-forward, 2008-01 to 2023-10 (15.8 years)

Costs on, lag 1, Sharpe in excess of BIL (the sleeve gives up the T-bill rate:
its cash is idle in the day).

| Book | Excess Sharpe (SE) | Return a year | Volatility | Max drawdown | Cost drag a year |
|---|---|---|---|---|---|
| **Primary: SPY every night** | 0.15 (0.25) | 1.7% | 12.2% | −29.5% | 3.5% |
| V1: QQQ every night | 0.23 | 2.9% | 13.0% | −31.0% | 4.6% |
| V2: SPY on weeknights only | 0.26 | 2.7% | 10.2% | −29.1% | 2.7% |
| The intraday complement: SPY from each open to its close | 0.03 | −0.2% | 15.5% | −39.9% | 3.6% |
| SPY held, with dividends | 0.51 | 9.3% | 20.4% | −51.6% | 0.0% |
| S0, the live model | 0.27 | 3.3% | 13.0% | −38.0% | 0.1% |
| 50/50 S0 + primary (dividends on S0's basis) | 0.19 | 2.1% | 11.3% | −31.7% | 1.8% |

### The gate, criterion by criterion

| Criterion | Value | Pass |
|---|---|---|
| 1. Excess Sharpe ≥ 0.40 | 0.15 | **no** |
| 1b. Excess Sharpe above SPY held's | 0.15 against 0.51 | **no** |
| 2. Deflated Sharpe ≥ 0.90 (n = 33) | 0.16 | **no** |
| 3. Bootstrap 5th percentile of the Sharpe > 0 | −0.15 | **no** |
| 4. Every variant's Sharpe > 0 | worst 0.23 (V1) | yes |
| 5. Max drawdown no deeper than 60% of SPY held's (−31.0%) | −29.5% | yes |
| 6. No day outside the mandate | 0 days | yes |
| 7. Excess Sharpe ≥ 0.20 at twice the costs | −0.14 | **no** |
| 8. Worst night no worse than −12% | −10.4% (2020-03-16) | yes |
| 9. Worst month no worse than SPY held's (−16.4%) | −19.4% (March 2020) | **no** |
| 10. A return above SPY held's in each stress window | smallest margin +2.3 points (2015–16) | yes |
| 11a. 50/50 with S0 beats S0's Sharpe | 0.19 against 0.27 | **no** |
| 11b. 50/50 with S0 loses no more than S0 in each window | 2022: −12.2% against −9.8% | **no** |

### Costs

| Costs | Primary | V1 (QQQ) | V2 (weeknights) | Intraday complement |
|---|---|---|---|---|
| 0× | 0.44 | 0.62 | 0.53 | 0.26 |
| 1× | 0.15 | 0.23 | 0.26 | 0.03 |
| 2× | −0.14 | −0.15 | −0.02 | −0.21 |
| 3× | −0.43 | −0.54 | −0.30 | −0.44 |

Excess Sharpe at each multiple of the costs. The primary's own round trips cost
3.5% a year at 1×, 7.1% at 2× and 11.0% at 3×. The harness's monthly trims add
under 0.01%. The commission alone is small at SPY's prices; the round trip is
mostly the SEC fee on each sale (0.30 bps) and the 0.5 bps allowance on the
open.

### Stress windows

| Window | Night (primary) | Day (complement) | SPY held | S0 | 50/50 S0 + primary |
|---|---|---|---|---|---|
| 2008 crisis (Sep 15 to Nov 20) | −15.8% | −29.7% | −39.6% | −26.9% | −21.6% |
| 2010 flash crash (Apr 23 to Jul 2) | −2.9% | −14.1% | −15.1% | −8.6% | −6.0% |
| 2011 (Jul 22 to Oct 3) | −9.6% | −10.6% | −17.8% | −10.3% | −10.1% |
| 2015–16 (Aug 17 to Feb 11) | −9.4% | −5.5% | −11.7% | −10.1% | −10.2% |
| February 2018 (Jan 26 to Feb 9) | −2.3% | −5.7% | −7.7% | −6.7% | −4.5% |
| Late 2018 (Sep 20 to Dec 24) | +1.8% | −22.1% | −18.6% | −7.8% | −3.0% |
| 2020 crash (Feb 19 to Mar 23) | −28.2% | −7.6% | −33.3% | −28.0% | −28.1% |
| 2022 | −15.0% | −8.2% | −18.1% | −9.8% | −12.2% |

Which half of the day a crash came in changed from crash to crash. 2008 and late
2018 were sold in the day; the 2020 crash and 2022 came overnight, with the
futures trading while the cash market was shut. The night is not a safer half.

## Holdout, run once (2023-10-12 to 2026-10-16)

| Book | Excess Sharpe (SE) | Max drawdown | Worst day |
|---|---|---|---|
| Primary | 0.94 (0.69) | −16.3% | −4.0% (2024-08-05) |
| Intraday complement | −0.10 (0.58) | −14.9% | −4.8% |
| SPY held | 1.10 (0.73) | −18.7% | −5.8% |
| S0 | 0.65 (0.64) | −10.9% | −5.8% |
| 50/50 S0 + primary | 0.97 (0.70) | −11.9% | −3.1% |

No kill fired. The papers' pattern shows here: the night carried the return and
the day lost a little. Holding SPY still scored higher, and one three-year
stretch can't overturn sixteen.

## Deflated Sharpe

33 trials so far: S1's 8, S3a's 6, R2's 9, R3's 4, R1's 3 and these 3. Their
Sharpes' variance is 0.0360 a year. The primary's deflated Sharpe is 0.16.

## After the verdict

These were found after the results, so they aren't hypotheses to test on this
data:
- **V2, weeknights only, earned more than the primary before costs** (5.7%
  against 5.4% a year), while sitting out the 825 weekend and holiday nights
  the primary held. So those nights lost money before costs over this window.
- **QQQ's night was stronger than SPY's** before costs (0.62 against 0.44),
  but its costs are higher (QQQ traded near $25 in 2008, so its per-share
  commission was large), and it fails at 1× too.

## What this says

- The overnight drift is real in some periods (the holdout shows it) and not in
  others, as CXO Advisory's tests of SPY found. Over 2008 to 2023 the day
  carried a large share of SPY's return.
- Trading it through the auctions costs about 3.5% of NAV a year at this size,
  mostly the SEC fee on every morning's sale and the uncertainty of the open's
  price. Whatever edge the night has is smaller than that.
- **For the research queue:** strategies that trade every day need an edge of
  several percent a year before costs. The fund's next candidates should hold
  for weeks: futures carry and trend, once the curve archive has history.

## Files

- `HYPOTHESIS.md`: the rules, the 3 trials and the criteria, fixed before any
  run, with the review's amendments and the Deviations section. Its last
  section ("How it would be traded") still says the test holds the early-close
  nights; the review's first amendment made them flat, as its rules section
  says, and the run follows the amendment.
- `data.py`: the data checks (the opens against a second source, the
  dividends, the calendar). It downloads nothing.
- `r4_overnight.py`: one command reruns everything (`--stage walkforward` or
  `--stage holdout` for one stage).
- `results/`:
  - `verdict.csv` and the criteria for the walk-forward and the holdout
  - the books, the stress windows and the bootstrap
  - the cost sensitivity at 0 to 3× costs
  - the correlations
  - the constructed overnight series and the daily returns
  - the 33 trials and the deflated-Sharpe inputs
  - the data checks
