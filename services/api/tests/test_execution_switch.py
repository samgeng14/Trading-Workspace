"""config/execution.yaml's submit switches on the dashboard: the rebalance
preview shows them (its Submit starts a daily run, which obeys them), and a run
they turned into a dry run says so in the Runs view and the schedule."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from dv02_api import runs

from .test_mandate import checks_of, client_for, unlevered


def _switch_off(engine_tree) -> None:
    path = engine_tree / "config" / "execution.yaml"
    path.write_text(path.read_text().replace("allow_submit: true", "allow_submit: false"))


def test_the_committed_switches_let_a_rebalance_go(engine_tree, targets_day, started) -> None:  # noqa: ARG001
    with client_for(unlevered()) as c:
        body = c.get("/api/v1/trading/rebalance/preview").json()
    check = checks_of(body)["execution"]
    assert check["status"] == "pass" and "send paper orders" in check["detail"]
    assert body["can_submit"] is True


def test_switched_off_the_preview_blocks_submit_and_says_why(engine_tree, targets_day, started) -> None:  # noqa: ARG001
    _switch_off(engine_tree)
    with client_for(unlevered()) as c:
        body = c.get("/api/v1/trading/rebalance/preview").json()
        check = checks_of(body)["execution"]
        assert check["status"] == "fail"
        assert check["detail"] == "config/execution.yaml has allow_submit: false: a run would be a dry run"
        assert body["can_submit"] is False
        res = c.post("/api/v1/trading/rebalance", json={"confirm": "SUBMIT"})
        assert res.status_code == 409 and "allow_submit: false" in res.json()["detail"]
    assert started == []


def test_a_missing_file_counts_as_off(engine_tree, targets_day, started) -> None:  # noqa: ARG001
    (engine_tree / "config" / "execution.yaml").unlink()
    with client_for(unlevered()) as c:
        check = checks_of(c.get("/api/v1/trading/rebalance/preview").json())["execution"]
    assert check["status"] == "fail" and "could not be read" in check["detail"]


def test_a_run_the_switches_made_a_dry_run_is_the_days_run(engine_tree) -> None:
    folder = engine_tree / "artifacts" / "runs"
    folder.mkdir(parents=True, exist_ok=True)
    doc = {
        "run_id": "20261015_153501",
        "date": "2026-10-15",
        "ok": True,
        "dry_run": True,
        "submit_disabled": "config/execution.yaml has allow_submit: false",
        "should_rebalance": True,
        "steps": {"submit_orders": {"ok": True, "skipped": True, "reason": "dry_run"}},
    }
    (folder / "daily_run_20261015_153501.json").write_text(json.dumps(doc))
    run = next(r for r in runs.load_runs() if r["id"] == "daily-20261015_153501")
    assert run["summary"] == (
        "Dry run: basket built, nothing sent; orders are switched off "
        "(config/execution.yaml has allow_submit: false)"
    )
    # 16:00 ET that Thursday: the scheduled run happened, as a dry run; not "no run recorded"
    (job,) = runs.schedule(datetime(2026, 10, 15, 20, 0, tzinfo=timezone.utc))
    assert job["status"] == "done" and "orders are switched off" in job["detail"]


def test_the_runs_view_says_what_the_signals_ran_on() -> None:
    targets = {"ok": True, "optimizer": "momentum_tilted", "latest_rebalance_date": "2026-10-15",
               "signal_prices": "total_return"}  # fmt: skip
    assert runs._step_detail("generate_targets", targets) == (
        True,
        "momentum_tilted on total-return prices, rebalance date 2026-10-15",
    )
    refreshed = {"ok": False, "refreshed": ["BIL", "SPY"], "errors": {"VIXY": "no data"}}
    assert runs.STEP_LABELS["total_return_closes"] == "Total-return closes"
    assert runs._step_detail("total_return_closes", refreshed) == (
        False,
        "2 ETFs refreshed for the next run's signals; not refreshed: VIXY",
    )
