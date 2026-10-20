"""Refresh the model's total-return closes: IBKR ADJUSTED_LAST bars, split- and
dividend-adjusted, one series per model ETF.

They go to data/market/cleaned/prices/etf_total_return_closes.parquet, which
target generation reads for momentum and volatility
(src/data/cleaning/total_return.py); the price master stays the source of
order prices. IBKR rescales a symbol's whole adjusted history at each
ex-dividend date, so every refresh replaces each symbol's series whole. A
symbol IBKR doesn't return keeps its previous series.

The daily runner refreshes it after its orders are sent (it is never on the
way to the market-on-close cutoff), and `bin/tradeit refresh` before it
builds targets.

    python -m src.production.pipeline.append_total_return             # refresh
    python -m src.production.pipeline.append_total_return --compare   # momentum, price vs total return
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import date
from pathlib import Path
from typing import Any, Callable, Iterable

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.data.cleaning.total_return import normalize_adjusted  # noqa: E402

log = logging.getLogger(__name__)

ADJUSTED_PARQUET = PROJECT_ROOT / "data/market/cleaned/prices/etf_total_return_closes.parquet"
BAR_TYPE = "ADJUSTED_LAST"
# Longest first; IBKR returns what it has. Ten years covers every lookback the
# targets use many times over.
DURATIONS: tuple[str, ...] = ("10 Y", "5 Y", "2 Y")

# symbol -> bars (date, close), or raises
AdjustedFetcher = Callable[[str, str], pd.DataFrame]


def load_adjusted(path: Path | None = None) -> pd.DataFrame:
    """The stored adjusted closes, or an empty frame before the first refresh."""
    path = path or ADJUSTED_PARQUET
    if not path.exists():
        return normalize_adjusted(None)
    return normalize_adjusted(pd.read_parquet(path))


def _write(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    df.to_parquet(tmp, index=False)
    os.replace(tmp, path)


def refresh_adjusted(
    fetch: AdjustedFetcher,
    symbols: Iterable[str],
    *,
    through: date,
    path: Path | None = None,
) -> dict[str, Any]:
    """Fetch every symbol's adjusted history with ``fetch`` and store it.

    Bars after ``through`` (a session not yet over) are dropped: a partial
    day's close would be compared with the master's final one. ``ok`` is
    False when any symbol failed; the others are stored either way.
    """
    path = path or ADJUSTED_PARQUET
    old = load_adjusted(path)
    fresh: list[pd.DataFrame] = []
    errors: dict[str, str] = {}
    cut = pd.Timestamp(through)
    for symbol in dict.fromkeys(s.strip().upper() for s in symbols):
        failures = []
        bars = None
        for duration in DURATIONS:
            try:
                got = fetch(symbol, duration)
            except Exception as exc:  # noqa: BLE001 — one symbol's failure doesn't stop the rest
                failures.append(f"{duration}: {type(exc).__name__}: {exc}")
                continue
            if got is not None and len(got):
                bars = got
                break
            failures.append(f"{duration}: no bars")
        if bars is None:
            errors[symbol] = "; ".join(failures)
            continue
        rows = normalize_adjusted(bars.assign(symbol=symbol))
        rows = rows[rows["date"] <= cut]
        if rows.empty:
            errors[symbol] = f"no completed session through {through}"
            continue
        fresh.append(rows)

    replaced = {str(s) for f in fresh for s in f["symbol"].unique()}
    kept = old[~old["symbol"].isin(replaced)]
    out = normalize_adjusted(pd.concat([kept, *fresh], ignore_index=True))
    _write(out, path)
    for symbol, why in errors.items():
        log.warning("Total-return closes: %s not refreshed (%s); its previous series is kept", symbol, why)
    return {
        "ok": not errors,
        "action": "total_return_closes",
        "refreshed": sorted(replaced),
        "errors": errors,
        "through": str(through),
        "path": str(path),
    }


def _ibkr_fetch(ib: Any) -> AdjustedFetcher:
    try:
        from ib_async import Stock
    except ImportError:  # pragma: no cover
        from ib_insync import Stock  # type: ignore

    from src.broker.ibkr.contracts import primary_exchange

    def fetch(symbol: str, duration: str) -> pd.DataFrame:
        contract = Stock(symbol, "SMART", "USD", primaryExchange=primary_exchange(symbol))
        ib.qualifyContracts(contract)
        # ADJUSTED_LAST takes no end date: IBKR adjusts up to now.
        bars = ib.reqHistoricalData(
            contract,
            endDateTime="",
            durationStr=duration,
            barSizeSetting="1 day",
            whatToShow=BAR_TYPE,
            useRTH=True,
            formatDate=1,
        )
        return pd.DataFrame(
            [{"date": pd.to_datetime(b.date).normalize(), "close": float(b.close)} for b in bars]
        )

    return fetch


def append_total_return(
    host: str = "127.0.0.1",
    port: int = 4002,
    client_id: int = 107,
    symbols: Iterable[str] | None = None,
) -> dict[str, Any]:
    """Refresh the model universe's adjusted closes from IBKR."""
    from src.broker.ibkr.client import fail_fast
    from src.common.clock import last_completed_session
    from src.production.generate_targets import model_universe
    from src.production.pipeline.append_ibkr_daily import HISTORY_TIMEOUT_S, _connect

    symbols = tuple(symbols or model_universe())
    try:
        ib = _connect(host, port, client_id)
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "action": "total_return_closes",
            "error": f"IBKR connection failed at {host}:{port} client_id={client_id}: {exc}",
        }
    try:
        return refresh_adjusted(
            fail_fast(_ibkr_fetch(ib), HISTORY_TIMEOUT_S), symbols, through=last_completed_session()
        )
    finally:
        ib.disconnect()


