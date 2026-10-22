from __future__ import annotations

import logging
import math
import os
import shutil
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable, Iterable

import pandas as pd

try:
    from ib_async import IB, Stock
except ImportError:
    from ib_insync import IB, Stock  # type: ignore

from src.broker.ibkr.client import connect_ib, fail_fast
from src.common.clock import last_completed_session
from src.data.cleaning.price_basis import (
    COLUMNS,
    MergeReport,
    merge_into_master,
    normalize_bars,
    repair_master_basis,
)

log = logging.getLogger(__name__)


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
    # Price-basis check (see src/data/cleaning/price_basis.py)
    refetched: list[str] = field(default_factory=list)
    refused: list[dict[str, str]] = field(default_factory=list)
    basis: dict[str, dict[str, Any]] = field(default_factory=dict)
    lookbacks: dict[str, str] = field(default_factory=dict)
    backup_path: str | None = None
    error: str | None = None
    # the last session merged; bars of a session not yet over are dropped
    completed_through: str | None = None
    partial_bars_dropped: int = 0


PROJECT_ROOT = Path(__file__).resolve().parents[3]
MASTER_CSV = PROJECT_ROOT / "data/market/cleaned/prices/etf_prices_master.csv"
MASTER_PARQUET = PROJECT_ROOT / "data/market/cleaned/prices/etf_prices_master.parquet"
SNAPSHOTS_DIR = PROJECT_ROOT / "data/market/snapshots"

# The IBKR bar type the master is built from. TRADES daily bars come back
# adjusted for splits (not dividends) as of the day they are requested, which
# is the committed master's basis (SPY 2020-03-23 = 222.95, the unadjusted
# close; UNG and USO rescaled for their reverse splits). The daily fetch and
# the full-history refetch use the same type, so a refetch is on the same
# basis as everything appended after it.
BAR_TYPE = "TRADES"

# Durations tried, longest first, when a symbol's whole history is refetched
# after a basis change. 40 years covers the oldest series in the master (SPY
# from 1993); IBKR returns what it has. Shorter fallbacks keep the refetch
# working if IBKR refuses a long one; rows older than what comes back are
# dropped (the master is copied aside first).
FULL_HISTORY_DURATIONS: tuple[str, ...] = ("40 Y", "30 Y", "20 Y", "10 Y")

# Calendar days fetched before a symbol's newest row, so new bars always
# overlap the master (about a week of trading days) for the basis check.
OVERLAP_MARGIN_DAYS = 10

# (symbol, duration) -> bars
Fetcher = Callable[[str, str], pd.DataFrame]


def _load_master() -> pd.DataFrame:
    if MASTER_PARQUET.exists():
        df = pd.read_parquet(MASTER_PARQUET)
    elif MASTER_CSV.exists():
        df = pd.read_csv(MASTER_CSV)
    else:
        raise FileNotFoundError(f"Missing master file: {MASTER_CSV}")

    df.columns = [c.lower().strip() for c in df.columns]
    required = ["date", "symbol", "open", "high", "low", "close", "volume"]
    for col in required:
        if col not in df.columns:
            raise ValueError(f"Missing required column: {col}")

    df["date"] = pd.to_datetime(df["date"]).dt.normalize()
    df["symbol"] = df["symbol"].astype(str).str.upper().str.strip()
    return df[required].copy()


def _write_master(df: pd.DataFrame) -> None:
    """Write CSV and parquet, each through a temp file so a crash never leaves half a file."""
    for path, write in ((MASTER_CSV, df.to_csv), (MASTER_PARQUET, df.to_parquet)):
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        write(tmp, index=False)
        os.replace(tmp, path)


def _backup_master(tag: str) -> Path | None:
    """Copy the current master aside before a symbol's history is replaced."""
    src = MASTER_PARQUET if MASTER_PARQUET.exists() else MASTER_CSV
    if not src.exists():
        return None
    SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = SNAPSHOTS_DIR / f"etf_prices_master_before_{tag}_{ts}{src.suffix}"
    shutil.copy2(src, dest)
    return dest


