"""Startup paths the server runs before it serves a request."""

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.database import engine
from app.init_db import init_db
from app.models.user import User
from tests.helpers import db_session


def test_pre_start_probes_the_database():
    # The Docker CMD runs this module before uvicorn, so an import error here stops
    # every deploy.
    from app.backend_pre_start import init

    init(engine)


@pytest.mark.parametrize("secret", ["", "changethis"])
def test_deployed_settings_refuse_a_default_secret(secret):
    with pytest.raises(ValidationError, match="SECRET_KEY"):
        Settings(APP_ENV="production", SECRET_KEY=secret)


def test_local_settings_allow_the_default_secret():
    with pytest.warns(UserWarning, match="SECRET_KEY"):
        Settings(APP_ENV="local", SECRET_KEY="changethis")


def test_default_user_is_seeded_only_locally(monkeypatch):
    from app.core.config import settings

    with db_session() as db:
        db.query(User).filter(User.email == settings.DEFAULT_USER).delete()
        db.commit()
        monkeypatch.setattr(settings, "APP_ENV", "production")
        init_db(db)
        db.commit()
        assert (
            db.query(User).filter(User.email == settings.DEFAULT_USER).first() is None
        )
        # The computer opponent is needed everywhere.
        assert db.query(User).filter(User.email == settings.BOT_USER_EMAIL).first()
