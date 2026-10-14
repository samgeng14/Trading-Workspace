# DV02 Fund API

FastAPI service behind the dashboard. It implements the contract in
`schemas/api/v1.yaml` over real data. `apps/web` runs on it with
`bin/tradeit up`, and on simulated data with `bin/tradeit demo`.

## Run it

```bash
bin/tradeit setup
bin/tradeit doctor
bin/tradeit up
```

- `setup` runs once. It creates `.venv` (Python 3.11/3.12), installs the
  dashboard packages and writes `.env`.
- `doctor` checks the IBKR connection end to end.
- `up` serves the API on 127.0.0.1:8000 and the dashboard on localhost:5173.

IB Gateway has to be running and logged in. Either:

- **Let it log in for you:** run `bin/tradeit login` once to save the paper
  login on this Mac (`ibgw/README.md`); `bin/tradeit up` then starts it
  in Docker.
- **Run it yourself:** start IB Gateway (or TWS) on paper, port 4002 (TWS:
  7497), and set these in **Configure → Settings → API → Settings**:
  - **Enable ActiveX and Socket Clients:** on.
  - **Read-Only API:** off. The doctor's order previews and the dashboard's
    cancel need it off. Nothing is sent without your typed confirmation.
  - **Trusted IPs:** include 127.0.0.1.

The API binds to 127.0.0.1 only. Interactive docs are at
http://127.0.0.1:8000/docs.

## Who can call it

There is no login. Listening on 127.0.0.1 keeps other machines out, but not
the web pages open in your own browser, so the API checks every request:

- **Host:** only 127.0.0.1, localhost, ::1, and the IP address the request
  arrived on. A hostile domain that resolves to 127.0.0.1 (DNS rebinding)
  arrives under its own name and is refused. To reach the API by another
  name, list it in `DV02_ALLOWED_HOSTS` (comma-separated, in `.env` or the
  environment).
- **Origin,** on the stream and every POST: only the dashboard (localhost or
  127.0.0.1, port 5173), the API's own pages (port 8000, where `/docs` is),
  and a page served from the host and port the request went to (`vite
  preview`, or the API on another `DV02_API_PORT`). Other pages get 403,
  and the stream is refused before it opens (close code 1008, which the
  browser sees as HTTP 403). `DV02_ALLOWED_ORIGINS` adds more. Scripts (curl,
  Python, `bin/tradeit`) send no Origin and are allowed.
- **Content-Type:** a POST body must be `application/json`, else 415.
  Another site's page can send a text/plain or form body without asking
  first, and that used to be enough to start a rebalance. A POST with no body
  (cancel, run the doctor) needs no Content-Type.

`npm run dev:api` proxies `/api` with `changeOrigin: false`, so the browser's
own Host and Origin (`localhost:5173`) reach the API unchanged and pass.

## Where each number comes from

