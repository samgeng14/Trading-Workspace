"""
capture.py — After the close, save the day's record from IBKR to the store: its
executions (fills), every position, and the closing NAV.

    python -m src.storage.capture        # dv02-capture.timer, 16:30 ET on weekdays

IBKR's API reports the current day's executions only, so the daily run at 15:35
the next day can't see the fills of the market-on-close orders it sent; this
job reads them the same evening. Read-only, on its own client ID ("capture").
The NAV is saved only once the day's session has closed: a catch-up the next
morning, or a day the market was closed, saves the fills and positions only.
"""
from __future__ import annotations

import logging
import sys
from datetime import datetime, timezone
from typing import Any

from src.storage.db import StoreConfig, connect, load_config
from src.storage.record import record_fills, record_nav, record_positions

log = logging.getLogger(__name__)


def capture(profile: str = "paper", *, client: Any = None, cfg: StoreConfig | None = None,
            now: datetime | None = None) -> dict[str, Any]:  # fmt: skip
    from src.broker.ibkr.executions import list_executions
    from src.broker.ibkr.settings import make_client
    from src.common import clock
    from src.production.runtime.build_paper_basket import fetch_positions_df

    cfg = cfg or load_config()
    if not cfg.enabled:
        return {"ok": True, "skipped": f"the store is off: {cfg.off_because}"}
    now = now or datetime.now(timezone.utc)
    day = now.astimezone(clock.NYSE_TZ).date()
    client = client or make_client(profile, "capture", readonly=True)
    client.connect()  # a gateway cut off from IBKR raises here (IBKRSyncError)
    try:
        fills = list_executions(profile=profile, client=client)
        positions = fetch_positions_df(client)
        summary = client.account_summary()
    finally:
        client.disconnect()

    def num(tag: str) -> float | None:
        try:
            return float(str(summary[tag]).replace(",", ""))
        except (KeyError, ValueError):
            return None

    nav, cash = num("NetLiquidation"), num("TotalCashValue")
    session = clock.market_session_for(day)
    closed = session is not None and now >= session.close_at
    with connect(cfg) as conn:
        added = record_fills(conn, profile, fills)
        held = record_positions(conn, profile, now, positions.to_dict("records"))
        if closed and nav is not None:
            record_nav(conn, profile, day, nav, cash=cash, source="ibkr_close")
    return {
        "ok": True,
        "day": day.isoformat(),
        "fills": len(fills),
        "fills_added": added,
        "positions": held,
        "nav": nav if closed else None,
        "where": cfg.where,
    }


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    logging.getLogger("ib_async").setLevel(logging.WARNING)
    try:
        out = capture()
    except Exception as exc:  # noqa: BLE001 — one line, and a failed unit for the alert
        print(f"capture failed: {type(exc).__name__}: {exc}")
        return 1
    if out.get("skipped"):
        print(out["skipped"])
        return 0
    print(
        f"{out['day']}: {out['fills']} executions ({out['fills_added']} new), {out['positions']} positions"
        + (f", NAV ${out['nav']:,.2f}" if out.get("nav") is not None else ", no NAV (the session isn't closed)")
        + f" -> {out['where']}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
