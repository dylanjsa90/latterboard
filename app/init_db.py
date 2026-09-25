import logging
import secrets

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
        for column in Base.metadata.tables[User.__tablename__].columns:
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
    Base.metadata.create_all(bind=engine)
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

    seed_bot_user(db)

    crud_puzzle.seed_defaults(db)


def seed_bot_user(db: Session) -> User:
    """The computer-controlled opponent players are matched with when no human is
    available.

    It is a perfectly ordinary user row, because `match.inviter_id`/`invitee_id`
    are non-null foreign keys and nothing else in the match code needs to know the
    difference. It never signs in: it plays through `app/api/bot_turn.py`, so its
    password is random and thrown away rather than being a known value someone
    could log in with.
    """
    bot = crud_user.get_user_by_email(db, settings.BOT_USER_EMAIL)
    if bot is None:
        bot = crud_user.create_user(
            db=db,
            user_in=UserCreate(
                email=settings.BOT_USER_EMAIL,
                username=settings.BOT_USER_USERNAME,
                password=secrets.token_urlsafe(32),
                display_name=settings.BOT_DISPLAY_NAME,
                birth_year=2000,
            ),
        )
        logging.info("Created bot user %s", bot.username)
    if not bot.is_bot:
        # Set separately because `create_user` builds the row from `UserCreate`,
        # which has no `is_bot`. Also repairs a row seeded before the column existed.
        bot.is_bot = True
        db.add(bot)
        db.commit()
        db.refresh(bot)
    return bot
