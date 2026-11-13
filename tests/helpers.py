"""Helpers shared across test files. Fixtures live in conftest.py."""

from collections.abc import Generator
from contextlib import contextmanager

from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.orm import Session

from app.database import SessionLocal, engine

MATCHES = "/api/v1/matches"
Headers = dict[str, str]


@contextmanager
def db_session() -> Generator[Session, None, None]:
    """A session for reading or arranging rows directly; commit explicitly."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def recorded_queries() -> Generator[list[str], None, None]:
    """The SQL statements run while the block runs, for checking a route's query count."""
    statements: list[str] = []

    def record(_conn: object, _cursor: object, statement: str, *_args: object) -> None:
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", record)
    try:
        yield statements
    finally:
        event.remove(engine, "before_cursor_execute", record)


def login(client: TestClient, email: str, password: str = "secret123") -> Headers:
    r = client.post(
        "/api/v1/login/access-token", data={"username": email, "password": password}
    )
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def signup_and_login(
    client: TestClient, email: str, username: str, password: str = "secret123"
) -> Headers:
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
    assert r.status_code == 201, r.text
    return login(client, email, password)


def start_match(
    client: TestClient,
    inviter_headers: Headers,
    opponent_headers: Headers,
    game: str = "wordle",
) -> int:
    """Invites `opponent` (the `opponent_headers` user) and accepts; returns the id."""
    r = client.post(
        f"{MATCHES}/invite",
        json={"opponent_username": "opponent", "game": game},
        headers=inviter_headers,
    )
    assert r.status_code == 201, r.text
    match_id: int = r.json()["id"]
    r = client.post(f"{MATCHES}/{match_id}/accept", headers=opponent_headers)
    assert r.status_code == 200, r.text
    return match_id


def match_detail(client: TestClient, headers: Headers, match_id: int) -> dict:
    r = client.get(f"{MATCHES}/{match_id}", headers=headers)
    assert r.status_code == 200, r.text
    return r.json()
