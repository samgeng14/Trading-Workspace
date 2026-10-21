"""
settings.py — the one place IBKR connection settings are resolved.

Every module that opens a socket to TWS / IB Gateway should get its
host, port, account and clientId from here instead of hardcoding them.

Precedence (highest first):

  1. explicit arguments (e.g. a CLI ``--port``)
  2. environment: ``IBKR_HOST``, ``IBKR_PORT_PAPER`` / ``IBKR_PORT_LIVE``,
     ``IBKR_ACCOUNT_PAPER`` / ``IBKR_ACCOUNT_LIVE``,
     ``IBKR_CLIENT_ID_BASE_PAPER`` / ``IBKR_CLIENT_ID_BASE_LIVE``
     (``.env`` in the repo root or trading-engine/ fills in unset ones)
  3. ``config/broker.yaml`` (``ibkr.host``, ``ibkr.<profile>.port`` …)
  4. defaults below (IB Gateway ports)

Port map (host side):

    =============  =====  =====
    app            paper  live
    =============  =====  =====
    IB Gateway     4002   4001
    TWS            7497   7496
    =============  =====  =====

``ibgw/docker-compose.yml`` publishes the container's paper socket on
host port 4002, so the docker gateway looks like a native IB Gateway.

Client IDs: a gateway session accepts at most 32 API clients and
refuses a second connection that reuses a connected clientId (error
326). Orders can only be modified or cancelled by the clientId that
placed them. So every component gets a fixed role ID —
``base + ROLE_OFFSETS[role]`` — instead of picking numbers ad hoc.
"""
from __future__ import annotations

import os
import socket
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .client import IBKRClient, IBKRConnectionConfig

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
BROKER_CONFIG_PATH = _PROJECT_ROOT / "config" / "broker.yaml"
DOTENV_PATHS = (_PROJECT_ROOT / ".env", _PROJECT_ROOT.parent / ".env")

PROFILES = ("paper", "live")

DEFAULT_HOST = "127.0.0.1"
GATEWAY_PORTS = {"paper": 4002, "live": 4001}
TWS_PORTS = {"paper": 7497, "live": 7496}
DEFAULT_CLIENT_ID_BASE = {"paper": 300, "live": 400}

# Offsets from the profile's client-id base. "orders" is the only role
# that places or cancels orders; keeping it fixed means every order the
# system ever placed can be managed from the same clientId.
ROLE_OFFSETS: dict[str, int] = {
    "orders": 0,
    "account": 1,
    "market_data": 2,
    "market_data_indices": 3,
    "market_data_futures": 4,
    "executions": 5,
    "reconcile": 6,
    "dashboard": 7,
    "dashboard_actions": 8,
    "doctor": 9,
    "backfill": 10,
    "research": 11,
    "kill": 12,  # bin/tradeit status: read-only, beside a run in progress
    "gateway_check": 13,  # deploy/bin/gateway-ready, just before the daily run
}


@dataclass(frozen=True, slots=True)
class IBKREndpoint:
    profile: str
    host: str
    port: int
    account: str | None
    client_id_base: int
    source: dict[str, str]

    def client_id(self, role: str) -> int:
        return self.client_id_base + _role_offset(role)


def _check_profile(profile: str) -> str:
    p = (profile or "").strip().lower()
    if p not in PROFILES:
        raise ValueError(f"profile must be one of {PROFILES}, got {profile!r}")
    return p


def _role_offset(role: str) -> int:
    try:
        return ROLE_OFFSETS[role]
    except KeyError:
        raise ValueError(
            f"unknown IBKR client role {role!r}; add it to ROLE_OFFSETS"
        ) from None


