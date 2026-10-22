"""
sleeves.py — The fund's sleeves and where each stands against the research gate
(config/sleeves.yaml), for the dashboard's Strategies page.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import yaml

PATH = Path(__file__).resolve().parents[2] / "config" / "sleeves.yaml"
STATUSES = ("live", "paper", "shadow", "research", "failed")
FIELDS = ("id", "name", "status", "since", "summary", "sharpe", "sharpe_se", "max_drawdown", "report")


def load(path: Path = PATH) -> list[dict[str, Any]]:
    """The sleeves, each with every field (None where the file leaves one out).
    Raises ValueError on an unknown status or a repeated id."""
    doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    out, seen = [], set()
    for raw in doc.get("sleeves") or []:
        s = {k: raw.get(k) for k in FIELDS}
        if s["status"] not in STATUSES:
            raise ValueError(f"sleeve {s['id']}: status {s['status']!r} isn't one of {', '.join(STATUSES)}")
        if s["id"] in seen:
            raise ValueError(f"sleeve {s['id']} is listed twice")
        seen.add(s["id"])
        if isinstance(s["since"], date):
            s["since"] = s["since"].isoformat()
        s["summary"] = " ".join(str(s["summary"] or "").split())
        out.append(s)
    return out
