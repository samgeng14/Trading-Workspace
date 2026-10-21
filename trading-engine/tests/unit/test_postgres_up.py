"""deploy/bin/postgres-up starts the store, makes its password once (written to
the env file and pg_password, never shown), and runs deploy/bin/migrate, which
applies the migrations and loads the engine's files with the engine's own
Python. Here with a fake docker and a fake python."""
from __future__ import annotations

import os
import re
import stat
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO = Path(__file__).resolve().parents[3]
SCRIPT = REPO / "deploy" / "bin" / "postgres-up"

ENV_TEMPLATE = """# the store
DV02_DB_USER=dv02
DV02_DB_PASSWORD=
DV02_ALLOWED_HOSTS=dv02.example.com
"""


@pytest.fixture
def box(tmp_path: Path):
    """An env file (mode 640), a fake docker whose pg_isready answers after
    ``ready_after`` polls, and a fake python that notes what it was run with."""
    (tmp_path / "trading-engine").mkdir()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    state = tmp_path / "state"
    state.mkdir()
    env_file = tmp_path / "dv02.env"
    env_file.write_text(ENV_TEMPLATE)
    env_file.chmod(0o640)
    pw_file = tmp_path / "pg_password"

    def write(name: str, body: str) -> None:
        path = bin_dir / name
        path.write_text("#!/usr/bin/env bash\n" + body)
        path.chmod(0o755)

    write("docker", f'''
echo "$*" >> {state}/docker
case "$*" in
  *pg_isready*)
    n=$(cat {state}/polls 2>/dev/null || echo 0); n=$((n+1)); echo $n > {state}/polls
    [ "$n" -gt "$(cat {state}/ready_after)" ] ;;
esac
''')
    write("python", f'''
echo "$* enabled=$DV02_DB_ENABLED password=${{DV02_DB_PASSWORD:+set}}" >> {state}/python
''')

    def run(*, ready_after: int = 0) -> subprocess.CompletedProcess:
        (state / "ready_after").write_text(str(ready_after))
        env = {
            **{k: v for k, v in os.environ.items() if not k.startswith("DV02_")},
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "DV02_ROOT": str(tmp_path),
            "DV02_PYTHON": str(bin_dir / "python"),
            "DV02_ENV_FILE": str(env_file),
            "DV02_PG_PASSWORD_FILE": str(pw_file),
            "DV02_PG_WAIT_POLLS": "3",
            "DV02_PG_POLL_S": "0",
        }
        return subprocess.run(["bash", str(SCRIPT)], env=env, capture_output=True, text=True, timeout=30)

    def lines(name: str) -> list[str]:
        f = state / name
        return f.read_text().splitlines() if f.exists() else []

    def password() -> str:
        (pw,) = re.findall(r"^DV02_DB_PASSWORD=(.*)$", env_file.read_text(), re.M)
        return pw

    return SimpleNamespace(run=run, lines=lines, password=password, env_file=env_file, pw_file=pw_file)


def test_the_first_run_makes_a_password_it_never_shows(box) -> None:
    out = box.run()
    assert out.returncode == 0, out.stderr
    pw = box.password()
    assert re.fullmatch(r"[0-9a-f]{48}", pw)
    assert pw not in out.stdout + out.stderr and "made the database password" in out.stdout
    assert box.pw_file.read_text() == pw
    assert stat.S_IMODE(box.pw_file.stat().st_mode) == 0o600
    assert stat.S_IMODE(box.env_file.stat().st_mode) == 0o640  # rewritten in place
    assert box.env_file.read_text() == ENV_TEMPLATE.replace("DV02_DB_PASSWORD=", f"DV02_DB_PASSWORD={pw}")
    assert any(c.endswith("up -d") for c in box.lines("docker"))
    assert box.lines("python") == ["-m src.storage.migrate enabled=1 password=set",
                                   "-m src.storage.backfill enabled=1 password=set"]  # fmt: skip


def test_running_it_again_keeps_the_password(box) -> None:
    box.run()
    pw = box.password()
    out = box.run()
    assert out.returncode == 0 and box.password() == pw and box.pw_file.read_text() == pw
    assert "made the database password" not in out.stdout


def test_a_password_already_set_is_the_one_postgres_gets(box) -> None:
    box.env_file.write_text(ENV_TEMPLATE.replace("DV02_DB_PASSWORD=", 'DV02_DB_PASSWORD="chosen-by-the-owner"'))
    out = box.run()
    assert out.returncode == 0 and box.pw_file.read_text() == "chosen-by-the-owner"


def test_it_waits_for_postgres_and_stops_if_it_never_comes(box) -> None:
    assert box.run(ready_after=2).returncode == 0
    out = box.run(ready_after=99)
    assert out.returncode == 1 and "Postgres didn't come up" in out.stderr


@pytest.mark.skipif(os.geteuid() == 0, reason="root can write any file")
def test_it_needs_to_write_the_env_file(box) -> None:
    box.env_file.chmod(0o440)
    out = box.run()
    assert out.returncode == 1 and "run as root" in out.stderr
    assert box.lines("docker") == []
