"""Copies the configured SQLite database, safely even while the app is running.

    uv run python -m app.backup_db [dest]                         # locally
    APP_ENV=production .venv/bin/python -m app.backup_db [dest]   # on the VM
    docker compose exec app python -m app.backup_db [dest]        # in Docker

`dest` defaults to the database's own path plus `.bak`. Uses SQLite's backup API, which
takes a consistent snapshot including commits still in the `-wal` file. A `cp` of the
`.db` file alone would miss those, or catch a commit halfway.

Restoring needs the service stopped and the old `-wal` and `-shm` files deleted first:
see "Backing up and restoring the database" in README.md.
"""

import sqlite3
import sys
from contextlib import closing
from pathlib import Path

from sqlalchemy.engine import make_url

from app.core.config import settings


def backup(source: Path, dest: Path) -> None:
    if not source.is_file():
        # sqlite3.connect would create an empty database and back that up.
        raise SystemExit(f"No database at {source}")
    with (
        closing(sqlite3.connect(source)) as src,
        closing(sqlite3.connect(dest)) as dst,
    ):
        src.backup(dst)


def main() -> None:
    url = make_url(settings.DATABASE_URL)
    if not url.drivername.startswith("sqlite") or not url.database:
        raise SystemExit("Only a file-backed SQLite database can be backed up this way")
    source = Path(url.database)
    dest = (
        Path(sys.argv[1])
        if len(sys.argv) > 1
        else source.with_name(source.name + ".bak")
    )
    backup(source, dest)
    print(f"Backed up {source} to {dest}")  # noqa: T201  (a CLI's output)


if __name__ == "__main__":
    main()
