"""GET /ops/exit-test: Phase 0's exit test as the engine scores it
(trading-engine/src/runtime/exit_test.py), over the API's engine tree."""

from __future__ import annotations

import csv
import json

from fastapi.testclient import TestClient

from dv02_api import engine
from dv02_api.app import create_app


def test_a_session_with_all_its_evidence_passes_and_today_waits(engine_tree, fake_broker, frozen_now) -> None:
    artifacts = engine.artifacts_dir()
    day = "2026-10-08"  # the Thursday before the frozen Friday
    (artifacts / "runs").mkdir(parents=True, exist_ok=True)
    run = {"run_id": "20261008_193500", "ok": True, "steps": {"reconcile": {"ok": True, "diffs": []}}}
    (artifacts / "runs" / "daily_run_20261008_193500.json").write_text(json.dumps(run))
    (artifacts / "doctor").mkdir(parents=True, exist_ok=True)
    doctor = {"ran_at": f"{day}T19:15:00+00:00", "results": [{"name": "quotes", "status": "WARN"}]}
    (artifacts / "doctor" / f"doctor_paper_{day}.json").write_text(json.dumps(doctor))
    with (artifacts / "api" / "nav_paper.csv").open() as f:
        close = next(float(r["nav"]) for r in csv.DictReader(f) if r["date"] == day)
    (artifacts / "api" / "flex_nav_paper.csv").write_text(f"date,nav\n{day},{close + 50}\n")

    app = create_app("paper", broker=fake_broker, background=False)
    with TestClient(app) as client:
        body = client.get("/api/v1/ops/exit-test").json()
    sessions = {s["day"]: s for s in body["sessions"]}
    assert body["needed"] == 10 and body["nav_tolerance"] == 0.001 and len(sessions) == 15
    assert sessions[day]["status"] == "PASS"
    assert sessions[day]["checks"]["nav"]["note"].startswith("close $")
    assert sessions["2026-10-09"]["status"] == "PENDING"  # today: the run is still ahead
    assert body["streak"] == 1  # 10-07 had no run