def _fetch_symbol_bars(ib: IB, symbol: str, lookback: str) -> pd.DataFrame:
    contract = Stock(symbol, "SMART", "USD")
    ib.qualifyContracts(contract)

    bars = ib.reqHistoricalData(
        contract,
        endDateTime="",
        durationStr=lookback,
        barSizeSetting="1 day",
        whatToShow=BAR_TYPE,
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


_UNIT_DAYS = {"S": 1 / 86400, "D": 1, "W": 7, "M": 31, "Y": 366}


def _duration_days(duration: str) -> float | None:
    try:
        n, unit = str(duration).split()
        return float(n) * _UNIT_DAYS[unit.upper()]
    except (ValueError, KeyError):
        return None


def lookback_for(last_date: pd.Timestamp | None, lookback: str, today: pd.Timestamp) -> str:
    """``lookback``, stretched so the fetch reaches OVERLAP_MARGIN_DAYS before ``last_date``.

    New bars must overlap a symbol's stored rows, or their price basis
    can't be checked. A symbol that fell behind (an outage, a hole at the
    end) gets a longer request than the caller's default.
    """
    base = _duration_days(lookback)
    if last_date is None or pd.isna(last_date) or base is None:
        return lookback
    need = (pd.Timestamp(today).normalize() - pd.Timestamp(last_date).normalize()).days + OVERLAP_MARGIN_DAYS
    if need <= base:
        return lookback
    return f"{need} D" if need <= 365 else f"{math.ceil(need / 365)} Y"


def completed_sessions_only(fetch: Fetcher, through: date, dropped: list[str]) -> Fetcher:
    """``fetch`` without bars dated after ``through``, each dropped one's symbol added to ``dropped``.

    IBKR's daily bar for a session that is still trading is partial: its close
    is the last trade so far. Kept, it would become the targets' close.
    """
    cut = pd.Timestamp(through)

    def fetch_completed(symbol: str, duration: str) -> pd.DataFrame:
        bars = fetch(symbol, duration)
        if bars is None or bars.empty:
            return bars
        later = pd.to_datetime(bars["date"]).dt.normalize() > cut
        dropped.extend([symbol] * int(later.sum()))
        return bars[~later]

    return fetch_completed


def fetch_full_history(fetch: Fetcher, symbol: str) -> pd.DataFrame:
    """The symbol's longest available history, trying FULL_HISTORY_DURATIONS in order."""
    failures: list[str] = []
    for duration in FULL_HISTORY_DURATIONS:
        try:
            bars = fetch(symbol, duration)
        except Exception as exc:  # noqa: BLE001
            failures.append(f"{duration}: {type(exc).__name__}: {exc}")
            continue
        if bars is not None and len(bars):
            log.info("%s: refetched %d daily bars (%s, %s)", symbol, len(bars), duration, BAR_TYPE)
            return bars
        failures.append(f"{duration}: no bars")
    raise RuntimeError("IBKR returned no history (" + "; ".join(failures) + ")")


def _log_report(report: MergeReport) -> None:
    for sym in report.refetched:
        r = report.results[sym]
        dropped = f"; {r.rows_dropped} older rows dropped" if r.rows_dropped else ""
        log.warning(
            "PRICE BASIS: %s changed basis (%s). Replaced its whole history with a fresh "
            "IBKR %s history from %s (%d rows%s).",
            sym, r.reason, BAR_TYPE, r.first_date, len(r.rows), dropped,
        )
    for item in report.refused:
        log.error("PRICE BASIS: merge REFUSED for %s: %s", item["symbol"], item["reason"])


def _refused_error(refused: list[dict[str, str]], verb: str) -> str:
    names = ", ".join(r["symbol"] for r in refused)
    detail = " | ".join(f"{r['symbol']}: {r['reason']}" for r in refused)
    return (
        f"Price basis mismatch: {verb} for {names}. Their old rows are kept and no new bars "
        "were merged for them, so their prices are stale and on an outdated basis; do not "
        "trade on them. With IB Gateway up, run: bin/tradeit refresh "
        f"(or python -m src.production.backfill_gaps --repair-basis --symbols {' '.join(r['symbol'] for r in refused)}). "
        f"Details: {detail}"
    )


def append_bars(
    fetch: Fetcher,
    *,
    profile: str = "paper",
    lookback: str = "15 D",
    master: pd.DataFrame | None = None,
    today: datetime | pd.Timestamp | None = None,
    through: date | None = None,
) -> dict[str, Any]:
    """Fetch every master symbol with ``fetch`` and merge with a price-basis check.

    IBKR-agnostic core of :func:`append_ibkr_daily`. ``fetch(symbol,
    duration)`` returns daily bars; it also serves full-history refetches
    when a symbol's basis changed. ``ok`` is False when any symbol's merge
    was refused (the master is still written for every other symbol).
    Only bars up to ``through`` are merged, by default the last session whose
    bar is final (src/common/clock.py): a run before the close keeps
    yesterday's close as the latest.
    """
    through = through or last_completed_session()
    partial: list[str] = []
    fetch = completed_sessions_only(fetch, through, partial)
    SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)
    master = _load_master() if master is None else normalize_bars(master)
    symbols = sorted(master["symbol"].unique().tolist())
    today_ts = pd.Timestamp(today or datetime.now()).normalize()
    last_dates = master.groupby("symbol")["date"].max()

    fetched_frames: list[pd.DataFrame] = []
    errors: list[str] = []
    lookbacks: dict[str, str] = {}

    for symbol in symbols:
        lb = lookback_for(last_dates.get(symbol), lookback, today_ts)
        if lb != lookback:
            lookbacks[symbol] = lb
        try:
            df_symbol = fetch(symbol, lb)
            if df_symbol is not None and not df_symbol.empty:
                fetched_frames.append(normalize_bars(df_symbol, symbol))
            else:
                errors.append(f"{symbol}: no data returned")
        except Exception as exc:
            errors.append(f"{symbol}: {exc}")

    fetched = pd.concat(fetched_frames, ignore_index=True) if fetched_frames else pd.DataFrame(columns=COLUMNS)

    before_len = len(master)
    report = merge_into_master(master, fetched, refetch=lambda s: fetch_full_history(fetch, s))
    _log_report(report)
    if partial:
        log.info(
            "Dropped %d bars dated after %s, the last completed session (%s): their session isn't over.",
            len(partial), through, ", ".join(sorted(set(partial))),
        )
    backup = _backup_master("refetch") if report.refetched else None
    combined = report.master
    after_len = len(combined)

    _write_master(combined)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    snapshot_path = SNAPSHOTS_DIR / f"ibkr_daily_append_{ts}.csv"
    fetched.to_csv(snapshot_path, index=False)

    refused = report.refused
    error = _refused_error(refused, "merge refused") if refused else None
    errors.extend(f"{r['symbol']}: basis mismatch, merge refused: {r['reason']}" for r in refused)

    result = AppendResult(
        ok=not refused,
        profile=profile,
        action="append_ibkr_daily",
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
        refetched=report.refetched,
        refused=refused,
        basis=report.basis(),
        lookbacks=lookbacks,
        backup_path=str(backup) if backup else None,
        error=error,
        completed_through=through.isoformat(),
        partial_bars_dropped=len(partial),
    )
    return asdict(result)


