import logging

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.core.base_class import Base
from app.core.config import settings
from app.crud import puzzle as crud_puzzle
from app.crud import user as crud_user
from app.database import engine
from app.models.user import User
from app.schemas.user import UserCreate


def ensure_user_columns(bind: Engine) -> list[str]:
    """Adds the nullable `user` columns an older database lacks; returns their names.

    `create_all` creates missing tables but never alters existing ones, and there are
    no migrations yet, so without this new profile columns never reach old databases.
    """
    present = {column["name"] for column in inspect(bind).get_columns(User.__tablename__)}
    # `user` is a reserved word in Postgres, so names go through the dialect's quoting.
    quote = bind.dialect.identifier_preparer.quote
    added: list[str] = []
    with bind.begin() as conn:
        # Base is declared untyped (see the create_all call below).
        for column in Base.metadata.tables[User.__tablename__].columns:  # type: ignore[attr-defined]
            if column.name in present or not column.nullable:
                continue
            column_type = column.type.compile(dialect=bind.dialect)
            conn.execute(
                text(
                    f"ALTER TABLE {quote(User.__tablename__)} "
                    f"ADD COLUMN {quote(column.name)} {column_type}"
                )
            )
            added.append(column.name)
    if added:
        logging.info("Added user columns: %s", ", ".join(added))
    return added


def init_db(db: Session) -> None:
    Base.metadata.create_all(bind=engine)  # type: ignore
    ensure_user_columns(engine)
    logging.info("initializing db, bound engine")

    user = crud_user.get_user_by_email(db, settings.DEFAULT_USER)

    if not user:
        user = crud_user.create_user(
            db=db,
            user_in=UserCreate(
                email=settings.DEFAULT_USER,
                username=settings.DEFAULT_USER_USERNAME,
                password=settings.DEFAULT_USER_PASSWORD,
                display_name="First User",
                birth_year=2000,
            ),
        )
        logging.info(f"Created user {user}")

    crud_puzzle.seed_defaults(db)
