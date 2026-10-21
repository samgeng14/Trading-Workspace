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

**The 04:14 ET dry run on 2026-10-21** (on the VM, in the scheduled run's
environment, nothing sent) passed every gate:
- the reconcile matched IBKR to the share, and bars came in through 10-20
- the drift gate said rebalance: the basket's composition changed (drift 0.75%)
- the basket was 10 small orders, a net buy of about $2k: buy VPU 29, ETHA 65,
  VHT 10, VEA 32, GLD 3 and IBIT 23; sell UNG 784, QQQ 4, VIS 8 and VWO 20
- pre-trade risk and no-leverage passed: gross 99.3% → 99.5% of NAV, cash
  $7,429 → $5,445

The 15:35 run rebuilds the basket at that hour's prices, so expect about
that. The same morning the futures bars were fixed: five of the ten had never
qualified (a currency passed as `localSymbol`; grains on ECBOT, Brent on
ICEEU), and the futures and index masters kept the partial bar of a session
still trading. Neither master feeds the model.

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

## 2026-10-21: the first rebalance from the VM, and orders IBKR didn't acknowledge

**The run.** Doctor at 15:15 ET: 0 failed. At 15:35 the drift gate rebalanced:
9 market-on-close orders (sell UNG 645, QQQ 4, VIS 9, VWO 18; buy VPU 26,
VHT 10, ETHA 47, VEA 28, VNQ 11), no-leverage PASS (gross 99.3%, cash $7,429
→ $6,998). All 9 filled at the close. The 16:30 capture saved the 9 fills and a
NAV of $1,047,727.07; the API recorded the close at $1,047,800.70. The exit
test has the session as doctor, run and reconcile pass, NAV pending Flex.

**What went wrong.** The gateway acknowledged none of the 9 orders while the
batch went out: each stayed `PendingSubmit`, with no status, warning or error
in the run's log. The order path waited 30 s after each order, then recorded
it as `ERROR` with no ids, so 9 orders took 4.5 minutes (15:35:13 to 15:39:44).
The reconcile skips `ERROR` orders as never placed, so the next day's run would
have seen 9 breaks of $1,000 to $6,400, armed the kill switch and halted the
fund.

**Fixed (af1fe9b1, deployed that evening).** The batch now goes out first,
then one wait, 30 s in all, for IBKR's acknowledgments. An order still
unacknowledged is recorded with its last status (`PendingSubmit`), its order
id and a note, and the reconcile counts it as placed. `ERROR` stays for an
order that never went out. The submission log's `acknowledged_s` column says
how long IBKR took to acknowledge each order.