def repair_bars(
    fetch: Fetcher,
    *,
    symbols: Iterable[str] | None = None,
    window: str = "1 Y",
    master: pd.DataFrame | None = None,
) -> dict[str, Any]:
    """Find symbols whose stored history mixes price bases and refetch them whole.

    IBKR-agnostic core of :func:`repair_ibkr_basis`. Flags a symbol when its
    rows contain a split-ratio jump or a one-day spike that reverts, or when
    IBKR's bars over ``window`` disagree with the stored rows. The master is
    rewritten only when a history was replaced (after a backup copy). Bars
    of a session not yet over are left out, as in :func:`append_bars`.
    """
    fetch = completed_sessions_only(fetch, last_completed_session(), [])
    master = _load_master() if master is None else normalize_bars(master)
    report = repair_master_basis(
        master,
        refetch=lambda s: fetch_full_history(fetch, s),
        recent=lambda s: fetch(s, window),
        symbols=symbols,
    )
    _log_report(report)
    unchecked = [{"symbol": s, "reason": report.results[s].reason} for s in report.symbols("unchecked")]
    for item in unchecked:
        log.warning("PRICE BASIS: %s not compared with IBKR: %s", item["symbol"], item["reason"])

    backup = None
    if report.refetched:
        backup = _backup_master("repair")
        _write_master(report.master)

    refused = report.refused
    return {
        "ok": not refused,
        "action": "repair_basis",
        "symbols_checked": sorted(report.results),
        "refetched": report.refetched,
        "refused": refused,
        "unchecked": unchecked,
        "basis": report.basis(),
        "backup_path": str(backup) if backup else None,
        "csv_path": str(MASTER_CSV),
        "parquet_path": str(MASTER_PARQUET),
        "error": _refused_error(refused, "repair failed") if refused else None,
    }


