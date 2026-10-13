# Handoff: where DV02 stands and what to do next

Last updated 2026-10-13 by the session that built PR
[samgeng14/Trading-Workspace#12](https://github.com/samgeng14/Trading-Workspace/pull/12)
(branch `claude/serene-babbage-0204ht`, draft). `CLAUDE.md` has the owner's
rules; this file has the state. Update both as you go.

## Start here

1. Pull the branch. On the owner's Mac the clone is `~/Code/deskd-live`:
   ```bash
   cd ~/Code/deskd-live
   git fetch https://github.com/samgeng14/Trading-Workspace.git claude/serene-babbage-0204ht
   git status            # commit or stash local edits first
   git merge --ff-only FETCH_HEAD
   bin/tradeit engine    # rebuild the C++ engine after every pull that touches engine/
   ```
2. Read `git log --oneline -15` and compare with "Done" below: the previous
   session may have pushed more after writing this (see "In flight").
3. Run the checks in `CLAUDE.md` once, so you start from green.

## Done (all pushed, CI green through 3f533f2e; later commits pushed 2026-10-13)

- **C++ engine** (`engine/`): the no-leverage check every order goes through,
  the backtester (ported from Rust; Rust is gone), Python bindings, CI on
  GCC+sanitizers, Clang and macOS.
- **No leverage enforced** on both order paths: the dashboard's
  preview/submit (`services/api/dv02_api/mandate.py`) and the daily runner
  (`trading-engine/src/runtime/leverage.py`), counting every holding,
  futures at notional, TQQQ at 3x.
- **Dashboard** (`apps/web`): six pages, Journal (WSJ) theme, live stream
  over a WebSocket (`/api/v1/stream`), IBKR-style return chart with
  1W/MTD/1M/3M/6M/YTD/1Y/All, a demo mode (MSW) and a staging mode (real
  API over a made-up account).
- **API** (`services/api`): contract `schemas/api/v1.yaml`, Host/Origin/
  Content-Type lockdown, kill switch, Flex NAV history fetched a year at a
  time, PortfolioAnalyst daily NAV import (the account's history starts
  2026-03-06; Flex only has it from 2026-04-15).
- **Phase 0 pieces**: MOSEK out (free solvers), market-on-close/
  market-on-open execution with the run at 15:35 ET, early closes in the
  clock, price-basis checks on the price master, dead code deleted,
  trading-engine tests in CI (0 failures, from 19).

## In flight when this was written

The previous session had two background jobs running. Each lands as its own
commit if it finishes. If `git log` doesn't show them, they died: do them
from the description here.

### A. Phase 0 gate: drift gate, EOD reconcile, execution.yaml

Already in the tree: `trading-engine/src/runtime/end_of_day_reconcile.py`
(positions-based reconcile: an "expected book" written by
`record_submission`, compared with IBKR's positions, kill switch armed on a
material break), `config/risk_limits.yaml` read by
`src/portfolio/risk/exposure_limits.py` (never looser than the no-leverage
policy), `_load_execution_config` / `_submit_disabled` in
`src/production/daily_runner.py`. If not committed, these edits may be
uncommitted or missing on your machine: check `git log -- trading-engine/src/runtime/end_of_day_reconcile.py`.

Still to do if it didn't land:
1. `_needs_rebalance_drift` must compare **today's targets** with the
   account's **current positions** (`_current_positions`: IBKR now, else the
   last snapshot, over NAV), not the previous run's reconciliation file.
2. Honour `config/execution.yaml`: when `_submit_disabled(...)` gives a
   reason, the run is a dry run and the run log says why.
3. Run the EOD reconcile as the daily run's first step and record its result;
   after a real submission call `record_submission` with the positions
   snapshot, the submission log and `result["execution"]`.
4. Rewrite the four tests that still use the old `diff_book_vs_account`
   (`tests/integration/test_runtime_e2e.py`, `tests/unit/test_adversarial_edge_cases.py`,
   `tests/unit/test_reconciler.py`) to go through `record_submission` and a
   fake client; add tests for each new behaviour and for loading
   `risk_limits.yaml`.

### B. Phase 3: cost model and walk-forward

