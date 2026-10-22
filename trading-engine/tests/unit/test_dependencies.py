"""Every package the runtime installs (services/api/requirements.txt: what bin/tradeit
setup, the VM and CI install) is declared in trading-engine/pyproject.toml too, so
the project's own list says what it runs on (Phase 0: "declare deps")."""
from __future__ import annotations

import re
import tomllib
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _name(spec: str) -> str:
    return re.split(r"[\\[<>=!~ ;]", spec.strip(), maxsplit=1)[0].lower().replace("_", "-")


def test_the_runtime_requirements_are_declared() -> None:
    required = {
        _name(line)
        for line in (PROJECT_ROOT.parent / "services" / "api" / "requirements.txt").read_text().splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    poetry = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text())["tool"]["poetry"]
    declared = {k.lower().replace("_", "-") for k, v in poetry["dependencies"].items()
                if not (isinstance(v, dict) and v.get("optional"))}  # fmt: skip
    assert required - declared == set(), f"in requirements.txt but not declared: {sorted(required - declared)}"
    assert "pandas" in declared and "mosek" not in declared  # the two Phase 0 named
