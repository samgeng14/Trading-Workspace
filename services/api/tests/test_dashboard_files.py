"""DV02_WEB_DIST: the API serves the built dashboard on its own port (the VM,
behind a Cloudflare tunnel), with the API routes and the guard unchanged."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from dv02_api.app import create_app

from .conftest import FakeBroker


@pytest.fixture()
def dist(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "dist"
    (root / "assets").mkdir(parents=True)
    (root / "index.html").write_text("<!doctype html><title>DV02</title>")
    (root / "assets" / "app-1a2b.js").write_text("console.log('dv02')")
    (root / "favicon.svg").write_text("<svg/>")
    (tmp_path / "secret.txt").write_text("not the dashboard's")
    monkeypatch.setenv("DV02_WEB_DIST", str(root))
    return root


def _client() -> TestClient:
    return TestClient(create_app("paper", broker=FakeBroker(), background=False))


def test_the_dashboard_and_its_files_are_served(engine_tree, dist) -> None:  # noqa: ARG001
    with _client() as c:
        page = c.get("/")
        assert page.status_code == 200 and "<title>DV02</title>" in page.text
        assert page.headers["cache-control"] == "no-cache"
        assert c.get("/assets/app-1a2b.js").text == "console.log('dv02')"
        assert c.get("/favicon.svg").text == "<svg/>"
        # a dashboard route loaded directly (a refresh, a bookmark) gets the app
        assert "<title>DV02</title>" in c.get("/positions").text


def test_api_routes_are_unchanged_and_unknown_ones_stay_404(engine_tree, dist) -> None:  # noqa: ARG001
    with _client() as c:
        assert c.get("/api/v1/status").json()["mode"] == "paper"
        missing = c.get("/api/v1/nothing-here")
        assert missing.status_code == 404 and missing.headers["content-type"] == "application/problem+json"
        assert c.get("/api").status_code == 404


def test_nothing_outside_the_build_is_served(engine_tree, dist) -> None:  # noqa: ARG001
    with _client() as c:
        for path in ("/../secret.txt", "/%2e%2e/secret.txt", "/assets/../../secret.txt"):
            res = c.get(path)
            assert "not the dashboard's" not in res.text, path


def test_the_guard_still_refuses_a_foreign_host(engine_tree, dist) -> None:  # noqa: ARG001
    with _client() as c:
        res = c.get("/", headers={"host": "evil.example"})
        assert res.status_code == 400 and "DV02_ALLOWED_HOSTS" in res.text


def test_the_tunnels_hostname_gets_the_dashboard_once_allowed(engine_tree, dist, monkeypatch) -> None:  # noqa: ARG001
    monkeypatch.setenv("DV02_ALLOWED_HOSTS", "dv02.example.com")
    with _client() as c:
        page = c.get("/", headers={"host": "dv02.example.com"})
        assert page.status_code == 200 and "<title>DV02</title>" in page.text
        assert c.get("/api/v1/status", headers={"host": "dv02.example.com"}).status_code == 200


def test_a_build_without_index_html_is_refused_at_startup(engine_tree, tmp_path, monkeypatch) -> None:  # noqa: ARG001
    monkeypatch.setenv("DV02_WEB_DIST", str(tmp_path))
    with pytest.raises(FileNotFoundError, match="npm run build"):
        create_app("paper", broker=FakeBroker(), background=False)


def test_without_the_setting_nothing_is_served(engine_tree, monkeypatch) -> None:  # noqa: ARG001
    monkeypatch.delenv("DV02_WEB_DIST", raising=False)
    with _client() as c:
        assert c.get("/").status_code == 404
