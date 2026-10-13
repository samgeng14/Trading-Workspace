"""The daily run reconciles earlier orders first, sends nothing when that reconcile
could not run, and records what it sends in the expected book."""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

pytest.importorskip("dv02_engine.risk")

from src.broker.ibkr import settings  # noqa: E402
from src.execution import kill_switch  # noqa: E402
from src.production import daily_runner  # noqa: E402
from src.production.runtime import order_service  # noqa: E402
from src.production.runtime import submit_paper_orders as spo  # noqa: E402
from src.runtime import end_of_day_reconcile as eod  # noqa: E402
from tests.unit.test_expected_book import MOC  # noqa: E402
from tests.unit.test_leverage import MODEL_BOOK, write_run  # noqa: E402


def outcome(**kw) -> eod.EodReconcileResult:
    base = {"ok": True, "profile": "paper", "as_of": "2026-10-13", "diffs": [], "material_mismatch": False, "report_path": None}
    return eod.EodReconcileResult(**{**base, **kw})


def test_step_reconcile_reports_each_outcome(monkeypatch) -> None:
    seen = []
    monkeypatch.setattr(settings, "make_client", lambda profile, role, **kw: seen.append((profile, role, kw)) or object())
    results = iter(
        [
            outcome(skipped="no expected book yet: the first submission records one"),
            outcome(diffs=[{"symbol": "TLT"}], material_mismatch=True, report_path="/r/paper_eod_2026-10-13.json"),
            outcome(ok=False, error="IBKR connect failed at 127.0.0.1:4002: refused"),
        ]
    )
    monkeypatch.setattr(eod, "reconcile_end_of_day", lambda **kw: next(results))

    out = daily_runner.step_reconcile("127.0.0.1", 4002, "paper")
    assert out["ok"] is True and out["skipped"] is True and "no expected book" in out["reason"]
    assert seen[-1] == ("paper", "reconcile", {"host": "127.0.0.1", "port": 4002, "readonly": True})

    out = daily_runner.step_reconcile("127.0.0.1", 4002, "paper")
    assert out["ok"] is True and out["material_mismatch"] is True and out["diffs"] == [{"symbol": "TLT"}]

    out = daily_runner.step_reconcile("127.0.0.1", 4002, "paper")
    assert out["ok"] is False and "IBKR connect failed" in out["error"]


def test_a_reconcile_that_raises_is_a_failed_step(monkeypatch) -> None:
    monkeypatch.setattr(settings, "make_client", lambda *a, **k: object())

    def boom(**kw):
        raise RuntimeError("book on fire")

    monkeypatch.setattr(eod, "reconcile_end_of_day", boom)
    out = daily_runner.step_reconcile("127.0.0.1", 4002, "paper")
    assert out["ok"] is False and "RuntimeError: book on fire" in out["error"]


@pytest.fixture
def clean_run(tmp_path, monkeypatch) -> Path:
    """A run's files for an unlevered book with one buy, and nothing armed."""
    monkeypatch.setattr(daily_runner, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(spo, "_project_root", lambda: tmp_path)
    monkeypatch.setattr(kill_switch, "KILL_SWITCH_PATH", tmp_path / "artifacts" / "runs" / "kill_switch.json")
    write_run(tmp_path, MODEL_BOOK, [("TLT", 400)], cash=100_000)
    return tmp_path


def test_a_failed_reconcile_blocks_the_submit_step(clean_run: Path, monkeypatch) -> None:
    monkeypatch.setattr(order_service, "submit_paper_orders", lambda **kw: pytest.fail("nothing may be sent"))
    out = daily_runner.step_submit_orders("127.0.0.1", 4002, 300, "paper", dry_run=False, reconciled=False)
    assert out["ok"] is False and out["blocked"] is True
    assert "reconcile did not pass" in out["reasons"][0]
    # a dry run still reports what the gate would do
    out = daily_runner.step_submit_orders("127.0.0.1", 4002, 300, "paper", dry_run=True, reconciled=False)
    assert out == {"ok": True, "action": "submit_orders", "skipped": True, "reason": "dry_run"}


def test_a_real_submission_is_recorded_in_the_expected_book(clean_run: Path, monkeypatch) -> None:
    submission = clean_run / "data" / "broker" / "orders" / "paper_orders_submitted_20261013_153601.csv"

    def fake_submit(**kw):
        pd.DataFrame(
            [{"timestamp": "2026-10-13T15:36:01", "symbol": "TLT", "action": "BUY", "order_type": "MOC", "tif": "DAY",
              "quantity": 400, "delta_shares": 400, "estimated_trade_dollars": 40_000.0, "status": "Submitted",
              "order_id": 8, "perm_id": 11}]
        ).to_csv(submission, index=False)  # fmt: skip
        return {"ok": True, "submitted": True, "submission_path": str(submission),
                "result": {"submitted": True, "rows": 1, "execution": MOC}}  # fmt: skip

    monkeypatch.setattr(order_service, "submit_paper_orders", fake_submit)
    out = daily_runner.step_submit_orders(
        "127.0.0.1", 4002, 300, "paper", dry_run=False, run_id="20261013_153500", reconciled=True
    )
    assert out["ok"] is True and out["recorded"]["ok"] is True
    book = eod.load_expected_book("paper", clean_run)
    assert book["positions"]["TLT"] == 1_800.0 and book["positions_from"] == "the positions snapshot of run 20261013_153500"
    assert [(o["symbol"], o["quantity"], o["perm_id"], o["fills_at"]) for o in book["orders"]] == [
        ("TLT", 400.0, "11", MOC["fills_at"])
    ]


def test_a_submission_that_cannot_be_recorded_says_so(clean_run: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        order_service, "submit_paper_orders",
        lambda **kw: {"ok": True, "submitted": True, "submission_path": str(clean_run / "missing.csv"), "result": {}},
    )  # fmt: skip
    out = daily_runner.step_submit_orders("127.0.0.1", 4002, 300, "paper", dry_run=False, run_id="r", reconciled=True)
    assert out["ok"] is True and out["recorded"]["ok"] is False and "missing.csv" in out["recorded"]["error"]
    assert eod.load_expected_book("paper", clean_run) is None
