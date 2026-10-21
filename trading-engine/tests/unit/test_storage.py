"""The Postgres store for the trading record (src/storage), against a real
Postgres: DV02_TEST_DATABASE_URL names one (CI starts it; locally, any throwaway
database). Each test gets its own schema. Without the URL the database tests
skip; the rest still run."""
from __future__ import annotations

import csv
import json
import logging
import os
import sys
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

URL = os.environ.get("DV02_TEST_DATABASE_URL", "")

from src.storage import backfill as backfill_mod  # noqa: E402
from src.storage import capture as capture_mod  # noqa: E402
from src.storage import record  # noqa: E402
from src.storage.db import StoreConfig, connect, load_config  # noqa: E402
from src.storage.migrate import migrate  # noqa: E402

CDT = timezone(timedelta(hours=-5))


@pytest.fixture
def store():
    """A migrated store in a schema of its own, dropped afterwards."""
    if not URL:
        pytest.skip("DV02_TEST_DATABASE_URL isn't set: no Postgres to test against")
    import psycopg
    from psycopg.conninfo import make_conninfo

    schema = f"t_{uuid.uuid4().hex[:12]}"
    with psycopg.connect(URL, autocommit=True) as admin:
        admin.execute(f"CREATE SCHEMA {schema}")
    cfg = StoreConfig(True, make_conninfo(URL, options=f"-c search_path={schema}"), f"test/{schema}")
    conn = connect(cfg)
    migrate(conn)
    yield SimpleNamespace(conn=conn, cfg=cfg, schema=schema,
                          url=f"{URL}{'&' if '?' in URL else '?'}options=-csearch_path%3D{schema}")  # fmt: skip
    conn.close()
    with psycopg.connect(URL, autocommit=True) as admin:
        admin.execute(f"DROP SCHEMA {schema} CASCADE")


def rows(conn, sql: str, *args) -> list[tuple]:
    return conn.execute(sql, args).fetchall()


# ── config and migrations ─────────────────────────────────────────────────


def test_the_store_is_off_unless_switched_on_with_a_password() -> None:
    assert load_config(env={}).enabled is False
    off = load_config(env={"DV02_DB_ENABLED": "1"})
    assert not off.enabled and "no password" in off.off_because
    on = load_config(env={"DV02_DB_ENABLED": "1", "DV02_DB_PASSWORD": "pw", "DV02_DB_USER": "dv02"})
    assert on.enabled and on.where == "127.0.0.1:5432/trading" and "password=pw" in on.conninfo
    assert load_config(env={"DV02_DATABASE_URL": "postgresql://u:p@db.example:6543/x"}).where == "db.example:6543/x"


def test_a_files_time_says_which_zone_its_stamp_is_in() -> None:
    assert record.zone_from("20261016_003532", datetime(2026, 10, 16, 5, 36, 2, tzinfo=timezone.utc)) == CDT
    assert record.zone_from("20261020_193500", datetime(2026, 10, 20, 19, 35, 31, tzinfo=timezone.utc)) == timezone.utc
    # copied days later without its times: no zone is that far off
    assert record.zone_from("20261016_003532", datetime(2026, 10, 21, 7, 20, tzinfo=timezone.utc)) is None
    assert record.zone_from("not-a-run", datetime(2026, 10, 21, tzinfo=timezone.utc)) is None


def test_migrations_apply_once_and_record_themselves(store) -> None:
    assert migrate(store.conn) == []  # the fixture applied them
    done = [r[0] for r in rows(store.conn, "SELECT version FROM schema_migrations ORDER BY version")]
    assert done[0] == "001_market_data" and done[-1] == "007_record_keys" and len(done) == 7
    (qty_type,) = rows(store.conn, "SELECT data_type FROM information_schema.columns "
                                   "WHERE table_schema = %s AND table_name = 'positions' AND column_name = 'qty'",
                       store.schema)[0]  # fmt: skip
    assert qty_type == "double precision"


# ── the writers ───────────────────────────────────────────────────────────


def test_a_run_is_one_row_however_often_it_is_recorded(store) -> None:
    doc = {"run_id": "20261020_193500", "profile": "paper", "ok": False, "halted": "EOD reconciliation mismatch",
           "steps": {"submit_orders": {"blocked": True, "reasons": ["Kill switch is ARMED"]}}}  # fmt: skip
    record.record_run(store.conn, doc, started_at=datetime(2026, 10, 20, 19, 35, tzinfo=timezone.utc),
                      ended_at=datetime(2026, 10, 20, 19, 35, 32, tzinfo=timezone.utc))  # fmt: skip
    record.record_run(store.conn, {**doc, "note": "written again after the total-return step"},
                      started_at=datetime(2026, 10, 20, 19, 35, tzinfo=timezone.utc),
                      ended_at=datetime(2026, 10, 20, 19, 35, 40, tzinfo=timezone.utc))  # fmt: skip
    (row,) = rows(store.conn, "SELECT status, error, duration_ms, payload->>'note' FROM run_logs")
    assert row == ("halted", "kill switch armed: EOD reconciliation mismatch", 40_000,
                   "written again after the total-return step")  # fmt: skip


