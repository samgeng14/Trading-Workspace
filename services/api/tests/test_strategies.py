"""GET /strategies: the sleeves against the research gate, and H2's shadow book from
the files trading-engine/src/production/shadow_h2.py writes."""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from dv02_api import engine
from dv02_api.app import create_app


def get(fake_broker) -> dict:
    app = create_app("paper", broker=fake_broker, background=False)
    with TestClient(app) as client:
        return client.get("/api/v1/strategies").json()


def test_before_the_shadow_has_a_record(engine_tree, fake_broker, frozen_now) -> None:
    body = get(fake_broker)
    assert [s["status"] for s in body["sleeves"]][:2] == ["live", "shadow"]
    assert body["shadow"]["start"] == "2026-10-22" and body["shadow"]["points"] == []
    assert body["shadow"]["decision"] is None and body["shadow"]["through"] is None


def test_the_shadow_books_record(engine_tree, fake_broker, frozen_now) -> None:
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
    shadow = get(fake_broker)["shadow"]
    assert shadow["sessions"] == 2 and shadow["through"] == "2026-10-26"
    assert shadow["points"][-1] == {"date": "2026-10-26", "h2": 1.001, "live": 1.0, "sleeve": 1.002}
    assert shadow["decision"]["weights"]["BIL"] == 0.62 and shadow["would_be_orders"][0]["quantity"] == 3298
