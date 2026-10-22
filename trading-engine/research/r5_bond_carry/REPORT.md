# R5, timing Treasury duration by the curve's carry: report

Run on 2026-10-22 against HYPOTHESIS.md. It was fixed before any run and
reviewed. The review kept the draft's rule (the sign of the carry), its
total-return basis and criterion 11's 2022 window. It tightened criterion 5 to
a drawdown no deeper than 60% of TLT held's, as S3a and R4 judged theirs. No
deviation was needed.

`python r5_bond_carry.py` (with the repo's `.venv`) regenerates every number
into `results/` in about four seconds, and three full runs matched all 22
files byte for byte. S0 on the usual basis scores 0.268623, R4's exact figure.

## Verdict

**TLT while its carry is positive fails the gate, on 5 of its 14 criteria:**
- its Sharpe: 0.28 against 0.40;
- its deflated Sharpe: 0.33 against 0.90;
- its bootstrap: the 5th percentile is −0.14;
- its drawdown: −44.0% against a limit of −28.1%;
- 2022 beside S0.

The holdout fired no kill. The primary isn't taken further.

**Most of the verdict grades holding TLT, not the timing.**
- **The rule held TLT on 178 of its 190 decisions,** as the file counted
  before the run. So criteria 1, 2, 3, 5 and 11b mostly measure long
  Treasuries from 2008 to 2023. TLT held scored 0.19.
- **Only criterion 1b tests the timing, and it passed: 0.28 against TLT
  held's 0.19.** But the walk-forward had two episodes in bills, and one of
  them, from December 2022, made nearly all the difference. Two episodes can't
  establish an edge.
- **2022 decided criteria 5 and 11b.** TLT's carry turned negative only at
  the end of November 2022, so the rule held TLT for nearly all of 2022 and
  lost 26.8% that year (TLT held lost 31.1%). Beside S0, the 50/50 book lost
  18.3% against S0's 9.8%.

## Walk-forward, 2008-01 to 2023-10 (15.8 years)

Costs on, lag 1, total returns, Sharpe in excess of BIL with its income.

| Book | Excess Sharpe (SE) | Return a year | Volatility | Max drawdown | Cost drag a year |
|---|---|---|---|---|---|
| **Primary: TLT while its carry is positive** | 0.28 (0.26) | 3.9% | 14.9% | −44.0% | 0.01% |
| V1: IEI while its carry is positive | 0.33 | 1.9% | 3.7% | −14.5% | 0.02% |
| V2: SHY while its carry is positive | 0.19 | 1.0% | 1.2% | −5.8% | 0.03% |
| TLT held | 0.19 | 2.5% | 15.5% | −46.9% | 0.00% |
| IEI held | 0.34 | 2.0% | 4.1% | −14.5% | 0.01% |
| SHY held | 0.26 | 1.1% | 1.5% | −5.7% | 0.00% |
| S0_TR, the live model with its distributions | 0.33 | 4.3% | 13.0% | −36.7% | 0.09% |
| S0 on the usual basis | 0.27 | 3.3% | 13.0% | −38.0% | 0.10% |
| 50/50 S0_TR + primary | 0.49 | 4.7% | 8.6% | −26.1% | 0.05% |

The file gives half-spreads only for the four bond ETFs, so S0_TR's other
ETFs keep S1's default of 2 bps (R4's S0 had SPY at 1). S0's whole cost drag
is 0.09% a year, so that reading changes nothing.

### The gate, criterion by criterion

| Criterion | Value | Pass |
|---|---|---|
| 1. Excess Sharpe ≥ 0.40 | 0.28 | **no** |
| 1b. Excess Sharpe above TLT held's | 0.28 against 0.19 | yes |
| 2. Deflated Sharpe ≥ 0.90 (n = 36) | 0.33 | **no** |
| 3. Bootstrap 5th percentile of the Sharpe > 0 | −0.14 | **no** |
| 4. Every variant's Sharpe > 0 | worst 0.19 (V2) | yes |
| 5. Max drawdown no deeper than 60% of TLT held's (−28.1%) | −44.0% | **no** |
| 6. Cost drag ≤ 1% a year | 0.01% | yes |
| 7. No day outside the mandate | 0 days | yes |
| 8. Worst day no worse than −12% | −6.6% (2020-03-17) | yes |
| 9. Worst month no worse than TLT held's (−13.0%, January 2009) | −13.0%, the same month | yes |
| 10. In each of 17 stress windows, no more than 0.25 points below TLT held | smallest margin 0.00 | yes |
| 11a. 50/50 with S0_TR beats S0_TR's Sharpe | 0.49 against 0.33 | yes |
| 11b. 50/50 with S0_TR loses no more than S0_TR in each of R3's windows | 2022: −18.3% against −9.8% | **no** |
| 12. Daily correlation with S0_TR ≤ 0.50 | −0.25 | yes |

### The timing, episode by episode

Each stretch a rule spent in bills, against its own ETF held over the same
sessions. The switch back's cost is inside; the switch in filled the session
before.

