# S1, cross-asset trend on ETF proxies: report

Run on 2026-10-21 against the hypothesis fixed beforehand in HYPOTHESIS.md.
`python s1_trend.py` (with the repo's `.venv`) regenerates every number here
into `results/`, identically (checked). The prices are the owner's Mac's
refreshed price master (through 2026-10-16) and the git-ignored dividend-adjusted
closes. A fresh checkout's master ends 2026-05-06: it reproduces the
walk-forward, but not the whole holdout.

## Verdict

**S1 fails the research gate as a standalone sleeve: no shadow, no paper.**
The primary rule (blend of 3, 6 and 12-month signs, long/short, inverse-vol,
monthly) fails criteria 1 to 4: its out-of-sample Sharpe is indistinguishable
from zero. It passes the other four: drawdown, crisis behaviour, low
correlation with S0, and cost drag.

It is a good hedge for S0, and that is what to test next (below).

## Headline numbers

Walk-forward out of sample, 2008-01 to 2023-10 (15.8 years), costs on, lag 1.
Sharpe is in excess of BIL, the T-bill return the money earns anyway.

| Book | Excess Sharpe (SE) | Annual excess return | Volatility | Max drawdown |
|---|---|---|---|---|
| S1 primary | 0.15 (0.25) | 0.9% | 6.0% | −10.3% |
| S0, the live model | 0.27 (0.26) | 3.5% | 13.0% | −38.0% |
| 50/50 S0 + S1 | 0.32 (0.26) | 2.2% | 6.9% | −16.2% |

- Deflated Sharpe of the primary, counting all 8 variants: 0.44. The gate needs 0.90.
- Block bootstrap, 63-day blocks: 5th percentile −0.22, median 0.14, 95th 0.50.
- Turnover 2.4 times a year; cost drag 0.25% a year.

## The gate, criterion by criterion

| Criterion | Value | Pass |
|---|---|---|
| 1. Excess Sharpe ≥ 0.40 | 0.15 | no |
| 2. Deflated Sharpe ≥ 0.90 (8 trials) | 0.44 | no |
| 3. Bootstrap 5th percentile > 0 | −0.22 | no |
| 4. Every long/short lookback > 0 | 12-month: −0.02 | no |
| 5. Max drawdown no worse than −20% | −10.3% | yes |
| 6. Positive in 2008 and 2022 | +10.2%, +2.2% | yes |
| 7. Correlation with S0 ≤ 0.50, and the 50/50 book beats S0 | −0.08; 0.32 vs 0.27 | yes |
| 8. Cost drag ≤ 1% a year | 0.25% | yes |

## What it does well: it pays when S0 loses

| Period | S1 primary | S0 | 50/50 |
|---|---|---|---|
| 2008 | +10.2% | −23.1% | −6.9% |
| 2020 crash (Feb 19 to Mar 23) | +2.8% | −28.0% | −13.5% |
| 2022 | +2.2% | −9.8% | −3.5% |

- Daily correlation with S0 is −0.08; monthly, −0.04.
- In S0's worst tenth of months S0 averages −6.7% and S1 +0.2%.
- Held half and half inside the one 100% gross budget, the two keep S0's
  return per unit of risk and cut its worst drawdown from −38% to −16%.

## The variants (parameter sensitivity)

| Variant | Excess Sharpe | Max drawdown | Days over 100% gross |
|---|---|---|---|
| 3m long/short | 0.13 | −18.6% | 1,163 |
| 6m long/short | 0.22 | −11.8% | 1,206 |
| 12m long/short | −0.02 | −18.3% | 1,310 |
| blend long/short (primary) | 0.15 | −10.3% | 923 |
| 3m long/flat | 0.33 | −12.4% | 0 |
| 6m long/flat | 0.37 | −8.3% | 0 |
| 12m long/flat | 0.22 | −11.0% | 0 |
| blend long/flat | 0.33 | −8.5% | 0 |

The long/flat versions do better (best deflated Sharpe 0.76), but they weren't
the declared primary, so they don't change the verdict. Choosing one now would
be fitting the gate to the results.

## Holdout, run once (2023-10 to 2026-10)

| Book | Excess Sharpe (SE) | Max drawdown | Correlation with S0 |
|---|---|---|---|
| S1 primary | 0.57 (0.62) | −5.7% | 0.62 |
| S0 | 0.66 (0.64) | −10.9% | 1.00 |
| 50/50 | 0.69 (0.64) | −6.8% | 0.96 |

The holdout doesn't kill S1 (the kill was a Sharpe under −0.5 or a drawdown
worse than −15%). In this bull market the two moved together, as trend does
when everything rises. Three years can't confirm a Sharpe: the standard error
is about 0.6.

## Found along the way

- **S0 wouldn't pass this gate either.** Its 0.27 (0.26) out of sample is no
  different from zero, and its −38% drawdown is outside the 20% criterion.
  The live model is a plumbing test (PLAN §9, S0), and these numbers say to
  keep treating it as one.
- **A long/short book drifts over the mandate between rebalances.** Decided
  at 99.5% gross, the primary was over 100% on 923 days, peaking at 111.5% on
  2022-12-02; the engine's daily mandate check caught it. A long/short sleeve
  needs about 90% gross at rebalance, or a daily trim back under 100%.
  Long-only and long/flat books can't drift over.
- **Dividends before 2016-10 are missing.** IBKR's adjusted closes on disk
  start on 2016-10-18 (IEI and SHY on 2017-08-03), so earlier P&L is price
  only. Adding each fund's average yield back gives S1 0.23 and S0 0.34: the
  same verdict. Fetching IBKR's adjusted history further back would remove the
  approximation.
- **Data to check:** UNG misses 28 days in 2010 and VIXY 263 (forward-filled;
  VIXY is S0's only). SLV fell 28.5% on 2026-01-30, in the holdout; confirm it
  against a second source.

## What next

1. **Pre-register "S0 with a trend overlay"** as its own hypothesis: the 50/50
   book above, or S1 long/flat as S0's risk manager. It must count the 8
   trials already spent. The drawdown benefit appears in every variant,
   which is what makes it worth a test.
2. **The futures version of S1**, the one the literature's Sharpe comes from:
   about 20 markets instead of 10 (FX, rate tenors, grains, Brent), trend now
   on IBKR's continuous futures history, carry from the curve archive
   recorded every evening since 2026-10-21. Carry can only be tested on the
   years the archive covers, so the archive comes first.
3. Fix the data: IBKR's adjusted closes back to each fund's start, and the
   UNG, VIXY and SLV checks above.

## Files

- `HYPOTHESIS.md`: the rules, the split, the trials and the pass criteria,
  fixed before any run; one note appended after.
- `s1_trend.py`: one command reruns everything.
  `--stage walkforward` never lets the holdout reach the backtester.
- `results/`:
  - `walkforward_criteria.csv`: the gate checklist
  - `walkforward_books.csv`, `walkforward_variants.csv`,
    `walkforward_regimes.csv`, `walkforward_correlation.csv`,
    `walkforward_bootstrap.csv`, `walkforward_dividend_sensitivity.csv`
  - `holdout_*.csv`, `data_checks.csv`
  - the daily returns and the primary's weights