# One bars request: a whole-history refetch takes seconds, so this only ends a hang.
HISTORY_TIMEOUT_S = 120.0


def _connect(host: str, port: int, client_id: int) -> IB:
    """Connected, or IBKRSyncError when the gateway is cut off from IBKR (see
    src/broker/ibkr/client.py), with every bars request capped at HISTORY_TIMEOUT_S."""
    return connect_ib(host, port, client_id, request_timeout=HISTORY_TIMEOUT_S, ib=IB())


def _fetcher(ib: IB):
    """The bars fetch for one connection; after a request times out, the rest fail at once."""
    return fail_fast(lambda sym, dur: _fetch_symbol_bars(ib, sym, dur), HISTORY_TIMEOUT_S)


def _connection_error(profile: str, action: str, host: str, port: int, client_id: int, exc: Exception) -> dict[str, Any]:
    return {
        "ok": False,
        "profile": profile,
        "action": action,
        "error": f"IBKR connection failed at {host}:{port} client_id={client_id}: {exc}",
        "csv_path": str(MASTER_CSV),
        "parquet_path": str(MASTER_PARQUET),
    }


def append_ibkr_daily(
    profile: str = "paper",
    host: str = "127.0.0.1",
    port: int = 4002,
    client_id: int = 101,
    lookback: str = "15 D",
) -> dict[str, Any]:
    master = _load_master()
    try:
        ib = _connect(host, port, client_id)
    except Exception as exc:
        return _connection_error(profile, "append_ibkr_daily", host, port, client_id, exc)
    try:
        # The connection stays open through the merge: a basis mismatch refetches.
        return append_bars(
            _fetcher(ib),
            profile=profile,
            lookback=lookback,
            master=master,
        )
    finally:
        ib.disconnect()


def repair_ibkr_basis(
    profile: str = "paper",
    host: str = "127.0.0.1",
    port: int = 4002,
    client_id: int = 101,
    symbols: Iterable[str] | None = None,
    window: str = "1 Y",
) -> dict[str, Any]:
    master = _load_master()
    try:
        ib = _connect(host, port, client_id)
    except Exception as exc:
        return _connection_error(profile, "repair_basis", host, port, client_id, exc)
    try:
        result = repair_bars(
            _fetcher(ib),
            symbols=symbols,
            window=window,
            master=master,
        )
    finally:
        ib.disconnect()
    result["profile"] = profile
    return result
