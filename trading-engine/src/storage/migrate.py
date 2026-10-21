"""
migrate.py — Create the store's tables: db/migrations/*.sql in order, each once.

    python -m src.storage.migrate       # deploy/bin/migrate runs this on the VM

schema_migrations records each file applied. A file runs in its own
transaction, so a failure leaves the store at the last good migration.
"""
from __future__ import annotations

import sys
from pathlib import Path

from src.storage.db import MIGRATIONS_DIR, connect, load_config


def migrate(conn, migrations_dir: Path = MIGRATIONS_DIR) -> list[str]:
    """Apply the migrations not yet applied; return their names."""
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        " version TEXT PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT now())"
    )
    done = {row[0] for row in conn.execute("SELECT version FROM schema_migrations")}
    applied = []
    for path in sorted(migrations_dir.glob("*.sql")):
        if path.stem in done:
            continue
        with conn.transaction():
            conn.execute(path.read_text(encoding="utf-8"))
            conn.execute("INSERT INTO schema_migrations (version) VALUES (%s)", (path.stem,))
        applied.append(path.stem)
    return applied


def main() -> int:
    cfg = load_config()
    if not cfg.enabled:
        print(f"The store is off: {cfg.off_because}. Nothing to migrate.")
        return 0
    with connect(cfg) as conn:
        applied = migrate(conn)
    print(f"{cfg.where}: " + (f"applied {', '.join(applied)}" if applied else "up to date"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
