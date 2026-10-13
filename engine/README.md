# DV02 Fund engine (C++)

The fund's core math in C++20, with Python bindings (nanobind) as the
package `dv02_engine`. Python code imports it; the math lives only here.

- `dv02_engine.risk`: the no-leverage check every order goes through
  (`include/dv02/risk.hpp`, `src/risk.cpp`).
- `dv02_engine.backtest`: the portfolio backtester (below).

## The no-leverage mandate

The fund is long/short with no leverage. `risk.check(book, orders, policy)`
takes the whole account and the orders about to be sent, and says whether
the book after those orders stays inside the policy.

**Inputs.** The book is NAV, cash (negative is a margin loan) and every
position: symbol, signed quantity, price, multiplier and asset class.
Every holding counts, the model's or not. Each order has a symbol, signed
quantity, expected fill price, multiplier, asset class and commission.

**The policy** (`risk.Policy()` defaults):

| Limit | Default | Measure |
|---|---|---|
| `max_gross` | 1.0 | (long + \|short\|) / NAV, every position at notional: quantity × price × multiplier, so futures count at full notional |
| `min_cash` | 0.0 | cash / NAV. Below 0 is a margin loan |
| `max_single_name` | 0.25 | \|net notional of one symbol\| / NAV |
| `min_net`, `max_net` | −1.0, 1.0 | (long − \|short\|) / NAV. Checked only when tighter than the gross limit; at the defaults the gross limit already covers them |

**The book after the orders.** Orders fill at their expected prices. A
holding already in the book is re-marked at the order's price, and NAV moves
with it. Cash pays or receives quantity × price × multiplier, except for
futures, futures options and FX, which move no cash. Commissions come out of
cash and NAV.

**What blocks** (`ok` is false):

- NAV not a positive number, or cash missing.
- Anything that can't be priced: a position or order with a non-zero
  quantity and no finite, positive price or multiplier, or an order with no
  finite commission. Leverage that can't be measured is treated as leverage.
- Options and futures options, held or ordered: their premium understates
  what they are exposed to, and delta-adjusted notional isn't measured yet.
- After the orders: gross over `max_gross`, cash under `min_cash`, the
  largest name over `max_single_name` (and net outside its range, when that
  is tighter than gross). Limits are compared with a 1e-9 tolerance.

**Reduce-only orders.** When every traded symbol moves toward zero without
flipping sides, a limit breached after the orders is reported but does not
block, as long as that measure is no worse than before. Selling down a
levered book is always allowed; adding to one never is. The input checks
(NAV, cash missing, unpriced, options) block regardless.

**The result** (`risk.result_dict(r)` gives it as plain data): `ok`,
`reduce_only`, the exposure `before` and `after` (NAV, cash, long and short
dollars, gross, net and cash as fractions of NAV, the largest name), every
breach with its rule, whether it blocks, the value, the limit and a message,
and `buy_capacity`: how many more dollars of cash-settled buying would still
pass gross and cash.

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

Both block every order when `dv02_engine` can't be imported, and say which
Python is missing it.

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
