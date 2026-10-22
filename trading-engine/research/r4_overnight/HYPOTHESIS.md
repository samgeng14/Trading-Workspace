# R4, the overnight drift: hypotheses and test plan

Written on 2026-10-22, before any backtest in this folder ran. This file is
only appended to after the results (docs/PLAN.md §9, research gate, step 1;
research/README.md).

Already read:
- the reports and hypotheses of research/r1_calendar_macro and
  research/r3_vix_futures;
- research/README.md;
- R1's trials.csv, which lists the 30 trials counted so far.

What was looked at before this was written: the papers below, and only what
`python data.py` prints:
- the opens' integrity;
- the two price sources against each other;
- the dividends from two sources;
- the calendar;
- the lowest and highest closes since 2008, for the commission in bps;
- the dates, not the sizes, of the sessions that opened more than 7% from
  the prior close, to confirm each was a real event.

No rule's return, no average overnight or intraday return, no SPY return on
any day.

## Data, all on disk (`data.py`)

| Source | What | Coverage |
|---|---|---|
| The ETF master (`etf_prices_master.parquet`) | open, high, low, close, volume | SPY from 1993-02-01, QQQ from 1999-03-11, both to 2026-10-16 |
| The vendor files the master was built from (`data/market/raw/etf/`) | the same bars and a gross total-return index | to 2026-03-06; the master holds them to 2026-02-09 |
| Most likely IBKR's daily bars (TRADES, regular hours, `append_ibkr_daily.py`) | the master's rows from 2026-02-10 | 173 sessions |
| IBKR's dividend-adjusted closes (`etf_total_return_closes.parquet`) | a second reading of the dividends | from 2016-10-19 (SPY) |
| S1's panel (`s1_trend.load_panel`) | BIL, and S0's ETFs for S0 | from 2005-01-03 |

What the checks found:
- **The opens are complete and inside every day's range.** No open is
  missing, at or below zero, or outside the day's low and high, and no bar is
  a single price all day, for SPY or QQQ.
- **A filled open is rare from 2008.** A filled open, the prior close
  repeated, shows as an open equal to the prior close.
  - SPY: 33 such days in 1993 and 25 in 1994, then single figures; 0 to 3 a
    year from 2008.
  - QQQ: 0 to 10 a year through 2018 (the 10 in 2013 spread through the
    year), at most 1 a year since.
  - At a one-cent tick, some happen by chance.
- **The master's closes equal the vendor's** on all 8,331 common sessions
  (SPY) and all 6,789 (QQQ).
- **The opens of the last weeks differ by a few cents.** From 2026-02-10 the
  master's rows have another source, most likely IBKR's bars: their volumes
  run at about 76% (SPY) and 79% (QQQ) of the vendor's, where every earlier
  session's volume is the vendor's exactly. On the 18 sessions both sources
  cover, 2026-02-10 to 2026-03-06:
  - SPY: the opens differ on 12, by up to $0.04 (0.58 bps), the master's
    higher on 4 and lower on 8;
  - QQQ: on 9, by up to $0.07 (1.16 bps), higher on 6 and lower on 3;
  - the closes agree on every session, the highs and lows on all but one, by
    a cent or two;
  - every earlier session's open is identical.