SENT = [
    {"timestamp": "2026-10-16T00:35:35", "symbol": "SPY", "action": "SELL", "order_type": "MKT", "tif": "OPG",
     "quantity": 100, "estimated_trade_dollars": 74_400.0, "status": "PreSubmitted", "order_id": 7, "perm_id": 912},
    {"timestamp": "2026-10-16T00:35:35", "symbol": "TLT", "action": "BUY", "order_type": "MKT", "tif": "OPG",
     "quantity": 400, "estimated_trade_dollars": 33_000.0, "status": "PreSubmitted", "order_id": 8, "perm_id": 913},
    {"timestamp": "2026-10-16T00:35:36", "symbol": "XLE", "action": "SELL", "order_type": "MKT", "tif": "OPG",
     "quantity": 675, "estimated_trade_dollars": 60_000.0, "status": "ERROR", "error": "no security definition"},
]  # fmt: skip


def test_orders_are_keyed_by_ibkrs_permid_else_the_batch(store) -> None:
    assert record.record_submission(store.conn, "paper", "20261016_003535", SENT, tz=CDT) == 3
    assert record.record_submission(store.conn, "paper", "20261016_003535", SENT, tz=CDT) == 3  # again: same rows
    got = rows(store.conn, "SELECT idempotency_key, status, qty, price, run_id, submitted_at FROM orders ORDER BY symbol")
    assert [g[0] for g in got] == ["paper:perm:912", "paper:perm:913", "paper:20261016_003535:XLE:SELL"]
    assert [g[1] for g in got] == ["submitted", "submitted", "error"]
    assert got[0][2:5] == (100, 744.0, 20261016003535)
    assert got[0][5] == datetime(2026, 10, 16, 5, 35, 35, tzinfo=timezone.utc)  # the Mac's Central time


def test_fills_find_their_orders_and_fill_them(store) -> None:
    record.record_submission(store.conn, "paper", "20261016_003535", SENT[:2], tz=CDT)
    fills = [
        {"exec_id": "e1", "perm_id": "912", "order_id": "7", "symbol": "SPY", "qty": -100, "price": 745.1,
         "side": "SLD", "ts": "2026-10-16T13:30:00+00:00"},
        {"exec_id": "e2", "perm_id": "913", "order_id": "8", "symbol": "TLT", "qty": 250, "price": 82.4,
         "side": "BOT", "ts": "2026-10-16T13:30:00+00:00"},
    ]  # fmt: skip
    assert record.record_fills(store.conn, "paper", fills) == 2
    assert record.record_fills(store.conn, "paper", fills) == 0  # again: nothing new
    status = dict(rows(store.conn, "SELECT symbol, status FROM orders"))
    assert status == {"SPY": "filled", "TLT": "partial"}
    linked = rows(store.conn, "SELECT f.exec_id, o.symbol FROM fills f JOIN orders o ON o.id = f.order_id ORDER BY 1")
    assert linked == [("e1", "SPY"), ("e2", "TLT")]
    # the rest of TLT fills; recording the submission again keeps the fills' status
    record.record_fills(store.conn, "paper", [{**fills[1], "exec_id": "e3", "qty": 150}])
    record.record_submission(store.conn, "paper", "20261016_003535", SENT[:2], tz=CDT)
    assert dict(rows(store.conn, "SELECT symbol, status FROM orders")) == {"SPY": "filled", "TLT": "filled"}


