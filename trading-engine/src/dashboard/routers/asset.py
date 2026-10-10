"""
asset.py - Asset Analysis routes.

Sub-views:
  - GET  /asset/positions             -> live IBKR positions snapshot
  - POST /asset/positions/refresh     -> pull a fresh snapshot from IBKR
  - GET  /asset/composition           -> latest target weights + delta vs current
  - GET  /asset/composition/history   -> wide-format historical weights
  - GET  /asset/attribution           -> contributors / detractors / cumulative
  - GET  /asset/orders/calendar       -> daily buy/sell counts across all submissions
  - GET  /asset/orders/day            -> all orders for a given day
"""
from __future__ import annotations

import re
from datetime import date, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from fastapi import APIRouter, HTTPException, Query

from src.dashboard.routers._helpers import (
    df_to_records,
    get_profile,
    parse_iso_date,
)
from src.dashboard.utils import (
    get_active_optimizer,
    get_actual_portfolio_attribution,
    get_actual_portfolio_trades,
    get_historical_nav,
    get_historical_weights,
    get_latest_actual_portfolio_audit,
    get_latest_portfolio_attribution_audit,
    get_latest_production_audit,
    get_local_active_optimizer_for_attribution,
    get_portfolio_backtest_attribution,
    get_portfolio_backtest_trades,
    get_position_data_for_date,
)


router = APIRouter()
PROJECT_ROOT = Path(__file__).resolve().parents[3]


# ── Live positions ──────────────────────────────────────────────────────────

@router.get("/positions")
def positions(profile: str | None = None) -> dict[str, Any]:
    profile = profile or get_profile()
    positions_dir = PROJECT_ROOT / "data" / "broker" / "positions"
    account_dir = PROJECT_ROOT / "data" / "broker" / "account"

    snapshot_path = _latest_csv(positions_dir, f"{profile}_positions_snapshot*.csv")
    if snapshot_path is None:
        return {"available": False, "message": f"No {profile} positions snapshot found."}

    sev, ts_label = _staleness(snapshot_path)
    nav = _read_account_value(
        account_dir, profile, ["NetLiquidation", "EquityWithLoanValue"]
    )
    cash = _read_account_value(
        account_dir, profile, ["TotalCashValue", "AvailableFunds", "CashBalance"]
    )

    df = _load_positions(snapshot_path, nav)
    if df.empty:
        return {
            "available": True,
            "message": "No open positions found in the latest snapshot.",
            "snapshot": snapshot_path.name,
            "snapshot_severity": sev,
            "snapshot_timestamp": ts_label,
        }

    longs = df.loc[df["shares"] > 0]
    shorts = df.loc[df["shares"] < 0]

    summary = {
        "open_positions": int(len(df)),
        "long_market_value": float(longs["market_value"].sum()) if not longs.empty else 0.0,
        "short_market_value": float(shorts["market_value"].sum()) if not shorts.empty else 0.0,
        "net_market_value": float(df["market_value"].sum()),
        "gross_market_value": float(df["abs_market_value"].sum()),
        "unrealized_pnl_total": float(df["unrealized_pnl"].sum()),
        "nav": float(nav) if nav is not None else None,
        "cash": float(cash) if cash is not None else None,
    }

    rows = df[
        [
            "symbol",
            "shares",
            "avg_price",
            "last_price",
            "market_value",
            "unrealized_pnl",
            "realized_pnl",
            "weight_pct",
            "abs_market_value",
        ]
    ].copy()

    if cash is not None and cash != 0:
        cash_row = pd.DataFrame(
            [
                {
                    "symbol": "USD CASH",
                    "shares": 0.0,
                    "avg_price": 1.0,
                    "last_price": 1.0,
                    "market_value": float(cash),
                    "unrealized_pnl": 0.0,
                    "realized_pnl": 0.0,
                    "weight_pct": (100.0 * cash / nav) if nav else 0.0,
                    "abs_market_value": abs(float(cash)),
                }
            ]
        )
        rows = pd.concat([rows, cash_row], ignore_index=True)

    winners = (
        df.sort_values("unrealized_pnl", ascending=False)[
            ["symbol", "unrealized_pnl"]
        ].head(5)
    )
    losers = (
        df.sort_values("unrealized_pnl", ascending=True)[
            ["symbol", "unrealized_pnl"]
        ].head(5)
    )
    pnl_chart = df[["symbol", "unrealized_pnl"]].to_dict(orient="records")

    return {
        "available": True,
        "profile": profile,
        "snapshot": snapshot_path.name,
        "snapshot_severity": sev,
        "snapshot_timestamp": ts_label,
        "summary": summary,
        "positions": df_to_records(rows),
        "winners": df_to_records(winners),
        "losers": df_to_records(losers),
        "pnl_chart": [
            {"symbol": str(r["symbol"]), "unrealized_pnl": float(r["unrealized_pnl"])}
            for r in pnl_chart
        ],
        "endpoints": _ibkr_endpoint_choices(profile),
    }


