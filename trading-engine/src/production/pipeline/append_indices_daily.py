from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

try:
    from ib_async import IB, Index
except ImportError:
    from ib_insync import IB, Index  # type: ignore


@dataclass
class AppendResult:
    ok: bool
    profile: str
    action: str
    symbols_requested: int
    symbols_with_data: int
    new_rows_fetched: int
    new_rows_added_to_master: int
    latest_date: str | None
    snapshot_path: str | None
    csv_path: str
    parquet_path: str
    errors: list[str]
    preview: list[dict[str, Any]]


PROJECT_ROOT = Path(__file__).resolve().parents[3]
MASTER_CSV = PROJECT_ROOT / "data/market/cleaned/prices/indices_prices_master.csv"
MASTER_PARQUET = PROJECT_ROOT / "data/market/cleaned/prices/indices_prices_master.parquet"
SNAPSHOTS_DIR = PROJECT_ROOT / "data/market/snapshots"

# Map our internal ticker → IBKR Index contract spec (symbol, exchange, currency).
# IBKR sometimes uses different roots than Barchart (e.g. DXY → "DX" on NYBOT).
INDEX_CONTRACTS: dict[str, tuple[str, str, str]] = {
    "VIX": ("VIX", "CBOE", "USD"),
    "DXY": ("DX", "NYBOT", "USD"),
}


def _load_master() -> pd.DataFrame:
    cols = ["date", "symbol", "open", "high", "low", "close", "volume"]
    if MASTER_PARQUET.exists():
        df = pd.read_parquet(MASTER_PARQUET)
    elif MASTER_CSV.exists():
        df = pd.read_csv(MASTER_CSV)
    else:
        return pd.DataFrame(columns=cols)

    df.columns = [c.lower().strip() for c in df.columns]
    for col in cols:
        if col not in df.columns:
            df[col] = pd.NA

    df["date"] = pd.to_datetime(df["date"]).dt.normalize()
    df["symbol"] = df["symbol"].astype(str).str.upper().str.strip()
    return df[cols].copy()


def _fetch_symbol_bars(ib: IB, symbol: str, lookback: str) -> pd.DataFrame:
    spec = INDEX_CONTRACTS.get(symbol)
    if spec is None:
        raise ValueError(f"No IBKR index contract mapping for {symbol}; add to INDEX_CONTRACTS.")

    ib_symbol, exchange, currency = spec
    contract = Index(ib_symbol, exchange, currency)
    ib.qualifyContracts(contract)

    bars = ib.reqHistoricalData(
        contract,
        endDateTime="",
        durationStr=lookback,
        barSizeSetting="1 day",
        whatToShow="TRADES",
        useRTH=True,
        formatDate=1,
    )

    rows: list[dict[str, Any]] = []
    for bar in bars:
        rows.append(
            {
                "date": pd.to_datetime(bar.date).normalize(),
                "symbol": symbol,
                "open": float(bar.open),
                "high": float(bar.high),
                "low": float(bar.low),
                "close": float(bar.close),
                "volume": float(bar.volume) if bar.volume is not None else 0.0,
            }
        )
    return pd.DataFrame(rows)


def append_indices_daily(
    profile: str = "paper",
    host: str = "127.0.0.1",
    port: int = 4002,
    client_id: int = 103,
    lookback: str = "15 D",
) -> dict[str, Any]:
    SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)

    master = _load_master()
    # Always try every symbol we have a contract mapping for, even on a cold-start.
    symbols = sorted(set(INDEX_CONTRACTS.keys()) | set(master["symbol"].unique().tolist()))

    from src.broker.ibkr.client import connect_ib, fail_fast
    from src.production.pipeline.append_ibkr_daily import HISTORY_TIMEOUT_S

    try:
        # fails on a gateway cut off from IBKR; caps each bars request (src/broker/ibkr/client.py)
        ib = connect_ib(host, port, client_id, request_timeout=HISTORY_TIMEOUT_S, ib=IB())
    except Exception as exc:
        return {
            "ok": False,
            "profile": profile,
            "action": "append_indices_daily",
            "error": f"IBKR connection failed at {host}:{port} client_id={client_id}: {exc}",
            "csv_path": str(MASTER_CSV),
            "parquet_path": str(MASTER_PARQUET),
        }

    fetched_frames: list[pd.DataFrame] = []
    errors: list[str] = []
    fetch = fail_fast(lambda sym: _fetch_symbol_bars(ib, sym, lookback), HISTORY_TIMEOUT_S)

    try:
        for symbol in symbols:
            try:
                df_symbol = fetch(symbol)
                if not df_symbol.empty:
                    fetched_frames.append(df_symbol)
                else:
                    errors.append(f"{symbol}: no data returned")
            except Exception as exc:
                errors.append(f"{symbol}: {exc}")
    finally:
        ib.disconnect()

    fetched = pd.concat(fetched_frames, ignore_index=True) if fetched_frames else pd.DataFrame(
        columns=["date", "symbol", "open", "high", "low", "close", "volume"]
    )

    before_len = len(master)
    combined = pd.concat([master, fetched], ignore_index=True)
    combined["date"] = pd.to_datetime(combined["date"]).dt.normalize()
    combined["symbol"] = combined["symbol"].astype(str).str.upper().str.strip()
    combined = combined.drop_duplicates(subset=["symbol", "date"], keep="last")
    combined = combined.sort_values(["symbol", "date"]).reset_index(drop=True)
    after_len = len(combined)

    MASTER_CSV.parent.mkdir(parents=True, exist_ok=True)
    combined.to_csv(MASTER_CSV, index=False)
    combined.to_parquet(MASTER_PARQUET, index=False)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    snapshot_path = SNAPSHOTS_DIR / f"ibkr_indices_daily_append_{ts}.csv"
    fetched.to_csv(snapshot_path, index=False)

    result = AppendResult(
        ok=True,
        profile=profile,
        action="append_indices_daily",
        symbols_requested=len(symbols),
        symbols_with_data=int(fetched["symbol"].nunique()) if not fetched.empty else 0,
        new_rows_fetched=len(fetched),
        new_rows_added_to_master=after_len - before_len,
        latest_date=str(combined["date"].max().date()) if not combined.empty else None,
        snapshot_path=str(snapshot_path),
        csv_path=str(MASTER_CSV),
        parquet_path=str(MASTER_PARQUET),
        errors=errors,
        preview=combined.groupby("symbol", as_index=False).tail(1).sort_values("symbol").to_dict(orient="records"),
    )
    return asdict(result)
