"""Rebalance preview with the engine's own basket math and pre-trade gate.

The preview runs ``build_reconciliation`` (the function the daily runner uses)
on live positions and NAV, then ``run_pre_trade_risk_checks`` (the runner's
gate: kill switch, NAV, price-file validation, exposure limits), then the C++
no-leverage check over every holding plus the basket's orders (``mandate``).
The basket sells every share holding outside the model whole; futures and
options outside it are left alone and named in a warning.
Submitting starts the daily runner itself, so there is one order path.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Iterable
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


def execution_text(symbols: Iterable[str], now: datetime) -> str:
    """How the orders execute if the daily runner sends them at ``now``: the engine's
    own choice (src/broker/ibkr/auction.py), market-on-close until its deadline
    (15:45 ET, 12:45 on an early close), else market-on-open at the next session."""
    from src.broker.ibkr.auction import execution_for

    ex = execution_for(symbols, at=now)
    day = f"{ex.session:%a %b} {ex.session.day}"
    if ex.auction == "close" and ex.moc_deadline is not None:
        by = ex.moc_deadline.astimezone(nyse.ET)
        return (
            f"Market-on-close through the daily runner: filled at today's close ({day}) "
            f"if the run sends them by {by:%H:%M} ET, else at the next open"
        )
    return f"Market-on-open through the daily runner: filled at the next open, {day}"


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
    from src.production.generate_targets import model_universe
    from src.production.runtime.build_paper_basket import (
        build_reconciliation,
        default_config,
        holdings_outside_model,
        load_last_closes,
        load_latest_targets,
    )
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
    columns = ["symbol", "current_shares", "sec_type", "market_price"]
    if snap is None:
        positions = pd.DataFrame(columns=columns)
        nav = 0.0
    else:
        # every holding: the basket sells shares outside the model and leaves futures and options alone
        positions = pd.DataFrame(
            [
                {
                    "symbol": p.symbol,
                    "current_shares": p.quantity,
                    "sec_type": p.sec_type,
                    "market_price": mandate.price_of(p),
                }
                for p in snap.positions
            ],
            columns=columns,
        )
        nav = snap.nav

    model = model_universe()
    recon, orders = build_reconciliation(
        targets,
        positions,
        nav or 1.0,
        cash_buffer=cfg.cash_buffer,
        min_trade_dollars=cfg.min_trade_dollars,
        model=model,
        last_closes=load_last_closes(cfg.prices_path),
    )
    order_syms = set(orders["symbol"]) if len(orders) else set()

    price_now = {p.symbol: (p.last or 0.0) for p in snap.positions} if snap else {}
    value_now = {p.symbol: p.market_value for p in snap.positions} if snap else {}
    rows = []
    basket = []  # what the leverage check sees: the orders at the prices est_notional uses
    for r in recon.itertuples(index=False):
        close = float(r.close)  # NaN for a holding outside the model with no price anywhere
        px = price_now.get(r.symbol) or close
        value = int(r.current_shares) * px if math.isfinite(px) else value_now.get(r.symbol, 0.0)
        trade = int(r.delta_shares) if r.symbol in order_syms else 0
        cost = float(r.estimated_commission) if trade else 0.0
        if trade:
            # an unpriced order goes in as it is: the engine blocks it rather than guess a price
            basket.append({"symbol": r.symbol, "quantity": trade, "price": close, "commission": cost})
        rows.append(
            {
                "symbol": r.symbol,
                "current_weight": value / nav if nav else 0.0,
                "target_weight": float(r.target_weight),
                "current_qty": int(r.current_shares),
                "target_qty": int(r.target_shares),
                "trade_qty": trade,
                "est_notional": trade * close if math.isfinite(close) else 0.0,
                "est_cost": cost,
            }
        )
    outside = holdings_outside_model(targets.loc[targets["symbol"].astype(str).str.upper().isin(model)], positions)
    if outside.sell:
        checks.append(
            _check(
                "untargeted",
                "Holdings outside the targets",
                "warn",
                f"{', '.join(outside.sell)}: not in the model, so this rebalance sells them",
            )
        )
    for warning in outside.warnings():
        checks.append(_check("untargeted_kept", "Holdings the basket can't sell", "warn", warning))

    buy = sum(r["est_notional"] for r in rows if r["est_notional"] > 0)
    sell = -sum(r["est_notional"] for r in rows if r["est_notional"] < 0)
    n_orders = sum(1 for r in rows if r["trade_qty"])

    if snap is not None:
        gate = run_pre_trade_risk_checks(
            basket_df=recon,
            nav=nav,
            prices_path=cfg.targets_path.parent.parent / "prices" / "etf_prices_master.parquet",
            as_of=last_complete,  # the session the data check wants the targets from
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

    return {
        "as_of": now.astimezone(timezone.utc).isoformat(timespec="seconds"),
        "sleeve": sleeve_name(),
        "targets_date": targets_date.isoformat(),
        "execution": execution_text(order_syms or model, now),
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
