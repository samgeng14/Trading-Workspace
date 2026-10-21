# S1, cross-asset trend on ETF proxies: hypothesis and test plan

Written on 2026-10-21, before any backtest of S1 ran. Results go in REPORT.md;
this file is only appended to after that (docs/PLAN.md §9, research gate,
step 1).

## Mechanism

An asset's own return over the past 3 to 12 months predicts its return over
the next month: time-series momentum. Two explanations hold up:
- Investors under-react to news at first and over-react later.
- Hedgers (producers, insurers, pension funds) pay a premium to move risk,
  and slow-moving capital lets prices trend while it is absorbed.

The evidence is long and broad:
- Moskowitz, Ooi and Pedersen (2012): 58 futures markets, 1985–2009, a
  diversified Sharpe near 1.
- Hurst, Ooi and Pedersen (2017): positive returns in every decade since
  1880, and the best results in the worst equity markets.

Known weaknesses:
- Losses when trends reverse sharply (2009, 2016, 2019, the 2020 rebound).
- Weaker results across the industry since about 2010.
- Publication decay (McLean and Pontiff 2016).

Why it could earn a place next to S0: S0 is long-only cross-sectional momentum
(the strongest of 21 ETFs, always fully invested). S1 compares each asset
with its own past, can be short or flat, and sizes by risk. In a falling
market S1 should be flat or short where S0 is long, so the two should be
weakly correlated, and S1 should help most when S0 hurts most.

## Universe and data

One ETF per asset class, as proxies for the plan's futures universe:

| ETF | Asset class | First close on disk |
|---|---|---|
| SPY | US equity | 1993 |
| VEA | Developed ex-US equity | 2007-07 |
| VWO | Emerging equity | 2005-03 |
| TLT | Long Treasuries | 2002 |
| IEI | 3–7 year Treasuries | 2007-01 |
| GLD | Gold | 2004-11 |
| SLV | Silver | 2006-05 |
| USO | Crude oil | 2006-04 |
| UNG | Natural gas | 2010-03 |
| VNQ | US REITs | 2004-09 |
| BIL | T-bills: holds the unused budget | 2007-06 |

Not covered by ETF proxies: FX, grains, short rates, credit, futures carry.
The futures version adds those once the futures-curve archive has history.

Prices, read-only:
- `data/market/cleaned/prices/etf_prices_master.parquet`: raw daily closes and
  volumes.
- `etf_total_return_closes.parquet`: IBKR's dividend-adjusted closes, which
  start on 2016-10-18 only.

Signals and P&L use the raw close up to 2016-10-18 and the total-return close
after it, chained on that date. Before then the P&L leaves out dividends:
- Longs are understated by about each fund's yield (TLT about 3%, VNQ about
  4%, SPY about 2% a year).
- Shorts are flattered by the same amount.

A sensitivity run (declared here, not a strategy variant) adds each ETF's
average dividend yield, measured where both series overlap (2016–2026), back
to its pre-2016 daily returns.

## Rules

On each decision date t, using closes up to t only:

- **Eligibility:** an asset trades only with 253 closes up to t, so every
  variant sees the same assets on the same dates, and late listings (VEA 2007,
  UNG 2010) enter only when their history allows.
- **Signal:** r_L = P_t / P_{t−L} − 1 for L of 63, 126 and 252 trading days (3,
  6 and 12 months).
  - Single-lookback variants: s = sign(r_L).
  - The blend: the mean of the three signs.
  - Long/flat variants set every negative sign to 0, so the blend becomes the
    share of lookbacks that are positive.
- **Sizing, inside the no-leverage mandate:**
  - σ_i: the annualized standard deviation of asset i's last 63 daily returns.
  - Raw weight w_i = s_i / σ_i.
  - Scaled so that, with every eligible asset at full signal, gross would be
    99.5% of NAV: w_i × 0.995 / Σ_j (1 / σ_j) over the eligible assets. Weaker
    or fewer signals leave the book less invested; it is never scaled back up.
  - Each |w_i| is capped at 20%, the fund's one-name limit; the cut isn't
    reallocated.
  - Vol ceiling: when the risky book's ex-ante volatility (63-day sample
    covariance) is over 10% a year, every risky weight is scaled down to 10%.
    It only scales down (PLAN §9, allocation).
  - BIL gets 0.995 − Σ|w_i|.
  - Gross is at most 99.5% of NAV and cash at least 0.5% before costs: inside
    the mandate, long/short included.
