import logging
import os
import warnings
from typing import Annotated, Any, Literal

from dotenv import load_dotenv
from pydantic import (
    AnyUrl,
    BeforeValidator,
    EmailStr,
    HttpUrl,
    computed_field,
    model_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict
from typing_extensions import Self

logger = logging.getLogger("uvicorn")

_here = os.path.dirname(os.path.abspath(__file__))
_app_dir = os.path.normpath(os.path.join(_here, ".."))  # backend/app/

logger.debug(f"app_dir: {_app_dir}")
APP_ENV = os.environ.get("APP_ENV", "local")

_envFile = ".env"
if APP_ENV == "local":
    _envFile = _envFile + ".local"
load_dotenv(os.path.join(_app_dir, _envFile))

logger.debug(f"os.environ: {os.environ}")

def parse_cors(v: Any) -> list[str] | str:
    if isinstance(v, str) and not v.startswith("["):
        return [i.strip() for i in v.split(",") if i.strip()]
    elif isinstance(v, list | str):
        return v
    raise ValueError(v)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file="app/" + _envFile,
        env_ignore_empty=True,
        extra="ignore",
    )
    API_V1_STR: str = "/api/v1"
    APP_ENV: Literal["local", "staging", "production"] = APP_ENV
    SECRET_KEY: str = os.environ.get("SECRET_KEY", "changethis")
    # 60 minutes * 24 hours * 8 days = 8 days
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 8
    FRONTEND_HOST: str = "http://localhost:5173"
    FRONTEND_HOST_ALT: str = "http://localhost:5174"
    ENVIRONMENT: Literal["local", "staging", "production"] = "local"

    BACKEND_CORS_ORIGINS: Annotated[
        list[AnyUrl] | str, BeforeValidator(parse_cors)
    ] = []

    @computed_field  # type: ignore[prop-decorator]
    @property
    def all_cors_origins(self) -> list[str]:
        return [str(origin).rstrip("/") for origin in self.BACKEND_CORS_ORIGINS] + [
            self.FRONTEND_HOST,
            self.FRONTEND_HOST_ALT,
        ]

    PROJECT_NAME: str = os.environ.get("PROJECT_NAME", "Latterboard API")
    SENTRY_DSN: HttpUrl | None = None
    SQLITE_DATABASE_URL: str = os.getenv("SQLITE_DATABASE_URL", "sqlite:///./sql_app.db")

    DB_TYPE: str = os.environ.get("DB_TYPE", "sqlite")

    # Fetch variables
    DB_USER: str = os.getenv("user")
    DB_PASSWORD: str = os.getenv("password")
    DB_HOST: str = os.getenv("host")
    DB_PORT: str = os.getenv("port")
    DB_NAME: str = os.getenv("dbname")

    @computed_field
    @property
    def DATABASE_URL(self) -> str:
        # Construct the SQLAlchemy connection string
        return self.SQLITE_DATABASE_URL if self.DB_TYPE == "sqlite" else  f"postgresql+psycopg2://{self.DB_USER}:{self.DB_PASSWORD}@{self.DB_HOST}:{self.DB_PORT}/{self.DB_NAME}?sslmode=require"


    RAW_DATABASE_URL: str = os.getenv("DATABASE_URL", "")

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

    EMAIL_TEST_USER: EmailStr = "test@example.com"
    FIRST_SUPERUSER: EmailStr = "super.user@root.com"
    FIRST_SUPERUSER_PASSWORD: str = "localroot"

    DEFAULT_USER: str = "first.user@test.com"
    DEFAULT_USER_PASSWORD: str = "password"
    DEFAULT_USER_USERNAME: str = "first.user@test.com"

    REDIS_URL: str = os.environ.get("REDIS_URL", "redis://localhost:6379")

    MAX_DAILY_PLAYS_PER_GAME: int = 5

    WS_HEARTBEAT_SECONDS: int = 30

    MATCH_MAX_GUESSES: int = 6
    MATCH_INVITE_EXPIRY_MINUTES: int = 15

    def _check_default_secret(self, var_name: str, value: str | None) -> None:
        if value == "changethis":
            message = (
                f'The value of {var_name} is "changethis", '
                "for security, please change it, at least for deployments."
            )
            if self.ENVIRONMENT == "local":
                warnings.warn(message, stacklevel=1)
            else:
                raise ValueError(message)

    @model_validator(mode="after")
    def _enforce_non_default_secrets(self) -> Self:
        self._check_default_secret("SECRET_KEY", self.SECRET_KEY)
        self._check_default_secret(
            "FIRST_SUPERUSER_PASSWORD", self.FIRST_SUPERUSER_PASSWORD
        )
        return self


settings = Settings()  # type: ignore
