import logging

from sqlalchemy.orm import Session

from app.core.base_class import Base
from app.core.config import settings
from app.crud import user as crud_user
from app.database import engine
from app.schemas.user import UserCreate


def init_db(db: Session):
    Base.metadata.create_all(bind=engine)  # type: ignore
    logging.info("initializing db, bound engine")

    user = crud_user.get_user_by_email(db, settings.DEFAULT_USER)

    if not user:
        user = crud_user.create_user(
            db=db,
            user_in=UserCreate(
                email=settings.DEFAULT_USER,
                username=settings.DEFAULT_USER_USERNAME,
                password=settings.DEFAULT_USER_PASSWORD,
            ),
        )
        logging.info(f"Created user {user}")
