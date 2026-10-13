# Handoff: where DV02 stands and what to do next

Last updated 2026-10-13 by the session that built PR
[samgeng14/Trading-Workspace#12](https://github.com/samgeng14/Trading-Workspace/pull/12)
(branch `claude/serene-babbage-0204ht`, draft). `CLAUDE.md` has the owner's
rules; this file has the state. Keep both current as you work.

## Start here

1. Pull the branch. On the owner's Mac the clone is `~/Code/deskd-live`:
   ```bash
   cd ~/Code/deskd-live
   git status            # commit or stash local edits first
   git fetch https://github.com/samgeng14/Trading-Workspace.git claude/serene-babbage-0204ht
   git merge --ff-only FETCH_HEAD
   bin/tradeit engine    # rebuild the C++ engine after any pull that touches engine/
   .venv/bin/pip install pytest jsonschema httpx ruff==0.8.6   # what the checks need beyond `tradeit setup`
   ```
2. Look at PR #12's checks on GitHub. CI was green through 3f533f2e; then
   `web / staging-e2e` failed on 0a0eee77 and f663141d (the Command Center's
   Today's P&L y labels ran a pixel past the panel in the Journal theme), and
   7fe9553b fixes it. If anything is red on the latest commit, fix that first.
3. Compare `git log --oneline -15` with "Done" and "In flight" below.
4. Run the checks in `CLAUDE.md` once, so you start from green.

## Done (pushed)

- **C++ engine** (`engine/`): the no-leverage check, the backtester (ported
  from Rust; Rust is gone), Python bindings, CI on GCC+sanitizers, Clang,
  macOS.
- **No leverage enforced** on the dashboard's preview/submit
  (`services/api/dv02_api/mandate.py`) and the daily runner
  (`trading-engine/src/runtime/leverage.py`), counting every holding,
  futures at notional, TQQQ at 3x. **Not yet on a third path**: see Next 1.
- **Dashboard** (`apps/web`): six pages, Journal (WSJ) theme, live stream
  over a WebSocket (`/api/v1/stream`), IBKR-style return chart with
  1W/MTD/1M/3M/6M/YTD/1Y/All, a demo mode (MSW) and a staging mode (the real
  API over a made-up account, `bin/tradeit staging`, `npm run e2e:staging`).
- **API** (`services/api`): contract `schemas/api/v1.yaml`, Host/Origin/
  Content-Type lockdown, kill switch (blocks new orders; doesn't cancel
  working ones), Flex NAV history fetched a year at a time, PortfolioAnalyst
  daily NAV import (the account's history starts 2026-03-06; Flex only has
  it from 2026-04-15).
- **Phase 0 pieces**: MOSEK out (free solvers), market-on-close/
  market-on-open execution with the run at 15:35 ET (`src/broker/ibkr/auction.py`),
  early closes in the clock, price-basis checks on the price master, dead
  code deleted, trading-engine tests in CI (0 failures, from 19).

## In flight when this was written

Two background jobs were finishing work that existed **only as uncommitted
files in the previous session's container**. Each was to land as its own
commit once its tests passed. Check:

```bash
git log --oneline origin/claude/serene-babbage-0204ht -- trading-engine/src/runtime/end_of_day_reconcile.py engine/src/costs.cpp
```

If a commit for it is there, it landed: review it and move on. If not, none
of that job's code exists on your machine, and you rebuild it from this
description.

### A. Phase 0 gate: drift gate, EOD reconcile, config

What it adds:
- `trading-engine/src/runtime/end_of_day_reconcile.py`, rewritten to be
  positions-based. `record_submission(profile, run_id=, positions=, orders=,
  execution=, root=)` adds each real submission to an "expected book" JSON
  (`data/broker/reconciliations/{profile}_expected_book.json`): the positions
  snapshot the basket was built on, plus each order. `reconcile_end_of_day(
  profile=, material_threshold_usd=, as_of=, root=, client=, now=)` adds the
  orders at their IBKR fills (in full when IBKR reports none, since its API
  keeps one day of fills). It compares the result with IBKR's positions to
  the share, writes a report, arms the kill switch on a break worth $100 or
  more (or one it can't price), then resets the book to IBKR's positions. It
  waits while a market-on-close auction is still ahead (fills_at + 30 min). An
  unreachable gateway is a failure, never "clean".
- `src/portfolio/risk/exposure_limits.py` reads `config/risk_limits.yaml`
  (`load_exposure_limits`), every key required, unknown keys an error, and no
  value looser than the no-leverage policy (gross 1.0, net 1.0, one name 0.25).
- `src/production/daily_runner.py`:
  - The drift gate (`_needs_rebalance_drift`) compares **today's targets**
    with the account's **current positions** (`_current_positions`: IBKR now,
    else the last snapshot, over NAV). Before, it read the previous run's
    reconciliation file, so it never saw composition changes.
  - The EOD reconcile runs as the first step of each run.
  - `record_submission` is called after each real submission.
  - `config/execution.yaml` is honoured (`_load_execution_config`, `_submit_disabled`).
- Tests that drive the old reconcile, rewritten to go through
  `record_submission` and a fake client:
  - `tests/integration/test_runtime_e2e.py`: `test_full_cycle_happy_path` and
    the three `test_eod_*` tests (they patch `diff_book_vs_account`)
  - `tests/unit/test_adversarial_edge_cases.py::test_reconcile_rerun_does_not_double_arm`
  - `tests/unit/test_reconciler.py::test_reconcile_reports_unreachable_broker_as_failure`
    (patches `reconciler._make_readonly_client`)

  Plus new tests for each behaviour and for loading `risk_limits.yaml`.

**Ask the owner before wiring `execution.yaml`.** The committed file says
`allow_submit: false` and `dry_run: true`. Nothing reads it today, so the
scheduled run trades. Once `_submit_disabled` is wired in, those values turn
every run into a dry run and the fund stops trading. The owner installed the
schedule to send paper orders automatically, so the expected values are
`mode: paper`, `allow_submit: true`, `dry_run: false`. Confirm that with the
owner, set it in the same commit, and add a test that a run with that config
submits.

### B. Phase 3: cost model and walk-forward

What it adds:
- `engine/include/dv02/costs.hpp` and `engine/src/costs.cpp`:
  - IBKR Pro Fixed commission ($0.005 a share, $1 minimum, 1% of value maximum)
  - per-contract futures fees
  - half-spread
  - square-root impact (k·σ·√(traded/ADV), from history before the trade only)
  - borrow on shorts and interest on positive cash, both actual/360 on calendar days
  - futures roll
- The cost model wired into `engine/src/backtest.cpp`, bit-for-bit identical
  to the plain backtest when off.
- C++ tests: `engine/tests/test_costs.cpp` and `test_backtest_costs.cpp`
  (109 C++ tests passed with sanitizers).
- `engine/python/bind_costs.cpp` and `dv02_engine/costs.py`.
- `engine/python/dv02_engine/walkforward.py`: folds (expanding or rolling),
  an embargo gap, an untouched holdout, trades `lag` rows after the decision,
  and the deflated Sharpe ratio (Bailey & López de Prado).
- Python tests: `engine/tests/python/test_costs.py` and `test_walkforward.py`
  (look-ahead canary, holdout untouched, deflated Sharpe on hand-computed values).

`walkforward.py` must import nothing beyond what the engine CI's Python job
installs: numpy, pandas, polars and pytest.

## Next, in this order

### 1. Safety fixes (Phase 0, code only)

- **Close the third order path.** `python -m src.production.runtime.submit_paper_orders --submit`
  reaches `place_order` with no kill-switch check and no no-leverage check:
  both live only in `daily_runner.step_submit_orders`. Move
  `require_kill_switch_clear()` and `check_leverage()` into
  `submit_paper_orders()` (`trading-engine/src/production/runtime/submit_paper_orders.py`),
  the one function that places orders, before it connects. Delete the dead
  Streamlit page `src/cli/submit_orders.py`, which calls
  `order_service.submit_paper_orders` with no checks. The runner imports that
  service (`daily_runner.py`), so move it out of `src/dashboard` rather than
  delete it. Add tests: an armed switch, or a book that ends levered, sends nothing.
- **Skip market holidays.** The runner skips only weekends. On Thanksgiving
  (Thu 2026-11-26) the 15:35 run would rebalance and send market-on-open
  orders for the 27th. In `daily_runner` (and its `--skip-if-ran-today`
  guard), skip with a logged reason when `not clock.is_us_business_day(today)`,
  with a test for 2026-11-26. On early-close days (Nov 27, Dec 24) the 15:35
  run is after the 13:00 close and goes market-on-open: log that it is by
  design, or schedule an earlier run on those days.
- **Remove the old 16:32 scheduler.** `python -m src.production.scheduler --install-launchagent`
  writes the same LaunchAgent label (`com.capitalfund.daily-runner`) at 16:32
  with RunAtLoad, overwriting `bin/tradeit schedule install`, so every run
  would go after the close. `trading-engine/AUTOTRADING_SETUP.md` tells the
  owner to run it. Delete `src/production/scheduler.py` (the only apscheduler
  import) and `src/runtime/schedule.py`, or reduce them to pointers to
  `bin/tradeit schedule`. Then change every remaining "16:32" to 15:35 ET
  (14:35 Central) in:
  - `services/api/README.md`
  - `trading-engine/README.md`
  - `trading-engine/AUTOTRADING_SETUP.md`
  - `ibgw/README.md`
  - `trading-engine/scripts/HEADLESS.md`
- **Stale prices block.** Today they only warn. A model-universe symbol whose
  last bar is behind `clock.last_completed_session()` by more than the
  validator's tolerance should be a blocking error in
  `src/data/cleaning/validators.py`, so the pre-trade checks stop the run. Add a test.
- **A real kill.** Arming the switch only writes a file; a market-on-close
  basket already sent still fills.
  - Add `bin/tradeit kill "why"`, `resume` and `status`.
  - Make kill cancel every working order. The API can already cancel by
    reconnecting as the placing client (`services/api/dv02_api/broker.py`).
  - Resume only after the doctor and a reconcile pass.

### 2. Check the first real runs (with the owner)

The market-on-close change has never run against IBKR. The first real run
after the owner pulls will also sell the ~15 holdings outside the model and
TQQQ, and clear the ~$26k margin loan. On that day, check the run log and
the dashboard's blotter:
- the orders went market-on-close and filled at that close
- the holdings outside the model and TQQQ are gone
- gross is at most 100% of NAV and cash is at least 0 afterwards

Once, send a small paper order after 15:45 ET to confirm IBKR accepts and
holds the market-on-open (MKT, tif OPG) order that `auction.py`'s late path sends.

### 3. The always-on host: the owner's Oracle Cloud VM (needs Chrome)

Today everything runs on the Mac, which must be awake at 14:35 Central. The
plan (`docs/PLAN.md` §12, Phase 2) moves the gateway, runner, API and
Postgres to the owner's Oracle VM.

**Read the console through Claude in Chrome** (setup below). The owner signs
in to Oracle Cloud in Chrome; Claude only reads. Never type, store or ask for
the password or MFA code. From Compute → Instances → the instance, record
these here:
- region
- shape (e.g. VM.Standard.A1.Flex is ARM64 Ampere; VM.Standard.E2.1.Micro is x86)
- OCPUs and memory
- boot volume size and OS image
- public IP
- the VCN security list's ingress rules
- anything else already running there (the plan mentions a Kalshi desk)

Then build it as files in the repo (a new `deploy/` folder), each step tested:

- **IB Gateway.** `ibgw/docker-compose.yml` uses `ghcr.io/gnzsnz/ib-gateway:stable`.
  Check that image supports the VM's CPU architecture first. IBKR allows
  **one session per username**: once the VM's gateway logs in, run
  `bin/tradeit gateway stop` on the Mac and never run both. The Mac's
  container has `restart: unless-stopped`, so remove it.
- **API and runner as systemd units.** Run the runner on a systemd timer at
  15:35 **America/New_York**, Monday to Friday, set in that zone (not the VM's).
  - Carry over the LaunchAgent's environment: `CAPITALFUND_OPTIMIZER=momentum_tilted`,
    the IBKR client ID and ports, `--skip-if-ran-today`.
  - Teach `services/api/dv02_api/runs.py` to read the systemd timer instead of the plist.
  - Remove the Mac's LaunchAgent (`bin/tradeit schedule remove`) so nothing
    trades twice.
- **Move the state, not just the code.** These git-ignored folders exist only on the Mac:
  - `trading-engine/artifacts/`: kill switch, the Flex and PortfolioAnalyst
    NAV cache, run logs
  - `trading-engine/data/broker/`: the drift cooldown state, reconciliations
    and the expected book, positions snapshots

  Copy them to the VM and run `bin/tradeit refresh` there. Without them the
  drift gate cold-starts and rebalances at once, and the equity curve loses
  its pre-Flex history. Do one dry run on the VM and compare its targets with
  the Mac's before enabling the timer.
- **Postgres** for orders, fills, positions and NAV.
  - `trading-engine/config/db.yaml` and `trading-engine/db/migrations` disagree
    (`market_data` vs `market_bars`). Neither has a NAV table, and the
    credentials are placeholders. Make them agree, add NAV, and take
    credentials from the VM's `.env`.
  - Add the Trades section to the Flex query and load Flex trades into
    `fills`, so the blotter and the reconcile have history beyond today.
- **Phone access.** Cloudflare Tunnel + Cloudflare Access in front of the
  dashboard and API, never an open port. The API refuses unknown Hosts:
  - add the tunnel's hostname (e.g. `dv02.example.com`) to `DV02_ALLOWED_HOSTS`
  - if the dashboard is served from a different host than the API, add its
    full origin (`https://dash.example.com`) to `DV02_ALLOWED_ORIGINS`; a page
    from the API's own host and port already passes
- **Backups and alerts.** Back up the data and Postgres to Cloudflare R2. Add a
  heartbeat alert when the daily run doesn't report.
- **Secrets** live only on the VM (`ibgw/.env`, `.env`), entered by the owner
  over SSH, never in git or chat.

### 4. Design pass on every page

Use the staging harness (`bin/tradeit staging`, `npm run e2e:staging`):
- audit each page at 1440 and 390 px in both themes
- fix what reads badly
- verify the fixes

Add a staging test for anything you fix, as for the y-label gutter.

### 5. Smaller items

- **Adjusted prices** (Phase 0 scope, not done). The price master is IBKR
  TRADES bars: split-adjusted, not dividend-adjusted. Momentum on the bond
  ETFs (TLT, IEI, SHY, BIL) ignores their income. Either add a per-symbol
  total-return factor in `generate_targets`, or move to IBKR ADJUSTED_LAST
  with `price_basis.py`'s full refetch. Compare the bond ETFs' momentum before
  and after.
- Review `services/api/dv02_api/flex.py` once more (its review was cut off).
- `trading-engine/pyproject.toml` still lists `mosek`. Make it optional (needs
  `poetry lock`) or leave it: without a license the free solvers run.
- `docs/PLAN.md`:
  - mark the Phase 0 items done
  - record the WRDS decision (limited, needs Duo: manual only)
  - record the PortfolioAnalyst import and the infrastructure status
- Keep PR #12 green and its description current.

### 6. Then the plan's phases

Phase 0's exit test is the owner's: ten trading days in a row with the doctor
green, the run executing, the EOD reconcile matching the broker to the share,
and NAV matching IBKR's statement. After that, `docs/PLAN.md` §14:
- the data platform (Phase 2)
- the engine's shadow OMS (Phase 3)
- sleeves through the research gate (Phase 4)

## Using Chrome from Claude Code (for step 3)

Claude in Chrome works from a **local** Claude Code session on the Mac (the
CLI or the VS Code extension), not from a cloud session at claude.ai/code.

1. **Install.**
   - Google Chrome.
   - The Claude in Chrome extension (version 1.0.36 or later) from the Chrome
     Web Store. Pin it, sign in with the Claude account, and grant its permissions.
2. **Sign in.** Claude Code must be signed in with `/login` to a Pro, Max,
   Team or Enterprise account; an API key won't enable Chrome. If the account
   has several organizations, the extension must use the one `/status` shows.
3. **Start.** In Terminal: `cd ~/Code/deskd-live && claude --chrome`. Confirm
   the one-time dialog. `/chrome` should then show "Status: Enabled" and
   "Extension: Installed".
4. **Sign in to the site yourself.** Sign in to Oracle Cloud in that Chrome
   window before asking Claude to read it. Claude uses the browser's login.
   On a login page or CAPTCHA it pauses for you, and it asks before acting on
   a site.
5. **If it's not detected:**
   - check `chrome://extensions`
   - update Claude Code
   - run `/chrome` → Reconnect extension
   - restart Chrome: the first enable writes a native messaging host that
     Chrome reads only at startup

Docs: https://code.claude.com/docs/en/chrome

## Owner's to-do list (only the owner can do these)

- Pull and rebuild (Start here), then import the daily NAV history once:
  ```bash
  cd ~/Code/deskd-live/services/api
  ../../.venv/bin/python -m dv02_api.portfolio_analyst ~/Downloads/<the daily PortfolioAnalyst CSV>
  ```
- Delete any `DV02_INCEPTION_*` lines from `.env` (the import replaces them).
- `bin/tradeit schedule install`, for the new 15:35 ET time, until the VM takes
  over. Don't run `python -m src.production.scheduler`: it installs the old
  16:32 schedule.
- Decide `config/execution.yaml` (In flight, A): should the scheduled run send
  paper orders automatically (`allow_submit: true`, `dry_run: false`)?
- Market data and permissions (`docs/PLAN.md` §7, §15):
  - subscribe the live login to the US Securities Snapshot and Futures Value
    Bundle, and share market data with the paper user
  - keep the live login closed while the VM's gateway runs
  - confirm the account is a margin account with permission to short
- Sign in to Oracle Cloud in Chrome when Claude asks (step 3).
