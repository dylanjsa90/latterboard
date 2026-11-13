from typing import Any

from sqlalchemy import create_engine, event
from sqlalchemy.engine.interfaces import DBAPIConnection
from sqlalchemy.orm import sessionmaker

from app.core.config import settings

_is_sqlite = settings.DATABASE_URL.startswith("sqlite")

engine = create_engine(
    url=settings.DATABASE_URL,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
    pool_recycle=3600,
    pool_timeout=30,
    connect_args={"check_same_thread": False} if _is_sqlite else {},
    echo=settings.DEBUG,
)


def sqlite_pragmas(dbapi_connection: DBAPIConnection, _record: Any) -> None:
    """WAL lets reads run alongside the one writer instead of waiting for it, and with
    synchronous=NORMAL a commit no longer waits on fsync: a power cut can lose the last
    few commits but can't corrupt the file. WAL keeps recent commits in a `-wal` file
    beside the database, so copy it with `python -m app.backup_db`, never `cp`.

    The busy timeout stays at the driver's 5 s. Lock waits happen in worker threads,
    not on the event loop, so a shorter one would only turn them into errors.
    """
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.close()


if _is_sqlite:
    event.listen(engine, "connect", sqlite_pragmas)

# 3. Create a SessionLocal class for database sessions
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