Already in the tree: `engine/include/dv02/costs.hpp`, `engine/src/costs.cpp`
(IBKR Pro Fixed commission, half-spread, square-root impact, borrow, cash
interest, futures roll), wired into the backtester (bit-for-bit identical
when off), C++ tests (109 pass with sanitizers), bindings and
`engine/python/dv02_engine/walkforward.py` (folds, embargo, holdout,
deflated Sharpe). Missing if it didn't land: Python tests
(`engine/tests/python/test_costs.py`, `test_walkforward.py`: look-ahead
canary, holdout untouched, deflated Sharpe on hand-computed values), an
adversarial review, and making sure `walkforward.py` needs nothing beyond
numpy/pandas (the engine CI's Python job installs only those).

## Next, in this order

### 1. The always-on host: the owner's Oracle Cloud VM (needs Chrome)

Today everything runs on the Mac, which must be awake at 14:35 Central for
the daily run. The plan (`docs/PLAN.md` §12, Phase 2) moves the gateway, the
runner, the API and Postgres to the owner's Oracle VM.

**Use Claude in Chrome for the console.** The owner signs in to Oracle Cloud
in Chrome; Claude only reads. Never type, store or ask for the password or
MFA code. From Compute → Instances → the instance, record in this file:
region, shape (for example VM.Standard.A1.Flex = ARM64 Ampere, or
VM.Standard.E2.1.Micro = x86), OCPUs, memory, boot volume size, OS image,
public IP, and the VCN security list's ingress rules. Also note anything else
already on it (the plan mentions a Kalshi desk).

Then build it as files in the repo (a new `deploy/` folder), each step tested:
- IB Gateway: `ibgw/docker-compose.yml` uses `ghcr.io/gnzsnz/ib-gateway:stable`.
  Check that image supports the VM's CPU architecture before anything else.
- The API and the daily runner as systemd units; the runner on a systemd
  timer at 15:35 **America/New_York**, Monday to Friday (the timer must not
  drift with the VM's own zone). The LaunchAgent on the Mac must then be
  removed (`bin/tradeit schedule remove`) so nothing trades twice.
- Postgres for orders, fills, positions and NAV (`trading-engine/config/db.yaml`
  has the table names; nothing writes them yet).
- Phone access: Cloudflare Tunnel + Cloudflare Access in front of the
  dashboard and API, never an open port. The API refuses unknown Hosts and
  Origins: add the tunnel's hostname to `DV02_ALLOWED_HOSTS` and
  `DV02_ALLOWED_ORIGINS`.
- Backups of the data and Postgres to Cloudflare R2; a heartbeat alert when
  the daily run doesn't report.
- Secrets live only on the VM (`ibgw/.env`, `.env`), entered by the owner
  over SSH, never in git or chat.

### 2. Design pass on every page

Use the staging harness (`bin/tradeit staging`, `npm run e2e:staging`) to
audit each page at 1440 and 390 px in both themes, fix what reads badly, and
verify. Known item: the dashed "no daily data" segment in the equity chart
is no longer needed now that the PortfolioAnalyst import fills the history.

### 3. Small leftovers

- `bin/tradeit flex`: its success and warning messages still describe the
  old single fetch; say it fetches the whole history a year at a time.
- Review `services/api/dv02_api/flex.py` once more (its review was cut off).
- `trading-engine/pyproject.toml` still lists `mosek`; make it optional
  (needs `poetry lock`), or leave it: without a license the free solvers run.
- `docs/PLAN.md`: mark Phase 0 items done, record the WRDS decision (manual,
  Duo), the PortfolioAnalyst import, and the infrastructure status.
- Keep PR #12 green and its description current.

### 4. Then the plan's phases

Phase 0's exit test is the owner's: ten trading days in a row with the doctor
green, the run executing, the EOD reconcile matching the broker to the share,
and NAV matching IBKR's statement. After that, `docs/PLAN.md` §14: the data
platform (Phase 2), the engine's shadow OMS (Phase 3), sleeves through the
research gate (Phase 4).

## Owner's to-do list (things only the owner can do)

- Pull and rebuild (above), then import the daily NAV history once:
  ```bash
  cd ~/Code/deskd-live/services/api
  ../../.venv/bin/python -m dv02_api.portfolio_analyst ~/Downloads/<the daily PortfolioAnalyst CSV>
  ```
- Delete any `DV02_INCEPTION_*` lines from `.env` (the import replaces them).
- Reinstall the schedule for the new 15:35 ET time: `bin/tradeit schedule install`
  (until the VM takes over).
- Sign in to Oracle Cloud in Chrome when Claude asks, for step 1.