| View | Source |
|---|---|
| NAV, cash, positions, day P&L | Live IBKR account updates and P&L subscriptions, on client ID 307 (paper) |
| Prices, previous close, ticker strip | IBKR quotes. Delayed 15 min unless real-time data is shared to the paper login |
| Intraday P&L | The API samples account P&L every 15 seconds while the market is open, to `artifacts/api/intraday/`. The chart's last point is the live P&L now |
| Live updates | One WebSocket, `/api/v1/stream`. About every 2 seconds the API computes status, the ticker strip, summary, positions, intraday P&L and orders once, and sends each open dashboard the ones that changed. Each frame's `data` is that endpoint's REST body. While the stream is up the dashboard re-checks those over REST every 30 seconds; without it, it polls every 5 seconds. A page from another site can't open the stream (see Who can call it). A topic that takes over 5 seconds (IBKR not answering) is skipped for that round so the others still go. On paper the prices underneath are delayed 15 minutes unless real-time data is shared to the paper login |
| NAV history, performance | IBKR Flex (below), plus the NAV the API records after each close, plus the engine's account snapshots. Inception is the first day with a NAV; `DV02_INCEPTION_DATE` (and `DV02_INCEPTION_NAV`) in `.env` pins it instead |
| Returns and ratios on a short record | Not invented. Ratios (vol, Sharpe, Sortino, beta, tracking error) need 20+ daily returns; under a year nothing is annualized; MTD/YTD need history that reaches the period start. Until then the API sends `null` and the dashboard says why |
| Benchmarks, betas, VaR | IBKR dividend-adjusted daily bars (cached in `artifacts/api/bars/`), else the engine's price master |
| Risk limits | Gross and cash: the C++ engine's measure of the whole account (below). Net, largest position, beta, volatility, drawdown and day P&L: computed here from the positions and the NAV history |
| Rebalance preview | The engine's own `build_reconciliation` and `run_pre_trade_risk_checks` on live positions, then the C++ no-leverage check on every holding plus the basket's orders |
| Submit | Starts the engine's daily runner with `--force-rebalance`, using the LaunchAgent's Python and environment. One order path, the engine's risk gate and kill switch, and the 15:35 ET run then skips the day |
| Orders | IBKR, all clients, this session. Offline, the engine's last submission files |
| Cancel | IBKR only lets the placing client cancel, so the API reconnects briefly as that client (the runner's 300) |
| Kill switch | The engine's file, `artifacts/runs/kill_switch.json`. Arming here stops the runner and cancels the orders still working at IBKR (best effort, as `bin/tradeit kill "why"` does). The POST takes `{"armed": true, "reason": "..."}` or `{"armed": false}`; anything else, `{}` included, is a 400 and changes nothing |
| Runs, schedule | `artifacts/runs/daily_run_*.json`; the LaunchAgent's time (15:35 ET, written in the Mac's zone) |
| Doctor | `src/broker/ibkr/doctor.py`, on client ID 309. It runs at startup and from the Run doctor button |

With IB Gateway down, the dashboard says so. It shows the engine's last saved
snapshot where one exists: futures and options at their multipliers, priced at
IBKR's mark when the snapshot was saved, else the last daily close (a NaN or
zero close is skipped). A holding with neither, or a future or option whose
multiplier the snapshot left empty, has no value and blocks the no-leverage
check as unpriced. Previews show but can't be submitted.

Positions keep IBKR's own type for the no-leverage check: a futures option is
an option (which blocks), FX is FX. The contract's `sec_type` has only STK,
ETF, FUT and OPT, so a futures option shows as OPT, and FX, bonds and crypto
show as STK with `asset_class` cash, fixed_income or crypto.

## No leverage

The fund is long/short with no leverage: gross (long plus short, futures at
full notional, leveraged and inverse ETFs at their economic exposure, TQQQ at
3x) at most 100% of NAV, and cash at or above 0 after the orders, so no margin
loan. The C++ engine checks it (`engine/README.md` has the exact
rules), and `dv02_api/mandate.py` feeds it the whole IBKR account: every
holding, including ones the model doesn't trade.

- **Preview:** the "No leverage" check runs on the book after the basket's
  orders. It fails, and the submit is refused, when that book would be over
  either limit, a name would be over 25% of NAV, a holding has no price, or
  the account holds options. A basket that only shrinks positions passes
  with a warning while the book is still over.
- **Risk:** gross and "Cash (no margin loan)" breach only beyond the limit,
  with no early warning: a fully invested book sits near 99.5% gross by
  design. Gross also shows as breached while a holding has no price or the
  account holds options, since the engine then blocks every order. A breach
  shows as a blocker on the Command Center.
- **Holdings outside the model** count toward both. The next rebalance sells
  every share holding outside the model whole (TQQQ, out of the model since
  2026-10-12, included), and the preview names them. Futures, options and
  other kinds outside it aren't sold automatically: they stay until you close
  them, and the preview warns about them. The model fills 99.5% of NAV, so
  while those add up to more than the other 0.5%, a basket that tops the
  model back up is blocked.
- **The daily runner** runs the same check from its own files before it
  sends anything (`trading-engine/src/runtime/leverage.py`).
- **Without the engine** (`dv02_engine` not installed for this Python), the
  preview fails, gross and cash show as breached, and nothing can be sent.
  `bin/tradeit setup` installs it; `bin/tradeit engine` rebuilds it after a
  pull. The status bar's Engine light shows the installed version once it
  imports and passes a self-check (a levered test book must be blocked), and
  an error naming the Python it is missing from otherwise.

