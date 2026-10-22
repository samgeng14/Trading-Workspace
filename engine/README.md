# DV02 Fund engine (C++)

The fund's core math in C++20, with Python bindings (nanobind) as the
package `dv02_engine`. Python code imports it; the math lives only here.

- `dv02_engine.risk`: the no-leverage check every order goes through
  (`include/dv02/risk.hpp`, `src/risk.cpp`).
- `dv02_engine.backtest`: the portfolio backtester (below).
- `dv02_engine.nyse` and `dv02_engine.oms`: the NYSE calendar and the order
  manager's decisions, run beside the Python runner in shadow (below).

## The no-leverage mandate

The fund is long/short with no leverage. `risk.check(book, orders, policy)`
takes the whole account and the orders about to be sent, and says whether
the book after those orders stays inside the policy.

**Inputs.** The book is NAV, cash (negative is a margin loan) and every
position: symbol, signed quantity, price, multiplier, asset class and
exposure. Every holding counts, the model's or not. Each order has a symbol,
signed quantity, expected fill price, multiplier, asset class, commission
and exposure.

**Exposure** is economic exposure per dollar of market value: 1 for an
ordinary holding, 3 for a 3x ETF such as TQQQ, −1 for an inverse ETF, so
holding an inverse ETF long counts as a short. A position's notional is
quantity × price × multiplier × exposure; cash still moves at market value
(quantity × price × multiplier). Futures and options are measured by their
multiplier and keep exposure 1. The callers take each symbol's exposure from
one table, `EXPOSURE` in `trading-engine/src/runtime/leverage.py` (today
`{"TQQQ": 3.0}`; an inverse ETF goes in as a negative factor), and look it up
for shares only.

**The policy** (`risk.Policy()` defaults):

| Limit | Default | Measure |
|---|---|---|
| `max_gross` | 1.0 | (long + \|short\|) / NAV, every position at its notional (above), so futures count at full notional and TQQQ at three times its market value |
| `min_cash` | 0.0 | cash / NAV. Below 0 is a margin loan |
| `max_single_name` | 0.25 | \|net notional of one symbol\| / NAV |
| `min_net`, `max_net` | −1.0, 1.0 | (long − \|short\|) / NAV. Checked only when tighter than the gross limit; at the defaults the gross limit already covers them |

**The book after the orders.** Every order fills at its expected price; the
check judges the book after all of them, whatever order they are sent in. A
holding already in the book is re-marked at the order's price, and NAV moves
with it; it keeps its own exposure. An order for a symbol not held opens a
position with the order's exposure. Cash pays or receives quantity × price ×
multiplier, except for futures, futures options and FX, which move no cash.
Commissions come out of cash and NAV.

**What blocks** (`ok` is false):

- NAV not a positive number, or cash missing.
- Anything that can't be priced: a position or order with a non-zero
  quantity and no finite, positive price or multiplier, or an exposure that
  is 0 or not finite, or an order with no finite commission. Leverage that
  can't be measured is treated as leverage.
- Options and futures options, held or ordered: their premium understates
  what they are exposed to, and delta-adjusted notional isn't measured yet.
- After the orders: gross over `max_gross`, cash under `min_cash`, the
  largest name over `max_single_name` (and net outside its range, when that
  is tighter than gross). Limits are compared with a 1e-9 tolerance. A gross
  breach names the holdings and orders counted at an exposure other than 1,
  up to eight ("counting TQQQ 3x at that exposure").

**Reduce-only orders.** When every traded symbol moves toward zero without
flipping sides, a limit breached after the orders is reported but does not
block, as long as that measure is no worse than before. Selling down a
levered book is always allowed; adding to one never is. The input checks
(NAV, cash missing, unpriced, options) block regardless.

**The result** (`risk.result_dict(r)` gives it as plain data): `ok`,
`reduce_only`, the exposure `before` and `after` (NAV, cash, long and short
dollars, gross, net and cash as fractions of NAV, the largest name), every
breach with its rule, whether it blocks, the value, the limit and a message,
and `buy_capacity`: how many more dollars of cash-settled buying at exposure
1 would still pass gross and cash.

IBKR secTypes map to asset classes with `risk.asset_class_from_ibkr`: STK
stock, ETF, FUT and CONTFUT future, OPT and WAR option, FOP futures option,
BOND and BILL bond, CASH FX, CRYPTO crypto, anything else other.

```python
from dv02_engine import risk

etf = risk.AssetClass.ETF
# 102.5% long on a $25,000 margin loan
book = risk.Book(1_000_000, -25_000, [risk.Position(s, 2050, 100.0, asset_class=etf)
                                      for s in ("SPY", "QQQ", "IWM", "TLT", "GLD")])

r = risk.check(book, [risk.Order("SPY", 100, 100.0, asset_class=etf, commission=1.0)])
r.ok                    # False
[b.message for b in r.breaches if b.blocking]
# ['gross 103.5% of NAV after these orders, over the 100.0% limit by $35,001',
#  'cash -$35,001 after these orders: a margin loan, short by $35,001']

r = risk.check(book, [risk.Order("SPY", -100, 100.0, asset_class=etf, commission=1.0)])
r.ok, r.reduce_only     # (True, True): still over, reported, but selling down may go
risk.result_dict(r)     # JSON-ready

# $50,000 of TQQQ is $150,000 of exposure
risk.Position("TQQQ", 500, 100.0, asset_class=etf, exposure=3.0).notional   # 150000.0
```

## Who calls it