@router.post("/positions/refresh")
def refresh_positions(
    profile: str | None = None,
    host: str = "127.0.0.1",
    port: int | None = None,
    client_id: int = 199,
) -> dict[str, Any]:
    profile = profile or get_profile()
    try:
        from src.broker.ibkr.client import IBKRClient, IBKRConnectionConfig
        from src.production.runtime.build_paper_basket import (
            fetch_account_snapshot_df,
            fetch_positions_df,
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"Could not import broker helpers: {exc}") from exc

    if port is None:
        port = _autodetect_port(profile)

    try:
        client = IBKRClient(
            IBKRConnectionConfig(host=host, port=int(port), client_id=int(client_id), account=None)
        )
        client.connect()
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "error": f"IBKR connection failed at {host}:{port}: {exc}. "
            "TWS paper=7497, TWS live=7496, IB Gateway paper=4002, IB Gateway live=4001.",
            "port": port,
        }

    try:
        positions_df = fetch_positions_df(client)
        account_df = fetch_account_snapshot_df(client)
    except Exception as exc:  # noqa: BLE001
        client.disconnect()
        return {"ok": False, "error": f"Snapshot fetch failed: {exc}", "port": port}
    finally:
        try:
            client.disconnect()
        except Exception:  # noqa: BLE001
            pass

    positions_dir = PROJECT_ROOT / "data" / "broker" / "positions"
    account_dir = PROJECT_ROOT / "data" / "broker" / "account"
    positions_dir.mkdir(parents=True, exist_ok=True)
    account_dir.mkdir(parents=True, exist_ok=True)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    positions_path = positions_dir / f"{profile}_positions_snapshot.csv"
    account_path = account_dir / f"{profile}_account_summary_{ts}.csv"
    positions_df.to_csv(positions_path, index=False)
    account_df.to_csv(account_path, index=False)

    return {
        "ok": True,
        "rows": len(positions_df),
        "timestamp": ts,
        "positions_file": str(positions_path),
        "account_file": str(account_path),
    }


# ── Composition ─────────────────────────────────────────────────────────────

TICKER_FULL_NAMES = {
    "BIL-US": "SPDR Bloomberg 1-3 Month T-Bill ETF",
    "ETHA-US": "iShares Ethereum Trust ETF",
    "EWJ-US": "iShares MSCI Japan ETF",
    "EZU-US": "iShares MSCI Eurozone ETF",
    "GLD-US": "SPDR Gold Shares",
    "IBIT-US": "iShares Bitcoin Trust ETF",
    "IEI-US": "iShares 3-7 Year Treasury Bond ETF",
    "INDA-US": "iShares MSCI India ETF",
    "MCHI-US": "iShares MSCI China ETF",
    "QQQ-US": "Invesco QQQ Trust",
    "SHY-US": "iShares 1-3 Year Treasury Bond ETF",
    "SLV-US": "iShares Silver Trust",
    "SPY-US": "SPDR S&P 500 ETF Trust",
    "TLT-US": "iShares 20+ Year Treasury Bond ETF",
    "UNG-US": "United States Natural Gas Fund",
    "USO-US": "United States Oil Fund",
    "VIXY-US": "ProShares VIX Short-Term Futures ETF",
}