## NAV history since inception (IBKR Flex)

The TWS API has no account history, so the full record comes from a Flex
query. `bin/tradeit flex` walks through it: it asks for the Query ID and the
token (hidden), loads your whole history with them, and saves them to `.env`
only if that worked. The query's period doesn't matter: IBKR serves at most
365 days per request, so the API asks for each year itself, back to the
account's first NAV (ten years at most). The Client Portal part, logged in
with the **paper** username:

1. **Performance & Reports → Flex Queries → Activity Flex Query → +**.
   Name it `DV02 NAV`, add the section **Net Asset Value (NAV) in Base**
   (all fields), format XML, any period. Save, and note the **Query ID**.
2. **Flex Web Service Configuration**: enable it and generate a **token**.
3. Run `bin/tradeit flex` and paste both. The Query ID must be digits only
   and the token 15 to 40 digits; anything else (a command pasted by
   mistake, say) is asked for again. If IBKR refuses them or can't be
   reached, the one-line reason is shown and `.env` keeps its previous
   values. Otherwise they go to `.env` at the repo root, which is
   gitignored; never commit the token.
4. It says how many days of NAV it loaded and their dates, with a note
   when something is off: under 30 days in all (the account is that new,
   or the token was made by a user who doesn't own it), or IBKR refusing
   date ranges (then only the query's own period loads; set it to Last 365
   Calendar Days). `bin/tradeit flex --refetch` loads the whole history
   again with the saved values, without asking.
5. The API caches it in `artifacts/api/flex_nav_paper.csv` and records the
   full load in `flex_backfill_paper.json` beside it; at startup and every
   6 hours after that it asks only for the last five weeks, one request.
   Restart it after saving new values; it reads `.env` once, at startup.

## Stale data

"Targets and prices … sessions old" means the daily runner hasn't refreshed
the engine's price and target files. `bin/tradeit refresh` runs it now as a
dry run: new bars and targets, no orders. The scheduled run then keeps them
fresh.

## The scheduled daily run

On a Mac, launchd starts the engine's daily runner Mon–Fri at 15:35 ET,
written in the Mac's own zone (14:35 in Central time): new bars and targets
from the last close, then paper orders, market-on-close, when the model calls
for a rebalance. Nothing runs until it is installed:

```bash
bin/tradeit schedule install   # asks first; checks the C++ engine
bin/tradeit schedule status    # loaded or not, the folder it runs from, its next run
bin/tradeit schedule remove
```

`install` writes `~/Library/LaunchAgents/com.capitalfund.daily-runner.plist`
for this checkout: `.venv`'s Python, `-m src.production.daily_runner
--profile paper --host … --port … --client-id 101 --skip-if-ran-today`, run
from `trading-engine`, logging to `trading-engine/data/logs/runtime/`. The
port and host come from `.env` (or `broker.yaml`), the optimizer from
`CAPITALFUND_OPTIMIZER` (default `momentum_tilted`), and the time from
`DV02_RUNNER_TIME`, so install again after changing them. The API reads the
same file: runs started from the dashboard use its Python and environment,
and a day that already has a successful run is skipped at 15:35. IB Gateway
has to be running and logged in then; the run doesn't start it. If the Mac
is asleep at 15:35 the run starts when it wakes (after 15:45 ET its orders go
market-on-open for the next session); if it is off, the day
is missed. It refuses to install while the engine (`dv02_engine`) can't be
imported, since the runner would then block every order, or while its gross
and cash limits let a levered test book through (the Engine light's
self-check).

## Tests

```bash
.venv/bin/pip install pytest jsonschema httpx
cd services/api && ../../.venv/bin/python -m pytest
```

What the tests cover:

- **Contract:** every endpoint is validated against `schemas/api/v1.yaml`,
  with undeclared fields rejected.
- **No real files:** a fake broker and a temp engine tree mean no test touches
  a gateway, your kill switch, or your runs.
- **Units:** the calendar, stats, Flex parsing, NAV merging, order mapping,
  the IBKR snapshot mapping, the live-account refusal, and the
  dashboard-started run.
- **No leverage** (`tests/test_mandate.py`): a levered book fails the preview
  and the submit is refused; `/risk` shows the gross and cash breaches; an
  unlevered book passes; a missing engine blocks. They need the engine
  installed in `.venv`, which `bin/tradeit setup` does.
- **Stream** (`tests/test_stream.py`): the first frames cover every topic,
  match the contract and are the REST bodies byte for byte; only changes go
  out after that; a failing topic is skipped and comes back; two dashboards
  share one computation; a slow one gets the newest value, not a backlog;
  a page from another site is refused; disconnects and shutdown clean up;
  a topic that hangs is cut off and the rest still go
  (`tests/test_stream_timeouts.py`).
- **Who can call it** (`tests/test_security.py`): another site's page can't
  open the stream or POST, and its text/plain POST no longer starts a
  rebalance; a non-JSON body gets 415; unknown Hosts and DNS rebinding are
  refused; scripts, the dashboard's origins, and the dashboard's real
  request headers through the Vite proxy (captured from Chrome) pass.
- **Holdings** (`tests/test_holdings.py`): futures options, FX, bonds and
  crypto keep their IBKR type for the no-leverage check and show in the
  contract's terms; the offline snapshot prices futures at their multiplier
  and IBKR's mark. **Engine status** (`tests/test_engine_status.py`):
  installed, missing, and failing its self-check.
- **Flex** (`tests/test_flex.py`, against a fake Flex service and clock):
  the year-by-year walk stops where the account opened, at an empty year,
  or after ten; requests stay under 1 a second and 10 a minute; a refused
  date range falls back to the query's own period; once loaded, a refresh
  is one request; errors print one line.
- **Staging** (`tests/test_staging.py`): both scenarios have the paper
  account's shape, and every endpoint answers in the contract's terms over
  them.

## Staging

The demo has short, tidy labels and a perfect history, so layout bugs that
only real data brings out slip through it. The staging API serves the real
app (every route and the stream) over a made-up copy of the paper account as
it was on 2026-10-12, with no IB Gateway: a DU account, NAV about $1.05M, a
margin loan of about $26k, 37 holdings at 102.5% of NAV (the 21-ETF model,
TQQQ, and 15 ETFs outside the model), delayed quotes, no orders today. During
the session quotes and P&L move every few seconds and the intraday samples
pile up.

```bash
bin/tradeit staging                    # fresh, dashboard on http://localhost:5174
bin/tradeit staging history --now 2026-10-12T14:30:00
cd services/api && ../../.venv/bin/python -m dv02_api.staging --scenario history --port 8099
```

- **fresh:** a new setup. One day of NAV (today's), so the equity panels
  show "No NAV history yet"; the price master and targets end 2026-05-06,
  so the preview blocks on stale targets; no LaunchAgent and no run today,
  so after 15:35 ET the schedule reports the run as missed; the doctor not run.
- **history:** an account that has been running. 131 trading days of Flex
  NAV through the latest close, daily bars, fresh targets, a daily run every
  session (a rebalance, a failure, holdings outside the model in cooldown),
  a doctor report.

`--now` starts the clock at that moment (ISO 8601; exchange time without an
offset) and lets it run. The staging API runs on UTC, so the same `--now`
gives the same page on every machine; the daily run is at 15:35 ET there as
everywhere.
Nothing real is read or written: the engine files are a temp folder deleted
on exit, `.env` and `IBKR_*` settings are ignored, Run doctor reports on the
made-up account, and Submit is refused instead of starting the daily
runner. `npm run e2e:staging` in `apps/web` runs the layout tests on it
(both themes, 1440 and 390 px wide).

## Troubleshooting

- **"nothing is listening on 127.0.0.1:4002":** IB Gateway isn't running,
  isn't logged in, or uses another port. Set `IBKR_PORT_PAPER` in `.env`.
- **The handshake times out:** 127.0.0.1 isn't a trusted IP, or another app
  holds client ID 307. Move the block with `IBKR_CLIENT_ID_BASE_PAPER` in
  `.env`.
- **"account U… is a live account":** the gateway is logged in to live while
  the API runs paper. It refuses on purpose.
- **Delayed data:** in Client Portal, log in to the live user, then
  **Settings → Paper Trading Account → share real-time market data**.
