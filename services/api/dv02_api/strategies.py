"""The Strategies page: the fund's sleeves against the research gate
(trading-engine/config/sleeves.yaml) and every shadow book, from the files
trading-engine/src/production/shadows.py writes each evening: H2's
(shadow_h2.py) and R3's (shadow_r3.py)."""

from __future__ import annotations

import csv
import importlib
import json
from typing import Any

from . import engine

# id: the book's name, what its sleeve line is, its value column in daily.csv, its module
BOOKS = {
    "h2": ("S0 with a trend overlay (H2)", "Trend sleeve", "h2_value", "src.production.shadow_h2"),
    "r3": ("S0 with the VIX futures premium (R3)", "VIX futures sleeve", "book_value", "src.production.shadow_r3"),
}


def _num(text: str | None) -> float | None:
    try:
        x = float(text) if text not in (None, "") else None
    except ValueError:
        return None
    return x if x is not None and x == x else None


def _signal(record: dict[str, Any]) -> str | None:
    """R3's latest signal in a sentence; None for a book without one."""
    s = record.get("signal")
    if not s:
        return None
    state = "contango: short the next session" if s["contango"] else "inverted: flat the next session"
    return (
        f"{s['date']}: the 30-day VIX futures at {s['cm30_price']:.2f} against the VIX at {s['vix']:.2f} "
        f"({s['premium']:+.1%}), {state}"
    )


def shadow_book(book_id: str) -> dict[str, Any]:
    name, sleeve, value, module = BOOKS[book_id]
    start = importlib.import_module(module).START  # trading-engine
    folder = engine.artifacts_dir() / "shadow" / book_id
    points = []
    daily = folder / "daily.csv"
    if daily.exists():
        with daily.open(newline="") as f:
            for r in csv.DictReader(f):
                book, live, alone = _num(r.get(value)), _num(r.get("live_value")), _num(r.get("sleeve_value"))
                if book is not None and live is not None and alone is not None:
                    points.append({"date": r["date"][:10], "book": book, "live": live, "sleeve": alone})
    latest = sorted(folder.glob("20*.json"))
    record = json.loads(latest[-1].read_text()) if latest else {}
    return {
        "id": book_id,
        "name": name,
        "sleeve": sleeve,
        "start": start.isoformat(),
        "sessions": int(record.get("sessions") or 0),
        "through": record.get("session"),
        "points": points,
        "decision": record.get("sleeve_decision"),
        "signal": _signal(record),
        "would_be_orders": [{**o, "quantity": int(o["quantity"])} for o in record.get("would_be_orders") or []],
        "holdings": [
            {"instrument": f"VXM {h['expiry']}", "quantity": h["vxm"], "notional": h["notional"]}
            for h in record.get("would_hold") or []
        ],
    }


def load() -> dict[str, Any]:
    from src.strategies import sleeves  # trading-engine: the checkout's own list, the same everywhere

    return {"sleeves": sleeves.load(), "shadows": [shadow_book(b) for b in BOOKS]}