def _load_yaml(path: Path) -> dict[str, Any]:
    try:
        import yaml
    except ImportError:
        return {}
    try:
        with path.open("r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except FileNotFoundError:
        return {}


_dotenv_loaded = False


def load_dotenv(paths: tuple[Path, ...] = DOTENV_PATHS) -> None:
    """Copy ``KEY=VALUE`` lines from ``.env`` files into ``os.environ``.

    Variables already set in the environment win. Runs once per process.
    """
    global _dotenv_loaded
    if _dotenv_loaded:
        return
    _dotenv_loaded = True
    for path in paths:
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip().removeprefix("export ").strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value


def _env_int(name: str) -> int | None:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        raise ValueError(f"environment variable {name} must be an integer, got {raw!r}") from None


def resolve_endpoint(
    profile: str = "paper",
    *,
    host: str | None = None,
    port: int | None = None,
    account: str | None = None,
    config_path: Path | None = None,
) -> IBKREndpoint:
    """Resolve where to connect for ``profile``, recording where each value came from."""
    profile = _check_profile(profile)
    suffix = profile.upper()
    load_dotenv()
    doc = _load_yaml(config_path or BROKER_CONFIG_PATH)
    ibkr = doc.get("ibkr") or {}
    prof = ibkr.get(profile) or {}
    source: dict[str, str] = {}

    if host:
        source["host"] = "argument"
    elif os.environ.get("IBKR_HOST", "").strip():
        host, source["host"] = os.environ["IBKR_HOST"].strip(), "env IBKR_HOST"
    elif ibkr.get("host"):
        host, source["host"] = str(ibkr["host"]), "broker.yaml"
    else:
        host, source["host"] = DEFAULT_HOST, "default"

    if port is not None:
        source["port"] = "argument"
    elif (env_port := _env_int(f"IBKR_PORT_{suffix}")) is not None:
        port, source["port"] = env_port, f"env IBKR_PORT_{suffix}"
    elif prof.get("port") is not None:
        port, source["port"] = int(prof["port"]), "broker.yaml"
    else:
        port, source["port"] = GATEWAY_PORTS[profile], "default"

    if account:
        source["account"] = "argument"
    elif os.environ.get(f"IBKR_ACCOUNT_{suffix}", "").strip():
        account = os.environ[f"IBKR_ACCOUNT_{suffix}"].strip()
        source["account"] = f"env IBKR_ACCOUNT_{suffix}"
    elif prof.get("account"):
        account, source["account"] = str(prof["account"]), "broker.yaml"
    else:
        account, source["account"] = None, "first managed account"

    if (env_base := _env_int(f"IBKR_CLIENT_ID_BASE_{suffix}")) is not None:
        base, source["client_id_base"] = env_base, f"env IBKR_CLIENT_ID_BASE_{suffix}"
    elif prof.get("client_id_base") is not None:
        base, source["client_id_base"] = int(prof["client_id_base"]), "broker.yaml"
    else:
        base, source["client_id_base"] = DEFAULT_CLIENT_ID_BASE[profile], "default"

    return IBKREndpoint(
        profile=profile,
        host=host,
        port=int(port),
        account=account,
        client_id_base=int(base),
        source=source,
    )


def client_id_for(role: str, profile: str = "paper") -> int:
    return resolve_endpoint(profile).client_id(role)


def port_is_open(host: str, port: int, timeout: float = 0.5) -> bool:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        return sock.connect_ex((host, int(port))) == 0
    except OSError:
        return False
    finally:
        sock.close()


def candidate_ports(profile: str = "paper") -> list[int]:
    """Configured port first, then the standard ports for the SAME profile.

    Never includes the other profile's ports: a paper job must not
    stumble onto a live session just because it was the only one open.
    """
    profile = _check_profile(profile)
    ordered = [
        resolve_endpoint(profile).port,
        GATEWAY_PORTS[profile],
        TWS_PORTS[profile],
    ]
    return list(dict.fromkeys(ordered))


def detect_port(profile: str = "paper", host: str | None = None) -> int:
    """First listening port from ``candidate_ports``; the configured port if none is."""
    endpoint = resolve_endpoint(profile)
    for port in candidate_ports(profile):
        if port_is_open(host or endpoint.host, port):
            return port
    return endpoint.port


def connection_config(
    profile: str = "paper",
    role: str = "account",
    *,
    host: str | None = None,
    port: int | None = None,
    client_id: int | None = None,
    readonly: bool = False,
    timeout: float = 10.0,
) -> IBKRConnectionConfig:
    endpoint = resolve_endpoint(profile, host=host, port=port)
    return IBKRConnectionConfig(
        host=endpoint.host,
        port=endpoint.port,
        client_id=client_id if client_id is not None else endpoint.client_id(role),
        readonly=readonly,
        account=endpoint.account,
        timeout=timeout,
        expected_profile=endpoint.profile,
    )


def make_client(profile: str = "paper", role: str = "account", **overrides: Any) -> IBKRClient:
    """An unconnected ``IBKRClient`` for ``profile`` / ``role``."""
    return IBKRClient(connection_config(profile, role, **overrides))


__all__ = [
    "BROKER_CONFIG_PATH",
    "DEFAULT_CLIENT_ID_BASE",
    "GATEWAY_PORTS",
    "IBKREndpoint",
    "PROFILES",
    "ROLE_OFFSETS",
    "TWS_PORTS",
    "candidate_ports",
    "client_id_for",
    "connection_config",
    "detect_port",
    "load_dotenv",
    "make_client",
    "port_is_open",
    "resolve_endpoint",
]