**Repaired that evening on the VM, from IBKR's executions (order ids 4 to 20,
permIds 355965664 to 355965672):**
- the expected book: the 9 orders are `Filled` with their ids. Before the repair
  it had 9 breaks; after, 0 with that day's fills visible and 0 without them
  (as 10-22's run sees it). The original is
  `data/broker/reconciliations/paper_expected_book.before_repair_20261021.json`.
- the submission log `paper_orders_submitted_20261021_193513.csv` (original in
  `data/broker/orders/repaired/`), and the store's orders, now linked to their
  fills.

**Still open: why the gateway didn't acknowledge.** Its stdout logged no
dialog and its own logs are encrypted. The likeliest cause is IB Gateway
holding each API order at a precaution or no-market-data ("blind trading")
warning that nothing on the headless VM answers; IBC leaves
`BypassOrderPrecautions` and `AllowBlindTrading` unset, and the paper login
has delayed data only. The orders still went out before the close, so
something released them. Turning those warnings off weakens a broker-side
check: **the owner decides.** First read 10-22's submission log: statuses
and `acknowledged_s`.

**Check on 2026-10-22:** step 0's reconcile should report 0 breaks (the
repaired book's first test), and the submission log should show the orders
sent within seconds of each other.

**Also that evening:** the VM took its 22 pending updates (Docker, Tailscale,
Node; 6 more wait on Ubuntu's phased rollout) and rebooted into kernel
7.0.0-1013-oracle. Everything came back by itself: the gateway logged in
within 20 s, doctor 0 failed, API, Postgres, Tailscale and the timers up.

## Datasets, catch-up and the Data page, since 2026-10-21

`trading-engine/src/data/datasets.py` registers the fund's 10 datasets, as
PLAN §11 asks:
- the price, total-return, futures and index masters, and the model targets
- the curve archive
- the closing and Flex NAV
- the doctor reports
- the store
Each has a watermark (the latest session it holds) and the session it should
hold by now, from the NYSE calendar and the schedule of the job that fills it.
It also has a catch-up where a missed day can be had again.
- `bin/tradeit data` shows each one's state: ok, behind, missing, or off when
  it isn't kept on that machine.
- `bin/tradeit catchup` fills the ones behind, records what it did in
  `artifacts/catchup/`, and refuses from 15:10 to 17:45 ET while the daily
  jobs write the same files. The curve archive recovers a missed evening the
  next day, for contracts still listed.
- The dashboard's **Data** page (GET /data/datasets, hotkey g d) shows the
  same, with the last catch-up. Phase 2's exit test is that page all green.

## The lake and the futures curve archive, since 2026-10-21

The plan's point-in-time lake has begun (`trading-engine/src/data/lake.py`):
- **What it is.** Parquet under `trading-engine/data/lake/<dataset>/date=<session>/`
  (git-ignored). Every row carries its session (`as_of`) and when it was known
  (`available_at`, UTC).
- **Writing and reading.** Nothing is overwritten: a session recorded again
  adds a file. `lake.read(dataset, keys=..., known_at=...)` returns, through
  DuckDB, what was known at a moment: the latest row per key by then.

Its first dataset is the forward archive the plan asks for ("Start recording
now"):
- `dv02-archive.timer`, at 17:20 ET on weekdays, records every listed futures
  contract expiring within about 13 months (`src/data/ingest/futures_curves.py`).
- It covers the 23 roots in `config/universes/futures.yaml`'s `archive` list,
  about 220 contracts, one daily bar each into `futures_curves`.
- IBKR keeps no history of expired contracts, so this archive is the only way
  sleeve S1's carry research gets curves; a missed evening is lost for good.
- The backup includes the lake, once R2 is set up.

## Research: S1 (cross-asset trend) on ETF proxies, 2026-10-21

`trading-engine/research/s1_trend/` took S1 through the plan's research gate,
with the hypothesis fixed before any backtest. The run used the C++ walk-forward
and costs, lag 1, a 3-year holdout and 8 counted trials. It is reproducible
with one command. **REPORT.md** has the numbers. In short:
- **It fails as a standalone sleeve.** Out of sample from 2008 to 2023 its
  excess Sharpe is 0.15 (SE 0.25), and its deflated Sharpe is 0.44 against
  the 0.90 required.
- **It hedges S0 well.** It made +10% in 2008, when S0 lost 23%, and its
  correlation with S0 is −0.08. Held 50/50 with S0 inside the one 100%
  budget, it cut the book's worst drawdown from −38% to −16%.
- **S0, the live model, wouldn't pass the gate either**: 0.27 (0.26), −38%
  drawdown.
- **Long/short books drift up to 111.5% gross between monthly rebalances.**
  A long/short sleeve needs a daily trim or about 90% gross at rebalance.

Next, by the report:
1. Pre-register "S0 with a trend overlay" as its own hypothesis, counting the
   8 trials already spent. **Done the same day, `research/s3a_vol_overlay/`:**
   - **H1**, S0 scaled down when its volatility is high (the plan's S3a),
     fails: its drawdown was 64% of S0's against a 60% limit, and the holdout
     would have killed it.
   - **H2**, half S0 and half S1 long/flat, passes all seven criteria. Its
     worst drawdown was −22% instead of −38% (shallower in 100% of bootstrap
     resamples), its Sharpe within noise of S0's, and the holdout didn't kill
     it.
   - By the rule fixed beforehand, H2 goes to **shadow mode** for at least
     three months: targets and would-be orders logged daily beside S0, nothing
     sent. That is the next piece to build.
2. The futures version of S1, once the curve archive has history.
3. IBKR's adjusted closes back past 2016.

## Phase 0's exit test, tracked since 2026-10-21

`bin/tradeit exit-test` (on the VM: `sudo -u dv02 /opt/dv02/bin/tradeit exit-test`)
scores each session on the plan's four checks and counts the streak; 10 in a
row passes (`src/runtime/exit_test.py`). The dashboard's Operations page shows
the same (GET /ops/exit-test), newest session first, with why a session failed:
- **doctor:** that day's report has no FAIL. `dv02-doctor.timer` runs the
  doctor at 15:15 ET on weekdays and keeps the day's copy in
  `artifacts/doctor/`; the dashboard's Run doctor keeps one too. Until
  2026-10-21 the doctor ran only when the API started, so no earlier session
  can pass.
- **run:** that day's last real run ended ok (dry runs don't count).
- **reconcile:** that run's reconcile found no difference at all.
- **nav:** the close the API recorded is within 0.10% of Flex's statement
  NAV for that day (`--nav-tolerance-pct`; the owner hasn't set a number).
  Since 2026-10-21 the API records the close once, 15 minutes after the
  closing auction. Before, every pass until midnight replaced it with
  after-hours marks, 0.01% to 0.12% from the statement on 10-12 to 10-20.

Today's checks and the last few sessions' NAV stay pending until their
evidence is in. If every session from 2026-10-21 passes, the tenth is
2026-11-03, decided when its statement arrives on 11-04.

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
- The indices step logs error 162 for DXY every run: the paper login has no
  market data for ICE US indices (NYBOT). Not used by the model; subscribe
  (owner) or drop it from `append_indices_daily.INDEX_CONTRACTS`.
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
- **Turn the alerts on.** Make six checks on healthchecks.io (free): the
  daily run (period 1 day, grace about 1 hour), the API (period 5 minutes),
  the backup (1 day), the post-close capture (1 day, grace about 1 hour)
  the doctor before the run (1 day, grace 30 minutes) and the curve archive
  (1 day, grace 1 hour). Put their ping URLs in `/etc/dv02/dv02.env` on the VM
  as `DV02_HEARTBEAT_RUNNER`, `DV02_HEARTBEAT_API`, `DV02_HEARTBEAT_BACKUP`,
  `DV02_HEARTBEAT_CAPTURE`, `DV02_HEARTBEAT_DOCTOR` and `DV02_HEARTBEAT_ARCHIVE`,
  then `sudo systemctl restart dv02-api`. Until
  then a failed or halted run tells nobody.
- **Say how close "NAV matching the statement" must be** for Phase 0's exit
  test. `bin/tradeit exit-test` uses 0.10% of NAV (about $1,000) until then.

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