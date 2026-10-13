"""/status's engine component: the C++ engine the no-leverage check runs on."""

from __future__ import annotations

import sys
from types import SimpleNamespace

import dv02_engine
import pytest
from fastapi.testclient import TestClient

from dv02_api.app import create_app

from .test_contract import assert_schema, response_schema


@pytest.fixture()
def status(engine_tree, fake_broker, frozen_now):  # noqa: ARG001
    def get() -> dict:
        with TestClient(create_app("paper", broker=fake_broker, background=False)) as c:
            body = c.get("/api/v1/status").json()
        assert_schema(body, response_schema("/status", "get"))
        return next(x for x in body["components"] if x["id"] == "engine")

    return get


def test_the_installed_engine_is_ok_with_its_version(status) -> None:
    engine = status()
    assert engine["status"] == "ok"
    assert f"dv02_engine {dv02_engine.__version__}" in engine["detail"]


def test_a_missing_engine_is_an_error_naming_this_python(status, monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "dv02_engine", None)  # import fails as if not installed
    assert status() == {
        "id": "engine",
        "label": "Engine",
        "status": "error",
        "detail": f"C++ engine not installed in {sys.executable}: run bin/tradeit setup",
    }


def test_an_engine_that_lets_leverage_through_is_an_error(status, monkeypatch) -> None:
    from dv02_engine import risk

    monkeypatch.setattr(risk, "check", lambda *a, **k: SimpleNamespace(ok=True, breaches=[]))
    engine = status()
    assert engine["status"] == "error"
    assert "failed its self-check (it let a levered book through)" in engine["detail"]
    assert "bin/tradeit engine" in engine["detail"]


def test_an_engine_that_raises_is_an_error(status, monkeypatch) -> None:
    from dv02_engine import risk

    def broken(*a, **k):
        raise TypeError("incompatible function arguments")

    monkeypatch.setattr(risk, "check", broken)
    engine = status()
    assert engine["status"] == "error" and "TypeError: incompatible function arguments" in engine["detail"]
