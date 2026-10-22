"""The engine's co-sign (config/engine.yaml, src/production/engine_cosign.py): in
shadow the engine's comparisons are logged and recorded only; in cosign a run
sends nothing unless they all matched. The committed role is shadow."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.production import engine_cosign  # noqa: E402

SAME = {"kind": "basket", "match": True, "summary": "same 9 orders"}


def write_role(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "engine.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_the_committed_role_is_shadow() -> None:
    # moving it to cosign is the owner's call, after 10 sessions in a row match
    assert engine_cosign.load() == {"role": "shadow"}


def test_only_shadow_and_cosign_are_read_and_anything_else_is_cosign(tmp_path: Path) -> None:
    assert engine_cosign.load(write_role(tmp_path, "engine:\n  role: cosign\n")) == {"role": "cosign"}
    assert engine_cosign.load(write_role(tmp_path, "engine:\n  role: shadow\n")) == {"role": "shadow"}
    for text in ("engine:\n  role: off\n", "engine:\n  role: Shadow\n", "engine: {}\n", "", "role: shadow\n"):
        out = engine_cosign.load(write_role(tmp_path, text))
        assert out["role"] == "cosign" and "neither shadow nor cosign" in out["note"], text
    out = engine_cosign.load(tmp_path / "missing.yaml")
    assert out["role"] == "cosign" and "could not be read" in out["note"]
    out = engine_cosign.load(write_role(tmp_path, "engine: [role: shadow\n"))
    assert out["role"] == "cosign" and "could not be read" in out["note"]


def test_the_verdict_needs_the_basket_and_every_comparison_the_run_made_to_match() -> None:
    drift = {"kind": "drift", "match": True, "summary": "same decision"}
    reconcile = {"kind": "reconcile", "match": True, "summary": "same 9 order counts, 0 breaks"}
    out = engine_cosign.verdict({"reconcile": reconcile, "drift": drift, "basket": SAME})
    assert out == {"ok": True, "reasons": [],
                   "checked": {"reconcile": reconcile["summary"], "drift": "same decision", "basket": "same 9 orders"}}  # fmt: skip

    # a run with no expected book yet, on the monthly cadence: only the basket
    assert engine_cosign.verdict({"reconcile": None, "drift": None, "basket": SAME})["ok"] is True

    out = engine_cosign.verdict({"drift": drift})
    assert out["ok"] is False and out["reasons"] == ["the engine made no basket comparison this run, so nothing to co-sign"]

    differs = {"kind": "drift", "match": False, "summary": "1 difference, the first: max drift"}
    out = engine_cosign.verdict({"drift": differs, "basket": SAME})
    assert out["ok"] is False
    assert out["reasons"] == ["the C++ engine's drift differs from Python's: 1 difference, the first: max drift"]

    # a comparison that couldn't run, or wasn't recorded, isn't a match
    for basket in ({"match": None, "summary": "not compared: this config records no shadow"},
                   {"match": False, "summary": "the shadow failed: ImportError: no dv02_engine"}):  # fmt: skip
        assert engine_cosign.verdict({"basket": basket})["ok"] is False


# ── the daily run's submit step ───────────────────────────────────────────

pytest.importorskip("dv02_engine.risk")

from src.production import daily_runner  # noqa: E402
from src.production.runtime import order_service  # noqa: E402
from src.execution import kill_switch  # noqa: E402
from src.production.runtime import submit_paper_orders as spo  # noqa: E402
from tests.unit.test_leverage import MODEL_BOOK, write_run  # noqa: E402
from tests.unit.test_runner_reconcile_wiring import outcome  # noqa: E402

DIFFERS = {"kind": "basket", "match": False, "summary": "1 difference, the first: orders, in sending order"}


@pytest.fixture
def clean_run(tmp_path, monkeypatch) -> Path:
    """A run's files for an unlevered book with one buy, and nothing armed (as
    test_runner_reconcile_wiring's), so the pre-trade gate passes."""
    root = tmp_path / "run"
    monkeypatch.setattr(daily_runner, "PROJECT_ROOT", root)
    monkeypatch.setattr(spo, "_project_root", lambda: root)
    monkeypatch.setattr(kill_switch, "KILL_SWITCH_PATH", root / "artifacts" / "runs" / "kill_switch.json")
    write_run(root, MODEL_BOOK, [("TLT", 400)], cash=100_000)
    return root


def sent(calls: list) -> object:
    def fake_submit(**kw):
        calls.append(kw)
        return {"ok": True, "submitted": False, "submission_path": None, "result": {}}

    return fake_submit