@router.get("/composition")
def composition(profile: str | None = None) -> dict[str, Any]:
    profile = profile or get_profile()
    audit_date = get_latest_production_audit(profile)
    if audit_date is None:
        return {
            "available": False,
            "message": f"No {profile} production audit data is available yet.",
        }

    try:
        df = get_position_data_for_date(audit_date, profile=profile)
    except Exception as exc:  # noqa: BLE001
        return {"available": False, "error": str(exc)}

    if df is None or df.empty:
        return {"available": False, "message": "No position data is available."}

    df = df.copy()
    df["full_name"] = df["ticker"].map(TICKER_FULL_NAMES).fillna(df["ticker"])
    df["ticker"] = df["ticker"].astype(str).str.replace("-US", "", regex=False)

    cash_weight = 1 - pd.to_numeric(df["weight"], errors="coerce").fillna(0).sum()
    cash_delta_value = -pd.to_numeric(df["delta_value"], errors="coerce").fillna(0).sum()
    cash_target_value: float | None = None
    try:
        nav_df = get_historical_nav(profile)
        if nav_df is not None and not nav_df.empty:
            latest_nav = pd.to_numeric(nav_df["nav"], errors="coerce").dropna()
            if not latest_nav.empty:
                cash_target_value = float(latest_nav.iloc[-1]) - float(
                    pd.to_numeric(df["target_value"], errors="coerce").fillna(0).sum()
                )
    except Exception:  # noqa: BLE001
        cash_target_value = None

    cash_row = {
        "ticker": "USD",
        "full_name": "Cash",
        "weight": cash_weight,
        "price": None,
        "target_value": cash_target_value,
        "target_shares": None,
        "delta_shares": None,
        "delta_value": cash_delta_value,
    }
    df = pd.concat([df, pd.DataFrame([cash_row])], ignore_index=True)

    return {
        "available": True,
        "profile": profile,
        "audit_date": audit_date.isoformat(),
        "rows": df_to_records(
            df[
                [
                    "ticker",
                    "full_name",
                    "weight",
                    "price",
                    "target_value",
                    "target_shares",
                    "delta_shares",
                    "delta_value",
                ]
            ]
        ),
    }


@router.get("/composition/history")
def composition_history(
    profile: str | None = None,
    start: str | None = None,
    end: str | None = None,
) -> dict[str, Any]:
    profile = profile or get_profile()
    optimizer_name = get_active_optimizer(profile)
    history = get_historical_weights(optimizer_name, profile=profile)
    if history is None or history.empty:
        return {"available": False, "message": "No historical position data."}

    history = history.copy()
    history["date"] = pd.to_datetime(history["date"]).dt.normalize()
    min_date = history["date"].min().date()
    max_date = history["date"].max().date()

    start_d = parse_iso_date(start, min_date)
    end_d = parse_iso_date(end, max_date)
    if start_d > end_d:
        return {"available": True, "error": "Start date must be on or before end date."}

    history = history[
        (history["date"].dt.date >= start_d) & (history["date"].dt.date <= end_d)
    ].copy()
    if history.empty:
        return {"available": True, "message": "No historical data in selected range."}

    tickers = [c for c in history.columns if c != "date"]
    history[tickers] = history[tickers].apply(pd.to_numeric, errors="coerce").fillna(0.0).round(4)
    display_cols = [c.replace("-US", "") for c in tickers]
    history = history.rename(columns=dict(zip(tickers, display_cols)))

    rows = [
        {
            "date": pd.Timestamp(row["date"]).strftime("%Y-%m-%d"),
            **{c: float(row[c]) for c in display_cols},
        }
        for _, row in history.iterrows()
    ]

    return {
        "available": True,
        "tickers": display_cols,
        "rows": rows,
        "min_date": min_date.isoformat(),
        "max_date": max_date.isoformat(),
        "start": start_d.isoformat(),
        "end": end_d.isoformat(),
    }


