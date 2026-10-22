"""
cboe_vx.py — VIX futures settlements and the VIX close from Cboe, for R3's shadow
book (src/production/shadow_r3.py). Free and keyless: Cboe's list of VX
contracts, each monthly contract's history file, and the VIX's daily history.

Cboe publishes a session's settlement overnight: on 2026-10-22 the front
contract's file had 10-21's row by 03:35 ET, and not at 23:30 the evening
before. So an evening fetch holds the session before.

The files are cached under data/market/raw/cboe/ (git-ignored): vx_list.json,
vx/VX_<expiry>.csv and VIX_History.csv. A contract still trading is fetched
again every time; one that has expired, once. Downloads are data only: parsed,
never run.

    python -m src.data.ingest.cboe_vx       # fetch, then print what the files hold
"""
from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

FOLDER = Path("data") / "market" / "raw" / "cboe"
LIST_URL = "https://www.cboe.com/us/futures/market_statistics/historical_data/product/list/VX/"
CURRENT_URL = "https://cdn.cboe.com/{path}"
VIX_URL = "https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv"
USER_AGENT = "DV02 paper fund (github.com/samgeng14/Trading-Workspace)"
PAUSE_S = 1.0  # between files: Cboe's servers are public, not ours
HISTORY_DAYS = 120  # contracts that expired this recently: the 30-day series needs the roll's months

Getter = Callable[[str], "bytes | None"]


def folder(root: Path = PROJECT_ROOT) -> Path:
    return root / FOLDER


def vix_path(root: Path = PROJECT_ROOT) -> Path:
    return folder(root) / "VIX_History.csv"


def contract_paths(root: Path = PROJECT_ROOT) -> list[Path]:
    return sorted((folder(root) / "vx").glob("VX_*.csv"))


def get(url: str) -> bytes | None:
    """The body, or None when the file isn't there (Cboe answers 403 for a contract it
    never listed)."""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                body = response.read()
            time.sleep(PAUSE_S)
            return body
        except urllib.error.HTTPError as exc:
            if exc.code in (403, 404):
                time.sleep(PAUSE_S)
                return None
            if attempt == 2:
                raise
        except Exception:  # noqa: BLE001
            if attempt == 2:
                raise
        time.sleep(5 * (attempt + 1))
    return None


def _save(path: Path, body: bytes) -> None:
    if body.lstrip()[:15].lower().startswith((b"<!doctype", b"<html")):
        raise RuntimeError(f"{path.name}: Cboe sent a web page, not data")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(body)
    tmp.replace(path)


def fetch(root: Path = PROJECT_ROOT, today: date | None = None, fetch_url: Getter = get) -> dict[str, Any]:
    """Cboe's VX list, every monthly contract still listed or expired within
    HISTORY_DAYS (one already cached and expired is kept), and the VIX history."""
    today = today or datetime.now(timezone.utc).date()
    base = folder(root)
    body = fetch_url(LIST_URL)
    if body is None:
        raise RuntimeError(f"Cboe's VX contract list isn't at {LIST_URL}")
    listing = json.loads(body)
    _save(base / "vx_list.json", body)
    monthly = [e for year in sorted(listing) for e in listing[year] if e.get("duration_type") == "M"]
    fetched = kept = 0
    for e in monthly:
        expiry = date.fromisoformat(e["expire_date"])
        if expiry < today - timedelta(days=HISTORY_DAYS):
            continue
        path = base / "vx" / Path(e["path"]).name
        if path.exists() and expiry < today:
            kept += 1
            continue  # expired: its file doesn't change
        data = fetch_url(CURRENT_URL.format(path=e["path"]))
        if data is not None:
            _save(path, data)
            fetched += 1
    body = fetch_url(VIX_URL)
    if body is None:
        raise RuntimeError(f"Cboe's VIX history isn't at {VIX_URL}")
    _save(vix_path(root), body)
    return {"contracts_fetched": fetched, "contracts_kept": kept, **coverage(root)}


def coverage(root: Path = PROJECT_ROOT) -> dict[str, Any]:
    """The latest settlement and VIX close the files hold."""
    from src.strategies import vix_premium as vp

    paths = contract_paths(root)
    if not paths:
        return {"settlements_through": None, "vix_through": None, "rows": 0}
    rows = vp.contracts(paths)
    settled = rows.loc[rows["settle"].notna() & ~rows["final"], "date"]
    vix = vp.read_vix(vix_path(root)) if vix_path(root).exists() else None
    return {
        "settlements_through": settled.max().date().isoformat() if len(settled) else None,
        "vix_through": vix.index[-1].date().isoformat() if vix is not None and len(vix) else None,
        "rows": int(len(rows)),
    }


def main(argv: list[str] | None = None) -> int:
    try:
        out = fetch()
    except Exception as exc:  # noqa: BLE001
        print(f"Cboe fetch failed: {type(exc).__name__}: {exc}")
        return 1
    print(f"VX contracts: {out['contracts_fetched']} fetched, {out['contracts_kept']} kept; settlements through "
          f"{out['settlements_through']}, VIX through {out['vix_through']}")  # fmt: skip
    return 0


if __name__ == "__main__":
    sys.exit(main())
