# Research: every study through the gate

Each folder is one study of a sleeve, run through docs/PLAN.md §9's research
gate. A sleeve trades only after it passes, then three months of shadow, then
paper. Where each one stands is `config/sleeves.yaml`, which the dashboard's
Strategies page shows.

## The studies

| Folder | Question | Data | Trials | Verdict |
|---|---|---|---|---|
| `s1_trend/` | S1, cross-asset trend on ETF proxies | the ETF master | 8 | fails alone (Sharpe 0.15); hedges S0 |
| `s3a_vol_overlay/` | S0 scaled by its volatility (H1); S0 with a trend overlay (H2) | as S1 | 6 | H1 fails; **H2 passes, in shadow since 2026-10-22** |
| `r2_vol_positioning/` | VIX term-structure timing, as an overlay on S0; COT hedging pressure | Cboe VIX and VIX3M, CFTC Commitments of Traders | 9 | all three fail |
| `r3_vix_futures/` | short the VIX futures premium while in contango | Cboe VX settlements since 2004 | 4 | **passes, fragile; in shadow since 2026-10-22** |
| `r1_calendar_macro/` | the turn of the month; with FOMC days; with jobs and CPI days | the Fed's FOMC calendar, ALFRED's release dates | 3 | fails (Sharpe 0.22; the days earn no more than SPY's others) |
| `r4_overnight/` | SPY (or QQQ) held only from the close to the next open, through the auctions | the ETF master's opens and closes, dividends | 3 | fails (Sharpe 0.15; the day earned too, and a round trip a night costs 3.5% a year) |
| `r5_bond_carry/` | TLT (or IEI, SHY) while its yield is above the 3-month bill's, else T-bills | FRED's and Treasury's daily yields; the bond ETFs' coupons from two sources | 3 | fails (Sharpe 0.28, TLT held's 0.19; it held TLT through 2022) |
| `r6_oil_inventories/` | USO while US crude stocks are below their five-year norm, else T-bills | EIA's weekly inventories as first published (its archive of 795 reports), WTI's spot and NYMEX curve | 3 | fails (Sharpe −0.26, below USO held's −0.16 and 80% of random timings; in oil for the 2008 and 2020 crashes) |
| `r7_treasury_auctions/` | IEI (or SHY, TLT) for 5 sessions after each Treasury auction in its range, else T-bills | Treasury's auction records (Fiscal Data and TreasuryDirect, which agree on all 1,512 since 2005) | 0 | **not run**: at the published effects' full pre-2008 strength the best rule nets a Sharpe of 0.47 after costs, below the bar of about 0.89, and the effect faded after 2009 |

**39 trials are counted so far.** The deflated Sharpe of every study counts all
of them, and the newest study's `results/trials.csv` lists them with their
Sharpes. Their variance (0.0635 a year after R6, whose three trials were the
first well below 0) sets the bar each new study must clear.

## The protocol, as these studies ran it

**A study can end at phase 1, before it spends a trial.** Each failed trial
raises the bar for the next study: after R6 a new primary needs a Sharpe of
about 0.89 to reach a deflated Sharpe of 0.90. So phase 1 also asks whether the
published evidence, after costs, could plausibly clear that. R7 couldn't, and
wasn't run.

1. **`HYPOTHESIS.md` first,** before any backtest or any look at a rule's
   returns: the mechanism and the literature, the data and its checks, one
   primary rule, a few variants (each a counted trial), the costs, the split,
   and every pass criterion with its limit. From R3 on it is reviewed before
   anything runs. After that it is only appended to: a "Deviations" section
   says what couldn't be done as written, and why.
2. **The walk-forward:** the C++ engine's (`dv02_engine.walkforward`), with
   costs on (S1's CostModel unless the study states others), lag 1 (decided on
   a close, traded at the next), the no-leverage mandate checked every day, and
   decisions from 2008-01-02 in yearly test blocks on an expanding window.
3. **The holdout:** the last 756 sessions of the panel ending 2026-10-16 (from
   2023-10-12), run once, after the walk-forward's results are written down.
   It can only kill: its rules are fixed in the hypothesis.
4. **The verdict** is the hypothesis's rule, applied as written. No variant
   replaces a failed primary.
5. **`REPORT.md`** says the verdict and the numbers, and leads with whatever
   makes the result weaker than it looks.
6. **Reproducible:** one command (`python <study>.py`, from the folder, with
   the repo's `.venv`) regenerates every number in `results/`, and a second run
   must match byte for byte. The research code stays as it was run; production
   ports a rule into `src/strategies/` with a test pinning it to this code.

## Data

- Downloads are free and need no login or key. Each study's `data.py` or
  `fetch.py` caches them in `_data/` (git-ignored) and fetches again what's
  missing. They are data only: parsed, never run.
- WRDS needs a Duo prompt: never automated (CLAUDE.md).
- The ETF prices are the repo's master. Up to R4, every book left out
  dividends before 2016-10, all alike.
- **R5 put every book on total returns** (`r5_bond_carry/data.py`'s
  `tr_closes`): the vendor's total-return index to 2026-03-06, then IBKR's
  adjusted closes. A bond fund's coupon is most of its return. On that basis
  S0 scores 0.33 instead of 0.27. Later studies should use it.
- **IBKR's adjusted closes have errors the vendor's index shows:**
  - They miss BIL's $0.39 distribution on 2024-12-19, so the earlier
    studies' 2024 T-bill return (in their holdouts) is 0.42% too low.
  - One-day blips that reverse: TLT 2023-08-09; SLV, VNQ, VWO and VIXY
    2017-03-20; and SPY's that R4 found.
  - Since 2026-10-22 the live signals find and repair both kinds
    (`src/data/cleaning/total_return.py`); the studies' results stay as run.
