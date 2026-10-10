from __future__ import annotations

from typing import Any

from ib_async import IB

from src.broker.ibkr.settings import resolve_endpoint


def _resolve_connection(profile: str = "paper") -> tuple[str, int, int]:
    endpoint = resolve_endpoint((profile or "paper").lower())
    return (endpoint.host, endpoint.port, endpoint.client_id("dashboard"))


def get_broker_status(
    profile: str = "paper",
    host: str | None = None,
    port: int | None = None,
    client_id: int | None = None,
) -> dict[str, Any]:
    default_host, default_port, default_client_id = _resolve_connection(profile)
    host = host or default_host
    port = port if port is not None else default_port
    client_id = client_id if client_id is not None else default_client_id

    ib = IB()
    try:
        ib.connect(host, port, clientId=client_id, timeout=8)
        managed_accounts = ib.managedAccounts()

        return {
            "ok": True,
            "connected": True,
            "profile": profile,
            "host": host,
            "port": port,
            "client_id": client_id,
            "managed_accounts": managed_accounts,
            "error": None,
        }
    except Exception as exc:
        return {
            "ok": False,
            "connected": False,
            "profile": profile,
            "host": host,
            "port": port,
            "client_id": client_id,
            "managed_accounts": [],
            "error": str(exc),
        }
    finally:
        if ib.isConnected():
            ib.disconnect()


def get_account_summary(
    profile: str = "paper",
    host: str | None = None,
    port: int | None = None,
    client_id: int | None = None,
) -> list[dict[str, Any]]:
    default_host, default_port, default_client_id = _resolve_connection(profile)
    host = host or default_host
    port = port if port is not None else default_port
    client_id = client_id if client_id is not None else default_client_id

    ib = IB()
    try:
        ib.connect(host, port, clientId=client_id, timeout=8)
        return [
            {
                "account": x.account,
                "tag": x.tag,
                "value": x.value,
                "currency": x.currency,
            }
            for x in ib.accountSummary()
        ]
    finally:
        if ib.isConnected():
            ib.disconnect()


def get_positions(
    profile: str = "paper",
    host: str | None = None,
    port: int | None = None,
    client_id: int | None = None,
) -> list[dict[str, Any]]:
    default_host, default_port, default_client_id = _resolve_connection(profile)
    host = host or default_host
    port = port if port is not None else default_port
    client_id = client_id if client_id is not None else default_client_id

    ib = IB()
    try:
        ib.connect(host, port, clientId=client_id, timeout=8)
        return [
            {
                "account": p.account,
                "symbol": p.contract.symbol,
                "secType": p.contract.secType,
                "exchange": p.contract.exchange,
                "currency": p.contract.currency,
                "position": p.position,
                "avgCost": p.avgCost,
            }
            for p in ib.positions()
        ]
    finally:
        if ib.isConnected():
            ib.disconnect()


def load_latest_positions(profile: str = "paper") -> "pd.DataFrame | None":
    """Load last saved positions snapshot from disk (no live IBKR connection needed)."""
    import pandas as pd
    from pathlib import Path
    root = Path(__file__).resolve().parents[3]
    snap = root / "data" / "broker" / "positions" / "paper_positions_snapshot.csv"
    if snap.exists():
        return pd.read_csv(snap)
    return None