# Handoff: where DV02 stands and what to do next

Last updated 2026-10-21 by the session that resumed the fund (below) and put
the trading record in Postgres ("The store", below). The 2026-10-20 session
before it found the fund halted since Monday and fixed why; the 2026-10-16
session wired `execution.yaml` (the owner said the scheduled run sends paper
orders), put the signals on total-return prices, moved the Flex walk off the
API's upkeep loop and wrote `deploy/` for the VM. All of it is on PR [samgeng14/Trading-Workspace#12](https://github.com/samgeng14/Trading-Workspace/pull/12)
(branch `claude/serene-babbage-0204ht`, draft; PR #13, a dashboard redesign
from another session, is stacked on it). `CLAUDE.md` has the owner's rules;
this file has the state. Keep both current as you work.

## Read first: halted 2026-10-19 to 2026-10-21, resumed

**Resumed 2026-10-21 at 03:09 ET** (the owner asked; the doctor passed and the
reconcile found 0 breaks against IBKR's 21 positions). The first rebalance from
the VM is that day's 15:35 ET run: check its run log and fills that evening.
Nothing had traded since the first basket filled at the open on Friday
2026-10-16 (37 orders, forced from the Mac at 00:35; its reconcile that evening
matched IBKR to the share: 21 positions).

**2026-10-21, before the resume.** The first resume attempt at 03:04 ET
refused, correctly: the gateway was cut off again. Its log showed why: at
00:16 ET, near IBKR's nightly reset, a reconnect got "Unrecognized Username or
Password" and the gateway sat on that dialog. `docker restart` logged it
straight back in with the same saved login (so the login is fine), and the
resume then passed. Since 856a2315 the runner's unit runs
`deploy/bin/gateway-ready` before each run: when the gateway isn't logged in to
IBKR it restarts the container once and waits for the login (under a minute,
inside the 15:45 ET market-on-close deadline). Tuesday 2026-10-20 between
02:01 and 02:23 ET the VM's gateway also fought a second session with the same
paper login: the Mac's old container from `~/TradingWorkstation`, started by
hand at 01:01 CDT (Docker hadn't restarted). **Don't run `bin/tradeit up` or
`gateway start` in `~/TradingWorkstation` while the VM trades**: that checkout
still has the login, and its gateway kicks the VM's (one session per login).

What happened:
- **Mon 2026-10-19, 15:35 ET.** The VM's IB Gateway had lost its own link to
  IBKR's servers (warning 2110, market data farms down) while its API socket
  still answered. Every startup request timed out; ib_async logged it and
  carried on with no positions. The run's reconcile read that as "the account
  holds nothing": 21 breaks, kill switch armed, and the expected book reset to
  empty. The run's bars request then hung until systemd stopped it at 45 min.
- **Tue 2026-10-20, 15:35 ET.** The gateway was fine. The reconcile compared
  IBKR's 21 positions with Monday's empty book: 21 breaks again (the switch was
  already armed), and the book reset to the true 21 positions. The basket was
  also blocked by the no-leverage check, for a second, independent reason: it
  sized trades at the targets' close while the account is valued at IBKR's
  marks, so on an up day it planned a net buy of $10,100 with $7,429 in cash
  (gross 100.3%, cash -$2,682).
- **Nobody was told.** Both failures fired the alert unit, which pings
  healthchecks.io, but `DV02_HEARTBEAT_*` isn't set on the VM.

Fixed and deployed to the VM (930e6bb1, 2026-10-20):
- aef80b97: every IBKR connection (the engine's client, the bar appends, the
  doctor, the API's dashboard link) fails with `IBKRSyncError` when the startup
  sync times out, instead of acting on an empty read. Requests time out (60 s,
  120 s for bars), and after one does, the rest of that loop fails at once.
  Monday's run would now stop at step 1 within seconds, without arming the
  switch or touching the book.
- ca86415b: the basket sizes held names at IBKR's mark (new `price` column;
  `close` stays the targets' close) and, given the account's cash, cuts the
  buys in proportion so the orders leave the 0.5% buffer (`trimmed_shares`,
  reported in the run log and the preview). Replayed on 2026-10-20's real
  inputs: 14 orders, net buy $546, gross 99.35%, cash $6,865 after; the check
  passes where the old basket was blocked.
- 15e711ae: a real run reports an armed kill switch as a failure ("halted" in
  the run log, "Halted: ..." on the dashboard), so the alert fires daily until
  someone resumes.
- 930e6bb1: `submit_paper_orders()` sends nothing while IBKR has a working order
  (from any client) on a symbol in the batch, or when it can't check. A second
  batch the same day (a forced re-run, a run after one crashed mid-send) would
  otherwise trade the same differences again, since positions don't show an
  order until it fills.

On the VM after the fixes: the doctor passes (quotes delayed, as before), IBKR
holds no working orders, and the expected book holds the true 21 positions, so
`resume` would pass. To resume (the owner's call, per `CLAUDE.md`):
```bash
ssh ubuntu@64.181.195.172          # or ubuntu@dv02 with Tailscale on
sudo -u dv02 /opt/dv02/bin/tradeit resume
```
The next run is 15:35 ET on the next trading day. Its basket then holds the
composition change the targets have wanted since 2026-10-16.

## The store (Postgres), since 2026-10-21

The trading record now also goes to Postgres on the VM
(`trading-engine/src/storage`; `deploy/postgres`, 127.0.0.1:5432, database
`trading`). Writing it is best effort: a store that is off or down is logged
and never stops a run, and the engine's files stay the record. Nothing reads
the store yet.
- **What writes it.**
  - The daily run: its log (`run_logs`, one row per run_id) and the orders
    it sent (`orders`, keyed by IBKR's permId).
  - `dv02-capture.timer`, 16:30 ET on weekdays (`src.storage.capture`,
    read-only, client ID 314): the day's executions into `fills` (each finds
    its order by permId and marks it filled or partial), every position, and
    the NAV once the session has closed (`nav`, source `ibkr_close`). IBKR's
    API shows only the current day's executions, so this evening read is the
    one that sees the market-on-close fills.
- **Loaded from the files** on 2026-10-21 by `sudo deploy/bin/postgres-up`
  (safe to run again; it loads anything new): 6 run logs; the 37 orders of
  2026-10-16, filled as that evening's reconcile saw them (no `fills` rows:
  its report has no execution IDs); 3 position snapshots; NAV for 163 days
  from Flex and 4 closes the API recorded.
- **On/off:** `DV02_DB_ENABLED=1` in `/etc/dv02/dv02.env`; `0` stops the
  writes. `postgres-up` made the database password on the VM (in that file
  and `/etc/dv02/pg_password`, root only). Nobody needs to know it.
- **Look inside:** `cd /opt/dv02 && sudo docker compose -f deploy/postgres/docker-compose.yml exec postgres psql -U dv02 trading`.
- **Check after 16:30 ET on 2026-10-21** (the first capture after a rebalance):
  `journalctl -u dv02-capture` gives the day's executions, and
  `SELECT symbol, side, qty, status FROM orders WHERE run_id > 20261021000000`
  shows the day's orders filled. A run started by hand with `bin/tradeit`
  doesn't load `/etc/dv02/dv02.env`, so it isn't written; `postgres-up` loads
  its files later.
- **Not yet:**
  - the dashboard and the reconcile reading the store
  - Flex trades into `fills`: needs the Trades section in the Flex query
    (owner)
  - backups of the store: `deploy/bin/backup` dumps it inside its container,
    once there is an R2 remote
  - a heartbeat for the capture (`DV02_HEARTBEAT_CAPTURE`)

## Start here

1. Pull the branch. On the owner's Mac the clone is `~/Code/deskd-live`, where
   `origin` is the GitHub repo (the folder began as a Kalshi-desk clone; that repo is the remote `kalshi` since 2026-10-21):
   ```bash
   cd ~/Code/deskd-live
   git status            # the four price/targets data files are modified by the local refresh: leave them
   git fetch origin && git merge --ff-only origin/claude/serene-babbage-0204ht
   bin/tradeit engine    # rebuild the C++ engine after any pull that touches engine/
   .venv/bin/pip install pytest jsonschema httpx ruff==0.8.6   # what the checks need beyond `tradeit setup`
   ```
2. Look at PR #12's checks on GitHub (`gh pr checks 12 --repo samgeng14/Trading-Workspace`).
   If anything is red on the latest commit, fix that first. Then read the VM's
   last run: `ssh ubuntu@64.181.195.172`, `systemctl status dv02-runner`, and
   `/opt/dv02/trading-engine/artifacts/runs/` (newest `daily_run_*.json`).
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

- **B, the cost model, is finished** (3a84e933): `engine/tests/python/test_costs.py`
  and `test_walkforward.py` (30 tests: commission tiers, half-spread,
  square-root impact, actual/360 accrual over a weekend, costs off bit-for-bit
  equal to the plain backtest, a look-ahead canary, the holdout untouched,
  the embargo, the deflated Sharpe on hand-computed values). The review fixed
  NaN and negative-day-count handling in `costs.cpp` and a wiped-out book's
  return in `walkforward.py`. The zero-interest smoke run was the default
  cash rate, 0 (off); it is 338 bps now, IBKR Pro's USD rate on 2026-10-13
  (benchmark less 0.5%, on balances over $10k), to be refreshed as the Fed
  moves. On the owner's Mac, `cmake` and `ninja` under /usr/local/bin are
  x86_64 binaries: a build whose Python module won't load needs
  `-DCMAKE_OSX_ARCHITECTURES=arm64` (CI is unaffected).
- **`config/execution.yaml` is read on every run** (2026-10-16). The owner
  decided the scheduled run sends paper orders by itself: `mode: paper`,
  `allow_submit: true`, `dry_run: false`, tested as the committed values
  (`trading-engine/tests/unit/test_execution_config.py`).
  - Any other value, or a file that can't be read, makes a run a dry run that
    says why (`submit_disabled` in the run log).
  - The dashboard shows it: the rebalance preview has an "Orders switched on"
    check that blocks Submit when off, and the Runs view and schedule show an
    orders-off run as the day's run, not a missed one.
  - The logic lives in `src/production/execution_config.py`, shared by the
    runner and the API.

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

### 2. The always-on host: the owner's Oracle Cloud VM

**Since 2026-10-16 (evening) the fund trades from the VM.** The VM holds the
gateway, and its `dv02-runner.timer` does the 15:35 ET run, first on Monday
2026-10-19. The Mac's gateway and LaunchAgent are off. `bin/tradeit switch
vm|mac|status`, run on the Mac, moves trading between the two
(`deploy/README.md`, "Switching between the Mac and the VM"). Check the
first VM run that evening: the run log, and the dashboard at
https://dv02.tail30c53b.ts.net.

- **The Mac's login.** The Mac's own IB Gateway login lives in
  `~/TradingWorkstation/ibgw/.env`, not in this checkout (`ibgw/.env` is
  absent here). Its stopped container (`ibgw-ib-gateway-1`, started from
  there) is what `switch mac` restarts. The VM's `ibgw/.env` is a copy of
  that file, streamed over SSH unread, because the first login typed on the
  VM had the live username in it.
- **The 17:42 dry run.** A dry run (`bin/tradeit refresh`) on the Mac at
  17:42 Central refreshed prices and targets from the 2026-10-16 close. Its
  drift gate said no rebalance: the composition changed, but the cooldown had
  3 days left. The dashboard's preview shows the 20 small trades those
  targets imply. The daily run decides again on Monday.

**The VM, `dv02` (built 2026-10-16 with `deploy/bin/bootstrap`):**
- Oracle Cloud us-chicago-1, AD-3, in the root compartment, subnet
  `deskd-public` of `deskd-vcn` (10.0.0.0/16)
- VM.Standard.A1.Flex (arm64 Ampere), 3 OCPU, 18 GB
- 50 GB boot volume, Canonical Ubuntu 24.04 (image 2026.09.18)
- public IP 64.181.195.172. The security list allows 22/tcp from anywhere,
  plus ICMP. The login is `ssh ubuntu@64.181.195.172` with the Mac's
  `~/.ssh/id_ed25519`, or `ssh ubuntu@dv02` over Tailscale
- Tailscale name `dv02.tail30c53b.ts.net`. `tailscale serve` puts the
  dashboard at https://dv02.tail30c53b.ts.net, on the tailnet only
- running: `dv02-api` (127.0.0.1:8000, foreign Hosts get 400),
  `dv02-api-check.timer`, IB Gateway (paper DUP317554; the doctor passes)
  and `dv02-runner.timer`
- the store: Postgres in Docker (`postgres-postgres-1`) and `dv02-capture.timer`
  (since 2026-10-21, "The store" above)
- not running yet: backups (no `DV02_BACKUP_REMOTE`), heartbeats
- the Mac's state (artifacts, data/broker, data/market/cleaned) was copied
  on 2026-10-16 after the close

Also on the account: `deskd-recorder` (A1, 1 OCPU, 6 GB, 150 GB boot,
64.181.211.165, AD-3). Together the two VMs use exactly the Always Free
allowance: 4 OCPU, 24 GB and 200 GB of block storage. The account is Pay As
You Go (its A1 limit is 250 cores), so anything beyond that is billed.

Learned while setting it up:
- Tailscale's login link fails in an embedded browser. Open it in your own
  browser.
- `tailscale serve` waits until Serve is enabled for the tailnet once, at
  the link it prints.
- The VM's checkout carries the Mac's newer copies of the tracked files in
  `data/market/cleaned`. `setup-app`'s `--ff-only` update stops if the
  branch changes those files: commit or reset them on the VM first.

**The files are ready (2026-10-16): [`deploy/README.md`](../deploy/README.md)**
is the runbook (setup, dry run, cut over, rollback), with:
- systemd units: the API, which now serves the built dashboard itself
  (`DV02_WEB_DIST`); the 15:35 America/New_York runner timer; an API check;
  a daily R2 backup; failure alerts
- heartbeat, backup and migrate scripts
- the tunnel config, Postgres and the env template

`deploy/bin/check` verifies them; CI runs it as `deploy / check`. What's left
needs the VM, and the owner at the console. The notes below are the
background the runbook follows:

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
- **Postgres** for orders, fills, positions and NAV. Done 2026-10-21: "The
  store" above. Left:
  - Add the Trades section to the Flex query and load Flex trades into
    `fills`, so the blotter and the reconcile have history beyond today.
  - Have the dashboard (the blotter, the equity curve) read the store.
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

- **Adjusted prices: done (2026-10-16), first run on IBKR 2026-10-20.** The
  VM's run refreshed the total-return closes for all 21 ETFs, none fell back to
  price, and `--compare` on the 2026-10-19 close shows the income each signal
  now counts over the 126-day lookback: BIL +1.79%, SHY +1.77%, IEI +1.84%,
  TLT +2.14%, VNQ +1.64%, VPU +1.25%, SPY +0.55%, QQQ +0.24%, and 0 for the
  funds without distributions (GLD, SLV, USO, UNG, VIXY, IBIT, ETHA, INDA).
  - Momentum and volatility run on total-return closes:
    - IBKR's ADJUSTED_LAST bars (split- and dividend-adjusted), kept in
      `data/market/cleaned/prices/etf_total_return_closes.parquet` (git-ignored)
    - refreshed after each daily run and by `bin/tradeit refresh`
      (`src/production/pipeline/append_total_return.py`)
    - applied by `src/data/cleaning/total_return.py`
  - The price master and order prices are unchanged.
  - A symbol whose adjusted bars disagree with the master on a split stays on
    price, and the run log names it. Until the first refresh, every symbol is
    on price and the run log says so.
  - The switch is `etfs.signal_prices` in `config/universes/etfs.yaml`: set it
    to `price` to go back.
  - On the Mac, after the first refresh, compare the two with
    `python -m src.production.pipeline.append_total_return --compare` (run from
    `trading-engine/`). Expect BIL, SHY, IEI and TLT to gain about their yield
    over the lookback. Then check that the next targets look sensible.
- ~~Review `services/api/dv02_api/flex.py` once more.~~ Done (07577133): a
  non-Flex reply (IBKR's maintenance page) no longer raises an empty error, the
  token can't reach a log, a traceback, the cache or the printed line, a dropped
  connection is retried, a corrupt cache keeps its readable rows, and the
  parser handles the other date formats and multi-account statements. The
  API's upkeep loop now runs the Flex walk as its own task, one at a time, so
  a slow walk can't delay the closing NAV, and its timers no longer wait out
  their interval after a recent reboot (2026-10-16).
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

- Don't start the Mac's gateway (`bin/tradeit up` in `~/TradingWorkstation`)
  while the VM trades; move trading with `bin/tradeit switch mac` instead.
- **Turn the alerts on.** Make four checks on healthchecks.io (free): the
  daily run (period 1 day, grace about 1 hour), the API (period 5 minutes),
  the backup (1 day) and the post-close capture (1 day, grace about 1 hour).
  Put their ping URLs in `/etc/dv02/dv02.env` on the VM as
  `DV02_HEARTBEAT_RUNNER`, `DV02_HEARTBEAT_API`, `DV02_HEARTBEAT_BACKUP` and
  `DV02_HEARTBEAT_CAPTURE`, then `sudo systemctl restart dv02-api`. Until
  then a failed or halted run tells nobody.

- Pull and rebuild (Start here), then import the daily NAV history once:
  ```bash
  cd ~/Code/deskd-live/services/api
  ../../.venv/bin/python -m dv02_api.portfolio_analyst ~/Downloads/<the daily PortfolioAnalyst CSV>
  ```
- Delete any `DV02_INCEPTION_*` lines from `.env` (the import replaces them).
- Trading runs on the VM (step 2). To move it back to the Mac:
  `bin/tradeit switch mac`. Optionally, save this checkout's own login with
  `bin/tradeit login` (the **paper** username), so the Mac's gateway no
  longer depends on `~/TradingWorkstation`.
- The limits in `config/risk_limits.yaml` were confirmed on 2026-10-16: one name
  at most 20%, net between 50% and 100%, at least 5 holdings.
- Market data and permissions (`docs/PLAN.md` §7, §15):
  - subscribe the live login to the US Securities Snapshot and Futures Value
    Bundle, and share market data with the paper user
  - keep the live login closed while the VM's gateway runs
  - confirm the account is a margin account with permission to short