def compare(lookback: int | None = None) -> list[dict[str, Any]]:
    """Each model ETF's momentum on price and on total return, latest session."""
    from src.data.cleaning.total_return import signal_closes
    from src.production import generate_targets as gt

    lookback = lookback or gt.MOMENTUM_LOOKBACK_DEFAULT
    prices = gt._load_prices(gt.model_universe())
    tr = signal_closes(prices, load_adjusted())
    prices = prices.assign(tr_close=tr.closes)
    out = []
    for symbol, g in prices.groupby("symbol"):
        g = g.sort_values("date")
        if len(g) <= lookback:
            continue
        price_mom = g["close"].iloc[-1] / g["close"].iloc[-1 - lookback] - 1
        tr_mom = g["tr_close"].iloc[-1] / g["tr_close"].iloc[-1 - lookback] - 1
        out.append(
            {
                "symbol": symbol,
                "as_of": str(g["date"].iloc[-1].date()),
                "momentum_price": round(float(price_mom), 5),
                "momentum_total_return": round(float(tr_mom), 5),
                "income": round(float(tr_mom - price_mom), 5),
                "on_price": tr.on_price.get(symbol),
            }
        )
    return sorted(out, key=lambda r: -r["income"])


def main(argv: list[str] | None = None) -> int:
    from src.broker.ibkr.settings import client_id_for, resolve_endpoint

    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ep = resolve_endpoint("paper")
    parser.add_argument("--host", default=ep.host)
    parser.add_argument("--port", type=int, default=ep.port)
    parser.add_argument("--client-id", type=int, default=client_id_for("backfill", "paper"), dest="client_id")
    parser.add_argument("--compare", action="store_true", help="print momentum on price vs total return; no IBKR")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    if args.compare:
        rows = compare()
        print(f"{'ETF':6} {'price mom':>10} {'total-return mom':>17} {'income':>8}  as of")
        for r in rows:
            flag = f"  (on price: {r['on_price']})" if r["on_price"] else ""
            print(
                f"{r['symbol']:6} {r['momentum_price']:>10.2%} {r['momentum_total_return']:>17.2%} "
                f"{r['income']:>8.2%}  {r['as_of']}{flag}"
            )
        return 0
    result = append_total_return(host=args.host, port=args.port, client_id=args.client_id)
    print(json.dumps({k: v for k, v in result.items() if k != "path"}, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
