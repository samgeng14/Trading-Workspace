"""deploy/bin/gateway-ready restarts IB Gateway once before the daily run when it
isn't logged in to IBKR, and src.broker.ibkr.gateway_check says whether it is.
On 2026-10-21 the VM's gateway sat on IBKR's "Unrecognized Username or Password"
dialog from 00:16 ET until a restart logged it straight back in."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
REPO = PROJECT_ROOT.parent
SCRIPT = REPO / "deploy" / "bin" / "gateway-ready"
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.broker.ibkr import gateway_check  # noqa: E402
from src.broker.ibkr.client import IBKRSyncError  # noqa: E402


class FakeClient:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.config = SimpleNamespace(host="127.0.0.1", port=4002)
        self.disconnected = False

    def connect(self) -> None:
        if self.error:
            raise self.error

    def managed_accounts(self) -> list[str]:
        return ["DUP317554"]

    def disconnect(self) -> None:
        self.disconnected = True


def test_the_check_says_ready_cut_off_or_no_gateway() -> None:
    ok = FakeClient()
    assert gateway_check.check(client=ok) == (0, "IB Gateway at 127.0.0.1:4002 is logged in to IBKR (DUP317554)")
    assert ok.disconnected
    code, line = gateway_check.check(client=FakeClient(IBKRSyncError("IB Gateway answered at 127.0.0.1:4002, but IBKR's servers didn't")))
    assert code == gateway_check.CUT_OFF and "IBKR's servers didn't" in line
    code, line = gateway_check.check(client=FakeClient(ConnectionRefusedError(61, "Connection refused")))
    assert code == gateway_check.NO_GATEWAY and "no usable IB Gateway at 127.0.0.1:4002" in line


def test_the_check_uses_its_own_client_id() -> None:
    from src.broker.ibkr import settings

    assert settings.ROLE_OFFSETS["gateway_check"] == 13
    assert len(set(settings.ROLE_OFFSETS.values())) == len(settings.ROLE_OFFSETS)


# ── the script, with a fake python (the check) and a fake docker ───────────


@pytest.fixture
def box(tmp_path: Path):
    """A root for the script: fake `python` answering the check from a list of exit
    codes, and fake `docker` whose log says ``login`` after a restart."""
    (tmp_path / "trading-engine").mkdir()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    state = tmp_path / "state"
    state.mkdir()

    def write(name: str, body: str) -> None:
        path = bin_dir / name
        path.write_text("#!/usr/bin/env bash\n" + body)
        path.chmod(0o755)

    # each call takes the next exit code from state/checks (one per line), else 0
    write("python", f'''
f={state}/checks; n=$(cat {state}/calls 2>/dev/null || echo 0); n=$((n+1)); echo $n > {state}/calls
code=$(sed -n "${{n}}p" "$f" 2>/dev/null); code=${{code:-0}}
[ "$code" = 0 ] && echo "IB Gateway at 127.0.0.1:4002 is logged in to IBKR" || echo "IBKR's servers didn't"
exit $code
''')
    write("docker", f'''
case "$1" in
  ps) cat {state}/container 2>/dev/null ;;
  restart) echo "$2" >> {state}/restarts ;;
  logs) cat {state}/login 2>/dev/null ;;
esac
''')

    def run(checks: list[int], *, container: str = "abc123", login: str = "IBC: Login has completed") -> subprocess.CompletedProcess:
        (state / "checks").write_text("\n".join(map(str, checks)) + "\n")
        (state / "container").write_text(container + ("\n" if container else ""))
        (state / "login").write_text(login + "\n")
        env = {
            **os.environ,
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "DV02_ROOT": str(tmp_path),
            "DV02_PYTHON": str(bin_dir / "python"),
            "DV02_GATEWAY_SETTLE_S": "0",
            "DV02_GATEWAY_POLL_S": "0",
        }
        return subprocess.run(["bash", str(SCRIPT)], env=env, capture_output=True, text=True, timeout=30)

    def restarts() -> list[str]:
        f = state / "restarts"
        return f.read_text().split() if f.exists() else []

    return SimpleNamespace(run=run, restarts=restarts)


def test_a_logged_in_gateway_is_left_alone(box) -> None:
    out = box.run([0])
    assert out.returncode == 0 and box.restarts() == []


def test_a_cut_off_gateway_is_restarted_once_and_checked_again(box) -> None:
    out = box.run([2, 0])
    assert out.returncode == 0
    assert box.restarts() == ["abc123"]
    assert "restarting its container" in out.stderr and "logged in again" in out.stderr


def test_a_refused_login_is_said_and_not_retried(box) -> None:
    out = box.run([2], login="IBC: detected dialog entitled: Unrecognized Username or Password; event=Opened")
    assert out.returncode == 0 and box.restarts() == ["abc123"]
    assert "IBKR refused the saved login" in out.stderr and "bin/tradeit login" in out.stderr


def test_no_container_is_said_and_the_run_goes_on(box) -> None:
    out = box.run([3], container="")
    assert out.returncode == 0 and box.restarts() == []
    assert "no IB Gateway container" in out.stderr
