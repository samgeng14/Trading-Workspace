"""Risk report: exposures, historical VaR/ES, forecast vol, limits and stress scenarios.

Historical simulation applies today's weights to the last year of daily
returns of each holding (dividend-adjusted closes from IBKR). Scenario P&L
uses each position's measured beta to SPY, its duration and its asset class.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from . import stats

SCENARIOS: list[tuple[str, str, dict[str, float]]] = [
    ("spx_down_10", "S&P 500 −10%", {"mkt": -0.10}),
    ("rates_up_100", "Rates +100 bp", {"rates_bp": 100}),
    ("commodities_down_15", "Commodities −15%", {"cmdty": -0.15}),
    ("crypto_down_30", "Crypto −30%", {"crypto": -0.30}),
    ("stagflation", "Stagflation shock", {"mkt": -0.08, "rates_bp": 50, "cmdty": 0.10}),
    ("march_2020", "March 2020 replay", {"mkt": -0.30, "rates_bp": -100, "cmdty": -0.40, "crypto": -0.40}),
]

# Defaults match the dashboard demo; config/risk_limits.yaml overrides them.
DEFAULT_LIMITS = {
    "gross": 1.5,
    "net": 1.0,
    "single_name": 0.20,
    "beta": 1.0,
    "vol": 0.12,
    "drawdown": -0.05,
    "day_loss": -0.02,
    "names": 25,
    "target_vol": 0.10,
}


def beta_of(rets: dict[date, float], mkt: dict[date, float], fallback: float) -> float:
    days = sorted(set(rets) & set(mkt))[-252:]
    if len(days) < 60:
        return fallback
    return stats.relative([rets[d] for d in days], [mkt[d] for d in days])["beta"]


def returns_from_closes(closes: list[tuple[date, float]]) -> dict[date, float]:
    out = {}
    for (_, p0), (d1, p1) in zip(closes, closes[1:], strict=False):
        if p0:
            out[d1] = p1 / p0 - 1
    return out


def portfolio_history(weights: dict[str, float], rets: dict[str, dict[date, float]], days: int = 252) -> list[float]:
    """Today's weights over the last ``days`` of history (days where every holding has a return)."""
    held = [s for s, w in weights.items() if w and s in rets]
    if not held:
        return []
    common = set.intersection(*(set(rets[s]) for s in held))
    out = []
    for d in sorted(common)[-days:]:
        out.append(sum(weights[s] * rets[s][d] for s in held))
    return out


def limit(id_: str, label: str, value: float, lim: float, unit: str, detail: str = "") -> dict[str, Any]:
    used = value / lim if lim else 0.0  # same sign as the limit counts toward it
    status = "breach" if used >= 1 else "warning" if used >= 0.8 else "ok"
    return {"id": id_, "label": label, "value": value, "limit": lim, "unit": unit, "status": status, "detail": detail}


def report(
    *,
    as_of: str,
    nav: float,
    positions: list[dict[str, Any]],
    history_rets: list[float],
    beta: float,
    drawdown: float,
    day_return: float,
    sleeves: list[dict[str, Any]],
    limits_cfg: dict[str, float],
    durations: dict[str, float],
) -> dict[str, Any]:
    cfg = {**DEFAULT_LIMITS, **limits_cfg}
    classes: dict[str, dict[str, float]] = {}
    for p in positions:
        c = classes.setdefault(p["asset_class"], {"long": 0.0, "short": 0.0})
        c["long" if p["weight"] >= 0 else "short"] += p["weight"]
    gross = sum(abs(p["weight"]) for p in positions)
    net = sum(p["weight"] for p in positions)

    sorted_rets = sorted(history_rets)
    if sorted_rets:
        var95 = -sorted_rets[int(0.05 * len(sorted_rets))]
        tail = sorted_rets[: max(1, int(0.025 * len(sorted_rets)))]
        es975 = -stats.mean(tail)
    else:
        var95 = es975 = 0.0
    forecast_vol = stats.annualized_vol(history_rets[-63:])

    cash_like = sorted((p for p in positions if p["sec_type"] != "FUT"), key=lambda p: -p["weight"])
    largest = cash_like[0] if cash_like else None
    top5 = sum(p["weight"] for p in cash_like[:5])

    scenarios = []
    for id_, label, shock in SCENARIOS:
        pnl = 0.0
        for p in positions:
            mv = p["market_value"]
            pnl += mv * p["beta"] * shock.get("mkt", 0.0)
            pnl += -mv * durations.get(p["symbol"], 0.0) * shock.get("rates_bp", 0.0) / 10_000
            if p["asset_class"] == "commodity":
                pnl += mv * shock.get("cmdty", 0.0)
            if p["asset_class"] == "crypto":
                pnl += mv * shock.get("crypto", 0.0)
        scenarios.append({"id": id_, "label": label, "pnl": pnl, "pnl_pct": pnl / nav if nav else 0.0})

    limits = [
        limit("gross", "Gross exposure", gross, cfg["gross"], "pct", "Long plus short notional, futures included"),
        limit("net", "Net exposure", net, cfg["net"], "pct"),
        limit(
            "single_name",
            "Largest position",
            largest["weight"] if largest else 0.0,
            cfg["single_name"],
            "pct",
            largest["symbol"] if largest else "",
        ),
        limit("beta", "Beta to S&P 500", beta, cfg["beta"], "ratio", "Measured on a year of daily returns"),
        limit("vol", "Forecast volatility", forecast_vol, cfg["vol"], "pct", "63-day, annualized"),
        limit("drawdown", "Drawdown from peak", drawdown, cfg["drawdown"], "pct", "From the NAV high-water mark"),
        limit("day_loss", "Day P&L", day_return, cfg["day_loss"], "pct", "Today, as a share of NAV"),
        limit("names", "Positions", float(len(positions)), cfg["names"], "count"),
    ]
    return {
        "as_of": as_of,
        "nav": nav,
        "by_asset_class": [{"asset_class": k, **v, "net": v["long"] + v["short"]} for k, v in classes.items()],
        "by_sleeve": sleeves,
        "limits": limits,
        "beta": beta,
        "forecast_vol": forecast_vol,
        "target_vol": cfg["target_vol"],
        "var_95_1d": var95,
        "es_975_1d": es975,
        "top5_weight": top5,
        "scenarios": scenarios,
    }
