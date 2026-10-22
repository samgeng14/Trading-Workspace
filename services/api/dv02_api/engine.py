"""Bridge to trading-engine.

The engine's modules import as ``src.*`` from the trading-engine directory, so
that directory goes on ``sys.path`` once, here, before anything imports them.
``DV02_ENGINE_ROOT`` overrides the location (tests point it at a temp tree).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
ENGINE_ROOT = Path(os.environ.get("DV02_ENGINE_ROOT", REPO_ROOT / "trading-engine")).resolve()

if str(ENGINE_ROOT) not in sys.path:
    sys.path.insert(0, str(ENGINE_ROOT))


def data_dir() -> Path:
    return ENGINE_ROOT / "data"


def artifacts_dir() -> Path:
    return ENGINE_ROOT / "artifacts"


def config_dir() -> Path:
    return ENGINE_ROOT / "config"


def api_state_dir() -> Path:
    """Where the API keeps its own small state (bar cache, intraday P&L, last doctor report)."""
    path = artifacts_dir() / "api"
    path.mkdir(parents=True, exist_ok=True)
    return path
