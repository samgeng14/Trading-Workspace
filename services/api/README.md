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

## Where each number comes from

| View | Source |
|---|---|
| NAV, cash, positions, day P&L | Live IBKR account updates and P&L subscriptions, on client ID 307 (paper) |
| Prices, previous close, ticker strip | IBKR quotes. Delayed 15 min unless real-time data is shared to the paper login |
| Intraday P&L | The API samples account P&L every minute while the market is open, to `artifacts/api/intraday/` |
| NAV history, performance | IBKR Flex (below), plus the NAV the API records after each close, plus the engine's account snapshots. Inception is the first day with a NAV; `DV02_INCEPTION_DATE` (and `DV02_INCEPTION_NAV`) in `.env` pins it instead |
| Returns and ratios on a short record | Not invented. Ratios (vol, Sharpe, Sortino, beta, tracking error) need 20+ daily returns; under a year nothing is annualized; MTD/YTD need history that reaches the period start. Until then the API sends `null` and the dashboard says why |
| Benchmarks, betas, VaR | IBKR dividend-adjusted daily bars (cached in `artifacts/api/bars/`), else the engine's price master |
| Rebalance preview | The engine's own `build_reconciliation` and `run_pre_trade_risk_checks` on live positions |
| Submit | Starts the engine's daily runner with `--force-rebalance`, using the LaunchAgent's Python and environment. One order path, the engine's risk gate and kill switch, and the 16:32 run then skips the day |
| Orders | IBKR, all clients, this session. Offline, the engine's last submission files |
| Cancel | IBKR only lets the placing client cancel, so the API reconnects briefly as that client (the runner's 300) |
| Kill switch | The engine's file, `artifacts/runs/kill_switch.json`. Arming here stops the runner too |
| Runs, schedule | `artifacts/runs/daily_run_*.json`; the LaunchAgent's time (16:32 Mac local) |
| Doctor | `src/broker/ibkr/doctor.py`, on client ID 309. It runs at startup and from the Run doctor button |

With IB Gateway down, the dashboard says so. It shows the engine's last saved
snapshot where one exists. Previews show but can't be submitted.

## NAV history since inception (IBKR Flex)

The TWS API has no account history, so the full record comes from a Flex
query. `bin/tradeit flex` walks through it: it asks for the Query ID and the
token (hidden), saves them to `.env`, and fetches the statement right away.
The Client Portal part, logged in with the **paper** username:

1. **Performance & Reports → Flex Queries → Activity Flex Query → +**.
   - Name: `DV02 NAV`
   - Sections: **Net Asset Value (NAV) in Base**, all fields
   - Format: XML
   - Period: Last 365 Calendar Days
2. Save, and note the **Query ID**.
3. **Flex Web Service Configuration**: enable it and generate a **token**.
4. Run `bin/tradeit flex` and paste both. They go to `.env` at the repo root,
   which is gitignored; never commit the token.
5. The API re-fetches at startup and every 6 hours, and caches the
   statement in `artifacts/api/flex_nav_paper.csv`.

## Stale data

"Targets and prices … sessions old" means the daily runner hasn't refreshed
the engine's price and target files. `bin/tradeit refresh` runs it now as a
dry run: new bars and targets, no orders. The scheduled run then keeps them
fresh.

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
