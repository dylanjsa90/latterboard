import os
import warnings
from typing import Annotated, Any, Literal, cast

from dotenv import load_dotenv
from pydantic import (
    AnyUrl,
    BeforeValidator,
    EmailStr,
    computed_field,
    model_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict
from typing_extensions import Self

_app_dir = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
)

# app/.env.local while APP_ENV=local (the default), app/.env otherwise. Real environment
# variables win over the file. Loaded into os.environ (rather than pydantic's env_file)
# because the lowercase database names below are read from there.
APP_ENV = os.environ.get("APP_ENV", "local")
load_dotenv(os.path.join(_app_dir, ".env.local" if APP_ENV == "local" else ".env"))


def parse_cors(v: Any) -> list[str] | str:
    if isinstance(v, str) and not v.startswith("["):
        return [i.strip() for i in v.split(",") if i.strip()]
    elif isinstance(v, list | str):
        return v
    raise ValueError(v)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_ignore_empty=True,
        extra="ignore",
    )
    API_V1_STR: str = "/api/v1"
    APP_ENV: Literal["local", "staging", "production"] = cast(
        Literal["local", "staging", "production"], APP_ENV
    )
    SECRET_KEY: str = "changethis"
    # 60 minutes * 24 hours * 8 days = 8 days
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 8
    # The web client ID from Google Cloud, the same one webcade uses. It's public, and
    # only an ID token issued for it is accepted. Empty turns Google sign-in off (404).
    GOOGLE_CLIENT_ID: str = ""

    FRONTEND_HOST: str = "http://localhost:5173"
    FRONTEND_HOST_ALT: str = "http://localhost:5174"
    # `vite preview`, for checking a production build locally.
    FRONTEND_HOST_PREVIEW: str = "http://localhost:4173"
    DEPLOYED_HOSTS: list[str] = [
        "https://preview.vocabchallenger.com",
        "https://vocabchallenger.com",
    ]

    DEBUG: bool = False
    BACKEND_CORS_ORIGINS: Annotated[
        list[AnyUrl] | str, BeforeValidator(parse_cors)
    ] = []

    @computed_field  # type: ignore[prop-decorator]
    @property
    def all_cors_origins(self) -> list[str]:
        return (
            [str(origin).rstrip("/") for origin in self.BACKEND_CORS_ORIGINS]
            + [
                self.FRONTEND_HOST,
                self.FRONTEND_HOST_ALT,
                self.FRONTEND_HOST_PREVIEW,
            ]
            + self.DEPLOYED_HOSTS
        )

    PROJECT_NAME: str = "Latterboard API"
    SQLITE_DATABASE_URL: str = "sqlite:///./sql_app.db"

    DB_TYPE: str = "sqlite"

    # Postgres, used when DB_TYPE isn't "sqlite". Set DB_USER, DB_PASSWORD, ... or, as
    # older deployments do, the lowercase user, password, host, port, and dbname.
    DB_USER: str = os.getenv("user", "postgres")
    DB_PASSWORD: str = os.getenv("password", "")
    DB_HOST: str = os.getenv("host", "localhost")
    DB_PORT: str = os.getenv("port", "5432")
    DB_NAME: str = os.getenv("dbname", "postgres")

    @computed_field  # type: ignore[prop-decorator]
    @property
    def DATABASE_URL(self) -> str:
        return (
            self.SQLITE_DATABASE_URL
            if self.DB_TYPE == "sqlite"
            else f"postgresql+psycopg2://{self.DB_USER}:{self.DB_PASSWORD}@{self.DB_HOST}:{self.DB_PORT}/{self.DB_NAME}?sslmode=require"
        )

    SMTP_TLS: bool = True
    SMTP_SSL: bool = False
    SMTP_PORT: int = 587
    SMTP_HOST: str | None = None
    SMTP_USER: str | None = None
    SMTP_PASSWORD: str | None = None
    EMAILS_FROM_EMAIL: EmailStr | None = None
    EMAILS_FROM_NAME: str | None = None

    @model_validator(mode="after")
    def _set_default_emails_from(self) -> Self:
        if not self.EMAILS_FROM_NAME:
            self.EMAILS_FROM_NAME = self.PROJECT_NAME
        return self

    EMAIL_RESET_TOKEN_EXPIRE_HOURS: int = 48

    @computed_field  # type: ignore[prop-decorator]
    @property
    def emails_enabled(self) -> bool:
        return bool(self.SMTP_HOST and self.EMAILS_FROM_EMAIL)

    # Seeded only when APP_ENV=local, for signing in during development.
    DEFAULT_USER: str = "first.user@test.com"
    DEFAULT_USER_PASSWORD: str = "password"
    DEFAULT_USER_USERNAME: str = "first_user"

    REDIS_URL: str = "redis://localhost:6379"

    MAX_DAILY_PLAYS_PER_GAME: int = 5

    WS_HEARTBEAT_SECONDS: int = 30

    MATCH_MAX_GUESSES: int = 6
    MATCH_INVITE_EXPIRY_MINUTES: int = 15
    # Friend invite links, sent by text or email, wait far longer than username invites.
    MATCH_INVITE_LINK_EXPIRY_DAYS: int = 7
    # Links one player can create per UTC day; also caps the invite email we send.
    MATCH_INVITE_LINK_DAILY_LIMIT: int = 20
    MATCHMAKING_QUEUE_TTL_SECONDS: int = 60
    # How long a player waits in the queue before we offer them the computer.
    # The client re-POSTs every TTL/3 (~20s), so this lands on their second poll.
    MATCHMAKING_BOT_WAIT_SECONDS: int = 20

    # The computer-controlled opponent. It never logs in: `init_db` gives it a
    # random password, and it plays through `app/services/bot_turn.py`, not HTTP.
    BOT_OPPONENT_ENABLED: bool = True
    BOT_USER_EMAIL: str = "robo@latterboard.com"
    BOT_USER_USERNAME: str = "robo"
    BOT_DISPLAY_NAME: str = "Robo"
    # Each bot move waits DELAY + random(0, JITTER) seconds, so it reads as
    # thinking rather than as a machine. Tests set the delay to 0.
    BOT_TURN_DELAY_SECONDS: float = 2.0
    BOT_TURN_DELAY_JITTER_SECONDS: float = 3.0
    # 0.0 = careless, 1.0 = plays the best candidate it knows every time.
    BOT_SKILL: float = 0.75

    def _check_default_secret(self, var_name: str, value: str | None) -> None:
        if value in (None, "", "changethis"):
            message = (
                f'{var_name} is empty or "changethis". Set a real value; '
                "it is required everywhere except APP_ENV=local."
            )
            if self.APP_ENV == "local":
                warnings.warn(message, stacklevel=1)
            else:
                raise ValueError(message)

    @model_validator(mode="after")
    def _enforce_non_default_secrets(self) -> Self:
        self._check_default_secret("SECRET_KEY", self.SECRET_KEY)
        return self


settings = Settings()
