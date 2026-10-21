"""
gateway_check.py — Is IB Gateway logged in to IBKR right now?

    python -m src.broker.ibkr.gateway_check            # exit 0 ready, 2 cut off, 3 no gateway

One read-only connection on its own client ID, the same startup sync every
engine connection makes (src/broker/ibkr/client.py): it completes only when
the gateway is logged in and connected to IBKR's servers. deploy/bin/gateway-ready
runs it before the daily run and restarts the gateway once when it fails.
"""
from __future__ import annotations

import argparse
import logging
import sys

from .client import IBKRSyncError
from .settings import make_client

READY, CUT_OFF, NO_GATEWAY = 0, 2, 3


def check(profile: str = "paper", *, client=None) -> tuple[int, str]:
    """(exit code, one line saying why)."""
    client = client or make_client(profile, "gateway_check", readonly=True)
    where = f"{client.config.host}:{client.config.port}"
    try:
        client.connect()
    except IBKRSyncError as exc:
        return CUT_OFF, str(exc)
    except Exception as exc:  # noqa: BLE001 — refused, a timed-out handshake, anything else
        return NO_GATEWAY, f"no usable IB Gateway at {where}: {type(exc).__name__}: {exc}"
    try:
        accounts = client.managed_accounts()
    finally:
        client.disconnect()
    return READY, f"IB Gateway at {where} is logged in to IBKR ({', '.join(accounts) or 'no accounts'})"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Is IB Gateway logged in to IBKR?")
    parser.add_argument("--profile", default="paper", choices=("paper", "live"))
    args = parser.parse_args(argv)
    logging.getLogger("ib_async").setLevel(logging.CRITICAL)
    code, line = check(args.profile)
    print(line)
    return code


if __name__ == "__main__":
    sys.exit(main())
