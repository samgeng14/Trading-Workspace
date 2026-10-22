# R1, calendar and macro-announcement timing: report

Run on 2026-10-22 against HYPOTHESIS.md. It was fixed before any run and
reviewed; the review raised SPY's and BIL's half-spreads to realistic levels,
also before any run, and the file says so. No deviation was needed.
`python r1_calendar_macro.py` (with the repo's `.venv`) regenerates every
number into `results/`, and two more full runs matched all 15 files byte for
byte. The prices, harness, lag and split are S1's, R2's and R3's. S0 with S1's
own costs scores 0.268481, R2's exact figure.

## Verdict

**The primary, SPY on the turn of the month, fails the gate.** It meets 7 of
the 10 criteria and misses the three that matter: its Sharpe (0.22 against
0.40), its deflated Sharpe (0.24 against 0.90) and its bootstrap (the 5th
percentile is −0.07). The holdout didn't kill it, and the primary isn't taken
further.

**The calendar adds almost nothing over SPY's other days.**
- SPY on every other session scored 0.31, above the primary's 0.22.
- Holding SPY throughout scored 0.45.
- On the turn-of-the-month days SPY earned 5.6 bps a day over T-bills,
  against 3.2 on the rest (t = 0.48): no different in either half of the
  period. The effect the 1988 and 2008 papers found doesn't show in 2008 to
  2023.

## Walk-forward, 2008-01 to 2023-10 (15.8 years)

Costs on (SPY's and BIL's half-spreads at 1 bp), lag 1, Sharpe in excess of
BIL.

| Book | Excess Sharpe (SE) | Volatility | Max drawdown | Cost drag | Deflated |
|---|---|---|---|---|---|
| **Primary: SPY on the turn of the month** | 0.22 (0.25) | 8.4% | −15.1% | 0.80% | 0.24 |
| V1: plus FOMC days | 0.41 | 9.0% | −13.8% | 1.19% | 0.50 |
| V2: plus FOMC, jobs and CPI days | 0.38 | 10.5% | −19.8% | 2.23% | 0.46 |
| The complement: SPY on every other session | 0.31 | 18.6% | −48.6% | 0.79% | |
| SPY held | 0.45 | 20.4% | −52.7% | 0.00% | |
| S0, the live model | 0.27 | 13.0% | −38.0% | 0.10% | |
| S1 blend long/flat | 0.33 | 4.8% | −8.5% | 0.08% | |
| 50/50 S0 + primary | 0.30 | 8.9% | −25.9% | | |

### The gate, criterion by criterion

| Criterion | Value | Pass |
|---|---|---|
| 1. Excess Sharpe ≥ 0.40 | 0.22 | **no** |
| 2. Deflated Sharpe ≥ 0.90 (n = 30) | 0.24 | **no** |
| 3. Bootstrap 5th percentile of the Sharpe > 0 | −0.07 | **no** |
| 4. Every variant's Sharpe > 0 | worst 0.38 | yes |
| 5. Max drawdown no worse than −20% | −15.1% | yes |
| 6. Cost drag ≤ 1% a year | 0.80% | yes |
| 7. No day outside the mandate | 0 days | yes |
| 8. Worst calendar month no worse than −10% | −7.3% | yes |
| 9. A return above SPY held's in each stress window | smallest margin +1.4 points (February 2018) | yes |
| 10. 50/50 with S0 beats S0's Sharpe | +0.03 | yes |

### Stress windows

| Window | Primary | SPY held | S0 |
|---|---|---|---|
| 2008 crisis (Sep 15 to Nov 20) | −0.5% | −39.9% | −26.9% |
| 2010 flash crash (Apr 23 to Jul 2) | −5.4% | −15.5% | −8.6% |
| 2011 (Jul 22 to Oct 3) | −11.8% | −18.2% | −10.3% |
| 2015–16 (Aug 17 to Feb 11) | −0.4% | −12.6% | −10.1% |
| February 2018 (Jan 26 to Feb 9) | −6.3% | −7.7% | −6.7% |
| Late 2018 (Sep 20 to Dec 24) | +1.2% | −18.7% | −7.8% |
| 2020 crash (Feb 19 to Mar 23) | +5.2% | −33.3% | −28.0% |
| 2022 | −2.0% | −18.2% | −9.8% |

In SPY a fifth of the time, it missed most of each crash, as any part-time
book would. That is criterion 9's floor, not an edge.

## Holdout, run once (2023-10-12 to 2026-10-16)

| Book | Excess Sharpe (SE) | Max drawdown |
|---|---|---|
| Primary | 0.48 (0.61) | −9.1% |
| The complement | 0.88 (0.68) | −15.7% |
| SPY held | 1.10 (0.73) | −18.7% |
| S0 | 0.65 (0.64) | −10.9% |

No kill fired (the rules: a Sharpe below −0.5, a drawdown worse than −15%).
The other days beat the turn of the month here too.

## After the verdict: FOMC days

This is not part of the gate. `day_edges.py` writes it to
`results/post_hoc_day_edges.csv`. SPY's daily return over T-bills, in bps:

| Days | 2008–2023 | 2008–2015 | 2016–2023 |
|---|---|---|---|
| Turn of the month | 5.6 (t = 0.48 against the rest) | 5.3 (t = 0.43) | 5.9 (t = 0.23) |
| FOMC days outside the turn of the month | 34.5 (t = 2.47 against the other days) | 53.7 (t = 2.70) | 12.0 (t = 0.49) |

The FOMC effect is what lifts V1, and it sits almost entirely before 2016.
That is Kurov, Wolfe and Gilbert's finding: the pre-FOMC drift disappeared
after 2015. **This isn't a new hypothesis to test on the same data.** An
FOMC-only rule chosen now would be chosen after seeing it work, on years when
it had already stopped working.

## Deflated Sharpe

30 trials so far: S1's 8, S3a's 6, R2's 9, R3's 4 and these 3. Their Sharpes'
variance is 0.0378 a year (R3's study had 0.0414). The primary's deflated
Sharpe is 0.24; V1's 0.50.

## What this says

- The known calendar anomalies, traded through the close as the fund trades,
  don't beat holding SPY over 2008–2023 after costs. The turn of the month
  shows only noise, and the FOMC drift faded as the literature says.
- Timing equity exposure by the calendar repeats R2's lesson about timing
  equity exposure by volatility. Each such sleeve is a part-time equity book,
  and over these years the full-time book was the one to beat.

## Files

- `HYPOTHESIS.md`: the rules, the 3 trials and the criteria, fixed before any
  run, with the costs amended at review.
- `fetch.py`: fetches and caches the FOMC calendar and ALFRED's release dates
  for the jobs and CPI reports (into `research/_data/`, git-ignored).
- `r1_calendar_macro.py`: one command reruns everything (`--stage walkforward`
  or `--stage holdout` for one stage).
- `day_edges.py`: the post-hoc table above.
- `results/`:
  - `verdict.csv` and the criteria for the walk-forward and the holdout
  - the books, stress windows and bootstrap
  - the days in SPY each year
  - the daily returns and the primary's decisions
  - the 30 trials and the deflated-Sharpe inputs
  - the data checks
  - `post_hoc_day_edges.csv`
