"""Fixtures: a temp engine data tree and a fake broker, so tests never touch a
gateway or the real kill switch / runs / NAV files."""

from __future__ import annotations

import csv
import json
import math
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dv02_api import engine, nyse  # noqa: E402
from dv02_api.broker import AccountSnap, BrokerStatus, PositionSnap  # noqa: E402

TODAY = date(2026, 10, 9)  # a Friday session


def _days(n: int) -> list[date]:
    return nyse.trading_days(TODAY - timedelta(days=int(n * 1.6)), TODAY)[-n:]


def _write_bars(folder: Path, symbol: str, start: float, drift: float, wobble: float) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    with (folder / f"{symbol}.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["date", "close"])
        px = start
        for i, d in enumerate(_days(300)):
            px *= 1 + drift + wobble * math.sin(i * 0.7 + len(symbol))
            w.writerow([d.isoformat(), f"{px:.4f}"])


@pytest.fixture()
def engine_tree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the API's data paths (and the engine's kill switch) at a temp tree."""
    root = tmp_path / "engine"
    monkeypatch.setattr(engine, "ENGINE_ROOT", root)
    from src.execution import kill_switch

    monkeypatch.setattr(kill_switch, "KILL_SWITCH_PATH", root / "artifacts" / "runs" / "kill_switch.json")
    monkeypatch.setenv("DV02_INCEPTION_DATE", "2025-10-15")
    monkeypatch.setenv("DV02_INCEPTION_NAV", "1000000")
    for var in ("IBKR_FLEX_TOKEN", "IBKR_FLEX_QUERY_ID", "IBKR_FLEX_NAV_QUERY_ID", "DV02_ENGINE_PYTHON"):
        monkeypatch.delenv(var, raising=False)

    bars = root / "artifacts" / "api" / "bars"
    paths = {
        "SPY": (4e-4, 8e-3), "AGG": (1e-4, 2e-3), "QQQ": (5e-4, 1e-2), "TLT": (0, 6e-3),
        "GLD": (3e-4, 7e-3), "IWM": (3e-4, 1e-2), "ACWI": (3e-4, 7e-3), "BIL": (1.5e-4, 1e-4),
    }  # fmt: skip
    for sym, (drift, wobble) in paths.items():
        _write_bars(bars, sym, 100.0, drift, wobble)

    # the API's own close records: a NAV path since inception
    navs = root / "artifacts" / "api" / "nav_paper.csv"
    with navs.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["date", "nav"])
        v = 1_000_000.0
        for i, d in enumerate(nyse.trading_days(date(2025, 10, 15), TODAY - timedelta(days=1))):
            w.writerow([d.isoformat(), f"{v:.2f}"])
            v *= 1 + 3e-4 + 6e-3 * math.sin(i * 0.9)

    runs = root / "artifacts" / "runs"
    runs.mkdir(parents=True, exist_ok=True)
    (runs / "daily_run_20261008_163201.json").write_text(json.dumps({
        "run_id": "20261008_163201", "date": "2026-10-08", "profile": "paper", "dry_run": False, "ok": True,
        "should_rebalance": False,
        "rebalance_decision": {"cadence": "drift", "rebalance": False, "reason": "max drift 1.2% < 5%"},
        "steps": {
            "append_daily": {"ok": True, "new_rows_added_to_master": 22, "latest_date": "2026-10-08", "errors": []},
            "generate_targets": {"ok": True, "optimizer": "momentum_tilted", "latest_rebalance_date": "2026-10-08"},
        },
    }))  # fmt: skip
    (runs / "daily_run_20261007_163203.json").write_text(json.dumps({
        "run_id": "20261007_163203", "date": "2026-10-07", "profile": "paper", "dry_run": False, "ok": False,
        "error": "append_daily failed",
        "steps": {"append_daily": {"ok": False, "error": "IBKR connection refused"}},
    }))  # fmt: skip

    acct = root / "data" / "broker" / "account"
    acct.mkdir(parents=True, exist_ok=True)
    (acct / "paper_account_summary_20261008_163210.csv").write_text(
        "tag,value\nNetLiquidation,1021000.00\nTotalCashValue,5000.00\n"
    )
    pos = root / "data" / "broker" / "positions"
    pos.mkdir(parents=True, exist_ok=True)
    (pos / "paper_positions_snapshot.csv").write_text(
        "symbol,current_shares,avg_cost\nSPY,400,600\nTLT,2000,85\nGLD,300,400\n"
    )
    return root


# ── fake ib_async objects for orders ───────────────────────────────────


def fake_trade(perm: int, symbol: str, action: str, qty: float, status: str, filled: float = 0.0,
               client_id: int = 300, order_type: str = "MKT", avg: float = 0.0) -> Any:  # fmt: skip
    t0 = datetime(2026, 10, 9, 14, 30, tzinfo=timezone.utc)
    return SimpleNamespace(
        order=SimpleNamespace(
            permId=perm,
            orderId=perm % 1000,
            clientId=client_id,
            action=action,
            totalQuantity=qty,
            orderType=order_type,
            lmtPrice=0.0,
            orderRef="",
        ),  # fmt: skip
        orderStatus=SimpleNamespace(status=status, filled=filled, avgFillPrice=avg),
        contract=SimpleNamespace(symbol=symbol, localSymbol=symbol, secType="STK"),
        log=[SimpleNamespace(time=t0, message=""), SimpleNamespace(time=t0 + timedelta(minutes=1), message="")],
        fills=[],
    )


@dataclass
class FakeBroker:
    connected: bool = True
    client_id: int = 307
    cancelled: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        from src.broker.ibkr import settings

        self.endpoint = settings.resolve_endpoint("paper")
        self.status = BrokerStatus(
            connected=self.connected,
            endpoint="127.0.0.1:4002",
            account="DU1234567" if self.connected else None,
            error=None if self.connected else "nothing is listening on 127.0.0.1:4002",
        )
        self._trades = [
            fake_trade(1001, "SPY", "BUY", 10, "Filled", filled=10, avg=612.5),
            fake_trade(1002, "TLT", "SELL", 50, "Submitted"),
        ]

    async def start(self) -> None: ...
    async def stop(self) -> None: ...

    def market_data_type(self) -> int | None:
        return 3 if self.connected else None

    def snapshot(self) -> AccountSnap | None:
        if not self.connected:
            return None
        p = [
            PositionSnap(1, "SPY", "SPY", "ETF", 400, 1, 600.0, 612.0, 608.0, 244_800.0, 4_800.0, 1_600.0),
            PositionSnap(2, "TLT", "TLT", "ETF", 2000, 1, 85.0, 86.5, 86.0, 173_000.0, 3_000.0, 1_000.0),
            PositionSnap(3, "GLD", "GLD", "ETF", 300, 1, 400.0, 420.0, 418.0, 126_000.0, 6_000.0, 600.0),
            PositionSnap(4, "MESZ6", "MES", "FUT", -5, 5, 6600.0, 6650.0, 6640.0, -166_250.0, -1_250.0, -250.0),
        ]
        return AccountSnap("DU1234567", "USD", 1_025_000.0, 480_000.0, 2_950.0,
                           datetime(2026, 10, 9, 18, 0, tzinfo=timezone.utc), p)  # fmt: skip

    def quotes(self) -> list[dict[str, Any]]:
        return [
            {
                "symbol": "SPX",
                "label": "S&P 500",
                "last": 6650.0,
                "change": 12.0,
                "change_pct": 0.0018,
                "kind": "index",
                "_type": 3,
            },  # fmt: skip
            {
                "symbol": "TNX",
                "label": "US 10Y",
                "last": 4.12,
                "change": -0.03,
                "change_pct": -0.0072,
                "kind": "yield",
                "_type": 3,
            },  # fmt: skip
        ]

    def trades(self) -> list[Any]:
        return self._trades if self.connected else []

    async def refresh_orders(self) -> None: ...

    async def cancel(self, trade: Any) -> None:
        trade.orderStatus.status = "Cancelled"
        self.cancelled.append(str(trade.order.permId))

    def intraday(self, d: date) -> list[tuple[datetime, float]]:
        t = datetime(2026, 10, 9, 13, 31, tzinfo=timezone.utc)
        return [(t + timedelta(minutes=i), 100.0 * i) for i in range(5)]


@pytest.fixture()
def fake_broker() -> FakeBroker:
    return FakeBroker()


@pytest.fixture()
def frozen_now(monkeypatch: pytest.MonkeyPatch) -> datetime:
    """Friday 2026-10-09 14:12 ET, market open."""
    from dv02_api import app as app_mod

    now = datetime(2026, 10, 9, 18, 12, tzinfo=timezone.utc)
    monkeypatch.setattr(app_mod, "_now", lambda: now)
    return now
