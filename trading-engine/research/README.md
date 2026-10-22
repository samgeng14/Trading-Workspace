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

**30 trials are counted so far.** The deflated Sharpe of every study counts all
of them, and the newest study's `results/trials.csv` lists them with their
Sharpes. Their variance (0.0378 a year after R1) sets the bar each new study
must clear.

## The protocol, as these studies ran it

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
- The ETF prices are the repo's master. Before 2016-10 they leave out
  dividends, for every book alike.
