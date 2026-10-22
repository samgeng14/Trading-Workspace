# R2, volatility and positioning data: report

Run on 2026-10-21 against HYPOTHESIS.md, fixed before any run (its Deviations
section, on COT reports the CFTC published late, was also appended before any
run). `python r2_vol_positioning.py` (with the repo's `.venv`) regenerates every
number here into `results/`; a second run matched all 22 files byte for byte.
The prices, costs, lag and split are S1's (research/s1_trend), and S0 from 2008
scores 0.268481 here, exactly as in S3a's results, so the books compare.

## Verdict

**All three hypotheses fail the gate. None goes to shadow mode.**

- **H1, the VIX term structure as risk timing (SPY while VIX is below VIX3M,
  else T-bills): fails, 3 of 8 criteria.** A Sharpe of 0.51 sounds good, but
  holding SPY over the same years scored 0.69. Leaving at inversions missed
  the rebounds and saved nothing on drawdown. In 2022 it lost 30.9% against
  SPY's 18.2%.
- **H1b, the same signal as an overlay on S0: fails, 1 of 7, and the holdout
  kills it.** It lowered S0's Sharpe (0.29 against 0.40), and it lost more
  than S0 in 2018 and 2022.
- **H2, COT hedging pressure on ETF proxies: fails, 4 of 10.** A Sharpe of
  0.27 with a bootstrap 5th percentile of −0.15. It lost money in 2008 and
  2022, and it correlates 0.65 with S0 and 0.71 with S1. What it holds is
  mostly a smaller version of a long book the fund already has.

## H1 and H1b: walk-forward, 2009-10 to 2023-10 (14.0 years)

VIX3M's free history starts in September 2009, so these windows start then.
Costs on, lag 1, Sharpe in excess of BIL.

| Book | Excess Sharpe (SE) | Volatility | Max drawdown | Cost drag a year |
|---|---|---|---|---|
| SPY held | 0.69 (0.30) | 17.4% | −33.7% | 0.0% |
| **H1 primary** (ratio ≥ 1.00 is out, daily) | 0.51 (0.28) | 13.8% | −34.6% | 0.6% |
| S0, the live model | 0.40 (0.28) | 11.7% | −28.3% | 0.1% |
| **H1b** (S0 to BIL while inverted) | 0.29 (0.27) | 9.5% | −21.6% | 0.9% |
| S1 blend long/flat (reference) | 0.33 (0.27) | 4.5% | −8.5% | 0.1% |

### H1, criterion by criterion

| Criterion | Value | Pass |
|---|---|---|
| 1. Excess Sharpe ≥ 0.40 | 0.51 | yes |
| 2. Deflated Sharpe ≥ 0.90 (n = 23) | 0.79 | **no** |
| 3. Bootstrap P(Sharpe ≥ SPY's) ≥ 80% | 9% | **no** |
| 4. Max drawdown ≤ 60% of SPY's | 103% | **no** |
| 5. Every variant's Sharpe ≥ SPY's | worst 0.31, 0.38 short | **no** |
| 6. Cost drag ≤ 1% a year | 0.6% | yes |
| 7. Loses less than SPY in each stress window | not in 2022 | **no** |
| 8. No day outside the mandate | 0 days | yes |

### H1b, criterion by criterion (against S0)

| Criterion | Value | Pass |
|---|---|---|
| 1. Max drawdown ≤ 60% of S0's | 76% | **no** |
| 2. Excess Sharpe ≥ S0's − 0.05 | −0.105 | **no** |
| 3. Bootstrap P(drawdown shallower) ≥ 80% | 59% | **no** |
| 4. Bootstrap P(Sharpe ≥ S0's − 0.05) ≥ 80% | 33% | **no** |
| 5. Cost drag ≤ 0.5% a year | 0.9% | **no** |
| 6. Loses less than S0 in each stress window | not in 2018 or 2022 | **no** |
| 7. No day outside the mandate | 0 days | yes |

### The stress windows

| Window | SPY held | H1 | S0 | H1b |
|---|---|---|---|---|
| 2011 (Jul 22 to Oct 3) | −18.2% | −13.1% | −10.3% | −6.3% |
| 2015–16 (Aug 17 to Feb 11) | −12.7% | −12.0% | −10.1% | −8.6% |
| 2018 (Sep 20 to Dec 24) | −18.7% | −15.9% | −7.8% | −8.6% |
| 2020 crash (Feb 19 to Mar 23) | −33.4% | −6.9% | −28.0% | −3.8% |
| 2022 | −18.2% | −30.9% | −9.8% | −14.0% |

The signal worked as described in the 2020 crash: it was out before the worst
of it. Its weakness is a grinding decline like 2022's: the curve inverted and
recovered again and again, and the rule went out and back in nine times that
year, each round trip selling low and buying back higher.

### The variants (no verdict rests on them)

| Variant | Excess Sharpe | Max drawdown |
|---|---|---|
| ratio ≥ 0.95 is out, daily | 0.56 | −33.7% |
| ratio ≥ 1.00 on its 5-day mean | 0.66 | −28.3% |
| ratio ≥ 0.95 on its 5-day mean | 0.31 | −32.1% |

Smoothing helped the 1.00 threshold and hurt the 0.95 one. Every variant
scored below holding SPY. The best of them has a deflated Sharpe of 0.915.
It is still a variant, and a variant can't replace a failed primary.

## H2: walk-forward, 2008-01 to 2023-10 (15.8 years)

| Book | Excess Sharpe (SE) | Volatility | Max drawdown | 2008 | 2022 |
|---|---|---|---|---|---|
| **H2 primary, TS-156** (hedging pressure above its 3-year median) | 0.27 (0.26) | 3.8% | −10.8% | −2.3% | −5.9% |
| TS-52 | 0.07 | 4.1% | −15.8% | −9.3% | −5.3% |
| XS-level (top half by hedging pressure) | 0.32 | 5.0% | −13.5% | −0.5% | −0.1% |
| Change-4w (Kang et al.'s liquidity premium) | 0.20 | 4.1% | −12.3% | −5.2% | −1.1% |
| S0 | 0.27 (0.26) | 13.0% | −38.0% | −23.1% | −9.8% |
| 50/50 S0 + H2 primary | 0.29 | 7.9% | −23.8% | −12.9% | −7.7% |

| Criterion | Value | Pass |
|---|---|---|
| 1. Excess Sharpe ≥ 0.40 | 0.27 | **no** |
| 2. Deflated Sharpe ≥ 0.90 (n = 23) | 0.48 | **no** |
| 3. Bootstrap 5th percentile of the Sharpe > 0 | −0.15 | **no** |
| 4. Every variant's Sharpe > 0 | worst 0.07 | yes |
| 5. Max drawdown no worse than −20% | −10.8% | yes |
| 6. Positive in 2008 and in 2022 | −2.3%, −5.9% | **no** |
| 7. Correlation with S0 ≤ 0.50; the 50/50 book beats S0 | 0.65; +0.02 | **no**; yes |
| 8. Cost drag ≤ 1% a year | 0.1% | yes |

The sleeve held 36% of NAV in risky assets on average and the rest in BIL. Its
low volatility comes from that cash, not from hedging. Run with the plain 7-day
rule for every report, including the 34 published late, it scores 0.271
against 0.274, so the late reports aren't what decided it.

## Holdout, run once (2023-10-12 to 2026-10-16)

| Book | Excess Sharpe (SE) | Max drawdown | Kill rule | Killed |
|---|---|---|---|---|
| SPY held | 1.10 (0.73) | −18.7% | | |
| H1 primary | 0.95 (0.70) | −11.3% | Sharpe more than 0.5 below SPY's, or a deeper drawdown | no |
| S0 | 0.65 (0.64) | −10.9% | | |
| H1b | 0.50 (0.61) | −13.6% | a deeper drawdown than S0's, or a Sharpe more than 0.5 below | **yes**: deeper drawdown |
| H2 primary | 0.67 (0.64) | −5.9% | Sharpe below −0.5, or a drawdown worse than −15% | no |

The holdout can only kill. H1 and H2 survive it, but they had already failed
the walk-forward.

## Deflated Sharpe

23 trials so far (S1's 8, S3a's 6, these 9). The variance of their Sharpes is
0.0215 a year. Deflated Sharpes are reported for every primary and are a
criterion for H1 and H2: H1 0.79, H1b 0.51, H2 0.48. For comparison, holding
SPY over H1's window is 0.93 and S1 blend long/flat 0.57.

## What this says

- **Timing the market off the VIX curve gives up return over these years.**
  2009 to 2023 rewarded staying invested. A signal that only cuts risk has to
  cut it a lot to make up the difference, and this one didn't cut the
  drawdown at all. The literature's evidence is stronger for predicting VIX
  futures returns, through the roll, than for timing equities. That is S3b,
  the volatility risk premium sleeve, which needs VIX futures history.
- **Hedging pressure on its own is a weak premium in these ten markets.**
  This fits Kang, Rouwenhorst and Tang's finding that the level effect fades
  once the short-term liquidity premium is controlled for. Their change
  variant didn't save it here either (0.20).
- **None of it is uncorrelated with what the fund already holds.** H2 is a
  diluted long book; H1 is SPY most days (92%). The roadmap needs sources of
  return that aren't the equity premium. Futures carry and a market-neutral
  equity book are the candidates (PLAN §9, S1 with carry and S2).

## Files

- `HYPOTHESIS.md`: the three hypotheses, the 9 trials and the criteria, fixed
  before any run, with the Deviations section.
- `data.py`: fetches and caches Cboe's index histories and the CFTC's legacy
  futures-only reports (into `research/_data/`, git-ignored).
- `r2_vol_positioning.py`: one command reruns everything (`--stage walkforward`
  or `--stage holdout` for one stage).
- `results/`:
  - `verdict.csv` and the criteria for the walk-forward and the holdout
  - the books, variants, stress windows, regimes and bootstraps
  - the daily returns, each primary's decisions, and H2's signals
  - the 23 trials and the deflated-Sharpe inputs
  - the data checks
