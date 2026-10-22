"""Unit tests for ``src.broker.ibkr.settings`` and the client's profile guard."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.broker.ibkr import settings  # noqa: E402
from src.broker.ibkr.client import (  # noqa: E402
    IBKRClient,
    IBKRConnectionConfig,
    IBKRWrongAccountError,
    is_paper_account,
)

_ENV_VARS = [
    "IBKR_HOST",
    "IBKR_PORT_PAPER",
    "IBKR_PORT_LIVE",
    "IBKR_ACCOUNT_PAPER",
    "IBKR_ACCOUNT_LIVE",
    "IBKR_CLIENT_ID_BASE_PAPER",
    "IBKR_CLIENT_ID_BASE_LIVE",
]


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in _ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    # Keep a developer's real .env out of these tests.
    monkeypatch.setattr(settings, "_dotenv_loaded", True)


def _write_yaml(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "broker.yaml"
    path.write_text(text)
    return path


def test_defaults_without_config(tmp_path):
    ep = settings.resolve_endpoint("paper", config_path=tmp_path / "missing.yaml")
    assert (ep.host, ep.port, ep.account, ep.client_id_base) == ("127.0.0.1", 4002, None, 300)
    assert ep.source["port"] == "default"
    live = settings.resolve_endpoint("live", config_path=tmp_path / "missing.yaml")
    assert (live.port, live.client_id_base) == (4001, 400)


def test_yaml_then_env_then_argument_precedence(tmp_path, monkeypatch):
    cfg = _write_yaml(tmp_path, "ibkr:\n  host: 10.0.0.5\n  paper:\n    port: 7497\n    account: DU111\n")
    ep = settings.resolve_endpoint("paper", config_path=cfg)
    assert (ep.host, ep.port, ep.account) == ("10.0.0.5", 7497, "DU111")
    assert ep.source == {
        "host": "broker.yaml",
        "port": "broker.yaml",
        "account": "broker.yaml",
        "client_id_base": "default",
    }

    monkeypatch.setenv("IBKR_PORT_PAPER", "4002")
    monkeypatch.setenv("IBKR_ACCOUNT_PAPER", "DU222")
    ep = settings.resolve_endpoint("paper", config_path=cfg)
    assert (ep.port, ep.account) == (4002, "DU222")
    assert ep.source["port"] == "env IBKR_PORT_PAPER"

    ep = settings.resolve_endpoint("paper", port=4999, config_path=cfg)
    assert ep.port == 4999 and ep.source["port"] == "argument"


def test_bad_env_port_is_a_clear_error(monkeypatch):
    monkeypatch.setenv("IBKR_PORT_PAPER", "four-thousand")
    with pytest.raises(ValueError, match="IBKR_PORT_PAPER"):
        settings.resolve_endpoint("paper")


def test_unknown_profile_rejected():
    with pytest.raises(ValueError, match="profile"):
        settings.resolve_endpoint("prod")


def test_client_ids_are_fixed_and_unique_per_role(tmp_path):
    ep = settings.resolve_endpoint("paper", config_path=tmp_path / "missing.yaml")
    ids = [ep.client_id(role) for role in settings.ROLE_OFFSETS]
    assert len(set(ids)) == len(ids)
    assert ep.client_id("orders") == 300
    with pytest.raises(ValueError, match="unknown IBKR client role"):
        ep.client_id("nope")


def test_paper_and_live_client_id_ranges_do_not_overlap():
    span = max(settings.ROLE_OFFSETS.values()) + 1
    paper = settings.DEFAULT_CLIENT_ID_BASE["paper"]
    live = settings.DEFAULT_CLIENT_ID_BASE["live"]
    assert paper + span <= live


def test_candidate_ports_never_cross_profiles(monkeypatch):
    monkeypatch.setenv("IBKR_PORT_PAPER", "4002")
    monkeypatch.setenv("IBKR_PORT_LIVE", "4001")
    assert settings.candidate_ports("paper") == [4002, 7497]
    assert settings.candidate_ports("live") == [4001, 7496]


def test_detect_port_falls_back_to_configured(monkeypatch):
    monkeypatch.setenv("IBKR_PORT_PAPER", "4002")
    monkeypatch.setattr(settings, "port_is_open", lambda host, port, timeout=0.5: False)
    assert settings.detect_port("paper") == 4002
    monkeypatch.setattr(settings, "port_is_open", lambda host, port, timeout=0.5: port == 7497)
    assert settings.detect_port("paper") == 7497


def test_connection_config_carries_profile_and_role(monkeypatch):
    monkeypatch.setenv("IBKR_PORT_PAPER", "4002")
    cfg = settings.connection_config("paper", "reconcile", readonly=True)
    assert cfg.port == 4002
    assert cfg.client_id == 300 + settings.ROLE_OFFSETS["reconcile"]
    assert cfg.readonly is True
    assert cfg.expected_profile == "paper"


def test_load_dotenv_does_not_override_existing(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("# comment\nIBKR_PORT_PAPER=7497\nexport IBKR_HOST='10.1.1.1'\n")
    monkeypatch.setenv("IBKR_HOST", "127.0.0.1")
    monkeypatch.setattr(settings, "_dotenv_loaded", False)
    settings.load_dotenv((env_file,))
    import os

    assert os.environ["IBKR_PORT_PAPER"] == "7497"
    assert os.environ["IBKR_HOST"] == "127.0.0.1"


# ── client profile guard ────────────────────────────────────────────────


class _FakeIB:
    def __init__(self, accounts):
        self._accounts = accounts
        self._connected = False

    def connect(self, *args, **kwargs):
        self._connected = True

    def isConnected(self):
        return self._connected

    def disconnect(self):
        self._connected = False

    def managedAccounts(self):
        return list(self._accounts)


def _client(accounts, expected_profile):
    client = IBKRClient(IBKRConnectionConfig(expected_profile=expected_profile))
    client.ib = _FakeIB(accounts)
    return client


def test_is_paper_account():
    assert is_paper_account("DU1234567")
    assert is_paper_account("df99")
    assert not is_paper_account("U1234567")


def test_paper_profile_refuses_live_account():
    client = _client(["U1234567"], "paper")
    with pytest.raises(IBKRWrongAccountError, match="profile=paper"):
        client.connect()
    assert client.is_connected is False


def test_live_profile_refuses_paper_account():
    client = _client(["DU1234567"], "live")
    with pytest.raises(IBKRWrongAccountError, match="profile=live"):
        client.connect()


def test_matching_profile_connects():
    client = _client(["DU1234567"], "paper")
    client.connect()
    assert client.is_connected is True


def test_no_expected_profile_skips_check():
    client = _client(["U1234567"], None)
    client.connect()
    assert client.is_connected is True
