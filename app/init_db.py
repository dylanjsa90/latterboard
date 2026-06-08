import logging

from sqlalchemy.orm import Session

from app.core.base_class import Base
from app.crud import user as crud_user
from app.database import engine
from app.schemas.user import UserCreate


def init_db(db: Session):
  logging.info("init_db called")
  Base.metadata.create_all(bind=engine) # type: ignore
  logging.info("initializing db")

  user = crud_user.get_user_by_email(db, "default.user@dev.com")

  if not user:
    user = crud_user.create_user(
      db=db, 
      user_in=UserCreate(email="default.user@dev.com", username="default-user", password="password"))
    logging.info(f"Created user {user}")
  