- **So the opens are uncertain by about a basis point.** One of the two opens
  is likely the opening auction's price and the other the first trade; the
  data can't say which.
  - This matters here: a first-trade open can manufacture an overnight drift.
    Quantpedia ("Dangers of Relying on OHLC Prices – the Case of Overnight
    Drift in GDX ETF", 2025, checked) found the gold miners ETF's night
    earning about 30% a year in daily bars, and 8.6% with the sell at 9:31
    from minute bars. Its SPY test changed only slightly; it gave no numbers.
  - A systematic gap of even 0.5 bps a night is worth about 1.3% a year. The
    costs below carry an allowance for it, and the cost criterion doubles it.
- **No second source for the opens could be fetched.** Stooq's daily files
  now sit behind a JavaScript check for browsers, and Nasdaq's quote API
  drops clients that aren't browsers. Neither was worked around.
- **The sessions opening more than 7% from the prior close** are real events:
  2001-09-17 (the reopening after September 11), 2008-10-24, 2020-03-09 and
  2020-03-16 for SPY; 2001-09-17, 2008-10-24, 2015-08-24 and 2020-03-16 for
  QQQ.
- **Dividends.** The vendor's total-return index gives each ex-date's cash:
  D_t = TR_t / TR_t-1 × P_t-1 − P_t, counted when above $0.02, the index's
  rounding.
  - The index never falls more than $0.02 short of a day's price move, for
    either ETF, so it shows no jump and reversal from a one-day error.
  - SPY: 133 ex-dates, 1993-03-19 to 2025-12-19, four a year. 2004 has a
    fifth, on 2004-11-15, before the panel starts.
  - QQQ: 85, from 2004-12-17, the first the index shows; four a year from
    2006, with a fifth in two years:
    - 2023-12-27 ($0.22), which IBKR's adjusted closes show too;
    - 2014-02-27 ($0.38), off QQQ's quarterly cycle, with no second source
      (IBKR's adjusted closes start in 2016). It is kept as the index has it:
      about 0.4% on one night of V1.
  - Against IBKR's adjusted closes, 2016-12 to 2026-03: the same 37 SPY and
    38 QQQ ex-dates, the amounts within $0.0052 and $0.0085.
  - IBKR alone shows a SPY "dividend" on 2018-09-04. It is a one-day blip in
    its adjustment factor (0.883272 between days at 0.883180), not a payment,
    and it is left out.
  - **The sources used:** the vendor's to 2026-03-06, IBKR's after. Those are
    2026-03-20, 2026-06-18 (a Thursday: the 19th was Juneteenth) and
    2026-09-18 for SPY; 2026-03-23, 06-22 and 09-21 for QQQ.
- **The calendar.**
  - The master lacks exactly the unscheduled closures, among them 2007-01-02,
    2012-10-29 and 30, 2018-12-05 and 2025-01-09.
  - It also has MLK Days before 1998, when NYSE stayed open; clock.py's rules
    wrongly close them, but that is outside this test's window.
  - 39 early closes (13:00 ET) fall from 2008.

## The mechanism, and what the literature says

**The pattern, in index ETFs and futures.** For US equity indices the return
has been earned largely outside trading hours, from the close to the next
open:
- **Cliff, Cooper and Gulen (2008),** "Return Differences between Trading and
  Non-trading Hours: Like Night and Day": US stocks, ETFs and index futures
  from 1993 to 2006. Close-to-open returns were strongly positive and returns
  in trading hours near zero or negative. The paper couldn't be fetched; this
  is how two summaries state it.
- **Kelly and Clark (2011),** "Returns in Trading versus Non-trading Hours:
  The Difference Is Day and Night", Journal of Asset Management 12(2): broad
  index ETFs, by Sharpe ratio. For QQQ (then QQQQ), 1999 to 2006, the abstract
  gives a close-to-open risk premium of about 23.7% against about −23.3% open
  to close, the first with the lower volatility.
- **Boyarchenko, Larsen and Whelan (2023),** "The Overnight Drift", Review of
  Financial Studies 36(9): E-mini S&P 500 futures around the clock.
  - Large positive returns in the opening hours of European markets.
  - They tie them to inventory risk: the returns follow the order imbalances
    at the previous US close, which dealers carry overnight.
  - Sell-offs are followed by strong overnight reversals, rallies by much
    weaker ones.
- **Bondarenko and Muravyev (2023),** "Market Return Around the Clock: A
  Puzzle", JFQA 58(3): E-mini futures, 2004 to 2018 in the working paper.
  - The four hours around the European open (23:30 to 03:30 ET in the working
    paper) carry the entire average market return, with a Sharpe ratio of 1.6
    that stays high after costs. The other 20 hours average about zero.
  - They attribute it to European investors processing the news that built
    up overnight, which resolves uncertainty: VIX futures rise overnight and
    fall around the European open.
  - The effect was stronger in the 2020 crisis.

**Explanations from individual stocks.** These two measured the cross-section
of stocks, not the index, and are cited for the mechanism only:
- **Berkman, Koch, Tuttle and Zhang (2012),** "Paying Attention: Overnight
  Returns and the Hidden Cost of Buying at the Open", JFQA 47(4): positive
  overnight returns that reverse during the day, driven by high opening
  prices, concentrated in stocks drawing retail attention. This sleeve sells
  at the open, the side that pattern favours.
- **Lou, Polk and Skouras (2019),** "A Tug of War: Overnight versus Intraday
  Expected Returns", JFE 134: strategies' profits are earned either entirely
  overnight (momentum and reversal) or entirely intraday (the others),
  typically with the opposite sign in the other period. Institutions trade
  in the day, against momentum.

**What that means for SPY held from the close to the open.** The position
holds every hour from 16:00 to 09:30, the European open among them. If the
futures papers are right, the night earns the market's return and the day
about nothing. The sleeve then earns the market's return less its costs and
less the T-bill rate it gives up.

**Decay, and what is out of sample.**
- McLean and Pontiff (2016): anomaly returns are about 58% lower after
  publication (as R1 cites).
- The ETF studies' samples end in 2006, so the walk-forward (2008 to 2023)
  comes after them. The futures papers' samples overlap most of it, so a pass
  there is less independent of them than it looks. The holdout (from 2023-10)
  is after every paper's sample.
- No checked study gives numbers for the drift after 2020. CXO Advisory's
  tests of SPY find the gap between night and day unstable across
  subperiods. Quantpedia's 2025 note found SPY's night little changed by a
  9:31 sell, without numbers.

## The instrument: the night's return as a series

The C++ walk-forward prices books close to close. A rule that holds SPY only
overnight is entered into it as a constructed instrument:
- **SPY_ON** steps each session t by the net value of one round trip, bought
  at the close of t-1 and sold at the open of t:

      L_t = L_t-1 × (1 − c_buy(t-1)) × (open_t × (1 − c_sell(t)) + D_t) / close_t-1

  - **D_t is the dividend going ex on session t.** The holder at the prior
    close owns the share across the ex-date, so it is owed the dividend while
    the price drops at the open. Leaving it out would charge the night the
    whole dividend yield, about 2% a year.
  - **c_buy and c_sell are each side's costs** (below), so the series carries
    every round trip's costs.
  - **The level starts at SPY's close on the panel's first date,**
    2005-01-03, so the harness's per-share commission on its trims is of the
    right size.
- **The night runs from one session to the next in the master.** A weekend
  or holiday night spans the closed days, and so does an unscheduled closure:
  2012's hurricane night runs from the close of 10-26 to the open of 10-31.
- **The idle money earns nothing.**
  - In the day, the 99.5% is cash between the open's sale and the close's
    purchase. IBKR pays interest on each day's ending settled cash, and the
    sleeve's morning sale and evening purchase settle on the same day, so its
    settled cash stays near the 0.5% buffer. That earns nothing either, as in
    S1's harness (cash interest 0).
  - **So the sleeve gives up the T-bill rate,** and its excess return over
    BIL charges it for that: each day's return less BIL's, from S1's panel.
  - Before 2016-10-18 that BIL leaves out its income, as for every study. It
    spares this sleeve 2008's T-bill income, about 1.4% that year (rates were
    near zero from 2009 to 2015).
- **How the walk-forward holds it.**
  - The book is SPY_ON at a constant 99.5% of NAV, re-sized monthly
    (rebalance_every 21) only to trim the drift.
  - Every real trade, the daily round trip, is already inside the series.
    So the harness charges SPY_ON no spread (half_spread_bps_by_symbol 0) and
    is given no volumes for it (so no impact; its trims are counted as
    unmeasured and reported). Its monthly trims pay S1's per-share
    commission, a few dollars.
  - The rules use the calendar and no market data, so the harness's lag 1
    only delays the first purchase by a session.
  - The harness supplies the folds, the holdout split and the daily mandate
    check; S1's functions the measures.
- **The variants are built the same way.** QQQ_ON from QQQ's opens, closes and
  dividends. SPY_WN is SPY_ON with every other night flat (factor 1, no
  trade).
- **The references come from the same data.**
  - SPY held: SPY_TR, close to close with its dividends,
    (close_t + D_t) / close_t-1, at 99.5%. It is charged SPY's costs (R1's
    1 bp half-spread and the commission), with no volumes.
  - SPY_ID, the intraday complement: bought at each open and sold at the same
    day's close, with the same costs per side and no dividend, since the share
    goes ex before the open.
  - S0: S1's targets, with R1's CostModel (S1's, with SPY's and BIL's
    half-spreads at 1 bp), as in R1's run.

