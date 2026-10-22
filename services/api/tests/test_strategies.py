"""GET /strategies: the sleeves against the research gate, and every shadow book from
the files trading-engine/src/production/shadows.py writes: H2's and R3's."""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from dv02_api import engine
from dv02_api.app import create_app


def get(fake_broker) -> dict:
    app = create_app("paper", broker=fake_broker, background=False)
    with TestClient(app) as client:
        return client.get("/api/v1/strategies").json()


def test_before_the_shadows_have_a_record(engine_tree, fake_broker, frozen_now) -> None:
    body = get(fake_broker)
    assert [s["status"] for s in body["sleeves"]][:3] == ["live", "shadow", "shadow"]
    assert [b["id"] for b in body["shadows"]] == ["h2", "r3"]
    for book in body["shadows"]:
        assert book["start"] == "2026-10-22" and book["points"] == [] and book["through"] is None
        assert book["decision"] is None and book["signal"] is None and book["holdings"] == []


def test_h2s_record(engine_tree, fake_broker, frozen_now) -> None:
    folder = engine.artifacts_dir() / "shadow" / "h2"
    folder.mkdir(parents=True)
    (folder / "daily.csv").write_text(
        "date,sleeve_value,sleeve_return,live_nav,live_return,h2_return,h2_value,live_value\n"
        "2026-10-23,1.0,0.0,1047000,0.001,0.0005,1.0005,1.001\n"
        "2026-10-26,1.002,0.002,1046000,-0.001,0.0005,1.001,1.0\n"
    )
    record = {
        "session": "2026-10-26",
        "sessions": 2,
        "sleeve_decision": {"decided": "2026-10-22", "executed": "2026-10-23", "weights": {"BIL": 0.62, "SPY": 0.11}},
        "would_be_orders": [{"symbol": "BIL", "action": "BUY", "quantity": 3298, "dollars": 301865.38}],
    }
    (folder / "2026-10-26.json").write_text(json.dumps(record))
    h2 = get(fake_broker)["shadows"][0]
    assert h2["id"] == "h2" and h2["sessions"] == 2 and h2["through"] == "2026-10-26"
    assert h2["points"][-1] == {"date": "2026-10-26", "book": 1.001, "live": 1.0, "sleeve": 1.002}
    assert h2["decision"]["weights"]["BIL"] == 0.62 and h2["would_be_orders"][0]["quantity"] == 3298
    assert h2["sleeve"] == "Trend sleeve" and h2["signal"] is None


def test_r3s_record_says_its_signal_and_the_futures_it_would_hold(engine_tree, fake_broker, frozen_now) -> None:
    folder = engine.artifacts_dir() / "shadow" / "r3"
    folder.mkdir(parents=True)
    (folder / "daily.csv").write_text(
        "date,sleeve_value,sleeve_return,live_nav,live_return,book_return,book_value,live_value\n"
        "2026-10-23,1.0,0.0,1047000,0.001,0.0005,1.0005,1.001\n"
    )
    record = {
        "session": "2026-10-23",
        "sessions": 1,
        "signal": {
            "date": "2026-10-23",
            "cm30_price": 18.24,
            "vix": 17.35,
            "premium": 0.0513,
            "contango": True,
            "front": "2026-11-18",
            "second": "2026-12-16",
        },
        "sleeve_decision": {"decided": "2026-10-22", "executed": "2026-10-23", "weights": {"VXCM": -0.1, "BIL": 0.895}},
        "would_hold": [
            {"expiry": "2026-11-18", "weight": 0.68, "settle": 18.24, "notional": -71196.0, "vx": -3.9, "vxm": -39}
        ],
    }
    (folder / "2026-10-23.json").write_text(json.dumps(record))
    r3 = get(fake_broker)["shadows"][1]
    assert r3["points"] == [{"date": "2026-10-23", "book": 1.0005, "live": 1.001, "sleeve": 1.0}]
    assert r3["signal"].startswith("2026-10-23: the 30-day VIX futures at 18.24 against the VIX at 17.35 (+5.1%)")
    assert r3["signal"].endswith("contango: short the next session")
    assert r3["holdings"] == [{"instrument": "VXM 2026-11-18", "quantity": -39, "notional": -71196.0}]
    assert r3["would_be_orders"] == [] and r3["sleeve"] == "VIX futures sleeve"
