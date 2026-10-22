"""deploy/bin/set-heartbeats saves each job's ping URL in the env file and pings it
once; deploy/bin/heartbeat sends a unit's ping, or its /fail when it failed."""
from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
ENV = """DV02_ALLOWED_HOSTS=dv02.example.com
#DV02_HEARTBEAT_RUNNER=https://hc-ping.com/<uuid-for-the-daily-run>
DV02_HEARTBEAT_API=https://hc-ping.com/api-old
DV02_DB_USER=dv02
"""


def fake_curl(tmp_path: Path) -> tuple[dict[str, str], Path]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "pings"
    curl = bin_dir / "curl"
    curl.write_text(f'#!/usr/bin/env bash\necho "${{@: -1}}" >> {log}\n')
    curl.chmod(0o755)
    return {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}"}, log


def test_each_url_is_saved_and_pinged(tmp_path) -> None:
    env_file = tmp_path / "dv02.env"
    env_file.write_text(ENV)
    env_file.chmod(0o640)
    env, pings = fake_curl(tmp_path)
    answers = "https://hc-ping.com/run-1\n\nnot a url\n" + "\n" * 4  # runner; keep the API; a typo for the doctor
    out = subprocess.run(["bash", str(REPO / "deploy" / "bin" / "set-heartbeats")], input=answers, text=True,
                         capture_output=True, env={**env, "DV02_ENV_FILE": str(env_file)}, timeout=30)  # fmt: skip
    assert out.returncode == 0, out.stderr
    text = env_file.read_text()
    assert "DV02_HEARTBEAT_RUNNER=https://hc-ping.com/run-1\n" in text and "#DV02_HEARTBEAT_RUNNER" not in text
    assert "DV02_HEARTBEAT_API=https://hc-ping.com/api-old\n" in text and "DV02_HEARTBEAT_DOCTOR" not in text
    assert "that isn't an https:// URL: not saved" in out.stdout
    assert pings.read_text().split() == ["https://hc-ping.com/run-1"]
    assert stat.S_IMODE(env_file.stat().st_mode) == 0o640  # rewritten in place


def test_a_failed_unit_pings_its_checks_fail_url(tmp_path) -> None:
    env, pings = fake_curl(tmp_path)
    env = {**env, "DV02_HEARTBEAT_API": "https://hc-ping.com/api-1", "DV02_HEARTBEAT_RUNNER": "https://hc-ping.com/run-1"}
    heartbeat = str(REPO / "deploy" / "bin" / "heartbeat")
    subprocess.run(["bash", heartbeat, "dv02-api-check.service", "fail"], env=env, check=True, timeout=30)
    subprocess.run(["bash", heartbeat, "runner"], env=env, check=True, timeout=30)
    assert pings.read_text().split() == ["https://hc-ping.com/api-1/fail", "https://hc-ping.com/run-1"]