def test_at_cosign_a_run_the_engine_decided_differently_sends_nothing(clean_run: Path, monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(engine_cosign, "ENGINE_YAML", write_role(tmp_path, "engine:\n  role: cosign\n"))
    monkeypatch.setattr(order_service, "submit_paper_orders", lambda **kw: pytest.fail("nothing may be sent"))
    out = daily_runner.step_submit_orders("127.0.0.1", 4002, 300, "paper", reconciled=True,
                                          engine={"drift": {"match": True, "summary": "same decision"}, "basket": DIFFERS})  # fmt: skip
    assert out["ok"] is False and out["blocked"] is True
    assert out["reasons"] == ["the C++ engine's basket differs from Python's: 1 difference, the first: orders, in sending order"]
    assert out["engine_cosign"]["role"] == "cosign" and out["engine_cosign"]["blocks"] is True

    # no comparisons at all: nothing to co-sign, so nothing goes
    out = daily_runner.step_submit_orders("127.0.0.1", 4002, 300, "paper", reconciled=True, engine=None)
    assert out["blocked"] is True and "no basket comparison" in out["reasons"][0]

    # a dry run reports the gate, as before, and sends nothing either way
    out = daily_runner.step_submit_orders("127.0.0.1", 4002, 300, "paper", dry_run=True, engine={"basket": DIFFERS})
    assert out == {"ok": True, "action": "submit_orders", "skipped": True, "reason": "dry_run"}


def test_at_cosign_a_run_the_engine_agrees_with_sends(clean_run: Path, monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(engine_cosign, "ENGINE_YAML", write_role(tmp_path, "engine:\n  role: cosign\n"))
    calls: list = []
    monkeypatch.setattr(order_service, "submit_paper_orders", sent(calls))
    out = daily_runner.step_submit_orders("127.0.0.1", 4002, 300, "paper", reconciled=True, engine={"basket": SAME})
    assert len(calls) == 1 and out["ok"] is True
    assert out["engine_cosign"]["ok"] is True and out["engine_cosign"]["blocks"] is False


def test_in_shadow_a_difference_is_recorded_and_python_still_sends(clean_run: Path, monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(engine_cosign, "ENGINE_YAML", write_role(tmp_path, "engine:\n  role: shadow\n"))
    calls: list = []
    monkeypatch.setattr(order_service, "submit_paper_orders", sent(calls))
    out = daily_runner.step_submit_orders("127.0.0.1", 4002, 300, "paper", reconciled=True, engine={"basket": DIFFERS})
    assert len(calls) == 1 and out["ok"] is True
    assert out["engine_cosign"]["role"] == "shadow" and out["engine_cosign"]["ok"] is False
    assert out["engine_cosign"]["blocks"] is False


def test_an_unreadable_role_co_signs(clean_run: Path, monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(engine_cosign, "ENGINE_YAML", tmp_path / "missing.yaml")
    monkeypatch.setattr(order_service, "submit_paper_orders", lambda **kw: pytest.fail("nothing may be sent"))
    out = daily_runner.step_submit_orders("127.0.0.1", 4002, 300, "paper", reconciled=True, engine={"basket": DIFFERS})
    assert out["blocked"] is True and out["engine_cosign"]["role"] == "cosign"
    assert "could not be read" in out["engine_cosign"]["note"]


def test_the_reconcile_step_hands_its_engine_comparison_to_the_run(monkeypatch) -> None:
    from src.broker.ibkr import settings
    from src.runtime import end_of_day_reconcile as eod

    monkeypatch.setattr(settings, "make_client", lambda *a, **k: object())
    shadow = {"kind": "reconcile", "match": True, "summary": "same 9 order counts, 0 breaks"}
    monkeypatch.setattr(eod, "reconcile_end_of_day", lambda **kw: outcome(engine=shadow))
    assert daily_runner.step_reconcile("127.0.0.1", 4002, "paper")["engine"] == shadow
    monkeypatch.setattr(eod, "reconcile_end_of_day", lambda **kw: outcome(skipped="no expected book yet"))
    assert "engine" not in daily_runner.step_reconcile("127.0.0.1", 4002, "paper")


def test_the_run_hands_the_submit_step_all_three_comparisons(tmp_path, monkeypatch) -> None:
    from datetime import date

    from tests.unit.test_execution_config import ALLOWED

    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "execution.yaml").write_text(ALLOWED)
    monkeypatch.setattr(daily_runner, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(daily_runner, "_today", lambda: date(2026, 11, 25))
    reconcile = {"kind": "reconcile", "match": True, "summary": "same 9 order counts, 0 breaks"}
    drift = {"kind": "drift", "match": True, "summary": "same decision"}
    ok = lambda **_: {"ok": True}  # noqa: E731
    for step in ("step_append_daily", "step_append_indices_daily", "step_append_futures_daily", "step_generate_targets"):
        monkeypatch.setattr(daily_runner, step, ok)
    monkeypatch.setattr(daily_runner, "step_reconcile", lambda **_: {"ok": True, "engine": reconcile})
    monkeypatch.setattr(daily_runner, "step_build_basket", lambda **_: {"ok": True, "engine_shadow": SAME})
    monkeypatch.setattr(daily_runner, "_load_rebalance_config", lambda: dict(daily_runner.DEFAULT_REBALANCE_CONFIG))
    monkeypatch.setattr(daily_runner, "_load_latest_targets", lambda: None)
    monkeypatch.setattr(daily_runner, "_current_positions", lambda *a, **k: (None, None, "none"))
    monkeypatch.setattr(daily_runner, "_holdings_outside_model", lambda *a, **k: None)
    monkeypatch.setattr(daily_runner, "_needs_rebalance", lambda *a, **k: (True, {"reason": "test", "engine": drift}))
    monkeypatch.setattr(daily_runner, "_engine_shadow", lambda kind, **kw: {"kind": kind, "match": True, "summary": "stub"})
    submits: list[dict] = []
    monkeypatch.setattr(daily_runner, "step_submit_orders", lambda **kw: submits.append(kw) or {"ok": True})
    daily_runner.run()
    assert [s["engine"] for s in submits] == [{"reconcile": reconcile, "drift": drift, "basket": SAME}]
