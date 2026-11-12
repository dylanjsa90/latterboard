import atexit
import os
import shutil
import tempfile

# Settings() reads the environment at import, so these must precede every app import.
# Only real env vars count: a developer's app/.env.local (SMTP, Google, a Postgres
# database) must not leak into the tests.
os.environ["SKIP_ENV_FILE"] = "1"
# A local install, whatever the shell says: only a local install seeds the default
# user the tests sign in as.
os.environ["APP_ENV"] = "local"
os.environ["SECRET_KEY"] = "tests-only-secret-key-at-least-32-bytes"
# A private database per run, so concurrent runs (or a dev server) can't collide.
os.environ["TEST_DB_DIR"] = tempfile.mkdtemp(prefix="latterboard-tests-")
os.environ["DB_TYPE"] = "sqlite"
os.environ["SQLITE_DATABASE_URL"] = f"sqlite:///{os.environ['TEST_DB_DIR']}/test.db"
# Keep heartbeat frames from interleaving with the frames websocket tests assert on
os.environ["WS_HEARTBEAT_SECONDS"] = "3600"
# The computer opponent moves in a background task, which the sync TestClient runs
# to completion before returning. With no thinking pause its frames therefore land
# in a deterministic order, and no test has to wait on a timer.
os.environ["BOT_TURN_DELAY_SECONDS"] = "0"
os.environ["BOT_TURN_DELAY_JITTER_SECONDS"] = "0"
import pytest
import redis as sync_redis
from fastapi.testclient import TestClient
from pwdlib import PasswordHash
from pwdlib.hashers.argon2 import Argon2Hasher
from sqlalchemy import event

from app.api.routes.matchmaking import QUEUE_PREFIX, WAITING_SINCE_PREFIX
from app.core import security
from app.core.base_class import Base
from app.core.config import settings
from app.database import engine
from app.init_db import init_db
from app.main import app
from app.models import User
from tests.helpers import db_session, signup_and_login

atexit.register(shutil.rmtree, os.environ["TEST_DB_DIR"], ignore_errors=True)

# Production argon2 settings cost ~0.1 s per hash or check, and every test seeds two
# users and most sign in. Hashes stay argon2, just cheap ones.
security.password_hash = PasswordHash(
    (Argon2Hasher(time_cost=1, memory_cost=1024, parallelism=1),)
)


@event.listens_for(engine, "connect")
def _skip_fsync(dbapi_connection, _record):
    """The test database is rebuilt for every test, so durability buys nothing. With
    it, each DDL commit in the reset waited on the disk: seconds per test on WSL."""
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA synchronous=OFF")
    cursor.execute("PRAGMA journal_mode=MEMORY")
    cursor.close()


@pytest.fixture(scope="session")
def client():
    # Session-scoped fixtures run before the per-test reset, and the app's startup
    # seeds rows, so the tables must exist already.
    Base.metadata.create_all(bind=engine)
    with TestClient(app) as c:
        yield c


@pytest.fixture(autouse=True)
def reset_db():
    """Reset database to a clean seeded state before each test."""
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    with db_session() as db:
        init_db(db)
        db.commit()


@pytest.fixture
def auth_headers(client):
    """Bearer token for the default user seeded by init_db."""
    r = client.post(
        "/api/v1/login/access-token",
        data={
            "username": settings.DEFAULT_USER,
            "password": settings.DEFAULT_USER_PASSWORD,
        },
    )
    assert r.status_code == 200
    token = r.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def superuser_headers(auth_headers):
    """The default user, promoted to superuser."""
    with db_session() as db:
        user = db.query(User).filter(User.email == settings.DEFAULT_USER).one()
        user.is_superuser = True
        db.commit()
    return auth_headers


@pytest.fixture
def inviter_headers(client):
    return signup_and_login(client, "inviter@example.com", "inviter")


@pytest.fixture
def opponent_headers(client):
    return signup_and_login(client, "opponent@example.com", "opponent")


@pytest.fixture
def empty_queues():
    """Clears matchmaking state; yields a Redis client for inspecting it.

    Tests share Redis and recreate users with the same ids, so a queue left over from
    another test would pair with the wrong player. The waiting-since hashes go too: a
    leftover entry makes the HSETNX a no-op, so the next test's player looks like they
    have been waiting for hours and is handed the computer on their first call.
    """
    client = sync_redis.Redis.from_url(settings.REDIS_URL)
    for prefix in (QUEUE_PREFIX, WAITING_SINCE_PREFIX):
        for key in client.scan_iter(match=f"{prefix}:*"):
            client.delete(key)
    yield client
    client.close()