def test_positions_keep_fractional_shares_and_navs_keep_their_source(store) -> None:
    at = datetime(2026, 10, 20, 20, 30, tzinfo=timezone.utc)
    snap = [{"symbol": "ETHA", "current_shares": 986.3333, "avg_cost": 53.57, "market_price": 54.95, "multiplier": 1.0},
            {"symbol": "SPY", "current_shares": 85, "market_price": 768.4}, {"symbol": "ZERO", "current_shares": 0}]  # fmt: skip
    assert record.record_positions(store.conn, "paper", at, snap) == 2
    assert record.record_positions(store.conn, "paper", at, snap) == 0
    (etha,) = rows(store.conn, "SELECT qty, market_value FROM positions WHERE symbol = 'ETHA'")
    assert etha[0] == pytest.approx(986.3333) and etha[1] == pytest.approx(986.3333 * 54.95)

    record.record_nav(store.conn, "paper", date(2026, 10, 20), 1_048_000.0, source="api_close")
    record.record_nav(store.conn, "paper", date(2026, 10, 20), 1_048_144.09, source="api_close", cash=7_429.32)
    record.record_nav(store.conn, "paper", date(2026, 10, 20), 1_048_150.00, source="flex")
    got = rows(store.conn, "SELECT source, nav, cash FROM nav ORDER BY source")
    assert got == [("api_close", 1_048_144.09, 7_429.32), ("flex", 1_048_150.0, None)]


def test_best_effort_never_raises(store, caplog) -> None:
    assert record.best_effort("x", lambda conn: None, StoreConfig(False, "", "off", "off")) is False
    down = StoreConfig(True, "host=127.0.0.1 port=1 dbname=x user=x password=x connect_timeout=1", "127.0.0.1:1/x")
    with caplog.at_level(logging.WARNING):
        assert record.best_effort("the run log", lambda conn: None, down) is False
    assert "couldn't record the run log at 127.0.0.1:1/x" in caplog.text
    assert record.best_effort("y", lambda conn: record.record_nav(conn, "paper", date(2026, 10, 21), 1.0,
                                                                   source="t"), store.cfg) is True  # fmt: skip


# ── the daily run, the post-close capture and the backfill ─────────────────