- **The API** (`services/api/dv02_api/mandate.py`) builds the book from the
  live IBKR account, every position. A position's price is its last price,
  else its market value / (quantity × multiplier), else 0, which blocks as
  unpriced. The rebalance preview's "No leverage" check runs it on the book
  plus the basket's orders, and a failure stops the submit. `/risk` reports
  gross and cash from the same measure.
- **The daily runner** (`trading-engine/src/runtime/leverage.py`) builds the
  book from the run's own files: the positions snapshot, the latest account
  summary (NetLiquidation, TotalCashValue) and the orders file
  `submit_paper_orders` sends. A holding is priced at IBKR's mark, else the
  reconciliation's close, else the price master's last close; otherwise it
  is unpriced and blocks. The snapshot has one row per future or option
  contract (MESZ6, not MES), and one IBKR reports without a multiplier
  blocks. `step_submit_orders` runs it next to the engine's
  target-weight gate (`run_pre_trade_risk_checks`), also on dry runs, and
  either one blocks.

Both take exposure from the same `EXPOSURE` table, and both block every
order when `dv02_engine` can't be imported, saying which Python is missing
it.

**The basket both paths check.** Holdings outside the model are sold at the
next rebalance: the basket (`build_reconciliation` in
`trading-engine/src/production/runtime/build_paper_basket.py`, which the
API's preview also runs) gives every share holding the targets don't include
a target of 0 and sells the whole position, however small. It is priced at
IBKR's mark, else the price master's last close, else left unpriced, which
this check blocks. Futures and options outside the model are not sold
automatically; they stay, count here, and the preview and the run log name
them in a warning. The daily runner rebalances while such shares are held
(under the drift cadence, once its cooldown since the last submission has
passed, so a sell still working isn't sent twice), and `submit_paper_orders`
sends every sell before any buy; it does not wait for fills.

## The order manager, in shadow

The first piece of the engine taking over order handling (docs/PLAN.md §6,
migration step 2). Python still sends every order; these make the same
decisions from the same inputs, and the runner records whether they agree.

- `nyse` (`src/nyse.cpp`): the NYSE calendar of
  `trading-engine/src/common/clock.py`, rule for rule (holidays, early closes,
  sessions, the next open, the last completed session), in New York local
  time. The Python face takes and returns `datetime` values.
- `oms` (`src/oms.cpp`):
  - `current_weights` and `rebalance_decision`: the drift gate
    (`daily_runner._current_weights`, `auto_rebalance.decide_rebalance_with_drift`),
    reasons word for word.
  - `plan_basket`: the reconciliation and orders of `build_reconciliation`,
    holdings outside the model sold whole, the cash trim, the file's sort, and
    `sells_first`'s sending order.
  - `execution_for`: market-on-close until the earliest cutoff among the
    batch's exchanges less 5 minutes, else market-on-open (`auction.py`).
  - `reconcile`: the run's first step (`end_of_day_reconcile.py`): the
    expected book's positions plus each order at the fills IBKR reports (else
    in full, or not at all if it never reached IBKR), every break against
    IBKR's positions valued at the mark, the order's price or the book's, and
    whether one is material (which arms the kill switch in Python).
  - `replay`, `unacknowledged`, `working`, `never_placed`: an order's status
    log as the order path reads it (`orders.acknowledged_after`, the WORKING
    and NOT_PLACED sets), so the engine can own order tracking later.
  - `check_limits` and `cash_after`: the pre-trade exposure limits on a
    basket's target weights (`exposure_limits.check_exposure_limits`, the
    limits in `config/risk_limits.yaml`), breach names and wording
    Python's own. With `risk.check`, the engine now holds every pre-trade
    check that isn't a file lookup (the kill switch, stale prices).

The match is meant to be exact, so the port keeps Python's arithmetic: sums in
numpy's pairwise order (`pairwise_sum`), no fused multiply-adds in `oms.cpp`
(`-ffp-contract=off`), and Python's quirks, noted where they are (fractional
shares truncate toward zero, as `astype(int)`). `trading-engine/src/production/engine_shadow.py`
runs the comparison on every run, and
`trading-engine/tests/unit/test_engine_oms.py` checks it on thousands of
random inputs.

## Build and test

`bin/tradeit setup` builds and installs the module into `.venv`, and offers
to install it into the daily runner's Python when that is a different one.
After pulling engine changes, `bin/tradeit engine` rebuilds and reinstalls it
in both, and builds and runs the C++ tests when cmake is installed. Either
needs a C++20 compiler; on macOS, the Command Line Tools
(`xcode-select --install`).

By hand:

```bash
# the Python module (pip fetches nanobind, and CMake when it is not installed)
.venv/bin/python -m pip install ./engine
.venv/bin/python -m pytest engine/tests/python

# the C++ library and tests
cmake -S engine -B engine/build/tests -DCMAKE_BUILD_TYPE=Debug
cmake --build engine/build/tests --parallel
ctest --test-dir engine/build/tests --output-on-failure
```

CMake options: `DV02_WERROR=ON` turns warnings into errors and
`DV02_SANITIZE=ON` adds AddressSanitizer and UndefinedBehaviorSanitizer (both
on in CI, `.github/workflows/engine.yml`); `DV02_BUILD_PYTHON` builds the
module, which `pip install` turns on.

## Backtester

`dv02_engine.backtest` is the C++ port of the former Rust hawk-backtester,
with the same call, frames and numbers. Its module docstring
(`python/dv02_engine/backtest.py`) lists where it deliberately differs.
