"""
shadows.py — Every shadow book, each evening (dv02-shadow.timer, 17:05 ET;
bin/tradeit shadow). Nothing is sent.

  * H2, S0 with a trend overlay (shadow_h2.py), from 2026-10-22
  * R3, short the VIX futures premium (shadow_r3.py), from 2026-10-22

Each runs on its own: one failing doesn't stop the other, and the unit fails,
so the alert fires, when any did.
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

BOOKS = (("h2", "src.production.shadow_h2"), ("r3", "src.production.shadow_r3"))


def main(argv: list[str] | None = None) -> int:
    failed = []
    for name, module in BOOKS:
        try:
            code = importlib.import_module(module).main()
        except Exception as exc:  # noqa: BLE001 — the next book still runs
            print(f"shadow {name} failed: {type(exc).__name__}: {exc}")
            code = 1
        if code:
            failed.append(name)
    if failed:
        print(f"failed: {', '.join(failed)}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
