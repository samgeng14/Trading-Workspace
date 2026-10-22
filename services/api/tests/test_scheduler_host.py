"""The schedule follows the host's scheduler: launchd on the Mac, the
dv02-runner.timer on the VM (deploy/), and says which to check or set up."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from fastapi.testclient import TestClient

from dv02_api import runs
from dv02_api.app import create_app

from .conftest import FakeBroker


def _vm(monkeypatch, tmp_path, *, timer_file: bool, enabled: bool) -> list[list[str]]:
    monkeypatch.setattr(runs.platform, "system", lambda: "Linux")
    monkeypatch.setattr(runs.shutil, "which", lambda name: "/usr/bin/systemctl" if name == "systemctl" else None)
    monkeypatch.setattr(runs, "SYSTEMD_UNITS", tmp_path)
    if timer_file:
        (tmp_path / runs.RUNNER_TIMER).write_text("[Timer]\n")
    calls: list[list[str]] = []

    def run(cmd, **_):
        calls.append(cmd)
        return SimpleNamespace(returncode=0 if enabled else 1)

    monkeypatch.setattr(runs.subprocess, "run", run)
    return calls


def test_the_vm_with_the_timer_installed_is_systemd(monkeypatch, tmp_path) -> None:
    calls = _vm(monkeypatch, tmp_path, timer_file=True, enabled=True)
    assert runs.scheduler() == "systemd" and runs.schedule_installed() is True
    assert calls == [["systemctl", "is-enabled", "--quiet", "dv02-runner.timer"]]


def test_before_deploy_bin_install_no_scheduler_is_known(monkeypatch, tmp_path) -> None:
    calls = _vm(monkeypatch, tmp_path, timer_file=False, enabled=True)
    assert runs.scheduler() is None and runs.schedule_installed() is None and calls == []


def test_the_mac_asks_launchd(monkeypatch) -> None:
    monkeypatch.setattr(runs.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(runs, "launch_agent_installed", lambda: False)
    assert runs.scheduler() == "launchd" and runs.schedule_installed() is False


def test_a_missed_run_on_the_vm_names_the_timer(monkeypatch, tmp_path, engine_tree) -> None:  # noqa: ARG001
    _vm(monkeypatch, tmp_path, timer_file=True, enabled=True)
    (job,) = runs.schedule(datetime(2026, 10, 15, 20, 30, tzinfo=timezone.utc))  # 16:30 ET, no run logged
    assert job["status"] == "failed"
    assert job["detail"] == (
        "no run recorded yet today; is dv02-runner.timer enabled? (systemctl list-timers dv02-runner.timer)"
    )


def test_the_vm_status_says_how_to_turn_the_timer_on(monkeypatch, tmp_path, engine_tree) -> None:  # noqa: ARG001
    _vm(monkeypatch, tmp_path, timer_file=True, enabled=False)
    with TestClient(create_app("paper", broker=FakeBroker(), background=False)) as c:
        comp = {x["id"]: x for x in c.get("/api/v1/status").json()["components"]}["scheduler"]
    assert comp["status"] == "warning"
    assert comp["detail"] == "Not scheduled: sudo systemctl enable --now dv02-runner.timer (deploy/README.md)"
