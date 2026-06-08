import logging

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import settings

# 1. Define the SQLite database URL
SQLALCHEMY_DATABASE_URL ="sqlite:///./sql_app.db"

# 2. Create the engine.
# "check_same_thread=False" is REQUIRED strictly for SQLite because FastAPI
# can interact with the database across multiple threads.
engine = create_engine(settings.DATABASE_URL,
    connect_args={"check_same_thread": False},
    echo=True
)

logging.info("Creating SessionLocal")
# 3. Create a SessionLocal class for database sessions
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
