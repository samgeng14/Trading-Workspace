"""Whether the C++ engine co-signs a daily run's orders (config/engine.yaml).

In shadow, the default, the engine's decisions are compared with Python's and
recorded, never acted on (src/production/engine_shadow.py). In cosign, a run
sends no order unless the engine decided its reconcile, its drift gate and its
basket exactly as Python did: the runner's step_submit_orders blocks the
orders otherwise, as it does for a failed pre-trade check. It is the first
step of the engine taking over (PLAN §6): its decisions bind, while Python
still builds and sends the orders.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

ENGINE_YAML = Path(__file__).resolve().parents[2] / "config" / "engine.yaml"
ROLES = ("shadow", "cosign")

# The comparisons a run's orders rest on, in the order the run makes them. The
# basket's, which also covers its limits and auction, must be there. The
# reconcile's and the drift gate's count when the run made them: a run with no
# expected book yet, or on the monthly cadence, has none to compare.
OPTIONAL = ("reconcile", "drift")
REQUIRED = ("basket",)


def load(path: Path | None = None) -> dict[str, Any]:
    """``{"role": "shadow" | "cosign"}``, with a ``note`` when the file didn't
    say one of them: any other value, or a file that can't be read, is cosign."""
    try:
        import yaml

        with open(path or ENGINE_YAML, encoding="utf-8") as f:
            doc = yaml.safe_load(f) or {}
        role = (doc.get("engine") or {}).get("role")
    except Exception as exc:  # noqa: BLE001 — unreadable means the stricter role
        return {"role": "cosign", "note": f"config/engine.yaml could not be read ({type(exc).__name__}: {exc})"}
    if role in ROLES:
        return {"role": role}
    return {"role": "cosign", "note": f"config/engine.yaml has role: {role!r}, which is neither shadow nor cosign"}


def verdict(comparisons: Mapping[str, Mapping[str, Any] | None]) -> dict[str, Any]:
    """``ok`` when every comparison the run made matched and the basket's is
    there; ``reasons`` lists what didn't, and ``checked`` each comparison's
    summary."""
    reasons: list[str] = []
    checked: dict[str, Any] = {}
    for kind in (*OPTIONAL, *REQUIRED):
        c = comparisons.get(kind)
        if not c:
            if kind in REQUIRED:
                reasons.append(f"the engine made no {kind} comparison this run, so nothing to co-sign")
            continue
        checked[kind] = c.get("summary")
        if c.get("match") is not True:
            reasons.append(f"the C++ engine's {kind} differs from Python's: {c.get('summary')}")
    return {"ok": not reasons, "reasons": reasons, "checked": checked}
