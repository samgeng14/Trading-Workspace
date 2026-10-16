"""config/db.yaml names exactly the tables db/migrations creates, so the two can't
drift apart again, and the migrations are numbered without gaps."""
from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

MIGRATIONS = sorted((PROJECT_ROOT / "db" / "migrations").glob("*.sql"))


def created_tables() -> set[str]:
    out: set[str] = set()
    for path in MIGRATIONS:
        out |= set(re.findall(r"CREATE TABLE IF NOT EXISTS (\w+)", path.read_text()))
    return out


def test_the_config_names_every_migrated_table_and_nothing_else() -> None:
    doc = yaml.safe_load((PROJECT_ROOT / "config" / "db.yaml").read_text())
    tables = doc["tables"]
    assert set(tables.values()) == created_tables()
    assert all(key == name for key, name in tables.items())
    assert "nav" in tables and "market_bars" in tables


def test_no_credentials_in_the_config() -> None:
    doc = yaml.safe_load((PROJECT_ROOT / "config" / "db.yaml").read_text())
    db = doc["database"]
    assert "password" not in db and "user" not in db
    assert db["user_env"] == "DV02_DB_USER" and db["password_env"] == "DV02_DB_PASSWORD"


def test_migrations_are_numbered_without_gaps() -> None:
    numbers = [int(p.name[:3]) for p in MIGRATIONS]
    assert numbers == list(range(1, len(MIGRATIONS) + 1))


def test_the_nav_table_keys_on_profile_day_and_source() -> None:
    sql = (PROJECT_ROOT / "db" / "migrations" / "006_nav.sql").read_text()
    assert "CREATE TABLE IF NOT EXISTS nav" in sql
    assert "ON nav (profile, nav_date, source)" in sql
