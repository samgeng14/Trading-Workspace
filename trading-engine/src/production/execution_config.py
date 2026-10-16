"""The submit switches in config/execution.yaml: may a daily run send orders?

The daily runner reads them on every real run, and the dashboard's rebalance
preview shows them, since its Submit button starts a daily run. A run sends
orders only when ``allow_submit: true``, ``dry_run: false`` and ``mode`` is the
run's profile; anything else, or a file that can't be read, makes the run a dry
run (every step and check, nothing sent).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

EXECUTION_YAML = Path(__file__).resolve().parents[2] / "config" / "execution.yaml"


def load(path: Path | None = None) -> dict[str, Any]:
    """``mode``, ``allow_submit`` and ``dry_run`` from the file, or ``error``
    when it can't be read."""
    try:
        import yaml

        with open(path or EXECUTION_YAML, encoding="utf-8") as f:
            doc = yaml.safe_load(f) or {}
        ex = doc.get("execution") or {}
        return {key: ex.get(key) for key in ("mode", "allow_submit", "dry_run")}
    except Exception as exc:  # noqa: BLE001 — any failure means "don't send"
        return {"error": f"{type(exc).__name__}: {exc}"}


def submit_disabled(cfg: dict[str, Any], profile: str) -> str | None:
    """Why the settings stop a run on ``profile`` sending orders, or None when
    they allow it. Each setting must be exactly right: a missing or misspelt one
    stops it."""
    if cfg.get("error"):
        return f"config/execution.yaml could not be read ({cfg['error']})"
    if cfg.get("allow_submit") is not True:
        return f"config/execution.yaml has allow_submit: {json.dumps(cfg.get('allow_submit'))}"
    if cfg.get("dry_run") is not False:
        return f"config/execution.yaml has dry_run: {json.dumps(cfg.get('dry_run'))}"
    if cfg.get("mode") != profile:
        return f"config/execution.yaml has mode: {cfg.get('mode')}, and this run's profile is {profile}"
    return None
