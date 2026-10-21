"""
db.py — The store: where it is, whether it is on, and a connection to it.

config/db.yaml names the host, port and database, and the environment variables
that hold the login (DV02_DB_USER, DV02_DB_PASSWORD: the VM's /etc/dv02/dv02.env,
never git or chat). The store is on only when DV02_DB_ENABLED=1 (or db.yaml's
``enabled: true``) and a password is set, so the Mac runs without one.
DV02_DATABASE_URL, a full connection string, overrides all of that (tests, CI).

Nothing in the daily run depends on the store: record.best_effort writes and,
when it can't, says so in the log and moves on.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlsplit

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DB_CONFIG_PATH = PROJECT_ROOT / "config" / "db.yaml"
MIGRATIONS_DIR = PROJECT_ROOT / "db" / "migrations"
CONNECT_TIMEOUT_S = 5  # a store that's down must not hold up a daily run
STATEMENT_TIMEOUT_MS = 15_000  # nor one that's stuck


@dataclass(frozen=True)
class StoreConfig:
    enabled: bool
    conninfo: str  # for psycopg.connect; holds the password, so never logged
    where: str  # host:port/database, for messages
    off_because: str = ""


def load_config(path: Path | None = None, env: Mapping[str, str] | None = None) -> StoreConfig:
    env = os.environ if env is None else env
    url = env.get("DV02_DATABASE_URL", "").strip()
    if url:
        u = urlsplit(url)
        return StoreConfig(True, url, f"{u.hostname}:{u.port or 5432}{u.path}")
    doc = yaml.safe_load((path or DB_CONFIG_PATH).read_text(encoding="utf-8")) or {}
    db = doc.get("database") or {}
    host, port, name = str(db.get("host", "127.0.0.1")), int(db.get("port", 5432)), str(db.get("name", "trading"))
    where = f"{host}:{port}/{name}"
    user = env.get(str(db.get("user_env", "DV02_DB_USER")), "").strip() or "dv02"
    password = env.get(str(db.get("password_env", "DV02_DB_PASSWORD")), "").strip()
    flag = env.get("DV02_DB_ENABLED", "").strip().lower()
    on = flag in ("1", "true", "yes") if flag else bool(db.get("enabled"))
    if not on:
        return StoreConfig(False, "", where, "DV02_DB_ENABLED isn't 1 (and db.yaml's enabled is false)")
    if not password:
        return StoreConfig(False, "", where, "no password in DV02_DB_PASSWORD")
    from psycopg.conninfo import make_conninfo  # only once the store is on: the Mac needn't have it

    conninfo = make_conninfo(
        host=host, port=port, dbname=name, user=user, password=password, connect_timeout=CONNECT_TIMEOUT_S,
        options=f"-c statement_timeout={STATEMENT_TIMEOUT_MS}",
    )
    return StoreConfig(True, conninfo, where)


def connect(cfg: StoreConfig | None = None, **kwargs: Any):
    """An autocommit connection: each writer opens its own transaction."""
    import psycopg

    cfg = cfg or load_config()
    return psycopg.connect(cfg.conninfo, autocommit=True, **kwargs)