# ── Attribution ─────────────────────────────────────────────────────────────

ATTR_REQUIRED_COLUMNS = {
    "date",
    "ticker",
    "gross_pnl",
    "slippage_cost",
    "commission_cost",
    "net_pnl",
    "contribution_bps",
}
TRADES_REQUIRED_COLUMNS = {
    "event_date",
    "execution_date",
    "ticker",
    "pre_trade_value",
    "pre_trade_weight",
    "target_value",
    "target_weight",
    "post_trade_value",
    "post_trade_weight",
    "trade_notional",
    "trade_direction",
    "slippage_cost",
    "commission_cost",
    "cost_bps",
}


@router.get("/attribution")
def attribution(
    profile: str | None = None,
    source: str = Query(default="simulated", description="simulated | actual"),
    start: str | None = None,
    end: str | None = None,
    metric: str = Query(default="net_pnl", description="net_pnl | contribution_bps"),
    top_n: int = 10,
) -> dict[str, Any]:
    profile = profile or get_profile()
    if source == "actual" and profile != "paper":
        return {
            "available": False,
            "message": "Actual portfolio attribution is paper-only.",
        }

    if source == "actual":
        latest = get_latest_actual_portfolio_audit(profile)
        attr_df = get_actual_portfolio_attribution(profile)
        trades_df = get_actual_portfolio_trades(profile)
    else:
        optimizer_name = (
            get_local_active_optimizer_for_attribution(profile)
            or get_active_optimizer(profile)
        )
        latest = get_latest_portfolio_attribution_audit(
            profile=profile, optimizer_name=optimizer_name
        )
        attr_df = get_portfolio_backtest_attribution(optimizer_name, profile=profile)
        trades_df = get_portfolio_backtest_trades(optimizer_name, profile=profile)

    if latest is None or attr_df is None or attr_df.empty:
        return {
            "available": False,
            "source": source,
            "message": f"No {source} attribution data is available yet.",
        }

    missing = ATTR_REQUIRED_COLUMNS - set(attr_df.columns)
    if missing:
        return {
            "available": False,
            "error": f"Attribution data is missing columns: {sorted(missing)}",
        }

    attr_df = _normalize_attribution(attr_df)
    min_date = attr_df["date"].min().date()
    max_date = attr_df["date"].max().date()
    start_d = parse_iso_date(start, min_date)
    end_d = parse_iso_date(end, max_date)
    if start_d > end_d:
        return {"available": True, "error": "Start date must be on or before end date."}

    filtered = attr_df[
        (attr_df["date"].dt.date >= start_d) & (attr_df["date"].dt.date <= end_d)
    ]
    if filtered.empty:
        return {"available": True, "message": "No attribution data in selected range."}

    trades_missing = trades_df is None or trades_df.empty
    filtered_trades = pd.DataFrame()
    if not trades_missing:
        miss = TRADES_REQUIRED_COLUMNS - set(trades_df.columns)
        if miss:
            trades_missing = True
        else:
            normalized = _normalize_trades(trades_df)
            filtered_trades = normalized[
                (normalized["execution_date"].dt.date >= start_d)
                & (normalized["execution_date"].dt.date <= end_d)
            ].copy()

    metric_column = "contribution_bps" if metric == "contribution_bps" else "net_pnl"
    summary = (
        filtered.groupby("ticker", as_index=False)
        .agg(
            gross_pnl=("gross_pnl", "sum"),
            slippage_cost=("slippage_cost", "sum"),
            commission_cost=("commission_cost", "sum"),
            net_pnl=("net_pnl", "sum"),
            contribution_bps=("contribution_bps", "sum"),
            active_day_count=("date", "nunique"),
        )
    )
    if not trades_missing and not filtered_trades.empty:
        traded = (
            filtered_trades.groupby("ticker", as_index=False)
            .agg(traded_day_count=("execution_date", "nunique"))
        )
        summary = summary.merge(traded, on="ticker", how="left")
    summary["traded_day_count"] = summary.get("traded_day_count", 0)
    summary = summary.fillna({"traded_day_count": 0})
    summary = summary.sort_values(metric_column, ascending=False).reset_index(drop=True)

    contributors = summary.nlargest(top_n, metric_column)
    detractors = summary.nsmallest(top_n, metric_column)
    chart_df = (
        pd.concat([contributors, detractors], axis=0)
        .drop_duplicates(subset=["ticker"])
        .sort_values(metric_column)
    )

    cumulative_df = _cumulative_top(filtered, metric_column=metric_column, top_n=top_n)

    kpis = {
        "gross_pnl": float(filtered["gross_pnl"].sum()),
        "slippage_and_commission": float(
            (filtered["slippage_cost"] + filtered["commission_cost"]).sum()
        ),
        "net_pnl": float(filtered["net_pnl"].sum()),
        "traded_ticker_count": int(
            0 if trades_missing or filtered_trades.empty
            else filtered_trades["ticker"].nunique()
        ),
    }

    blotter = pd.DataFrame()
    if not trades_missing and not filtered_trades.empty:
        blotter = filtered_trades.sort_values(
            ["execution_date", "event_date"], ascending=[False, False]
        ).copy()
        blotter["event_date"] = blotter["event_date"].dt.date
        blotter["execution_date"] = blotter["execution_date"].dt.date

    return {
        "available": True,
        "source": source,
        "profile": profile,
        "audit_date": latest.isoformat(),
        "range": {"start": start_d.isoformat(), "end": end_d.isoformat()},
        "metric": metric,
        "top_n": top_n,
        "kpis": kpis,
        "summary": df_to_records(summary),
        "contributors_chart": [
            {"ticker": str(r["ticker"]), "value": float(r[metric_column])}
            for _, r in chart_df.iterrows()
        ],
        "cumulative_chart": cumulative_df,
        "trade_blotter": df_to_records(blotter) if not blotter.empty else [],
        "trades_missing": trades_missing,
    }


