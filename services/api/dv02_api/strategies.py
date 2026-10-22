"""The Strategies page: the fund's sleeves against the research gate
(trading-engine/config/sleeves.yaml) and H2's shadow book, from the files
trading-engine/src/production/shadow_h2.py writes each evening."""

from __future__ import annotations

import csv
import json
from typing import Any

from . import engine


def _num(text: str | None) -> float | None:
    try:
        x = float(text) if text not in (None, "") else None
    except ValueError:
        return None
    return x if x is not None and x == x else None


def shadow_book() -> dict[str, Any]:
    from src.production import shadow_h2  # trading-engine

    folder = engine.artifacts_dir() / "shadow" / "h2"
    points = []
    daily = folder / "daily.csv"
    if daily.exists():
        with daily.open(newline="") as f:
            for r in csv.DictReader(f):
                h2, live, sleeve = _num(r.get("h2_value")), _num(r.get("live_value")), _num(r.get("sleeve_value"))
                if h2 is not None and live is not None and sleeve is not None:
                    points.append({"date": r["date"][:10], "h2": h2, "live": live, "sleeve": sleeve})
    latest = sorted(folder.glob("20*.json"))
    record = json.loads(latest[-1].read_text()) if latest else {}
    decision = record.get("sleeve_decision")
    return {
        "name": "S0 with a trend overlay (H2)",
        "start": shadow_h2.START.isoformat(),
        "sessions": int(record.get("sessions") or 0),
        "through": record.get("session"),
        "points": points,
        "decision": decision,
        "would_be_orders": [{**o, "quantity": int(o["quantity"])} for o in record.get("would_be_orders") or []],
    }


def load() -> dict[str, Any]:
    from src.strategies import sleeves  # trading-engine: the checkout's own list, the same everywhere

    return {"sleeves": sleeves.load(), "shadow": shadow_book()}
