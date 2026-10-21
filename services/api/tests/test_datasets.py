"""GET /data/datasets: every dataset's watermark against what should exist by now,
as the engine's registry measures it (trading-engine/src/data/datasets.py)."""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from dv02_api import engine
from dv02_api.app import create_app


def test_the_registry_over_the_apis_engine_tree(engine_tree, fake_broker, frozen_now) -> None:
    catchups = engine.artifacts_dir() / "catchup"
    catchups.mkdir(parents=True)
    record = {"at": "2026-10-09T22:00:00+00:00", "actions": [{"dataset": "total_return", "ok": True, "detail": ""}]}
    (catchups / "catchup_paper_20261009T220000Z.json").write_text(json.dumps(record))
    app = create_app("paper", broker=fake_broker, background=False)
    with TestClient(app) as client:
        body = client.get("/api/v1/data/datasets").json()
    rows = {d["name"]: d for d in body["datasets"]}
    assert len(rows) == 10 and body["last_catchup"] == record
    # the tree has the API's recorded closes through the day before the frozen Friday (14:12 ET)
    assert rows["close_nav"]["state"] == "ok" and rows["close_nav"]["expected"] == "2026-10-08"
    assert rows["futures_curves"]["state"] == "missing" and rows["store_close"]["state"] == "off"
