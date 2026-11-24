"""How the app runs SQLite (`app/database.py`) and copies it (`app/backup_db.py`)."""

import sqlite3
from contextlib import closing

import pytest

from app.backup_db import backup
from app.database import engine, sqlite_pragmas


def test_the_database_runs_in_wal_mode():
    with engine.connect() as conn:
        assert conn.exec_driver_sql("PRAGMA journal_mode").scalar() == "wal"


def test_commits_do_not_wait_on_fsync(tmp_path):
    with closing(sqlite3.connect(tmp_path / "app.db")) as conn:
        sqlite_pragmas(conn, None)
        assert conn.execute("PRAGMA synchronous").fetchone()[0] == 1  # NORMAL


def test_backup_includes_commits_still_in_the_wal(tmp_path):
    """A plain `cp` of the .db file misses whatever hasn't been checkpointed yet."""
    source, dest = tmp_path / "app.db", tmp_path / "app.db.bak"
    with closing(sqlite3.connect(source)) as live:
        sqlite_pragmas(live, None)
        live.execute("PRAGMA wal_autocheckpoint=0")
        live.execute("CREATE TABLE t (x)")
        live.execute("INSERT INTO t VALUES (42)")
        live.commit()
        assert (tmp_path / "app.db-wal").stat().st_size > 0

        backup(source, dest)

    with closing(sqlite3.connect(dest)) as copy:
        assert copy.execute("SELECT x FROM t").fetchall() == [(42,)]


def test_backup_refuses_a_missing_database(tmp_path):
    """sqlite3.connect would quietly create an empty file and back that up."""
    with pytest.raises(SystemExit):
        backup(tmp_path / "nope.db", tmp_path / "nope.db.bak")
    assert not (tmp_path / "nope.db").exists()
