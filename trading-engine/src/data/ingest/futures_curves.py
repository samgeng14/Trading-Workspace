"""
futures_curves.py — Record every listed futures contract's daily bar, each evening,
into the lake (src/data/lake.py, dataset ``futures_curves``).

    python -m src.data.ingest.futures_curves        # dv02-archive.timer, 17:20 ET on weekdays

This is the forward archive the plan asks for (docs/PLAN.md §11, "Start recording
now"). IBKR keeps no history of expired contracts and gives only a continuous
series for the past, so whole curves can't be pulled later; carry (sleeve S1)
needs them. One row per contract per session: the session's bar on regular hours
(the close is the last trade, not the exchange's settlement) and the contract's
expiry and size.

The roots are config/universes/futures.yaml's ``archive`` list, in IBKR's symbols
and exchanges; each root's contracts expiring within HORIZON_DAYS are recorded.
Read-only, on its own client ID ("archive"). A root or contract IBKR won't serve
(no market data permission, no trades that day) is reported and skipped; the rest
are kept. On a day the NYSE is closed it records nothing.
"""
from __future__ import annotations

import logging
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[3]
UNIVERSE_PATH = PROJECT_ROOT / "config" / "universes" / "futures.yaml"
DATASET = "futures_curves"
HORIZON_DAYS = 400  # every contract out to about 13 months: the curve carry is measured on
PACE_S = 1.0  # between bar requests: IBKR's pacing rules count them

log = logging.getLogger(__name__)


def archive_roots(path: Path = UNIVERSE_PATH) -> list[dict[str, str]]:
    doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    roots = (doc.get("futures") or {}).get("archive") or []
    return [{"symbol": str(r["symbol"]), "exchange": str(r["exchange"]), "currency": str(r.get("currency", "USD"))}
            for r in roots]  # fmt: skip


def _expiry(contract: Any, details: Any) -> date | None:
    for text in (getattr(details, "realExpirationDate", ""), getattr(contract, "lastTradeDateOrContractMonth", "")):
        text = str(text or "").split(" ")[0]
        if len(text) == 8 and text.isdigit():
            return datetime.strptime(text, "%Y%m%d").date()
    return None


def _price(value: Any) -> float | None:
    """A bar's price; it may be negative (WTI crude settled at -$37.63 on 2020-04-20)."""
    try:
        x = float(value)
    except (TypeError, ValueError):
        return None
    return x if x == x else None


def _count(value: Any) -> float | None:
    """A volume or contract size; IBKR sends -1 for "none"."""
    x = _price(value)
    return x if x is not None and x >= 0 else None


def record_sessions(ib: Any, roots: list[dict[str, str]], sessions: list[date], *, pace_s: float = PACE_S,
                    timeout_s: float = 60.0) -> tuple[dict[date, pd.DataFrame], list[str]]:  # fmt: skip
    """Every listed contract's bar for each of ``sessions``: the rows per session, and
    what couldn't be read (with IBKR's own message when it sent one). One request per
    contract covers every session, so a missed evening can be had the next day; a
    contract that expired in between is gone for good."""
    from ib_async import Future

    from src.broker.ibkr.client import fail_fast

    sessions = sorted(set(sessions))
    errors: dict[int, str] = {}  # conId -> IBKR's last error about it

    def on_error(_req_id: int, code: int, message: str, contract: Any) -> None:
        if contract is not None and getattr(contract, "conId", 0) and code not in (2104, 2106, 2107, 2108, 2158):
            errors[int(contract.conId)] = f"IBKR {code}: {message}"

    # after one request times out, the rest fail at once instead of 60 s apiece
    details_of = fail_fast(ib.reqContractDetails, timeout_s)
    bars_of = fail_fast(ib.reqHistoricalData, timeout_s)
    duration = f"{min((sessions[-1] - sessions[0]).days + 5, 30)} D"
    rows: dict[date, list[dict[str, Any]]] = {s: [] for s in sessions}
    missing: list[str] = []
    ib.errorEvent += on_error
    try:
        for root in roots:
            name = f"{root['symbol']}@{root['exchange']}"
            try:
                details = details_of(Future(symbol=root["symbol"], exchange=root["exchange"], currency=root["currency"]))
            except Exception as exc:  # noqa: BLE001
                missing.append(f"{name}: {type(exc).__name__}: {exc}")
                continue
            listed = [(d.contract, d, _expiry(d.contract, d)) for d in details or []]
            listed = sorted(((c, d, e) for c, d, e in listed
                             if e is not None and sessions[0] <= e <= sessions[-1] + timedelta(days=HORIZON_DAYS)),
                            key=lambda t: t[2])  # fmt: skip
            if not listed:
                missing.append(f"{name}: no contracts expiring within {HORIZON_DAYS} days")
                continue
            for contract, d, expiry in listed:
                ib.sleep(pace_s)
                try:
                    bars = bars_of(contract, endDateTime="", durationStr=duration, barSizeSetting="1 day",
                                   whatToShow="TRADES", useRTH=True, formatDate=1)  # fmt: skip
                except Exception as exc:  # noqa: BLE001
                    missing.append(f"{contract.localSymbol}: {type(exc).__name__}: {exc}")
                    continue
                by_day = {pd.Timestamp(b.date).date(): b for b in bars or []}
                wanted = [s for s in sessions if s <= expiry <= s + timedelta(days=HORIZON_DAYS)]
                absent = [s for s in wanted if s not in by_day]
                if absent:
                    why = errors.get(int(contract.conId)) or f"no bar for {', '.join(map(str, absent))}"
                    missing.append(f"{contract.localSymbol}: {why}")
                for session in wanted:
                    bar = by_day.get(session)
                    if bar is None:
                        continue
                    rows[session].append({
                        "root": root["symbol"],
                        "exchange": root["exchange"],
                        "local_symbol": contract.localSymbol,
                        "con_id": int(contract.conId),
                        "contract_month": str(getattr(d, "contractMonth", "") or contract.lastTradeDateOrContractMonth[:6]),
                        "expiry": pd.Timestamp(expiry),
                        "days_to_expiry": (expiry - session).days,
                        "multiplier": _count(contract.multiplier),
                        "currency": root["currency"],
                        "open": _price(bar.open),
                        "high": _price(bar.high),
                        "low": _price(bar.low),
                        "close": _price(bar.close),
                        "volume": _count(bar.volume),
                    })  # fmt: skip
    finally:
        ib.errorEvent -= on_error
    return {s: pd.DataFrame(r) for s, r in rows.items()}, missing


