import os

# Must precede all app imports so Settings() reads the test URL
os.environ["DATABASE_URL"] = "sqlite:///./test_app.db"
os.environ["SQLITE_DATABASE_URL"] = "sqlite:///./test_app.db"
os.environ["DB_TYPE"] = "sqlite"
# Keep heartbeat frames from interleaving with the frames websocket tests assert on
os.environ["WS_HEARTBEAT_SECONDS"] = "3600"
import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_db
from app.core.base_class import Base
from app.core.config import settings
from app.database import SessionLocal, engine
from app.init_db import init_db
from app.main import app


def override_get_db():
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


app.dependency_overrides[get_db] = override_get_db


@pytest.fixture(scope="session")
def client():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    with TestClient(app) as c:
        yield c


@pytest.fixture(autouse=True)
def reset_db():
    """Reset database to a clean seeded state before each test."""
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        init_db(db)
        db.commit()
    finally:
        db.close()


@pytest.fixture
def auth_headers(client):
    """Bearer token for the default user seeded by init_db."""
    r = client.post(
        "/api/v1/login/access-token",
        data={"username": settings.DEFAULT_USER, "password": settings.DEFAULT_USER_PASSWORD},
    )
    assert r.status_code == 200
    token = r.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def _signup_and_login(client, email: str, username: str, password: str = "secret123"):
    r = client.post(
        "/api/v1/users/",
        json={
            "email": email,
            "username": username,
            "password": password,
            "display_name": username.title(),
            "birth_year": 1990,
        },
    )
    assert r.status_code == 201
    r = client.post(
        "/api/v1/login/access-token",
        data={"username": email, "password": password},
    )
    assert r.status_code == 200
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.fixture
def inviter_headers(client):
    return _signup_and_login(client, "inviter@example.com", "inviter")


@pytest.fixture
def opponent_headers(client):
    return _signup_and_login(client, "opponent@example.com", "opponent")