## The rules

**The primary, chosen now: SPY every night.** At each close hold SPY at 99.5%
of NAV until the next open, through weekends and holidays; cash in the day.
- It is the literal claim of the papers above, and the trade the auctions
  allow.
- It is in SPY about 250 nights a year, with one round trip a night.
- *Amended at review, before any run:* **every rule sits out the night after
  an early close** (13:00 ET; 39 since 2008, about two a year). The daily run
  starts at 15:35 ET, after that close, so it can't buy in it. Flat those
  nights, with no trade, as the fund would be.

**Variants (2 trials):**
- **V1, QQQ every night:** the same rule on the other US index ETF in the
  model, the one Kelly and Clark tested. It tests whether the drift belongs
  to the index or to SPY.
- **V2, SPY on weeknights only:** hold only when the next session in the
  data falls on the next calendar day: Monday to Thursday nights, not before
  a holiday or a closure. Flat otherwise, with no trade.
  - It tests whether the drift is a weeknight effect, apart from the
    weekends and holidays.
  - The data's sessions are the calendar a trader had. The two state-funeral
    closures in the window (2018-12-05 and 2025-01-09) were announced days
    ahead, the second on 2024-12-31. The hurricane closure (2012-10-29)
    followed a weekend, when V2 is flat anyway.

