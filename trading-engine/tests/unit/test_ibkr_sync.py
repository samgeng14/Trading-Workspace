"""A gateway cut off from IBKR (warning 2110) answers the API socket but not the
startup requests. On 2026-10-19 the VM's reconcile read that as "the account
holds nothing", armed the kill switch and wiped the expected book, and the run's
first bars request hung until systemd stopped it 45 minutes later. Every
connection now fails instead, and no request waits forever."""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.broker.ibkr import client as client_mod  # noqa: E402
from src.broker.ibkr.client import (  # noqa: E402
    REQUEST_TIMEOUT_S,
    IBKRClient,
    IBKRConnectionConfig,
    IBKRSyncError,
    connect_ib,
    fail_fast,
    sync_failure,
)
from src.common import clock  # noqa: E402
from src.execution import kill_switch  # noqa: E402
from src.production.pipeline import append_ibkr_daily as app  # noqa: E402
from src.runtime import end_of_day_reconcile as eod  # noqa: E402

MONDAY = ["positions request timed out", "account updates for DUP317554 request timed out", "executions request timed out"]


class FakeIB:
    """ib_async.IB's connect as the doctor and the client call it."""

    RequestTimeout = 0.0

    def __init__(self, *, sync_timeouts=(), refuse=False) -> None:
        self.sync_timeouts = list(sync_timeouts)
        self.refuse = refuse
        self.connect_kwargs: dict = {}
        self.connected = False
        self.history_calls = 0

    def connect(self, host, port, clientId, timeout, **kwargs):  # noqa: N803
        self.connect_kwargs = {"clientId": clientId, "timeout": timeout, **kwargs}
        if self.refuse:
            raise ConnectionRefusedError(61, "Connection refused")
        if self.sync_timeouts and kwargs.get("raiseSyncErrors"):
            raise ConnectionError(list(self.sync_timeouts))  # what ib_async raises, after disconnecting
        self.connected = True

    def isConnected(self):  # noqa: N802
        return self.connected

    def disconnect(self):
        self.connected = False

    def managedAccounts(self):  # noqa: N802
        return ["DUP317554"]

    def qualifyContracts(self, *contracts):  # noqa: N802
        return list(contracts)

    def reqHistoricalData(self, contract, **kw):  # noqa: N802
        self.history_calls += 1
        raise TimeoutError()


def client_with(monkeypatch, fake: FakeIB) -> IBKRClient:
    monkeypatch.setattr(client_mod, "IB", lambda: fake)
    return IBKRClient(IBKRConnectionConfig(host="127.0.0.1", port=4002, client_id=306, readonly=True))


# ── the client ────────────────────────────────────────────────────────────


def test_the_client_refuses_a_gateway_cut_off_from_ibkr(monkeypatch) -> None:
    fake = FakeIB(sync_timeouts=MONDAY)
    with pytest.raises(IBKRSyncError) as err:
        client_with(monkeypatch, fake).connect()
    text = str(err.value)
    assert "IB Gateway answered at 127.0.0.1:4002, but IBKR's servers didn't" in text
    assert "positions request timed out" in text and "warning 2110" in text
    assert fake.connect_kwargs["raiseSyncErrors"] is True and fake.connect_kwargs["readonly"] is True


def test_a_healthy_connection_caps_every_request(monkeypatch) -> None:
    fake = FakeIB()
    c = client_with(monkeypatch, fake)
    c.connect()
    assert c.is_connected and fake.RequestTimeout == REQUEST_TIMEOUT_S == 60.0


def test_a_refused_socket_stays_a_connection_error(monkeypatch) -> None:
    with pytest.raises(ConnectionRefusedError):
        client_with(monkeypatch, FakeIB(refuse=True)).connect()
    assert sync_failure(ConnectionRefusedError(61, "Connection refused")) is None
    assert sync_failure(ConnectionError("Socket connection broken while connecting")) is None
    assert sync_failure(ConnectionError(["positions request timed out"])) == ["positions request timed out"]


def test_the_raw_connection_has_the_same_guards() -> None:
    with pytest.raises(IBKRSyncError, match="positions request timed out"):
        connect_ib("127.0.0.1", 4002, 101, ib=FakeIB(sync_timeouts=MONDAY))
    ib = connect_ib("127.0.0.1", 4002, 101, request_timeout=120.0, ib=FakeIB())
    assert ib.connected and ib.RequestTimeout == 120.0 and ib.connect_kwargs["raiseSyncErrors"] is True


# ── no request waits forever, and one timeout doesn't become twenty ────────


def test_fail_fast_stops_asking_after_one_timeout() -> None:
    calls: list[str] = []

    def fetch(symbol: str) -> str:
        calls.append(symbol)
        if symbol == "SPY":
            raise TimeoutError()
        return symbol

    wrapped = fail_fast(fetch, 120.0)
    assert wrapped("BIL") == "BIL"
    with pytest.raises(TimeoutError):
        wrapped("SPY")
    with pytest.raises(TimeoutError, match="not asked: IBKR stopped answering"):
        wrapped("TLT")
    assert calls == ["BIL", "SPY"]


def test_the_bars_append_asks_once_then_gives_up(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(app, "MASTER_CSV", tmp_path / "etf_prices_master.csv")
    monkeypatch.setattr(app, "MASTER_PARQUET", tmp_path / "etf_prices_master.parquet")
    monkeypatch.setattr(app, "SNAPSHOTS_DIR", tmp_path / "snapshots")
    days = pd.bdate_range("2026-10-12", "2026-10-16")
    master = pd.DataFrame(
        [{"date": d, "symbol": s, "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 1e6}
         for s in ("BIL", "SPY", "TLT") for d in days]
    )  # fmt: skip
    fake = FakeIB()
    result = app.append_bars(app._fetcher(fake), lookback="5 D", master=master, today=datetime(2026, 10, 19))
    assert fake.history_calls == 1
    errors = " | ".join(result.get("errors") or [])
    assert errors.count("not asked: IBKR stopped answering") == 2, errors


# ── the reconcile on Monday's gateway ─────────────────────────────────────


def test_the_reconcile_fails_without_arming_or_wiping_the_book(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(kill_switch, "KILL_SWITCH_PATH", tmp_path / "artifacts" / "runs" / "kill_switch.json")
    held = pd.DataFrame(
        [{"symbol": s, "current_shares": q, "avg_cost": 100.0, "sec_type": "STK", "multiplier": 1.0, "market_price": 100.0}
         for s, q in (("SPY", 85), ("TLT", 500))]
    )  # fmt: skip
    path = eod.record_submission(
        "paper", run_id="20261016_003532", positions=held, orders=pd.DataFrame(columns=["symbol", "action", "quantity"]),
        execution={"fills_at": "2026-10-16T09:30:00-04:00"}, root=tmp_path,
    )  # fmt: skip
    before = json.loads(path.read_text())
    res = eod.reconcile_end_of_day(
        profile="paper", root=tmp_path, client=client_with(monkeypatch, FakeIB(sync_timeouts=MONDAY)),
        now=datetime(2026, 10, 19, 15, 35, tzinfo=clock.NYSE_TZ),
    )  # fmt: skip
    assert res.ok is False and res.material_mismatch is False and res.diffs == []
    assert "IBKR's servers didn't" in (res.error or "")
    assert not kill_switch.is_kill_switch_armed()
    assert json.loads(path.read_text()) == before  # the book still expects SPY and TLT
    assert not list((tmp_path / "data" / "broker" / "reconciliations").glob("paper_eod_*.json"))
