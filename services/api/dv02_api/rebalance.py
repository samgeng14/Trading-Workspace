"""Rebalance preview with the engine's own basket math and pre-trade gate.

The preview runs ``build_reconciliation`` (the function the daily runner uses)
on live positions and NAV, then ``run_pre_trade_risk_checks`` (the runner's
gate: kill switch, NAV, price-file validation, exposure limits), then the C++
no-leverage check over every holding plus the basket's orders (``mandate``).
Submitting starts the daily runner itself, so there is one order path.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from . import mandate, nyse, runs
from .broker import AccountSnap

log = logging.getLogger(__name__)


def _check(id_: str, label: str, status: str, detail: str) -> dict[str, str]:
    return {"id": id_, "label": label, "status": status, "detail": detail}


def _money(v: float) -> str:
    return f"-${-v:,.0f}" if v < 0 else f"${v:,.0f}"


def leverage_check(snap: AccountSnap, orders: list[dict[str, Any]]) -> dict[str, str]:
    """The C++ no-leverage check on the whole book after these orders."""
    label = "No leverage"
    try:
        res = mandate.check(snap, orders)
    except mandate.EngineMissing as exc:
        return _check("leverage", label, "fail", str(exc))
    if not res["ok"]:
        return _check("leverage", label, "fail", "; ".join(b["message"] for b in res["breaches"] if b["blocking"]))
    if res["breaches"]:
        return _check("leverage", label, "warn", "; ".join(b["message"] for b in res["breaches"]))
    before, after = res["before"], res["after"]
    return _check(
        "leverage",
        label,
        "pass",
        f"gross {before['gross']:.1%} → {after['gross']:.1%} of NAV, cash {_money(after['cash'])} after; "
        "every holding counted",
    )


def sleeve_name() -> str:
    import os

    agent_env = (runs.launch_agent() or {}).get("EnvironmentVariables") or {}
    opt = str(agent_env.get("CAPITALFUND_OPTIMIZER") or os.environ.get("CAPITALFUND_OPTIMIZER", "")).strip()
    for r in runs.load_runs(5):
        for s in r["steps"]:
            if s["name"] == "Targets" and s["ok"]:
                opt = s["detail"].split(",")[0]
                break
        if opt:
            break
    return f"ETF momentum ({opt})" if opt and opt != "?" else "ETF momentum"


def preview(snap: AccountSnap | None, live: bool, broker_error: str | None, now: datetime) -> dict[str, Any]:
    """``snap`` may be the engine's saved snapshot when IBKR is down (``live`` False): the
    preview still shows, but nothing can be submitted."""
    import pandas as pd
    from src.production.runtime.build_paper_basket import build_reconciliation, default_config, load_latest_targets
    from src.runtime.risk_checks import run_pre_trade_risk_checks

    cfg = default_config()
    checks: list[dict[str, str]] = []
    targets = load_latest_targets(cfg.targets_path)
    targets_date = pd.to_datetime(targets["date"]).max().date()

    # data freshness: targets should be from the last completed session
    state = nyse.session_state(now)
    last_complete = state.trading_date if state.session != "open" else nyse.prev_trading_day(state.trading_date)
    stale_days = len(nyse.trading_days(targets_date, last_complete)) - 1
    checks.append(
        _check(
            "data",
            "Targets and prices",
            "pass" if stale_days <= 0 else "warn" if stale_days <= 2 else "fail",
            f"targets from the {targets_date} close"
            + (
                ""
                if stale_days <= 0
                else f", {stale_days} session{'s' if stale_days > 1 else ''} old; the run refreshes them"
            ),
        )
    )

    if not live:
        note = f"; showing the snapshot saved {snap.as_of.astimezone():%b %d %H:%M}" if snap else ""
        checks.append(_check("broker", "IBKR connection", "fail", (broker_error or "not connected") + note))
    if snap is None:
        positions = pd.DataFrame(columns=["symbol", "current_shares"])
        nav = 0.0
    else:
        stocks = [p for p in snap.positions if p.sec_type in ("STK", "ETF")]
        positions = pd.DataFrame(
            [{"symbol": p.symbol, "current_shares": p.quantity} for p in stocks], columns=["symbol", "current_shares"]
        )
        nav = snap.nav

    recon, orders = build_reconciliation(
        targets, positions, nav or 1.0, cash_buffer=cfg.cash_buffer, min_trade_dollars=cfg.min_trade_dollars
    )
    order_syms = set(orders["symbol"]) if len(orders) else set()

    price_now = {p.symbol: (p.last or 0.0) for p in snap.positions} if snap else {}
    rows = []
    basket = []  # what the leverage check sees: the orders at the prices est_notional uses
    for r in recon.itertuples(index=False):
        px = price_now.get(r.symbol) or float(r.close)
        trade = int(r.delta_shares) if r.symbol in order_syms else 0
        cost = float(r.estimated_commission) if trade else 0.0
        if trade:
            basket.append({"symbol": r.symbol, "quantity": trade, "price": float(r.close), "commission": cost})
        rows.append(
            {
                "symbol": r.symbol,
                "current_weight": (int(r.current_shares) * px / nav) if nav else 0.0,
                "target_weight": float(r.target_weight),
                "current_qty": int(r.current_shares),
                "target_qty": int(r.target_shares),
                "trade_qty": trade,
                "est_notional": trade * float(r.close),
                "est_cost": cost,
            }
        )
    targeted = set(recon["symbol"])
    untargeted = [
        p for p in (snap.positions if snap else []) if p.sec_type in ("STK", "ETF") and p.symbol not in targeted
    ]
    for p in untargeted:
        rows.append(
            {
                "symbol": p.symbol,
                "current_weight": p.market_value / nav if nav else 0.0,
                "target_weight": 0.0,
                "current_qty": int(p.quantity),
                "target_qty": int(p.quantity),
                "trade_qty": 0,
                "est_notional": 0.0,
                "est_cost": 0.0,
            }
        )
    if untargeted:
        checks.append(
            _check(
                "untargeted",
                "Holdings outside the targets",
                "warn",
                ", ".join(p.symbol for p in untargeted)
                + ": not in the model. They count toward the no-leverage check, but the engine doesn't trade "
                "them until you decide whether to sell them or keep them",
            )
        )

    buy = sum(r["est_notional"] for r in rows if r["est_notional"] > 0)
    sell = -sum(r["est_notional"] for r in rows if r["est_notional"] < 0)
    n_orders = sum(1 for r in rows if r["trade_qty"])

    if snap is not None:
        gate = run_pre_trade_risk_checks(
            basket_df=recon,
            nav=nav,
            prices_path=cfg.targets_path.parent.parent / "prices" / "etf_prices_master.parquet",
        )
        ks = gate.details.get("kill_switch") or {}
        checks.insert(
            0,
            _check(
                "kill_switch",
                "Kill switch",
                "fail" if ks.get("armed") else "pass",
                f"Armed: {ks.get('reason') or 'no reason given'}" if ks.get("armed") else "Clear",
            ),
        )
        expo = (gate.details.get("exposure") or {}).get("summary") or {}
        for reason in gate.blocking_reasons:
            if "kill switch" in reason.lower() or "kill-switch" in reason.lower():
                continue
            checks.append(_check("gate", "Engine pre-trade gate", "fail", reason))
        for warning in gate.warnings:
            # the price validator appends raw examples ("e.g. [('BIL', ...)]"); the count says enough
            checks.append(_check("gate_warn", "Engine pre-trade gate", "warn", warning.split(" e.g. ")[0]))
        if gate.ok:
            gross = expo.get("gross_exposure")
            detail = "exposure limits, price file and NAV pass"
            if isinstance(gross, (int, float)):
                detail += f" (gross after trades {gross:.1%})"
            checks.append(_check("gate", "Engine pre-trade gate", "pass", detail))
        checks.append(leverage_check(snap, basket))

    turnover = (buy + sell) / nav if nav else 0.0
    checks.append(
        _check(
            "orders",
            "Orders to send",
            "pass" if n_orders else "warn",
            f"{n_orders} orders, turnover {turnover:.1%} of NAV"
            if n_orders
            else f"nothing to trade: every difference is under ${cfg.min_trade_dollars:,.0f}",
        )
    )

    today = nyse.et_date(now)
    done = runs.submitted_today(today)
    if runs.RUN_STATE["running"]:
        checks.append(
            _check(
                "duplicate",
                "Duplicate guard",
                "fail",
                f"a daily run started at {runs.RUN_STATE['started_at']} is in progress",
            )
        )
    elif done:
        checks.append(
            _check(
                "duplicate",
                "Duplicate guard",
                "fail",
                f"Already submitted for this session ({done['started_at'][11:16]} run)",
            )
        )
    else:
        checks.append(_check("duplicate", "Duplicate guard", "pass", "No submission for this session yet"))

    if state.session == "open":
        when = "now"
    else:
        nxt = state.next_change_at.astimezone(nyse.ET)
        when = f"at the open, {nxt:%a %b} {nxt.day}"
    return {
        "as_of": now.astimezone(timezone.utc).isoformat(timespec="seconds"),
        "sleeve": sleeve_name(),
        "targets_date": targets_date.isoformat(),
        "execution": f"Market orders through the daily runner, filled {when}",
        "rows": sorted(rows, key=lambda r: -abs(r["est_notional"])),
        "totals": {
            "buy_notional": buy,
            "sell_notional": sell,
            "turnover": turnover,
            "est_cost": sum(r["est_cost"] for r in rows),
            "orders": n_orders,
        },
        "checks": checks,
        "can_submit": live and n_orders > 0 and all(c["status"] != "fail" for c in checks),
    }


def blocking(preview_doc: dict[str, Any]) -> list[str]:
    return [f"{c['label']}: {c['detail']}" for c in preview_doc["checks"] if c["status"] == "fail"]
