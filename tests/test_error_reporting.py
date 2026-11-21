"""Errors reported to Sentry when SENTRY_DSN is set (`app/init_sentry.py`)."""

import json
import logging
import os
from collections.abc import Iterator
from typing import Any

import pytest
import sentry_sdk
from fastapi.testclient import TestClient
from loguru import logger
from sentry_sdk.envelope import Envelope
from sentry_sdk.transport import Transport

from app.core.config import settings
from app.crud.user import user as crud_user
from app.init_sentry import scrub_event, setup_sentry
from app.main import app
from tests.helpers import db_session

EVENTS: list[dict[str, Any]] = []


class Capture(Transport):
    """Keeps events in memory instead of sending them to sentry.io."""

    def capture_envelope(self, envelope: Envelope) -> None:
        for item in envelope.items:
            if item.type == "event" and item.payload.json is not None:
                EVENTS.append(item.payload.json)


@pytest.fixture
def sentry_events(monkeypatch) -> Iterator[list[dict[str, Any]]]:
    """Sentry set up as the server sets it up, with events captured in memory."""
    EVENTS.clear()
    monkeypatch.setattr(settings, "SENTRY_DSN", "https://public@example.com/1")
    assert setup_sentry(transport=Capture)
    yield EVENTS
    # No SENTRY_DSN in the environment (conftest drops it), so this client is inert.
    sentry_sdk.init()


@pytest.fixture
def failing_client() -> TestClient:
    """Returns 500s instead of raising. No `with`, so the app's startup doesn't run
    a second time."""
    return TestClient(app, raise_server_exceptions=False)


def _break_get_user(monkeypatch) -> None:
    def boom(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("kaboom")

    monkeypatch.setattr(crud_user, "get_user", boom)


def test_tests_see_no_dsn():
    assert "SENTRY_DSN" not in os.environ
    assert settings.SENTRY_DSN == ""


def test_no_dsn_means_no_reporting():
    assert setup_sentry() is False
    assert not sentry_sdk.get_client().is_active()


def test_scrub_event_redacts_tokens():
    event: Any = {
        "request": {"query_string": "token=abc.def&x=1"},
        "logentry": {"message": "GET /ws/snake?token=abc"},
        "breadcrumbs": {"values": [{"message": "WebSocket /ws?token=abc"}]},
    }

    scrubbed = scrub_event(event, {})

    assert scrubbed["request"]["query_string"] == "token=[redacted]&x=1"
    assert scrubbed["logentry"]["message"] == "GET /ws/snake?token=[redacted]"
    assert scrubbed["breadcrumbs"]["values"][0]["message"] == (
        "WebSocket /ws?token=[redacted]"
    )


def test_scrub_event_redacts_emails_in_urls():
    event: Any = {
        "request": {"url": "http://testserver/api/v1/password-recovery/a.b@example.com"}
    }

    assert scrub_event(event, {})["request"]["url"] == (
        "http://testserver/api/v1/password-recovery/[email]"
    )


def test_db_error_params_not_reported(sentry_events, failing_client, monkeypatch):
    # The email check passing for a taken email, as when two signups race it.
    monkeypatch.setattr(crud_user, "get_user_by_email", lambda *_a, **_k: None)

    r = failing_client.post(
        "/api/v1/users/",
        json={
            "email": settings.DEFAULT_USER,
            "username": "racer",
            "password": "secret123x",
            "display_name": "Racer",
            "birth_year": 1987,
        },
    )
    sentry_sdk.flush()

    assert r.status_code == 500
    (event,) = sentry_events
    assert [v["type"] for v in event["exception"]["values"]][-1] == "IntegrityError"
    sent = json.dumps(event)
    assert settings.DEFAULT_USER not in sent
    assert "1987" not in sent


def test_scrub_event_leaves_other_events_alone():
    event: Any = {"message": "nothing to see"}

    assert scrub_event(event, {}) == {"message": "nothing to see"}


def test_route_exception_is_reported(
    sentry_events, failing_client, auth_headers, monkeypatch
):
    _break_get_user(monkeypatch)

    r = failing_client.get("/api/v1/users/1?token=SECRET", headers=auth_headers)
    sentry_sdk.flush()

    assert r.status_code == 500
    (event,) = sentry_events
    assert event["transaction"] == "/api/v1/users/{user_id}"
    assert event["environment"] == "local"
    assert [v["type"] for v in event["exception"]["values"]] == ["RuntimeError"]
    assert event["request"]["query_string"] == "token=[redacted]"
    assert event["request"]["headers"]["authorization"] == "[Filtered]"
    sent = json.dumps(event)
    assert "SECRET" not in sent
    assert auth_headers["Authorization"].removeprefix("Bearer ") not in sent


def test_event_carries_user_id_only(
    sentry_events, failing_client, auth_headers, monkeypatch
):
    with db_session() as db:
        user = crud_user.get_user_by_email(db, settings.DEFAULT_USER)
        assert user is not None
        user_id, email = user.id, user.email
    _break_get_user(monkeypatch)

    failing_client.get("/api/v1/users/1", headers=auth_headers)
    sentry_sdk.flush()

    (event,) = sentry_events
    assert event["user"] == {"id": user_id}
    # SQL breadcrumbs name columns (birth_year among them) but carry no values.
    assert email not in json.dumps(event)


def test_http_exception_not_reported(sentry_events, failing_client, auth_headers):
    r = failing_client.get("/api/v1/users/999999", headers=auth_headers)
    sentry_sdk.flush()

    assert r.status_code == 404
    assert sentry_events == []


def test_logged_error_is_reported(sentry_events):
    logger.error("matchmaking broke")
    sentry_sdk.flush()

    (event,) = sentry_events
    assert event["logentry"]["message"] == "matchmaking broke"


def test_stdlib_error_is_one_event(sentry_events):
    logging.getLogger("uvicorn.error").error("stdlib broke")
    sentry_sdk.flush()

    assert len(sentry_events) == 1
