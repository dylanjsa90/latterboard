import logging

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import APP_ENV, settings

_is_sqlite = settings.DATABASE_URL.startswith("sqlite")

logger = logging.getLogger("uvicorn")

logger.debug(f"Creating engine with DATABASE_URL: {settings.DATABASE_URL}  APP_ENV: {settings.APP_ENV} DB_TYPE: {settings.DB_TYPE} is_sqlite: {_is_sqlite}")

engine = create_engine(
    url=settings.DATABASE_URL,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
    pool_recycle=3600,
    pool_timeout=30,
    connect_args={"check_same_thread": False} if _is_sqlite else {},
    echo=APP_ENV == "local",
)


# 3. Create a SessionLocal class for database sessions
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