- **Rebalance:** every 21 trading days, with lag 1: decided on the close of t,
  traded at the close of t+1. This matches the live run, which decides on
  yesterday's close and trades at today's close.

## Costs

`dv02_engine.costs.CostModel`:
- IBKR Pro Fixed commissions.
- Half-spread of 2 bps on every trade, conservative for these funds.
- Square-root impact with k = 0.5, on the master's volumes.
- Borrow: 25 bps a year on shorts, and 100 bps for USO, UNG and SLV. That's my
  guess for commodity ETFs, not a quote.
- No interest on cash (cash_interest_bps = 0): idle money sits in BIL, which
  earns the T-bill rate.

The mandate check runs on every day.

## Statistics

- Sharpe on daily returns in excess of BIL's daily return, the T-bill rate the
  money earns anyway. Annualized over 252 days. The harness's rf = 0 Sharpe is
  reported beside it.
- Standard error sqrt((1 + SR² / 2) / T), T in years.
- Deflated Sharpe ratio (Bailey and López de Prado 2014) on the same excess
  returns, n_trials = 8. The trials' Sharpe variance comes from the eight
  variants.
- Block bootstrap: 63-day blocks, 2,000 resamples, a fixed seed.
- Calendar-year returns for 2008, 2020 and 2022, and for the 2020 crash
  (2020-02-19 to 2020-03-23).
- Correlation with S0's daily returns. S0 is the live model's monthly targets
  (`data/market/cleaned/targets/etf_targets_monthly.parquet`), scaled to 99.5%
  and run through the same backtester, costs and lag.
- A 50/50 book of S0 and S1. Each sleeve gets half the capital, so its gross
  is at most 0.5 × 99.5% and the book stays within one 99.5% budget. Daily
  returns are mixed 50/50, with no netting of the two sleeves' trades, which
  is conservative on cost.

## Split

- The panel runs from 2005-01-03 to 2026-10-16.
- **Holdout:** the last 756 trading days (about 2023-10 to 2026-10). It runs
  once, after the walk-forward results are written down.
- **Walk-forward:** decisions from 2008-01-02 to the holdout, in yearly test
  blocks (252 rows) on an expanding window. The rules have no fitted
  parameters, so the folds only cut the out-of-sample record into years. The
  out-of-sample span is about 15.8 years and includes 2008.

## Variants (all eight count as trials)

Lookback (3m, 6m, 12m, blend) × direction (long/short, long/flat). Fixed in
all of them: inverse-vol sizing, 63-day vol, the 10% ceiling, the 20% cap,
monthly rebalancing.

**Primary, chosen now: the blend, long/short.** Hurst, Ooi and Pedersen use a
blend of horizons. The verdict rests on the primary. The other seven are the
parameter-sensitivity check and the trial count.

## Pass criteria (walk-forward, the primary, unless stated)

1. Excess Sharpe at least 0.40.
2. Deflated Sharpe at least 0.90. 0.95 is the stricter convention, but at
   about 16 years and n_trials = 8 it needs a Sharpe near 0.65, above the
   plan's target of about 0.5 per sleeve.
3. The block bootstrap's 5th-percentile excess Sharpe above 0.
4. All four long/short lookbacks with a positive excess Sharpe.
5. Max drawdown no worse than −20%.
6. A positive return in 2008 and in 2022.
7. Correlation with S0 at most 0.50, and the 50/50 S0+S1 book with a higher
   excess Sharpe than S0 alone.
8. Cost drag at most 1% a year. Turnover is reported.

**Holdout:** kill if its excess Sharpe is below −0.5 or its max drawdown worse
than −15%. Otherwise it changes nothing: three years can't confirm a Sharpe
(SE about 0.6).

**Verdict:**
- **Pass:** all eight, and the holdout doesn't kill it. Then shadow mode:
  signals and would-be orders logged daily for at least 3 months (PLAN §9,
  step 7), then paper.
- **Fail 1, 2 or 3:** no shadow, no paper.
- **Fail only 7:** S1 may stand on its own but doesn't earn a slot next to S0.
- **Fail any other:** the report says which, and the gate isn't passed.

## Appended after the results (2026-10-21)

"Gross is at most 99.5% of NAV ... inside the mandate, long/short included"
held on decision and trade days only. Between monthly rebalances a long/short
book's gross drifts as both legs move. The primary was over 100% on 923 of
about 3,970 days, at most 111.5% (2022-12-02); the engine's mandate check
caught it. Long/flat books can't drift over 100% and never did. Nothing else
in this file changed after the runs.
