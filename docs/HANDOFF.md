# Handoff: where DV02 stands and what to do next

Last updated 2026-10-13 (evening) by the session that finished Phase 0's safety
work on PR [samgeng14/Trading-Workspace#12](https://github.com/samgeng14/Trading-Workspace/pull/12)
(branch `claude/serene-babbage-0204ht`, draft). `CLAUDE.md` has the owner's
rules; this file has the state. Keep both current as you work.

## Start here

1. Pull the branch. On the owner's Mac the clone is `~/Code/deskd-live`, where
   the GitHub repo is the remote `tw` (`origin` there is an older repo):
   ```bash
   cd ~/Code/deskd-live
   git status            # the four price/targets data files are modified by the local refresh: leave them
   git fetch tw && git merge --ff-only tw/claude/serene-babbage-0204ht
   bin/tradeit engine    # rebuild the C++ engine after any pull that touches engine/
   .venv/bin/pip install pytest jsonschema httpx ruff==0.8.6   # what the checks need beyond `tradeit setup`
   ```
2. Look at PR #12's checks on GitHub (`gh pr checks 12 --repo samgeng14/Trading-Workspace`).
   Every job was green on 3cc7966c. If anything is red on the latest commit, fix that first.
3. Compare `git log --oneline -15` with "Done" and "Unfinished work" below.
4. Run the checks in `CLAUDE.md` once, so you start from green. Two things to
   know on the owner's Mac:
   - The API tests read the engine's committed data files through the engine's
     own default paths, so with the Mac's refreshed `etf_prices_master.*` and
     `etf_targets_monthly.*` four of them fail. Stash those four files around
     the API run (`git stash push -- <the four files>` … `git stash pop`); CI
     has the committed data and passes.
   - The dashboard's e2e suites need Playwright's browser once:
     `cd apps/web && npx playwright install chromium`.

## Done (pushed)

- **C++ engine** (`engine/`): the no-leverage check, the backtester (ported
  from Rust; Rust is gone), the cost model and walk-forward (d783748c; its
  Python tests are the one thing unfinished, below), Python bindings, CI on
  GCC+sanitizers, Clang, macOS.
- **Every order path is gated.** `submit_paper_orders()`, the one function
  that places orders, runs the kill switch and the C++ no-leverage check on
  exactly the rows it sends, before it connects; the daily runner's own gate
  (exposure limits, price validation, the same two checks) runs first and logs
  the full report. The unguarded Streamlit submit page is gone; the order
  service lives in `src/production/runtime/order_service.py`.
- **The daily run** (`trading-engine/src/production/daily_runner.py`):
  - skips weekends and NYSE holidays (a real run writes a "Skipped: NYSE
    closed" run log; a dry run still refreshes data) and logs early closes as
    market-on-open by design;
  - reconciles first (step 0): the expected book (`src/runtime/end_of_day_reconcile.py`,
    `data/broker/reconciliations/{profile}_expected_book.json`) is the
    positions a basket was built on plus every order sent since, at the fills
    IBKR reports (in full when it reports none; its API keeps one day), compared
    to the share with IBKR's positions. A break of $100 or more, or one with no
    price, arms the kill switch; the book then resets to IBKR's positions. It
    waits while a market-on-close auction is still ahead; an unreachable gateway
    is a failure, and a failed reconcile blocks that run's orders;
  - records each real submission in the book (`record_submission`);
  - runs the drift gate on today's targets against the account's positions now
    (IBKR, else the last snapshot, over NAV), so a composition change is seen;
  - blocks on a model ETF whose last bar is more than one session behind the
    last completed session (`src/data/cleaning/validators.py`);
  - takes its exposure limits from `config/risk_limits.yaml`
    (`load_exposure_limits`: every key set, unknown keys an error, nothing
    looser than the mandate). That enforces the file's values for the first
    time: one name at most 20%, net between 50% and 100%, at least 5 holdings.
- **The kill is real.** `bin/tradeit kill "why"` arms the switch, then connects
  as the client that placed the orders and cancels every working one;
  `bin/tradeit resume` disarms only after the doctor and a reconcile pass;
  `bin/tradeit status` shows the switch, IBKR's working orders and the last
  reconcile (`src/execution/kill.py`). The dashboard's halt arms the switch
  and cancels the working orders too (`POST /ops/kill-switch`).
- **One schedule.** The old `src/production/scheduler.py` (16:32, RunAtLoad)
  and `src/runtime/schedule.py` are deleted; every doc says 15:35 ET and
  `bin/tradeit schedule`.
- **Dashboard** (`apps/web`): six pages, Journal (WSJ) theme, live stream, the
  IBKR-style return chart, a demo mode (MSW) and a staging mode (`bin/tradeit
  staging`, `npm run e2e:staging`). **API** (`services/api`): contract
  `schemas/api/v1.yaml`, Host/Origin lockdown, Flex NAV history, PortfolioAnalyst
  import; the Runs view labels the new Reconcile step and skipped runs.
- **Phase 0 pieces**: MOSEK out (free solvers), market-on-close/market-on-open
  execution with the run at 15:35 ET, early closes in the clock, price-basis
  checks, dead code deleted, trading-engine tests in CI (465 pass).

## Unfinished work

- **B, the cost model's Python tests and review.** A background agent was
  finishing `engine/tests/python/test_costs.py` and `test_walkforward.py`, an
  adversarial review of `costs.cpp` and `walkforward.py`, and the known bug
  that a cash-only book earned no interest (check the default cash rate and
  the interest path end to end). If it landed, there is a commit after
  3cc7966c touching only `engine/`; if not, that list is what to do, with the
  engine CI commands in `CLAUDE.md`. `walkforward.py` may import only the
  standard library, numpy and pandas.
- **`config/execution.yaml` is still not read.** `allow_submit` and `dry_run`
  are documented there and the runner has `_load_execution_config` and
  `_submit_disabled`, not yet called. The committed values are `false`/`true`,
  which would turn every scheduled run into a dry run once wired in. The owner
  has not said yet whether the scheduled run should send paper orders by
  itself; ask, set the values in the same commit as the wiring, and add a test
  that a run with that config submits (and one that a run without it doesn't).

## Next, in this order

### 1. Check the first real runs (with the owner)

Nothing is scheduled on the Mac right now (`bin/tradeit schedule status`), and
the market-on-close change has never sent an order. On 2026-10-13 at 20:10 CT
`bin/tradeit refresh` (the whole pipeline as a dry run, against the real paper
gateway) passed every gate: step 0 said "no expected book yet"; bars through
Oct 13; targets rebuilt; the drift gate cold-started (no prior basket state);
the basket had 36 orders, selling the 16 holdings outside the model (DBC, EMB,
EWJ, EWY, FXI, GDX, HYG, LQD, SMH, SOXX, TIP, TNA, TQQQ, XLE, XLP, XLU);
pre-trade risk PASS under the 20% single-name limit; no-leverage PASS, gross
103.5% → 99.4% of NAV and cash −$26,041 → +$6,199. `bin/tradeit doctor` passes
(the order preview is accepted; quotes are delayed 15 min until market data is
shared to the paper login). So the first real run after the owner installs the
schedule sends that basket market-on-close. On that day, check the run log
(`artifacts/runs/daily_run_*.json`) and the dashboard's blotter:
- step 0 (reconcile) says "no expected book yet", then the next day's says the
  book matches to the share
- the orders went market-on-close and filled at that close
- the holdings outside the model and TQQQ are gone
- gross is at most 100% of NAV and cash is at least 0 afterwards

Once, send a small paper order after 15:45 ET to confirm IBKR accepts and
holds the market-on-open (MKT, tif OPG) order that `auction.py`'s late path sends.
Then try `bin/tradeit kill "test"` with a working order and `bin/tradeit resume`.

### 2. The always-on host: the owner's Oracle Cloud VM (needs Chrome)

Today everything runs on the Mac, which must be awake at 14:35 Central. The
plan (`docs/PLAN.md` §12, Phase 2) moves the gateway, runner, API and
Postgres to the owner's Oracle VM. On 2026-10-13 Claude in Chrome was not
connected on the Mac, so this did not start.

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
  `bin/tradeit gateway stop` on the Mac and never run both. On the Mac the
  gateway container was started from `~/TradingWorkstation/ibgw` (an older
  checkout) and has `restart: unless-stopped`; stop it from there.
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
  - `trading-engine/data/broker/`: the drift cooldown state, reconciliations,
    the expected book, positions snapshots

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

### 3. Design pass on every page (done 2026-10-13)

Every page was captured at 1440 and 390 px in both themes on the staging
harness. Fixed: the Risk page's Sleeves table ran past its panel (the mandate
wraps now, with a staging test); the stale-prices block was a wall of text
(one line now); Operations said "0 passed · ran …" for a doctor that never ran.
Phone-width tables scroll sideways inside their panels, which is by design.
If the owner wants more, do another pass the same way (`e2e/staging/layout.spec.ts`
has the page list, the theme switch and the overflow check).

### 4. Smaller items

- **Adjusted prices** (Phase 0 scope, not done). The price master is IBKR
  TRADES bars: split-adjusted, not dividend-adjusted. Momentum on the bond
  ETFs (TLT, IEI, SHY, BIL) ignores their income. Either add a per-symbol
  total-return factor in `generate_targets`, or move to IBKR ADJUSTED_LAST
  with `price_basis.py`'s full refetch. Compare the bond ETFs' momentum before
  and after.
- Review `services/api/dv02_api/flex.py` once more (its review was cut off).
- `trading-engine/pyproject.toml` still lists `mosek`. Make it optional (needs
  `poetry lock`) or leave it: without a license the free solvers run.
- `docs/PLAN.md`: the Phase 0 known issues that are fixed are struck through
  in place; record the WRDS decision (limited, needs Duo: manual only), the
  PortfolioAnalyst import and the infrastructure status.
- The tests under `trading-engine/tests` are a package now (`__init__.py` in
  `tests/` and `tests/unit/`), so a test can import another test's fakes
  (`tests.unit.test_expected_book.FakeIB`); `tests/integration` isn't yet.
- Keep PR #12 green and its description current.

### 5. Then the plan's phases

Phase 0's exit test is the owner's: ten trading days in a row with the doctor
green, the run executing, the EOD reconcile matching the broker to the share,
and NAV matching IBKR's statement. After that, `docs/PLAN.md` §14:
- the data platform (Phase 2)
- the engine's shadow OMS (Phase 3)
- sleeves through the research gate (Phase 4)

## Using Chrome from Claude Code (for step 2)

Claude in Chrome works from a **local** Claude Code session on the Mac (the
CLI, the desktop app or the VS Code extension), not from a cloud session.

1. **Install.**
   - Google Chrome.
   - The Claude in Chrome extension (version 1.0.36 or later) from the Chrome
     Web Store: https://chromewebstore.google.com/detail/fcoeoabgfenejglbffodgkkbkcdhcgfn.
     Pin it, open its side panel, and sign in with the same Claude account as
     Claude Code.
2. **Sign in.** Claude Code must be signed in with `/login` to a Pro, Max,
   Team or Enterprise account; an API key won't enable Chrome. If the account
   has several organizations, the extension must use the one `/status` shows.
3. **Start.** In Terminal: `cd ~/Code/deskd-live && claude --chrome`. Confirm
   the one-time dialog. `/chrome` should then show "Status: Enabled" and
   "Extension: Installed". In the desktop app, the Chrome tools are on when the
   extension is connected (`list_connected_browsers` shows the browser).
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
- `bin/tradeit schedule install`, for the 15:35 ET time, until the VM takes
  over. Nothing is scheduled on the Mac today.
- Decide `config/execution.yaml` (Unfinished work): should the scheduled run send
  paper orders automatically (`allow_submit: true`, `dry_run: false`)?
- Confirm the limits `config/risk_limits.yaml` now enforces: one name at most
  20%, net between 50% and 100%, at least 5 holdings, cash buffer 0 (the basket
  keeps its own 0.5%). Loosen or tighten there; nothing may exceed the mandate.
- Market data and permissions (`docs/PLAN.md` §7, §15):
  - subscribe the live login to the US Securities Snapshot and Futures Value
    Bundle, and share market data with the paper user
  - keep the live login closed while the VM's gateway runs
  - confirm the account is a margin account with permission to short
- Connect Claude in Chrome and sign in to Oracle Cloud there when Claude asks (step 2).