def record_session(ib: Any, roots: list[dict[str, str]], session: date, **kwargs: Any) -> tuple[pd.DataFrame, list[str]]:
    by_session, missing = record_sessions(ib, roots, [session], **kwargs)
    return by_session[session], missing


def archive(*, client: Any = None, now: datetime | None = None, root_dir: Path | None = None,
            roots: list[dict[str, str]] | None = None, pace_s: float = PACE_S,
            sessions: list[date] | None = None) -> dict[str, Any]:  # fmt: skip
    """Record ``sessions`` (default: the last completed one; on a day the NYSE is
    closed, nothing) into the lake, one file per session."""
    from src.common import clock
    from src.data import lake

    now = now or datetime.now(timezone.utc)
    today = now.astimezone(clock.NYSE_TZ).date()
    if not sessions:
        if not clock.is_us_business_day(today):
            return {"ok": True, "skipped": f"{today} is not a session: nothing to record"}
        sessions = [clock.last_completed_session(now)]
    roots = roots if roots is not None else archive_roots()
    if client is None:
        from src.broker.ibkr.settings import make_client

        client = make_client("paper", "archive", readonly=True)
    client.connect()  # a gateway cut off from IBKR raises here (IBKRSyncError)
    try:
        by_session, missing = record_sessions(client.ib, roots, sessions, pace_s=pace_s)
    finally:
        client.disconnect()
    paths, counts = [], {}
    for session, rows in sorted(by_session.items()):
        counts[session.isoformat()] = len(rows)
        if not rows.empty:
            paths.append(str(lake.write(DATASET, rows, as_of=session, available_at=now, root=root_dir)))
    latest = by_session[max(by_session)]
    return {
        "ok": bool(paths),
        "session": max(by_session).isoformat(),
        "sessions": counts,
        "roots": int(latest["root"].nunique()) if not latest.empty else 0,
        "contracts": len(latest),
        "missing": missing,
        "path": paths[-1] if paths else None,
        "detail": ", ".join(f"{n} contracts for {s}" for s, n in counts.items()),
    }


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    logging.getLogger("ib_async").setLevel(logging.CRITICAL)
    try:
        out = archive()
    except Exception as exc:  # noqa: BLE001 — one line, and a failed unit for the alert
        print(f"futures curves failed: {type(exc).__name__}: {exc}")
        return 1
    if out.get("skipped"):
        print(out["skipped"])
        return 0
    print(f"{out['session']}: {out['contracts']} contracts of {out['roots']} roots -> {out['path']}")
    for line in out["missing"]:
        print(f"  not recorded: {line}")
    return 0 if out["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
