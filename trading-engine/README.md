# Capital Fund — Multi-Model Quant Trading Workspace

A self-contained, **paper-trading-first** quant system that picks ETFs every
month and submits orders to your Interactive Brokers paper account, all
on its own. Comes with a live dashboard, a kill switch, a system-health
audit page, and an extensible model catalogue you can add new strategies
to without touching the runtime.

> If you know what SPY, QQQ, and a momentum factor are but `pip install`
> still feels foreign, this README will walk you through it. **Plan on
> ~45 minutes for first install** if you don't have Python or IBKR set up
> yet, ~10 minutes if you do.

---

## What it does, in plain English

1. **Every weekday at 3:35 PM ET** (2:35 PM Central), your Mac wakes (if
   asleep), connects to a running IB Gateway, and pulls the last completed
   session's close prices for the model's 21 ETFs.
2. It runs the **multi-model strategy** — 14 different signal generators
   (momentum, mean reversion, volatility-targeting, MACD, RSI, …) score
   every ETF, an aggregator combines those scores into target weights,
   and an optimizer (optional) refines them with a covariance model.
3. If today is a rebalance day, it builds a basket and **submits market
   orders** to your paper account at IBKR.
4. A pre-trade **risk gate** and a **kill switch** guard every order
   submission. If anything looks wrong (concentration breach, stale data,
   kill switch armed) the system aborts before any network call to IBKR.
5. You can watch all of this in a Streamlit dashboard (`localhost:8501`)
   that shows live positions, equity curves, the order book, and a
   system-audit page that turns red when something needs your attention.

It is **paper trading by default** — port 7497 (TWS) or 4002 (IB Gateway).
You have to do the equivalent of editing a config to point at the live
ports (7496 / 4001). That's intentional.

---

## Prerequisites

You need three things before this works:

1. **A computer (Mac recommended)** with Python 3.11+. We'll install Python
   below if you don't have it.