No variant can replace a failed primary.

## Trials and the deflated Sharpe

- This study's trials: the primary and 2 variants, 3. With the 30 already
  spent (S1's 8, S3a's 6, R2's 9, R3's 4, R1's 3), **n = 33**.
- The trials' Sharpe variance comes from all 33: the 30 in R1's trials.csv
  and these three.
- The cost sensitivity runs below are robustness checks on the same rules,
  not trials.

## Costs, per side

| | Buy in the closing auction | Sell in the opening auction |
|---|---|---|
| IBKR Pro Fixed commission, $0.005 a share ($1 minimum, 1% cap). IBKR describes it as including exchange fees and most regulatory fees, so the auctions' fees are inside it | 50 / price bps: 0.06 to 0.73 bps for SPY over 2008 to 2026, its close running from $68.11 (2009-03-09) to $779.09 (2026-10-06); 0.12 bps at $430. QQQ: 0.07 to 1.96 bps | the same, at the open |
| SEC Section 31 fee, on sales | | **0.30 bps** ($30 per $1M), above every rate checked for the window: $11.00 (fiscal 2008), $25.70 then $12.70 (2010), $16.90 then $19.20 (2011), $22.40 then $17.40 (2013), $22.10 then $18.40 (2015), $5.10 (2021), $20.60 since 2026-04-04 |
| FINRA's trading activity fee, on sales | | $0.000195 a share in 2026 ($0.000166 in 2025), capped at $9.79 a trade: about $0.25 on $1M of SPY at $770, under 0.01 bps. The audit trail's fee IBKR passes on is smaller still. Both sit inside the allowance |
| Spread | none: an auction clears at one price | none |
| **Allowance** | **0.1 bps**: impact | **0.5 bps**: impact and the open's uncertainty |

- **The allowance is an assumption, chosen high.**
  - A $1M order is a sliver of SPY's auctions: the square-root impact model
    S1 uses gives about 0.03 bps against SPY's daily volume.
  - On the sell side it also covers the gap between the recorded open and
    the auction's price. The two sources put it at up to 0.58 bps for SPY and
    1.16 for QQQ, of either sign, and a first-trade open can bias a night's
    return (Quantpedia, above).
  - *Amended at review, before any run:* the draft charged the buy side
    0.5 bps too. But the close in the data is the closing auction's own price
    (the closes match the vendor's to the cent), so that was only margin. The
    buy side keeps 0.1 bps, three times the impact estimate.
- **A night's round trip** for SPY at $430: 0.12 + 0.1 + 0.12 + 0.30 + 0.5 ≈
  1.14 bps, about 2.8% of NAV a year over 250 nights at 99.5%.
  - It is 5.9% a year at SPY's 2009 low and 2.6% at its 2026 high.
  - QQQ's is far higher early on: at its 2008 low of $25.56 a round trip is
    about 4.8 bps, 12% a year.
- **Costs at k times:** the commission, the SEC fee and the allowance each
  multiplied by k, with the series rebuilt. At 2×, SPY's round trip at $430
  is about 2.3 bps, 5.7% of NAV a year.
- **Sources:**
  - IBKR's published Fixed schedule ($0.005 a share, $1 minimum, 1% maximum)
    and its description, read through IBKR's fee pages on its other sites;
    its main pricing page refused the fetch.
  - The SEC's fee-rate advisories for the Section 31 rates, and FINRA's and
    brokers' notices for the trading activity fee.
  - The allowance is this study's assumption.

## Split and measures (as R1's and R3's)

- **Walk-forward:** decisions from 2008-01-02, yearly test blocks on an
  expanding window, up to the holdout. The rules have nothing to fit; the
  harness is S1's so the numbers compare.
- **Holdout:** the last 756 sessions of the panel ending 2026-10-16, so from
  2023-10-12. Run once, after the walk-forward is written down.
- **Measures:**
  - excess returns over BIL (S1's panel), with Sharpe SE
    sqrt((1 + SR² / 2) / T);
  - the block bootstrap of 63-day blocks, 2,000 resamples, seed 21;
  - the deflated Sharpe with n = 33;
  - **cost drag:** the costs a year over the average value, the series' own
    (each night's c_buy and c_sell on the book) and the harness's.
- **Reference books,** through the same harness and window, reported and not
  counted as trials: SPY held (SPY_TR), SPY_ID (the intraday complement) and
  S0.
- **Reported for every book:**
  - the cost drag, the worst night and the worst month;
  - correlations with S0 and with SPY held;
  - the stress windows below.
- **A cost sensitivity table:** the primary, the variants and SPY_ID rebuilt
  at 0, 1, 2 and 3 times the costs above, each with its excess Sharpe,
  annual return and cost drag.

## Pass criteria (walk-forward, the primary)

*Amended at review on 2026-10-22, before any run.* The draft took the
stricter of R1's and R3's limits everywhere. For a book in SPY every night
that asks more than the hypothesis claims, so the crash limits below are
relative to SPY held. The claim is that the night earns the equity return
with less risk than the whole day, not that it misses equity crashes. One
criterion was added: beating SPY held, which is the claim itself. And the
cost robustness test now asks that the book still pay, not that it clear the
full bar, at doubled costs.

**Standalone:**
1. Excess Sharpe at least 0.40.
1b. **Excess Sharpe above SPY held's** (SPY_TR) over the same window. If the
    night doesn't beat holding SPY, trading twice a day buys nothing.
2. Deflated Sharpe at least 0.90 (n = 33).
3. Block-bootstrap 5th percentile of the excess Sharpe above 0.
4. Every variant's excess Sharpe above 0.
5. **Max drawdown no deeper than 60% of SPY held's** over the same window (as
   S3a judged its overlays against S0).
6. No day outside the mandate.
7. **Cost robustness,** in place of R1's and R3's cap on the cost drag, which
   a book trading twice a day fails by arithmetic: with every cost doubled,
   an excess Sharpe still at least 0.20.

**Crashes:**
8. **Worst single night** no worse than −12% of NAV, R3's worst-day
   tolerance.
9. **Worst calendar month** no worse than SPY held's worst calendar month.
10. **In each of R3's eight stress windows,** a return above SPY held's (R1's
    criterion). The windows:
    - 2008 crisis: 2008-09-15 to 2008-11-20
    - 2010 flash crash: 2010-04-23 to 2010-07-02
    - 2011: 2011-07-22 to 2011-10-03
    - 2015–16: 2015-08-17 to 2016-02-11
    - February 2018: 2018-01-26 to 2018-02-09
    - late 2018: 2018-09-20 to 2018-12-24
    - 2020 crash: 2020-02-19 to 2020-03-23
    - calendar 2022

Criteria 5, 9 and 10 ask a book that is in SPY every night to have taken
less of SPY's crashes than SPY did. Kelly and Clark found the night less
volatile than the day; these criteria test whether it also took less of the
crashes.

**Against the fund's book:**

11. The 50/50 book of S0 and the primary, with daily returns mixed as in the
    earlier studies, meets both of R3's tests:
    - a higher excess Sharpe than S0's;
    - a loss in each stress window no worse than S0's own.

    Here the primary leaves out its dividends before 2016-10-19, as S0's
    prices leave out its ETFs' dividends before IBKR's adjusted closes
    begin, so both halves stand on the same basis.

Correlations with S0 and with SPY held, the intraday complement and the cost
sensitivity table are reported, not judged.

**Holdout kills:** an excess Sharpe below −0.5, a drawdown deeper than SPY
held's over the holdout, or a night worse than −12%. (*Amended at review,
before any run:* the draft took R3's −15% drawdown, an absolute limit like the
crash criteria above. SPY held fell 18.7% in the holdout window.)

**Verdict.**
- **Pass:** every criterion and no holdout kill. Then shadow mode (PLAN §9,
  step 7). The shadow has to compare the market-on-open fills the paper
  account would get with the opens this test used.
- **Fail:** the report says which criteria failed, and the primary isn't
  taken further. No variant replaces a failed primary.

## How it would be traded (not tested here)

- **Every session,** a market-on-close buy of SPY for 99.5% of the sleeve's
  NAV, sent by 15:45 ET (`auction.py`'s cutoff).
- **After the close,** once that fill is confirmed, a market-on-open sell of
  the filled shares for the next session (time in force OPG). Sending it only
  after the buy fills means a missed buy can't leave a short.
- **Both orders go through the C++ check** (`dv02_engine.risk.check`). Gross
  is at most the 99.5% bought, plus the night's move; the morning's sale
  leaves cash.
- **On the 39 early closes since 2008,** the 15:35 run is after the 13:00
  close. The runner would need an early run those days, or the sleeve sits
  those nights out (about two a year; the test holds them).
- **The evening's buy is paid with the morning's sale proceeds,** which
  settle the next business day.
  - That needs a margin account, used without borrowing: the check keeps
    cash at or above 0.
  - The basket hands the check IBKR's TotalCashValue
    (`build_paper_basket.py`), which should count cash on the trade date.
    Confirm that in paper first.
- **S0 and the sleeve can't both hold 99.5% of the same NAV.** Together each
  gets its share, which is the 50/50 case criterion 11 tests.
- **What the shadow and paper periods must show:** the paper account's
  market-on-open fills against the opens in this test. They are the cost
  this data can't measure.

## Deviations

*Added on 2026-10-22, after the runs.* Neither changes a result that the
verdict turns on.
- **Criterion 7's "every cost doubled" leaves the harness's commission on
  the monthly trims at 1×.** CostModel can switch commissions on or off but
  not scale them. Every cost of the round trips is doubled, as "Costs at k
  times" says, with the series rebuilt at 2×. The trims' commission in the
  2× run is $142 over 15.8 years, about $9 a year on $1M; doubling it would
  move the excess Sharpe by about 0.0001.
- **The intraday complement's allowance is read per auction:** 0.5 bps on
  its buy at the open, and 0.1 bps on its sale at the close, with the SEC
  fee. Reading "the same costs per side" by side instead (0.1 on the buy, 0.5
  on the sale) gives the same total cost a round trip. The session's net
  factor changes only in the product of the two sides' costs, by about one
  part in a billion.