# ── Internals ───────────────────────────────────────────────────────────────

def _normalize_attribution(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["date"] = pd.to_datetime(out["date"]).dt.normalize()
    for col in ["gross_pnl", "slippage_cost", "commission_cost", "net_pnl", "contribution_bps"]:
        out[col] = pd.to_numeric(out[col], errors="coerce").fillna(0.0)
    return out.sort_values(["date", "ticker"]).reset_index(drop=True)


def _normalize_trades(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["event_date"] = pd.to_datetime(out["event_date"]).dt.normalize()
    out["execution_date"] = pd.to_datetime(out["execution_date"]).dt.normalize()
    for col in [
        "pre_trade_value",
        "pre_trade_weight",
        "target_value",
        "target_weight",
        "post_trade_value",
        "post_trade_weight",
        "trade_notional",
        "slippage_cost",
        "commission_cost",
        "cost_bps",
    ]:
        out[col] = pd.to_numeric(out[col], errors="coerce").fillna(0.0)
    return out


def _cumulative_top(
    attribution_df: pd.DataFrame, *, metric_column: str, top_n: int
) -> list[dict[str, Any]]:
    if attribution_df.empty:
        return []
    daily = attribution_df.groupby(["date", "ticker"], as_index=False)[metric_column].sum()
    totals = (
        daily.groupby("ticker", as_index=False)[metric_column]
        .sum()
        .assign(_abs=lambda df: df[metric_column].abs())
        .sort_values("_abs", ascending=False)
        .head(top_n)["ticker"]
        .tolist()
    )
    if not totals:
        return []
    daily["series"] = daily["ticker"].where(daily["ticker"].isin(totals), "Other")
    cum = (
        daily.groupby(["date", "series"], as_index=False)[metric_column]
        .sum()
        .sort_values(["series", "date"])
    )
    cum["cumulative_value"] = cum.groupby("series")[metric_column].cumsum()
    return [
        {
            "date": pd.Timestamp(row["date"]).strftime("%Y-%m-%d"),
            "series": str(row["series"]),
            "value": float(row["cumulative_value"]),
        }
        for _, row in cum.iterrows()
    ]


def _latest_csv(folder: Path, pattern: str) -> Path | None:
    files = sorted(folder.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)
    return files[0] if files else None


def _staleness(path: Path) -> tuple[str, str]:
    try:
        mtime = datetime.fromtimestamp(path.stat().st_mtime)
    except OSError:
        return "unknown", "-"
    age_days = (datetime.now() - mtime).total_seconds() / 86400.0
    if age_days < 1:
        sev = "ok"
    elif age_days < 3:
        sev = "warn"
    else:
        sev = "stale"
    return sev, mtime.strftime("%Y-%m-%d %H:%M:%S")


def _read_account_value(folder: Path, profile: str, tags: list[str]) -> float | None:
    files = sorted(folder.glob(f"{profile}_account_summary_*.csv"))
    if not files:
        return None
    try:
        df = pd.read_csv(files[-1])
    except Exception:  # noqa: BLE001
        return None
    df.columns = [str(c).lower() for c in df.columns]
    tag_col = next((c for c in ["tag", "key", "field", "name"] if c in df.columns), None)
    val_col = next((c for c in ["value", "amount", "val"] if c in df.columns), None)
    if tag_col is None or val_col is None:
        return None
    df[tag_col] = df[tag_col].astype(str)
    for tag in tags:
        hit = df.loc[df[tag_col].str.lower() == tag.lower()]
        if not hit.empty:
            try:
                return float(str(hit.iloc[0][val_col]).replace(",", "").replace("$", ""))
            except Exception:  # noqa: BLE001
                return None
    return None


def _load_positions(path: Path, nav: float | None) -> pd.DataFrame:
    df = pd.read_csv(path)
    if df.empty:
        return pd.DataFrame()

    def _first_col(candidates: list[str]) -> str | None:
        norm = {re.sub(r"[^a-z0-9]", "", c.lower()): c for c in df.columns}
        for cand in candidates:
            actual = norm.get(re.sub(r"[^a-z0-9]", "", cand.lower()))
            if actual:
                return actual
        return None

    def _to_num(series: pd.Series) -> pd.Series:
        return pd.to_numeric(
            series.astype(str)
            .str.replace(",", "", regex=False)
            .str.replace("$", "", regex=False),
            errors="coerce",
        )

    symbol_col = _first_col(["symbol", "ticker", "localsymbol", "contract", "fin_instr"])
    shares_col = _first_col(
        ["current_shares", "position", "pos", "shares", "quantity", "qty", "net_position"]
    )
    avg_col = _first_col(
        ["avg_cost", "average_cost", "avgcost", "avg_px", "avgpx", "average_price"]
    )
    last_col = _first_col(
        ["market_price", "last_price", "last", "price", "mkt_price", "mktpx"]
    )
    mv_col = _first_col(
        ["market_value", "marketvalue", "mkt_val", "marketval", "mktvl"]
    )
    unreal_col = _first_col(["unrealized_pnl", "unrealizedpnl", "unrealized", "upnl"])
    realized_col = _first_col(["realized_pnl", "realizedpnl", "realized", "rpnl"])

    out = pd.DataFrame()
    out["symbol"] = (
        df[symbol_col].astype(str).str.upper().str.strip() if symbol_col else "UNKNOWN"
    )
    out["shares"] = _to_num(df[shares_col]).fillna(0.0) if shares_col else 0.0
    out["avg_price"] = _to_num(df[avg_col]) if avg_col else pd.NA
    out["last_price"] = _to_num(df[last_col]) if last_col else pd.NA
    out["market_value"] = _to_num(df[mv_col]) if mv_col else pd.NA
    out["unrealized_pnl"] = _to_num(df[unreal_col]) if unreal_col else pd.NA
    out["realized_pnl"] = _to_num(df[realized_col]) if realized_col else 0.0

    if out["last_price"].isna().all():
        prices_dir = PROJECT_ROOT / "data" / "market" / "cleaned" / "prices"
        latest_prices_df = None
        for p in (prices_dir / "etf_prices_master.parquet", prices_dir / "etf_prices_master.csv"):
            if p.exists():
                try:
                    latest_prices_df = (
                        pd.read_parquet(p) if p.suffix == ".parquet" else pd.read_csv(p)
                    )
                except Exception:  # noqa: BLE001
                    latest_prices_df = None
                break
        if latest_prices_df is not None and not latest_prices_df.empty:
            latest_prices_df.columns = [c.lower() for c in latest_prices_df.columns]
            latest_prices_df["date"] = pd.to_datetime(latest_prices_df["date"], errors="coerce")
            latest = (
                latest_prices_df.dropna(subset=["date"])
                .sort_values(["symbol", "date"])
                .groupby("symbol", as_index=False)
                .tail(1)[["symbol", "close"]]
                .rename(columns={"close": "_last"})
            )
            latest["symbol"] = latest["symbol"].astype(str).str.upper()
            out = out.merge(latest, on="symbol", how="left")
            out["last_price"] = out["_last"]
            out = out.drop(columns=["_last"])

    if out["market_value"].isna().all() and not out["last_price"].isna().all():
        out["market_value"] = out["shares"] * out["last_price"]

    if (
        out["unrealized_pnl"].isna().all()
        and not out["last_price"].isna().all()
        and not out["avg_price"].isna().all()
    ):
        out["unrealized_pnl"] = out["shares"] * (out["last_price"] - out["avg_price"])

    out = out.fillna(0.0)
    out = out.loc[out["shares"] != 0].copy()
    if nav and nav != 0:
        out["weight_pct"] = 100.0 * out["market_value"] / nav
    else:
        out["weight_pct"] = 0.0
    out["abs_market_value"] = out["market_value"].abs()
    out = out.sort_values(["abs_market_value", "symbol"], ascending=[False, True]).reset_index(
        drop=True
    )
    return out


def _autodetect_port(profile: str) -> int:
    # Only ever probes ports for the same profile; see settings.candidate_ports.
    from src.broker.ibkr.settings import detect_port

    return detect_port(profile)


def _ibkr_endpoint_choices(profile: str) -> list[dict[str, Any]]:
    return [
        {"label": "Auto-detect", "port": _autodetect_port(profile)},
        {"label": "TWS - paper (7497)", "port": 7497},
        {"label": "TWS - live (7496)", "port": 7496},
        {"label": "IB Gateway - paper (4002)", "port": 4002},
        {"label": "IB Gateway - live (4001)", "port": 4001},
    ]


# ── Orders calendar ─────────────────────────────────────────────────────────

_ORDERS_DIR = PROJECT_ROOT / "data" / "broker" / "orders"
_FILENAME_DATE = re.compile(
    r"paper_orders_submitted_(\d{8})_(\d{6})\.csv$",
)


def _scan_order_files(profile: str) -> list[tuple[date, str, Path]]:
    """Walk paper_orders_submitted_*.csv and return (date, hms, path)."""
    out: list[tuple[date, str, Path]] = []
    if not _ORDERS_DIR.exists():
        return out
    for p in _ORDERS_DIR.glob(f"{profile}_orders_submitted_*.csv"):
        m = _FILENAME_DATE.search(p.name)
        if not m:
            continue
        try:
            d = date.fromisoformat(
                f"{m.group(1)[:4]}-{m.group(1)[4:6]}-{m.group(1)[6:8]}"
            )
        except ValueError:
            continue
        hms = f"{m.group(2)[:2]}:{m.group(2)[2:4]}:{m.group(2)[4:6]}"
        out.append((d, hms, p))
    return sorted(out)


def _read_orders_csv(path: Path) -> pd.DataFrame:
    try:
        df = pd.read_csv(path)
    except Exception:  # noqa: BLE001
        return pd.DataFrame()
    if df.empty:
        return df
    df.columns = [str(c).lower().strip() for c in df.columns]
    side_col = next((c for c in ["side", "action", "trade_direction"] if c in df.columns), None)
    if side_col and side_col != "side":
        df["side"] = df[side_col]
    if "side" in df.columns:
        df["side"] = df["side"].astype(str).str.upper().str.strip()
    if "qty" not in df.columns and "quantity" in df.columns:
        df["qty"] = df["quantity"]
    return df


@router.get("/orders/calendar")
def orders_calendar(
    profile: str | None = None,
    year: int | None = None,
    month: int | None = None,
) -> dict[str, Any]:
    """Return daily buy/sell counts (and a month grid) for the orders calendar."""
    profile = profile or get_profile()
    today = date.today()
    if year is None:
        year = today.year
    if month is None:
        month = today.month

    files = _scan_order_files(profile)
    daily: dict[date, dict[str, int]] = {}
    for d, _hms, path in files:
        df = _read_orders_csv(path)
        if df.empty or "side" not in df.columns:
            continue
        buy = int((df["side"] == "BUY").sum())
        sell = int((df["side"] == "SELL").sum())
        bucket = daily.setdefault(d, {"buy": 0, "sell": 0, "total": 0, "files": 0})
        bucket["buy"] += buy
        bucket["sell"] += sell
        bucket["total"] += buy + sell
        bucket["files"] += 1

    # Build a month grid: list of weeks, each week is 7 cells.
    import calendar as _cal

    cal = _cal.Calendar(firstweekday=6)  # Sunday-first
    weeks: list[list[dict[str, Any]]] = []
    for week in cal.monthdatescalendar(year, month):
        row = []
        for d in week:
            stats = daily.get(d, {"buy": 0, "sell": 0, "total": 0, "files": 0})
            row.append(
                {
                    "date": d.isoformat(),
                    "in_month": d.month == month,
                    "is_today": d == today,
                    **stats,
                }
            )
        weeks.append(row)

    available_dates = sorted(daily.keys())
    return {
        "profile": profile,
        "year": year,
        "month": month,
        "month_name": _cal.month_name[month],
        "weeks": weeks,
        "weekday_labels": ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"],
        "earliest": available_dates[0].isoformat() if available_dates else None,
        "latest": available_dates[-1].isoformat() if available_dates else None,
        "totals": {
            "buy": sum(v["buy"] for v in daily.values()),
            "sell": sum(v["sell"] for v in daily.values()),
            "days_with_orders": len(daily),
            "submission_files": sum(v["files"] for v in daily.values()),
        },
    }


@router.get("/orders/day")
def orders_for_day(
    day: str,
    profile: str | None = None,
) -> dict[str, Any]:
    """Return every order submitted on the given ISO date."""
    profile = profile or get_profile()
    target = parse_iso_date(day)
    if target is None:
        raise HTTPException(status_code=400, detail=f"Invalid date {day!r}")
    files = [f for f in _scan_order_files(profile) if f[0] == target]
    if not files:
        return {
            "available": False,
            "profile": profile,
            "date": target.isoformat(),
            "orders": [],
            "submissions": [],
        }
    orders: list[dict[str, Any]] = []
    submissions: list[dict[str, Any]] = []
    for d, hms, path in files:
        df = _read_orders_csv(path)
        submissions.append(
            {
                "time": hms,
                "file": path.name,
                "rows": int(len(df)),
            }
        )
        if df.empty:
            continue
        # Coerce numeric columns where possible.
        for col in ("qty", "filled", "remaining", "ref_price", "avgFillPrice"):
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
        df["_submission_time"] = hms
        orders.extend(df.to_dict(orient="records"))
    return {
        "available": True,
        "profile": profile,
        "date": target.isoformat(),
        "orders": [_clean_record(r) for r in orders],
        "submissions": submissions,
    }


def _clean_record(row: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in row.items():
        if isinstance(v, float):
            if not (v == v and v not in (float("inf"), float("-inf"))):
                v = None
        out[str(k)] = v
    return out
