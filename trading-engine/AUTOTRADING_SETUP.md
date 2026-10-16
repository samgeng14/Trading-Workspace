# Auto-Trading Setup Guide

> Paper portfolio, ~$1M NAV, ETF momentum strategy (AMMA)
> Data gap: March 13 → April 15 2026 needs to be filled first.

---

## Quick-start: what to run right now

All commands from the trading-engine directory:
```
cd /Users/tradingworkspace/TradingWorkspace/trading-engine
```

### Step 1 — Fill the data gap (ONE TIME)
IBKR must be running on port 4002 (paper).

```bash
python -m src.production.backfill_gaps --lookback "45 D"
```

This fetches ~45 days of daily bars for all 22 ETFs and appends them to the master price file.

### Step 2 — Regenerate targets
```bash
python -m src.production.generate_targets
```

This will now detect the March 31 end-of-month rebalance and possibly April's signal.

### Step 3 — Run the dashboard
```bash
streamlit run src/dashboard/streamlit_entrypoint.py --server.port 8501
```

Then open http://localhost:8501

---

## Daily auto-trading (AMMA runs automatically)

### Option A: macOS LaunchAgent (recommended — persists after reboots)

Install once, from the repo root:
```bash
bin/tradeit schedule install
```

This creates a LaunchAgent that runs the full pipeline for this checkout **every weekday at 3:35 PM ET** (2:35 PM Central), before the closing auction, so its orders fill at that day's close.

To check it's running:
```bash
bin/tradeit schedule status
```

To uninstall:
```bash
bin/tradeit schedule remove
```

### Option B: Manual trigger

```bash
# Dry run (no orders submitted)
python -m src.production.daily_runner --dry-run

# Full run with order submission
python -m src.production.daily_runner

# Force rebalance even if already done this month
python -m src.production.daily_runner --force-rebalance
```

---

## What happens on each daily run

```
3:35 PM ET (before the close; orders go market-on-close by 3:45 PM ET)
    │
    ├── 1. Append IBKR Daily Bars (lookback 5 D)
    │       → fetches bars through the last completed session (yesterday's close) for all 22 ETFs
    │       → appends to data/market/cleaned/prices/etf_prices_master.parquet
    │
    ├── 2. Generate Targets
    │       → runs 126-day momentum model on all ETFs
    │       → picks top-5 by momentum signal
    │       → writes data/market/cleaned/targets/etf_targets_monthly.parquet
    │
    ├── 3. Check if rebalance needed
    │       → if latest target date is BEFORE current month → YES
    │       → if already rebalanced this month → NO (skip orders)
    │
    └── 4. If rebalance needed:
            ├── Build paper basket (diff vs current IBKR positions)
            └── Submit paper orders to IBKR port 4002
```

Run logs are saved to: `artifacts/runs/daily_run_YYYYMMDD_HHMMSS.json`

---

## Strategy: AMMA (Adaptive Momentum)

The current model is a **momentum-based ETF rotation**:

- Universe: 21 ETFs (SPY, QQQ, GLD, SLV, TLT, IEI, SHY, BIL, IBIT, ETHA, USO, UNG, VEA, VWO, INDA, VFH, VHT, VIS, VNQ, VPU, VIXY), from `config/universes/etfs.yaml`. TQQQ (3x) left the model: the fund doesn't use leverage.
- Signal: 126-day (6-month) price momentum
- Selection: top-5 ETFs by momentum score
- Weight: equal weight (20% each)
- Rebalance: monthly (last trading day of each month)
- Cash buffer: 0.5% (kept as cash)

---

## Dashboard pages

| Page | Purpose |
|------|---------|
| **Home** | Ops status: broker connection, NAV, positions, data gap alert |
| **Run Pipeline** | Manual controls: backfill, append, targets, full pipeline |
| **Basket Review** | Preview what trades would be submitted |
| **Submit Orders** | Gone: orders go through the daily runner (`bin/tradeit`, or the DV02 dashboard's Submit), which runs every pre-trade check |
| **Orders & Fills** | View submitted orders and fills |
| **Positions** | Current IBKR positions |
| **Risk Monitor** | Pre-flight checks before submitting |
| **Portfolio Performance** | NAV curve, cash, P&L |
| **Backtest Charts** | ETF price history, momentum allocation history |

---

## Common issues

**"IBKR not reachable"**
→ Start IBG or TWS. Paper port = 4002. Make sure API connections are enabled.

**"Data gap: X trading days behind"**
→ Run `python -m src.production.backfill_gaps --lookback "45 D"` with IBKR running.

**"No targets found"**
→ Run `python -m src.production.daily_runner --skip-append --force-rebalance --dry-run` to regenerate.

**"Submit locked"**
→ Click "Unlock" in the sidebar, or run `python -m src.production.daily_runner --force-rebalance`.

**RuntimeError: Event loop is closed**
→ Harmless — happens on Streamlit shutdown. Not a problem.