2. **An Interactive Brokers paper account.** Free at
   [interactivebrokers.com](https://www.interactivebrokers.com/) — sign
   up and select "Paper Trading" mode. You won't trade real money.
3. **TWS or IB Gateway** — IBKR's desktop apps. Pick one:
   - **TWS** (Trader Workstation) — full trading workstation; what you'd
     use to look at charts. Bigger app.
   - **IB Gateway** — minimal headless version that just exposes the API.
     Lighter and recommended for unattended trading.
   Download both at
   [interactivebrokers.com/en/trading/tws.php](https://www.interactivebrokers.com/en/trading/tws.php).

> The system will work with either app. If you're running both, just pick
> one port (the System Audit page can autodetect either).

---

## First-time install (Mac)

### Step 1 · Install Python and `git` if you don't have them

Open the **Terminal** app (Spotlight → "Terminal"). Then:

```bash
# Install Homebrew (Mac's package manager) — skip if you already have it.
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

# Install Python 3.11 and git via Homebrew.
brew install python@3.11 git
```

### Step 2 · Clone this repo

```bash
# Pick a folder to put it in. The default is ~/TradingWorkspace.
mkdir -p ~/TradingWorkspace && cd ~/TradingWorkspace

# Clone via GitHub (replace the URL with your fork if you have one).
git clone https://github.com/<your-username>/trading-engine.git
cd trading-engine
```

### Step 3 · Set up a Python virtual environment

```bash
# Create a venv at ~/TradingWorkspace/.venv
python3 -m venv ../.venv

# Activate it. You'll need to run this every time you open a new Terminal.
source ../.venv/bin/activate

# You should now see "(.venv)" at the start of your Terminal prompt.

# Install the project's dependencies.
pip install --upgrade pip
pip install -r requirements.txt    # OR: pip install -e .  (if pyproject.toml is set up)
```

### Step 4 · Set up TWS or IB Gateway

Open whichever you installed and **log into your paper account**. Then:

**TWS:** File → Global Configuration → API → Settings:
  - ☑ Enable ActiveX and Socket Clients
  - Socket port: `7497` (paper) or `7496` (live)
  - ☐ Read-Only API _(must be unchecked, otherwise we can't submit)_
  - Trusted IPs: leave empty, or add `127.0.0.1`
  - Click OK
  - **File → Save Settings** (the API socket only opens after saving)

**IB Gateway:** Configure → Settings → API → Settings — same options;
ports are `4002` (paper) and `4001` (live).

Point the engine at it. Copy `.env.example` (repo root) to `.env` and set
`IBKR_PORT_PAPER` to 4002 (IB Gateway) or 7497 (TWS). Then run the
connection doctor, which checks the socket, API handshake, account type,
balances, contracts, daily bars, live vs. delayed quotes and order
permissions, and prints the fix for anything that fails:

```bash
python -m src.broker.ibkr.doctor
```

Every IBKR connection in the repo reads its host, port and clientId from
`src/broker/ibkr/settings.py` (env > `.env` > `config/broker.yaml`).

### Step 5 · Bootstrap the autonomous schedule

From the repo root, `bin/tradeit schedule install` installs a macOS
LaunchAgent that runs the daily runner for this checkout Mon-Fri at 15:35
ET, written in this Mac's zone; `bin/tradeit schedule status` shows whether it is loaded and
where it runs from, and `bin/tradeit schedule remove` takes it out.

Or run **all of the above + sleep settings + auto-login checklist** with
the bundled bootstrap script:

```bash
bash scripts/setup_automation.sh
```

### Step 6 · Open the dashboard

```bash
streamlit run src/dashboard/streamlit_entrypoint.py --server.port 8501
```

Browser opens to `http://localhost:8501`. Click around. The first thing
to verify is the **System Audit** page (left sidebar) — every row should
be green or yellow. Red rows tell you exactly what to fix.

---

## Daily operation

Once installed, the system runs itself:

| What | Where | When |
|---|---|---|
| Pull bars + rebalance + submit orders | LaunchAgent | Mon-Fri 15:35 ET |
| Watch what's happening | Dashboard at `localhost:8501` | Whenever |
| Force a refresh manually | Sidebar **Refresh ETF Data** button | Anytime |
| Halt all trading | `bin/tradeit kill "reason"` (arms the kill switch, then cancels every working order) | Emergency |
| Resume trading | `bin/tradeit resume` (only after the doctor and a reconcile pass) | After fix |
| Where things stand | `bin/tradeit status` (the switch, IBKR's working orders, the last reconcile) | Anytime |

If you want to manually fire the runner without waiting for the LaunchAgent:

```bash
# Pull data + regenerate targets + (if rebalance day) submit orders.
python -m src.production.daily_runner --port 7497

# Like above but skip order submission.
python -m src.production.daily_runner --port 7497 --dry-run

# Force a rebalance even if not month-end.
python -m src.production.daily_runner --port 7497 --force-rebalance
```

---

## What's in the box

```
trading-engine/
├── README.md                  ← you are here
├── scripts/                   ← one-shot setup scripts and operator docs
├── src/
│   ├── common/                ← shared utilities (clock, IDs, bundles)
│   ├── data/                  ← data ingest, cleaning, validators, features
│   ├── strategies/            ← signal architecture (etf, futures, options)
│   ├── trading_engine/        ← Polars-based registry pipeline (model/agg/opt)
│   ├── portfolio/             ← portfolio construction + risk constraints
│   ├── execution/             ← order routing + kill switch
│   ├── runtime/               ← scheduling, health checks, risk gates
│   ├── broker/                ← IBKR API wrappers
│   ├── storage/               ← persistence repos (DB-backed, future)
│   ├── backtest/              ← backtester wiring (future); the backtester is C++, in engine/
│   ├── cli/                   ← command-line entry points
│   ├── production/            ← daily runner, generate_targets
│   └── dashboard/             ← Streamlit UI
├── data/
│   ├── market/cleaned/        ← master price file + monthly target weights
│   ├── broker/                ← positions, account, fills, orders snapshots
│   └── logs/runtime/          ← daily_runner_*.log files
└── artifacts/runs/            ← per-run JSON logs + kill-switch state
```

Every folder under `src/` has its own README explaining what's in it
and how to extend it. Start there if you want to add a new model or a
new connector.

---

## How the multi-model architecture works

```
┌────────────────────────────────────────────────────┐
│  raw OHLCV bars (data/market/cleaned/prices/…)     │
└────────────┬───────────────────────────────────────┘
             │
             ▼
┌────────────────────────────────────────────────────┐
│  model_state — features (momentum_10, NATR_14, …)  │  ← src/trading_engine/model_state
└────────────┬───────────────────────────────────────┘
             │
             ▼
┌────────────────────────────────────────────────────┐
│  models — 14 factory types × N parameterizations   │  ← src/trading_engine/models
│  (momentum / AMMA / MACD / RSI / Bollinger / …)    │
└────────────┬───────────────────────────────────────┘
             │ per-model weight streams
             ▼
┌────────────────────────────────────────────────────┐
│  aggregators — combine model weights → portfolio   │  ← src/trading_engine/aggregators
│  (equal_weight, MVO, manual_weight, min_avg_dd)    │
└────────────┬───────────────────────────────────────┘
             │
             ▼
┌────────────────────────────────────────────────────┐
│  optimizers (optional) — risk-aware refinement     │  ← src/trading_engine/optimizers
│  using a covariance model from risk/               │
└────────────┬───────────────────────────────────────┘
             │
             ▼
┌────────────────────────────────────────────────────┐
│  basket build → reconciliation → IBKR orders       │  ← src/production
│  (with pre-trade risk gate + kill switch)          │
└────────────────────────────────────────────────────┘
```

You can swap any layer independently. Adding a new model = drop a file in
`src/trading_engine/models/catalogue/` + register it in `models/registry.py`.
That's it — the rest of the pipeline picks it up.

---

## Universe (today)

```
Equities         SPY  QQQ  VEA  VWO  INDA  VFH  VHT  VIS  VNQ  VPU
Commodities      GLD  SLV  USO  UNG
Fixed Income     TLT  IEI  SHY  BIL
Crypto           IBIT  ETHA
Volatility       VIXY
```

21 ETFs, listed in `config/universes/etfs.yaml` (`core`); target generation
trades only that list. TQQQ left the model on October 12, 2026: it is a 3x ETF,
and the fund doesn't use leverage. The price master still keeps its bars.

---

## Going beyond ETFs

The same `Signal` protocol used in `src/strategies/etf/` exists for
`src/strategies/futures/` and `src/strategies/options/` — those folders are
structured but currently empty. When you're ready to add an options
overlay or a futures hedge, you implement the same kind of signal class,
register it in the relevant catalogue, and hook it into a new aggregator.
The runtime, dashboard, risk gate, and kill switch don't change.

---

## Safety — what stops bad trades

Three layers of defense before any order leaves your laptop:

1. **Kill switch** (`src/execution/kill_switch.py`) — file-backed boolean.
   Armed by you (or by a future drawdown supervisor). Blocks every
   submission attempt with a clear error message.

2. **Pre-trade risk checks** (`src/runtime/risk_checks.py`) — runs on
   every basket:
   - data validators (no negative prices, no impossible moves, no stale
     symbols)
   - exposure limits (max single position 25%, gross ≤ 100%, net in
     [50%, 100%], min 5 holdings, max single trade 30% NAV)
   - basket non-empty + NAV > 0

3. **Duplicate-submission guard** (`src/production/runtime/order_service.py`)
   — basket fingerprint persisted to disk; a second submit of the same
   basket fingerprint is refused.

Failing any of the three aborts before connecting to IBKR. The error
message tells you which limit fired and by how much.

---

## Common questions

**Will this trade on a holiday?**
No. The runner uses a NYSE calendar (`src/common/clock.py`); it skips
holidays and weekends.

**Can I run this on Linux?**
Yes — except the macOS-specific LaunchAgent. Use `cron` or `systemd`
instead. The `daily_runner` itself is platform-agnostic.

**Do I need to keep the laptop awake at 15:35 ET?**
Yes, unless you set up the truly-headless variant in
`scripts/HEADLESS.md` (LaunchDaemon + IBC). The simpler path: enable
auto-login + a wake schedule five minutes before the run, in the Mac's
own time (`scripts/setup_automation.sh` offers it; in Central time that is
`pmset repeat wakeorpoweron MTWRF 14:30:00`).

**Will it sell my whole portfolio?**
No — by default the optimizer is `momentum_tilted` which holds all 22
ETFs with weights tilted toward winners (top weights ~10-12%, all
holdings ≥ 1%). The legacy `momentum_top5` still exists if you want a
concentrated 5-pick portfolio.

**Do I need a MOSEK license?**
No. The daily runner's target generation (`src/production/generate_targets.py`)
uses no solver. The research optimizers and the MVO aggregator in
`src/trading_engine/` get their cvxpy solver from `src/trading_engine/solvers.py`:
MOSEK only when the `mosek` package is installed and a license checks out,
otherwise CLARABEL, which comes with cvxpy. If you have a MOSEK license, keep
it outside the repo: save it as `~/mosek/mosek.lic`, or point
`MOSEKLM_LICENSE_FILE` at it (`*.lic` files are git-ignored). Set
`DV02_SOLVER=CLARABEL` (or any installed cvxpy solver) to force one. The MIQP
optimizer needs a mixed-integer solver (licensed MOSEK, or SCIP via
`pip install pyscipopt`); without one it warns and solves without its minimum
trade size rule.

**How do I switch from paper to live?**
You don't from this README. There's a deliberate friction step: the
`profile=live` code path requires you to flip a flag, set
`IBKR_PORT_LIVE`, and disarm the live-trading guard. Don't do it until
you've watched the paper system run for at least a month and the System
Audit page is consistently green.

---

## License

Internal use only. Not licensed for redistribution.
