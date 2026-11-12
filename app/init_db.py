import secrets

from loguru import logger
from sqlalchemy.orm import Session

from app.core.config import settings
from app.crud.puzzle import puzzle as crud_puzzle
from app.crud.user import user as crud_user
from app.models.user import User
from app.schemas.user import UserCreate


def init_db(db: Session) -> None:
    """Seeds the rows the app needs. The tables come from `alembic upgrade head`."""

    # A known sign-in for local development only; never seed it on a deployment.
    if settings.APP_ENV == "local" and not crud_user.get_user_by_email(
        db, settings.DEFAULT_USER
    ):
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
        logger.info("Created default user {}", user.username)

    seed_bot_user(db)

    crud_puzzle.seed_defaults(db)


def seed_bot_user(db: Session) -> User:
    """The computer-controlled opponent players are matched with when no human is
    available.

    It is a perfectly ordinary user row, because `match.inviter_id`/`invitee_id`
    are non-null foreign keys and nothing else in the match code needs to know the
    difference. It never signs in: it plays through `app/services/bot_turn.py`, so its
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
        logger.info("Created bot user {}", bot.username)
    if not bot.is_bot:
        # Set separately because `create_user` builds the row from `UserCreate`,
        # which has no `is_bot`. Also repairs a row seeded before the column existed.
        bot.is_bot = True
        db.add(bot)
        db.commit()
        db.refresh(bot)
    return bot