| Rule | In bills | Sessions | The rule | Its ETF held | Difference |
|---|---|---|---|---|---|
| Primary (TLT) | 2019-09-04 to 2019-10-01 | 20 | +0.1% | −2.5% | +2.6 points |
| Primary (TLT) | 2022-12-02 to 2023-10-11, the walk-forward's end | 215 | +4.0% | −14.0% | +18.0 points |
| V1 (IEI) | 2019-04-02 to 2019-12-02 | 170 | +1.3% | +4.2% | −2.9 points |
| V1 (IEI) | 2020-02-04 to 2020-04-01 | 41 | +0.2% | +4.6% | −4.4 points |
| V1 (IEI) | 2022-12-02 to 2023-10-11 | 215 | +4.0% | −1.0% | +5.0 points |
| V2 (SHY) | 2008-01-04 to 2008-02-01 | 20 | +0.3% | +1.4% | −1.1 points |
| V2 (SHY) | 2008-03-04 to 2008-04-01 | 20 | +0.1% | −0.1% | +0.2 points |
| V2 (SHY) | 2019-04-02 to 2019-12-02 | 170 | +1.3% | +2.3% | −1.0 points |
| V2 (SHY) | 2020-02-04 to 2020-04-01 | 41 | +0.3% | +2.2% | −1.9 points |
| V2 (SHY) | 2020-07-02 to 2020-08-03 | 22 | 0.0% | +0.1% | −0.2 points |
| V2 (SHY) | 2023-01-04 to 2023-10-11 | 194 | +3.7% | +1.8% | +1.9 points |

- **At the long end, the carry's sign was right both times.**
- **At the short and middle of the curve it was wrong in 2019 and early
  2020** (V2 in early 2008 too), and right in 2023. The 2- and 5-year yields
  fell below the bill's because the market expected the Fed to cut, and the
  notes rallied while the rules sat in bills.
- **Each variant scored below its ETF held:** V1 0.33 against IEI held's
  0.34, V2 0.19 against SHY held's 0.26.

### Stress windows

R3's eight windows, the fund's crashes:

| Window | Primary | TLT held | S0_TR | 50/50 S0_TR + primary |
|---|---|---|---|---|
| 2008 crisis (Sep 15 to Nov 20) | +10.7% | +10.7% | −26.6% | −9.2% |
| 2010 flash crash (Apr 23 to Jul 2) | +12.5% | +12.5% | −8.4% | +1.9% |
| 2011 (Jul 22 to Oct 3) | +30.8% | +30.8% | −10.0% | +9.0% |
| 2015–16 (Aug 17 to Feb 11) | +9.2% | +9.2% | −9.3% | −0.2% |
| February 2018 (Jan 26 to Feb 9) | −4.7% | −4.7% | −6.7% | −5.7% |
| Late 2018 (Sep 20 to Dec 24) | +5.0% | +5.0% | −7.8% | −1.5% |
| 2020 crash (Feb 19 to Mar 23) | +14.2% | +14.2% | −28.0% | −8.3% |
| **2022** | −26.8% | −31.1% | −9.8% | **−18.3%** |

The bond sell-offs, every rise of 100 bps or more in the 10-year yield:

