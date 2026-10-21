from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pandas as pd

try:
    from ib_async import IB, ContFuture
except ImportError:
    from ib_insync import IB, ContFuture  # type: ignore


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
MASTER_CSV = PROJECT_ROOT / "data/market/cleaned/prices/futures_prices_master.csv"
MASTER_PARQUET = PROJECT_ROOT / "data/market/cleaned/prices/futures_prices_master.parquet"
SNAPSHOTS_DIR = PROJECT_ROOT / "data/market/snapshots"

MASTER_COLUMNS = [
    "date", "symbol",
    "open", "high", "low", "close",
    "settlement", "bid", "ask", "volume", "open_interest",
]

# Map our internal ticker → IBKR continuous-future contract spec: (IBKR symbol,
# exchange, currency). IBKR's names differ from other vendors': ICE Brent is COIL
# on IPE, and the grains qualify on CBOT, not ECBOT. On 2026-10-21 each of these
# qualified on the paper gateway and returned daily bars. Tickers below mirror
# the universe in config/universes/futures.yaml `tracked:` list.
FUTURES_CONTRACTS: dict[str, tuple[str, str, str]] = {
    "BRN": ("COIL", "IPE", "USD"),
    "ZC":  ("ZC",  "CBOT",  "USD"),
    "ZW":  ("ZW",  "CBOT",  "USD"),
    "ZS":  ("ZS",  "CBOT",  "USD"),
    "ZO":  ("ZO",  "CBOT",  "USD"),
    "QG":  ("QG",  "NYMEX", "USD"),
    "QO":  ("QO",  "COMEX", "USD"),
    "QI":  ("QI",  "COMEX", "USD"),
    "QC":  ("QC",  "COMEX", "USD"),
    "RTY": ("RTY", "CME",   "USD"),
    # QM intentionally absent — see note in config/universes/futures.yaml.
}


def _load_master() -> pd.DataFrame:
    if MASTER_PARQUET.exists():
        df = pd.read_parquet(MASTER_PARQUET)
    elif MASTER_CSV.exists():
        df = pd.read_csv(MASTER_CSV)
    else:
        return pd.DataFrame(columns=MASTER_COLUMNS)

    df.columns = [c.lower().strip() for c in df.columns]
    for col in MASTER_COLUMNS:
        if col not in df.columns:
            df[col] = pd.NA

    df["date"] = pd.to_datetime(df["date"]).dt.normalize()
    df["symbol"] = df["symbol"].astype(str).str.upper().str.strip()
    return df[MASTER_COLUMNS].copy()


def _fetch_symbol_bars(ib: IB, symbol: str, lookback: str) -> pd.DataFrame:
    spec = FUTURES_CONTRACTS.get(symbol)
    if spec is None:
        raise ValueError(f"No IBKR futures contract mapping for {symbol}; add to FUTURES_CONTRACTS.")

    ib_symbol, exchange, currency = spec
    # by name: ContFuture's third positional argument is localSymbol, and a currency
    # there ("USD") matches no contract
    contract = ContFuture(ib_symbol, exchange, currency=currency)
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
                # IBKR historicalData doesn't return settlement/bid/ask/OI in
                # the TRADES window; leave them NA so downstream joins are clean.
                "settlement": pd.NA,
                "bid": pd.NA,
                "ask": pd.NA,
                "volume": float(bar.volume) if bar.volume is not None else 0.0,
                "open_interest": pd.NA,
            }
        )
    return pd.DataFrame(rows, columns=MASTER_COLUMNS)


def append_futures_daily(
    profile: str = "paper",
    host: str = "127.0.0.1",
    port: int = 4002,
    client_id: int = 105,
    lookback: str = "15 D",
    through: date | None = None,
) -> dict[str, Any]:
    """Append the tracked futures' daily bars to the master, through ``through``
    (default the last completed NYSE session). A session still trading has a
    partial bar, whose close is the last trade so far, so its bars are left out,
    and a master row of one (from before this rule) is dropped: the next run
    fetches the finished bar."""
    SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)

    master = _load_master()
    symbols = sorted(set(FUTURES_CONTRACTS.keys()) | set(master["symbol"].unique().tolist()))

    from src.broker.ibkr.client import connect_ib, fail_fast
    from src.common.clock import last_completed_session
    from src.production.pipeline.append_ibkr_daily import HISTORY_TIMEOUT_S, completed_sessions_only

    through = through or last_completed_session()

    try:
        # fails on a gateway cut off from IBKR; caps each bars request (src/broker/ibkr/client.py)
        ib = connect_ib(host, port, client_id, request_timeout=HISTORY_TIMEOUT_S, ib=IB())
    except Exception as exc:
        return {
            "ok": False,
            "profile": profile,
            "action": "append_futures_daily",
            "error": f"IBKR connection failed at {host}:{port} client_id={client_id}: {exc}",
            "csv_path": str(MASTER_CSV),
            "parquet_path": str(MASTER_PARQUET),
        }

    fetched_frames: list[pd.DataFrame] = []
    errors: list[str] = []
    unfinished: list[str] = []
    fetch = fail_fast(
        completed_sessions_only(
            lambda sym, duration: _fetch_symbol_bars(ib, sym, duration), through, unfinished
        ),
        HISTORY_TIMEOUT_S,
    )

    try:
        for symbol in symbols:
            try:
                df_symbol = fetch(symbol, lookback)
                if not df_symbol.empty:
                    fetched_frames.append(df_symbol)
                else:
                    errors.append(f"{symbol}: no data returned")
            except Exception as exc:
                errors.append(f"{symbol}: {exc}")
    finally:
        ib.disconnect()

    fetched = pd.concat(fetched_frames, ignore_index=True) if fetched_frames else pd.DataFrame(columns=MASTER_COLUMNS)

    master = master[master["date"] <= pd.Timestamp(through)]  # a session that wasn't over yet
    before_len = len(master)
    combined = pd.concat([master, fetched], ignore_index=True)
    combined["date"] = pd.to_datetime(combined["date"]).dt.normalize()
    combined["symbol"] = combined["symbol"].astype(str).str.upper().str.strip()
    # keep="last" so a fresh IBKR row replaces the Barchart row for the same (symbol, date).
    combined = combined.drop_duplicates(subset=["symbol", "date"], keep="last")
    combined = combined.sort_values(["symbol", "date"]).reset_index(drop=True)
    after_len = len(combined)

    MASTER_CSV.parent.mkdir(parents=True, exist_ok=True)
    combined.to_csv(MASTER_CSV, index=False)
    combined.to_parquet(MASTER_PARQUET, index=False)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    snapshot_path = SNAPSHOTS_DIR / f"ibkr_futures_daily_append_{ts}.csv"
    fetched.to_csv(snapshot_path, index=False)

    result = AppendResult(
        ok=True,
        profile=profile,
        action="append_futures_daily",
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
