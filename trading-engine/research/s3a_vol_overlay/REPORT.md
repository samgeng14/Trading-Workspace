# S3a, a volatility-managed overlay on S0, and S0 with a trend overlay: report

Run on 2026-10-21 against HYPOTHESIS.md, fixed before any run.
`python s3a_vol_overlay.py` (with the repo's `.venv`) regenerates every number
here into `results/`, identically (checked). The data, costs, lag and split are
S1's (research/s1_trend), so the books compare.

## Verdict

- **H1, S0 scaled down when its volatility is high: fails the gate.** It
  passes six of the seven criteria, but its worst drawdown is 64% of S0's and
  the limit was 60%. The holdout would have killed it too: its drawdown there
  was 0.2 points deeper than S0's.
- **H2, S0 with a trend overlay (half S0, half S1 long/flat): passes.** It meets
  all seven criteria and the holdout doesn't kill it. By the rule fixed
  beforehand it goes to **shadow mode as S0's risk manager**: its targets and
  would-be orders logged daily beside the live S0 for at least three months
  (PLAN §9, step 7), with nothing sent.

What H2 passed on is a large cut in drawdown at no cost in Sharpe. It did not
pass on a higher Sharpe: its edge over S0 (+0.04) is noise.

## Walk-forward, 2008-01 to 2023-10 (15.8 years)

Costs on, lag 1, Sharpe in excess of BIL.

| Book | Excess Sharpe (SE) | Volatility | Max drawdown | Share of S0's |
|---|---|---|---|---|
| S0, the live model | 0.27 (0.26) | 13.0% | −38.0% | 100% |
| H1 primary (21-day window, 10% target) | 0.32 (0.26) | 9.3% | −24.4% | 64% |
| H2 primary (50/50 S0 + S1 long/flat) | 0.31 (0.26) | 8.2% | −22.1% | 58% |

### The gate, criterion by criterion

| Criterion | H1 | H2 |
|---|---|---|
| 1. Max drawdown ≤ 60% of S0's | 64%: **no** | 58%: yes |
| 2. Excess Sharpe ≥ S0's − 0.05 | +0.055: yes | +0.041: yes |
| 3. Bootstrap P(drawdown shallower) ≥ 80% | 98%: yes | 100%: yes |
| 4. Bootstrap P(Sharpe ≥ S0's − 0.05) ≥ 80% | 88%: yes | 95%: yes |
| 5. Cost drag ≤ 0.5% a year | 0.2%: yes | 0.1%: yes |
| 6. Loses less than S0 in 2008, the 2020 crash and 2022 | yes | yes |
| 7. No day outside the mandate | yes | yes |

### When S0 lost most

| Period | S0 | H1 | H2 |
|---|---|---|---|
| 2008 | −23.1% | −14.2% | −11.0% |
| 2020 crash (Feb 19 to Mar 23) | −28.0% | −13.5% | −17.2% |
| 2022 | −9.8% | −8.5% | −6.2% |

### Paired bootstrap against S0

63-day blocks, 2,000 resamples, the same blocks for both books.

| Book | Sharpe difference, 5th to 95th percentile | Drawdown cut, 5th to 95th percentile |
|---|---|---|
| H1 primary | −0.09 to +0.19 | +1.3 to +22.4 points |
| H2 primary | −0.05 to +0.12 | +6.8 to +22.2 points |

The drawdown cut is robust. The Sharpe difference straddles zero for both.

## Holdout, run once (2023-10 to 2026-10)

| Book | Excess Sharpe (SE) | Max drawdown |
|---|---|---|
| S0 | 0.66 (0.64) | −10.9% |
| H1 primary | 0.58 (0.62) | −11.1%: deeper than S0's, so the holdout kills it |
| H2 primary | 0.83 (0.67) | −7.4% |

## The variants (no verdict rests on them)

| Variant | Excess Sharpe | Max drawdown |
|---|---|---|
| H1, 63-day window, 10% | 0.29 | −26.2% |
| H1, 21-day window, 8% | 0.30 | −20.3% |
| H1, 63-day window, 8% | 0.29 | −21.2% |
| H2, S0 with a trend filter | 0.32 | −16.6% |

- The trend filter had the smallest drawdown, but it failed the Sharpe
  bootstrap (70% against 80%).
- An 8% target would have passed H1's drawdown criterion. Choosing it now
  would be fitting the gate to the results.

## Deflated Sharpe

Reported, not a criterion; 14 trials (S1's 8, H1's 4, H2's 2): S0 0.63, H1
0.71, H2 0.69. None shows a standalone edge over cash. These are overlays that
claim to beat S0, not to beat zero.

## Caveats

- **H2 isn't independent of S1.** It was declared after S1's report suggested
  it, and the long/flat versions had already scored better there. Shadow mode
  and paper are where it gets an honest out-of-sample test.
- **The 50/50 book is a mix of daily returns.** That assumes the two halves are
  brought back to equal size every day, at no cost. A live H2 holds one book of
  netted targets, so shadow mode should measure the real drift and trading.
- **Dividends before 2016-10 are missing,** as in S1's study, for every book
  alike.
- **H1 doubles S0's turnover** (1.2 to 2.5 times a year). The cost drag stays at
  0.2%.

## What next

H2 to shadow mode. Each day after the daily run, without sending anything:
- the H2 book's targets: S0's targets at half weight, plus S1 long/flat's at half
- the orders it would send against the account's positions
- its tracking against the live S0

After three months, compare the record with this backtest. If it holds,
paper-trade it in place of S0 (an owner decision).

## Files

- `HYPOTHESIS.md`: both hypotheses, the 14 trials and the criteria, fixed
  before any run.
- `s3a_vol_overlay.py`: reuses research/s1_trend's data and rules; one command
  reruns everything.
- `results/`:
  - the criteria, books, bootstrap and regimes for the walk-forward
  - the holdout's books and criteria
  - the daily returns and H1's daily decisions