| Sell-off (the 10-year's rise) | Primary | TLT held | S0_TR | 50/50 S0_TR + primary |
|---|---|---|---|---|
| 2009-03-18 to 2009-06-10 (+147 bps) | −11.4% | −11.4% | +14.8% | +1.1% |
| 2010-10-06 to 2011-02-08 (+134) | −13.9% | −13.9% | +7.3% | −3.8% |
| 2012-07-25 to 2013-12-31 (+161) | −19.3% | −19.3% | +11.2% | −4.8% |
| 2016-07-05 to 2017-03-13 (+125) | −15.7% | −15.7% | +2.1% | −7.1% |
| 2017-09-07 to 2018-11-08 (+119) | −9.3% | −9.3% | +5.5% | −1.9% |
| 2020-08-04 to 2021-03-19 (+122) | −20.0% | −20.0% | +15.7% | −3.5% |
| 2021-07-19 to 2022-06-14 (+230) | −25.4% | −25.4% | −1.3% | −13.7% |
| 2022-08-01 to 2022-10-24 (+165) | −20.7% | −20.7% | −11.2% | −16.0% |
| 2023-04-05 to 2023-10-11 (+168, judged to the walk-forward's end) | +2.6% | −15.9% | −1.4% | +0.6% |

- **The rule was in TLT for every sell-off but 2023's** and lost what TLT
  lost. The carry stayed positive through all of them. It turned only in
  late 2022, after most of that year's fall, once the Fed had raised the
  bill's yield past the long one.
- **The equity crashes and the bond sell-offs split the record.**
  - Beside S0, the sleeve cut S0's loss by half or more in six of R3's eight
    windows, and a little in February 2018.
  - S0 gained in each of the six bond sell-offs from 2009 to early 2021, so
    those cost the 50/50 book little.
  - From mid-2021 the two fell together, and 2022 nearly doubled S0's loss.

### Costs

| Costs | Primary | V1 (IEI) | V2 (SHY) | TLT held |
|---|---|---|---|---|
| 0× | 0.281 | 0.336 | 0.204 | 0.188 |
| 1× | 0.281 | 0.330 | 0.188 | 0.187 |
| 3× | 0.279 | 0.318 | 0.157 | 0.187 |

Excess Sharpe at each multiple of the half-spreads and impact; the commission
stays at 1×. The primary switched three times in 15.8 years, so costs don't
matter to it.

## Holdout, run once (2023-10-12 to 2026-10-16)

| Book | Excess Sharpe (SE) | Return a year | Max drawdown | Worst day |
|---|---|---|---|---|
| Primary | −0.28 (0.59) | 1.7% | −12.4% | −3.0% (2025-04-07) |
| TLT held | −0.20 (0.58) | 0.9% | −16.5% | −3.0% (2025-04-07) |
| S0_TR | 0.64 (0.63) | 11.6% | −10.9% | −5.8% (2026-01-30) |
| 50/50 S0_TR + primary | 0.33 (0.59) | 6.8% | −7.2% | −3.2% (2026-01-30) |

- **No kill fired.** The kills: a Sharpe below −0.5, a drawdown deeper than
  TLT held's, or a day worse than −12%.
- **The rule started the holdout in bills** and stayed there to 2025-01-02,
  308 sessions: the 2024-11 decision still read a negative carry. It made
  +6.4% against TLT held's +4.0%, and lost 1.1% in the holdout's sell-off
  (2024-09-16 to 2025-01-13, +116 bps) against TLT held's 13.7%.
- **The primary's Sharpe trails TLT held's although it made more,** 1.7% a
  year against 0.9%, with a shallower drawdown. Both trailed bills, and the
  primary's lower volatility makes its smaller shortfall the larger negative
  ratio.

## Deflated Sharpe

36 trials so far: S1's 8, S3a's 6, R2's 9, R3's 4, R1's 3, R4's 3 and these 3.
Their Sharpes' variance is 0.0335 a year (0.0360 after R4). The primary's
deflated Sharpe is 0.33. The first 33 were measured on the price basis and
these three on total returns; the bar counts all 36 alike.

## After the verdict

These were found after the results, so they aren't hypotheses to test on this
data:
- **The 50/50 book's gain came from the stock-bond correlation, and the
  correlation turned in 2021.**
  - The sleeve's daily correlation with S0_TR was −0.25.
  - Beside S0 it lifted the Sharpe from 0.33 to 0.49, the largest gain any
    study has shown.
  - In 2022 rising yields hit both halves. In the holdout the 50/50 scored
    0.33 against S0_TR's 0.64.
- **S0 with its distributions scores 0.33, not 0.27.** Every earlier study
  priced S0 without its ETFs' distributions before IBKR's adjusted closes
  begin (2016-10).
  - Their comparisons with S0 were fair, since both sides stood on one basis.
  - But S0's own figure in `config/sleeves.yaml` (0.27, from S1's report)
    understates it by about 0.06.
- **IBKR's adjusted closes miss BIL's $0.39 distribution of 2024-12-19.**
  - So the earlier studies' T-bill return for 2024, inside their holdouts, is
    0.42% too low, and their excess returns that year too high by as much.
  - R5's series take the vendor's index there.
  - The live model's signals read the same closes. Their lookbacks (63 and
    126 sessions) passed that day long ago.

## What this says

- **From 2008 to 2023 the carry's sign said little about when to hold long
  Treasuries.**
  - It stayed positive through the long rally and through most of 2022's
    fall, and turned only once the Fed had pushed the bill's yield above the
    long one.
  - Its one large success, the inverted curve of 2023, is the window's
    best-known episode, and two episodes can't separate an edge from luck.
- **Long duration hedged the fund's growth scares and deepened its inflation
  scare.** TLT gained in six of the fund's eight crashes and lost in the two
  that came with rising yields, February 2018 and 2022. A duration sleeve is
  a bet that the stock-bond correlation stays negative. The gate's 2022 test
  stopped it, as designed.
- **For the research queue:** a sleeve that holds bonds needs a defence
  against inflation shocks, not only growth shocks. Any test of when to hold
  duration has few turns to learn from in 2008 to 2023. The yields go back to
  1962, so a longer test is possible, but it would be a new study,
  pre-registered on its own.

## Files

- `HYPOTHESIS.md`: the rules, the 3 trials and the criteria, fixed before any
  run, with the review's note and its amendment to criterion 5.
- `data.py`: the data checks:
  - the yields against Treasury's own files;
  - the coupons from two sources, each year's income and duration;
  - the carry on each decision day;
  - the sell-offs and the costs' inputs.
  `python data.py` fetches what's missing into `research/_data/` and prints
  every check.
- `r5_bond_carry.py`: one command reruns everything (`--stage walkforward` or
  `--stage holdout` for one stage).
- `results/`:
  - `verdict.csv` and the criteria for the walk-forward and the holdout
  - the books, the stress windows and the bootstrap
  - the episodes in bills, and each rule against its own ETF held
  - the cost runs at 0, 1 and 3×, and the correlations
  - the daily returns, the total-return series and each rule's decisions
  - the 36 trials and the deflated-Sharpe inputs
  - the data checks
