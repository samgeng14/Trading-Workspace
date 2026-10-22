# DV02 — working notes for Claude

DV02 is the owner's IBKR **paper** fund: one ETF model rebalanced daily, a
C++ risk engine, a FastAPI middle layer and a broadsheet-style dashboard. The
full plan is `docs/PLAN.md`. Where the work stands and what is next is
`docs/HANDOFF.md`: **read it first in a new session.**

## Rules from the owner (never break these)

- **Strictly no leverage.** Gross exposure ≤ 100% of NAV, cash ≥ 0, futures
  counted at notional, geared ETFs at their exposure (TQQQ 3x). Long/short is
  allowed inside that. Every order path goes through the C++ check
  (`dv02_engine.risk.check`); never weaken or bypass it, or any pre-trade check.
- **Languages: C++ and Python.** TypeScript only for the dashboard UI. No Rust.
- **Paper only.** The dashboard can't send live orders; the API binds to
  127.0.0.1 and refuses foreign Hosts and Origins. Keep it that way.
- **Credentials never pass through chat.** Don't ask for or accept IBKR
  usernames, passwords or Flex tokens. The owner types them into
  `bin/tradeit login` (saved to `ibgw/.env`, mode 600) and `bin/tradeit flex`
  (repo-root `.env`), both git-ignored. An old Flex token leaked into an
  earlier chat: never repeat it, look for it, or use it. Same for cloud
  consoles (Oracle, Cloudflare): the owner signs in; Claude never handles the
  password or MFA.
- **Never commit the owner's IBKR exports** (PortfolioAnalyst CSV/PDF: they
  carry the owner's name and account number). Only dates and NAV go into the
  git-ignored cache, `trading-engine/artifacts/api/flex_nav_<profile>.csv`.
- No solver licenses in the repo (`*.lic` is ignored). The free solvers
  (CLARABEL, ECOS, SCS) are the default; see `trading-engine/src/trading_engine/solvers.py`.
- Safety switches (`trading-engine/config/execution.yaml`'s `allow_submit` and
  `dry_run`, the kill switch, `risk_limits.yaml`, `config/engine.yaml`'s
  `role`) change only when the owner says so. To stop the fund, `bin/tradeit kill "why"` (arms the switch, then
  cancels every working order); `bin/tradeit resume` only after the doctor and
  a reconcile pass.

## Owner decisions already made (don't re-ask)

- Phase 0's NAV check: the recorded close within 0.10% of IBKR's statement
  (`src/runtime/exit_test.py`; the owner left the number to Claude).
- Holdings outside the model are sold at the next rebalance.
- TQQQ is out of the model (QQQ is already in it). The model universe is
  `trading-engine/config/universes/etfs.yaml`.
- Trade timing: the daily run starts 15:35 ET (14:35 Central, the owner's
  zone). Orders go market-on-close when sent by 15:45 ET; later, or when the
  market is closed, market-on-open for the next session
  (`trading-engine/src/broker/ibkr/auction.py`).
- WRDS is limited and needs a Duo prompt: don't automate it. Data comes from
  IBKR plus free sources.
- The dashboard's default theme is the Journal (WSJ-like) theme; dark is the
  alternative (`dv02.theme` in localStorage). The wordmark reads "DV02".
- The equity curve's default view is return since inception, like the IBKR app.

## Layout

| Path | What |
|---|---|
| `engine/` | C++20 library `dv02` (risk check, backtester, cost model; walk-forward in `dv02_engine.walkforward`; the NYSE calendar and the order manager's decisions, run in shadow beside the Python runner: `trading-engine/src/production/engine_shadow.py`) and its Python module `dv02_engine` (nanobind) |
| `trading-engine/` | Python: the daily runner (`src/production/daily_runner.py`: reconcile, bars, targets, drift gate, basket, orders), IBKR plumbing (`src/broker/ibkr/`), the expected-book reconcile (`src/runtime/end_of_day_reconcile.py`), the kill (`src/execution/kill.py`), Phase 0's exit test (`src/runtime/exit_test.py`), the Postgres store for the trading record (`src/storage/`, best effort: the files stay the record), data, research |
| `services/api/` | FastAPI app `dv02_api`: the dashboard's API over IBKR and the engine's files |
| `apps/web/` | React + TypeScript + Vite dashboard |
| `schemas/api/v1.yaml` | The API contract. Edit it first, then `npm run gen:api` in `apps/web` |
| `ibgw/` | IB Gateway in Docker with IBC auto-login |
| `bin/tradeit` | The one command for everything on the Mac (`bin/tradeit help`) |

## Checks to run before every push (what CI runs)

From the repo root:

```bash
# C++ engine: GCC with sanitizers and warnings as errors, then the Python module
cmake -S engine -B engine/build -DCMAKE_BUILD_TYPE=Debug -DDV02_WERROR=ON -DDV02_SANITIZE=ON
cmake --build engine/build --parallel && ctest --test-dir engine/build --output-on-failure
.venv/bin/pip install ./engine && .venv/bin/python -m pytest engine/tests/python -q

# trading-engine (CI also runs ruff on the list in .github/workflows/trading-engine.yml).
# The store's tests need a throwaway Postgres (CI starts one); without the URL they skip:
#   docker run -d --name dv02-test-pg -e POSTGRES_PASSWORD=test -e POSTGRES_DB=trading_test -p 127.0.0.1:55432:5432 postgres:16
#   export DV02_TEST_DATABASE_URL=postgresql://postgres:test@127.0.0.1:55432/trading_test
(cd trading-engine && ../.venv/bin/python -m pytest -q)

# API
(cd services/api && ../../.venv/bin/python -m pytest -q && ../../.venv/bin/ruff check . && ../../.venv/bin/ruff format --check .)

# dashboard: types in sync with the contract, typecheck, lint, format, unit, build, e2e
(cd apps/web && npm run gen:api && git diff --exit-code -- src/api/schema.d.ts \
  && npx tsc -b && npm run lint && npm run format:check && npm test && npm run build \
  && CI=1 npx playwright test \
  && CI=1 npx playwright test -c playwright.staging.config.ts)
```

`npx playwright test` is the demo e2e (MSW); the staging config runs the
real API over the made-up staging account.

`bin/tradeit setup` builds `.venv` (Python 3.12, else 3.11 or 3.13) with the
runtime packages, the C++ engine and the dashboard's npm packages. The checks
also need `.venv/bin/pip install pytest jsonschema httpx ruff==0.8.6`. On the
owner's Mac, `/usr/local/bin/cmake` is an Intel binary: build the engine with
`CMAKE_ARGS=-DCMAKE_OSX_ARCHITECTURES=arm64 ARCHFLAGS="-arch arm64"` (as
`bin/tradeit engine` does), or the module won't load in the arm64 Python.

## Style

Code comments and docs say what and why in plain sentences, no filler. Match
the surrounding file. Tests for every behaviour change. Commit messages: a
short title, then what changed and why.