def test_the_daily_runs_log_reaches_the_store(store, tmp_path, monkeypatch) -> None:
    from src.execution import kill_switch
    from src.production import daily_runner

    monkeypatch.setattr(daily_runner, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(kill_switch, "KILL_SWITCH_PATH", tmp_path / "kill_switch.json")
    monkeypatch.setenv("DV02_DATABASE_URL", store.url)
    daily_runner._write_run_log("20261021_193500", {"run_id": "20261021_193500", "profile": "paper", "ok": True,
                                                    "dry_run": False, "should_rebalance": False})  # fmt: skip
    assert rows(store.conn, "SELECT run_id, status FROM run_logs") == [("20261021_193500", "ok")]


def test_the_capture_saves_the_days_fills_positions_and_close(store) -> None:
    fill = SimpleNamespace(
        execution=SimpleNamespace(execId="e9", permId=777, orderId=3, shares=40, price=91.6, side="BOT",
                                  time=datetime(2026, 10, 21, 20, 0, tzinfo=timezone.utc)),
        contract=SimpleNamespace(symbol="BIL"),
    )  # fmt: skip
    position = SimpleNamespace(contract=SimpleNamespace(conId=1, symbol="BIL", secType="STK", multiplier="", localSymbol="BIL"),
                               position=603.0, avgCost=91.55)  # fmt: skip
    portfolio = SimpleNamespace(contract=position.contract, marketPrice=91.62)
    client = SimpleNamespace(
        connect=lambda: None, disconnect=lambda: None, positions=lambda: [position],
        account_summary=lambda: {"NetLiquidation": "1048900.10", "TotalCashValue": "3810.55"},
        ib=SimpleNamespace(fills=lambda: [fill], portfolio=lambda: [portfolio]),
    )  # fmt: skip
    out = capture_mod.capture(client=client, cfg=store.cfg, now=datetime(2026, 10, 21, 20, 30, tzinfo=timezone.utc))
    assert out == {"ok": True, "day": "2026-10-21", "fills": 1, "fills_added": 1, "positions": 1,
                   "nav": 1_048_900.10, "where": store.cfg.where}  # fmt: skip
    assert rows(store.conn, "SELECT exec_id, broker_perm_id, qty FROM fills") == [("e9", 777, 40.0)]
    assert rows(store.conn, "SELECT nav_date, nav, cash, source FROM nav") == [
        (date(2026, 10, 21), 1_048_900.10, 3_810.55, "ibkr_close")]  # fmt: skip
    # no NAV for a session that hasn't closed: a catch-up the next morning, or Thanksgiving
    for now in (datetime(2026, 10, 22, 12, 0, tzinfo=timezone.utc), datetime(2026, 11, 26, 21, 30, tzinfo=timezone.utc)):
        out = capture_mod.capture(client=client, cfg=store.cfg, now=now)
        assert out["nav"] is None and out["positions"] == 1
    assert len(rows(store.conn, "SELECT * FROM nav")) == 1


def test_the_backfill_loads_the_files_once(store, tmp_path) -> None:
    runs = tmp_path / "artifacts" / "runs"
    runs.mkdir(parents=True)
    mac = runs / "daily_run_20261016_003532.json"  # written on the Mac, in Central time
    mac.write_text(json.dumps({"run_id": "20261016_003532", "profile": "paper", "ok": True, "dry_run": False}))
    os.utime(mac, (datetime(2026, 10, 16, 5, 36, 2, tzinfo=timezone.utc).timestamp(),) * 2)
    vm = runs / "daily_run_20261020_193500.json"  # on the VM, in UTC
    vm.write_text(json.dumps({"run_id": "20261020_193500", "profile": "paper", "ok": False, "halted": "EOD mismatch"}))
    os.utime(vm, (datetime(2026, 10, 20, 19, 35, 32, tzinfo=timezone.utc).timestamp(),) * 2)
    copied = runs / "daily_run_20261012_182314.json"  # copied without its times: the zone is a guess
    copied.write_text(json.dumps({"run_id": "20261012_182314", "profile": "paper", "ok": True}))

    orders = tmp_path / "data" / "broker" / "orders"
    orders.mkdir(parents=True)
    sent = orders / "paper_orders_submitted_20261016_003535.csv"
    with sent.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(SENT[0]) + ["error"])
        w.writeheader()
        w.writerows(SENT[:2])
    os.utime(sent, (datetime(2026, 10, 16, 5, 35, 59, tzinfo=timezone.utc).timestamp(),) * 2)
    pd.DataFrame([{"symbol": "SPY", "action": "SELL", "quantity": 10, "status": "DRY_RUN"}]).to_csv(
        orders / "paper_orders_submitted_20261013_201100.csv", index=False)  # fmt: skip

    recon = tmp_path / "data" / "broker" / "reconciliations"
    recon.mkdir(parents=True)
    (recon / "paper_eod_2026-10-16.json").write_text(json.dumps(
        {"generated_at": "2026-10-16T22:42:03+00:00", "actual": {"SPY": 85, "ETHA": 986.3333},
         "orders": [{"perm_id": "912", "quantity": -100.0, "filled": -100.0, "price": 745.1},
                    {"perm_id": "913", "quantity": 400.0, "filled": 250.0, "price": 82.4},
                    {"perm_id": "", "quantity": 675.0, "filled": 0.0}]}))  # fmt: skip
    api = tmp_path / "artifacts" / "api"
    api.mkdir(parents=True)
    (api / "flex_nav_paper.csv").write_text("date,nav\n2026-10-15,1046000.00\n2026-10-16,1044717.06\n")
    (api / "nav_paper.csv").write_text("date,nav\n2026-10-16,1044717.06\n")

    first = backfill_mod.backfill(store.conn, root=tmp_path)
    assert first == {"runs": 3, "orders": 2, "orders_filled": 2, "positions": 2, "nav": 3}
    again = backfill_mod.backfill(store.conn, root=tmp_path)
    assert again["positions"] == 0 and again["orders_filled"] == 1  # nothing new; TLT is still partial
    counts = {t: rows(store.conn, f"SELECT count(*) FROM {t}")[0][0] for t in ("run_logs", "orders", "positions", "nav")}
    assert counts == {"run_logs": 3, "orders": 2, "positions": 2, "nav": 3}
    runs_ = {r[0]: r[1:] for r in rows(store.conn, "SELECT run_id, started_at, ended_at, duration_ms FROM run_logs")}
    assert runs_["20261016_003532"] == (datetime(2026, 10, 16, 5, 35, 32, tzinfo=timezone.utc),
                                        datetime(2026, 10, 16, 5, 36, 2, tzinfo=timezone.utc), 30_000)  # fmt: skip
    assert runs_["20261020_193500"][0] == datetime(2026, 10, 20, 19, 35, tzinfo=timezone.utc)
    assert runs_["20261012_182314"] == (datetime(2026, 10, 12, 18, 23, 14, tzinfo=timezone.utc), None, None)
    sent_at = {r[0] for r in rows(store.conn, "SELECT submitted_at FROM orders")}
    assert sent_at == {datetime(2026, 10, 16, 5, 35, 35, tzinfo=timezone.utc)}  # the Mac's Central time
    # the evening reconcile saw the fills IBKR's API no longer shows: filled, partial, no fills rows
    got = rows(store.conn, "SELECT symbol, status, (payload->'reconciled'->>'price')::float FROM orders ORDER BY 1")
    assert got == [("SPY", "filled", 745.1), ("TLT", "partial", 82.4)]
    assert rows(store.conn, "SELECT count(*) FROM fills") == [(0,)]
