# `src/` — source tree

One folder per subsystem. Each has its own README explaining its job and
how to extend it. Read in this order if you're new:

| # | Folder | What's in it | Read when |
|---|---|---|---|
| 1 | [`common/`](common/) | Shared utilities (clock, IDs, bundles) | First — most other code imports it |
| 2 | [`data/`](data/) | Ingest, cleaning, validators, features | You're adding a new data source |
| 3 | [`strategies/`](strategies/) | Lightweight Signal protocol + ETF / futures / options trees | You're sketching a new model |
| 4 | [`trading_engine/`](trading_engine/) | Polars-based registry pipeline (model → agg → opt) | You're adding a "real" model wired to the runner |
| 5 | [`portfolio/`](portfolio/) | Sizing, optimizers, risk constraints | You want to change how weights become orders |
| 6 | [`runtime/`](runtime/) | Pre-trade gate, healthcheck, schedule | You want to know how safety is enforced |
| 7 | [`execution/`](execution/) | Kill switch, order routing, fill monitor | You're going live or adding a new venue |
| 8 | [`broker/`](broker/) | IBKR API wrappers | You're swapping or extending the broker layer |
| 9 | [`storage/`](storage/) | Persistence repos (DB-backed; future) | You hit the limit of CSV/parquet on disk |
| 10 | [`backtest/`](backtest/) | Backtester wiring (future); the backtester itself is C++, `dv02_engine.backtest` in [`engine/`](../../engine/) | You want fast historical sims |
| 11 | [`cli/`](cli/) | `python -m src.cli.<command>` entry points | You're scripting ops |
| 12 | [`production/`](production/) | Daily runner, generate_targets | You want to understand the actual orchestration |
| 13 | [`dashboard/`](dashboard/) | Streamlit UI | You want to add a new screen |

## Conventions

- **Polars first** for the multi-strategy pipeline (`trading_engine/`),
  pandas in the dashboard (`dashboard/`). Don't mix inside a module.
- **Lazy imports** for anything that pulls in `ib_async`, `streamlit`,
  or `polars`. Top-level imports of these break the rest of the
  codebase if the dep is missing.
- **One `__init__.py` per package** — these are deliberately thin
  files that re-export the public API and document what's importable.
- **Registries over reflection.** Models, aggregators, optimizers,
  risk models all live in dict-based registries that map a string name
  to a spec dict. YAML configs reference these names.
- **Type hints required** on every public function. We use `from
  __future__ import annotations` so forward references work.

## Adding a new subsystem

If you find yourself wanting a new top-level folder (e.g.
`src/research/` for notebooks):

1. Create the folder + `__init__.py` + `README.md`.
2. Don't import it from existing subsystems unless you really need to
   — keep it leaf.
3. Add an entry to the table above.
