import os

# Must precede all app imports so Settings() reads the test URL
os.environ["DATABASE_URL"] = "sqlite:///./test_app.db"

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_db
from app.core.base_class import Base
from app.database import engine, SessionLocal
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
    with TestClient(app) as c:  # startup event seeds default.user@dev.com
        yield c


@pytest.fixture(scope="session")
def auth_headers(client):
    """Bearer token for the default user seeded by init_db."""
    r = client.post(
        "/api/v1/login/access-token",
        data={"username": "default.user@dev.com", "password": "password"},
    )
    assert r.status_code == 200
    token = r.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}
