import re
from datetime import datetime, timedelta, timezone

import jwt
import pytest

from app.api.routes import login as login_route
from app.core import security
from app.core.config import settings
from app.models import User
from tests.helpers import db_session, login

LOGIN_URL = "/api/v1/login/access-token"
TEST_TOKEN_URL = "/api/v1/login/test-token"

# Credentials for the default user seeded by init_db
DEFAULT_EMAIL = settings.DEFAULT_USER
DEFAULT_PASSWORD = settings.DEFAULT_USER_PASSWORD


def test_login_valid_credentials(client):
    r = client.post(
        LOGIN_URL, data={"username": DEFAULT_EMAIL, "password": DEFAULT_PASSWORD}
    )
    assert r.status_code == 200
    body = r.json()
    assert "access_token" in body
    assert body["token_type"] == "bearer"


def test_login_wrong_password(client):
    r = client.post(LOGIN_URL, data={"username": DEFAULT_EMAIL, "password": "wrong"})
    assert r.status_code == 401


def test_login_unknown_email(client):
    r = client.post(
        LOGIN_URL, data={"username": "nobody@example.com", "password": "pw"}
    )
    assert r.status_code == 401


def test_test_token_valid(client, auth_headers):
    r = client.post(TEST_TOKEN_URL, headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["email"] == DEFAULT_EMAIL


def test_test_token_missing(client):
    r = client.post(TEST_TOKEN_URL)
    assert r.status_code == 401


RECOVERY_URL = "/api/v1/password-recovery"
RESET_URL = "/api/v1/reset-password/"


def test_password_recovery_without_smtp_gives_the_usual_answer(client, monkeypatch):
    # With SMTP off, a registered email must get the same 200 as an unknown one,
    # not a 500 from `send_email`, or the response gives away who has an account.
    monkeypatch.setattr(settings, "SMTP_HOST", None)
    known = client.post(f"{RECOVERY_URL}/{DEFAULT_EMAIL}")
    unknown = client.post(f"{RECOVERY_URL}/nobody@example.com")
    assert known.status_code == unknown.status_code == 200
    assert known.json() == unknown.json()


def test_reset_password_rejects_a_malformed_token(client):
    r = client.post(
        RESET_URL, json={"token": "not-a-jwt", "new_password": "newpass123"}
    )
    assert r.status_code == 400


# --- password recovery and reset, with email on ------------------------------


@pytest.fixture
def sent_emails(monkeypatch) -> list[dict]:
    """Turns email on and captures what the recovery route would send."""
    sent: list[dict] = []
    monkeypatch.setattr(login_route, "send_email", lambda **kw: sent.append(kw))
    monkeypatch.setattr(settings, "SMTP_HOST", "smtp.example.com")
    monkeypatch.setattr(settings, "EMAILS_FROM_EMAIL", "games@example.com")
    return sent


def _reset_token(email: dict) -> str:
    match = re.search(r"/reset-password\?token=([\w.-]+)", email["html_content"])
    assert match, "the email should carry a reset link"
    return match.group(1)


def test_password_recovery_emails_a_link_that_resets_the_password(client, sent_emails):
    r = client.post(f"{RECOVERY_URL}/{DEFAULT_EMAIL}")
    assert r.status_code == 200
    assert [email["email_to"] for email in sent_emails] == [DEFAULT_EMAIL]

    token = _reset_token(sent_emails[0])
    r = client.post(RESET_URL, json={"token": token, "new_password": "brand-new-pw"})
    assert r.status_code == 200

    old = {"username": DEFAULT_EMAIL, "password": DEFAULT_PASSWORD}
    assert client.post(LOGIN_URL, data=old).status_code == 401
    login(client, DEFAULT_EMAIL, "brand-new-pw")


def test_password_recovery_for_an_unknown_email_sends_nothing(client, sent_emails):
    known = client.post(f"{RECOVERY_URL}/{DEFAULT_EMAIL}")
    unknown = client.post(f"{RECOVERY_URL}/nobody@example.com")
    assert known.json() == unknown.json()
    assert len(sent_emails) == 1


def _expired_token(email: str) -> str:
    past = datetime.now(timezone.utc) - timedelta(hours=1)
    return jwt.encode(
        {"exp": past, "nbf": past - timedelta(hours=1), "sub": email},
        settings.SECRET_KEY,
        algorithm=security.ALGORITHM,
    )


def _access_token(client) -> str:
    return login(client, DEFAULT_EMAIL, DEFAULT_PASSWORD)["Authorization"].split()[1]


@pytest.mark.parametrize(
    "make_token",
    [
        lambda _client: _expired_token(DEFAULT_EMAIL),
        lambda _client: security.generate_password_reset_token("nobody@example.com"),
        # A sign-in token names a user id, not an email, so it can't reset anything.
        _access_token,
    ],
    ids=["expired", "unknown-email", "access-token"],
)
def test_reset_password_rejects_a_token_it_did_not_issue_for_this(client, make_token):
    r = client.post(
        RESET_URL, json={"token": make_token(client), "new_password": "brand-new-pw"}
    )
    assert r.status_code == 400
    login(client, DEFAULT_EMAIL, DEFAULT_PASSWORD)


def test_reset_password_refuses_an_inactive_account(client):
    with db_session() as db:
        user = db.query(User).filter(User.email == DEFAULT_EMAIL).one()
        user.is_active = False
        db.commit()
    token = security.generate_password_reset_token(DEFAULT_EMAIL)
    r = client.post(RESET_URL, json={"token": token, "new_password": "brand-new-pw"})
    assert r.status_code == 400
    assert r.json()["detail"] == "Inactive user"


# --- the superuser-only preview of the recovery email -------------------------

HTML_CONTENT_URL = "/api/v1/password-recovery-html-content"


def test_recovery_email_preview_is_for_superusers(client, auth_headers):
    r = client.post(f"{HTML_CONTENT_URL}/{DEFAULT_EMAIL}", headers=auth_headers)
    assert r.status_code == 403


def test_recovery_email_preview(client, superuser_headers):
    r = client.post(f"{HTML_CONTENT_URL}/{DEFAULT_EMAIL}", headers=superuser_headers)
    assert r.status_code == 200
    assert "/reset-password?token=" in r.text
    assert DEFAULT_EMAIL in r.headers["subject"]

    r = client.post(f"{HTML_CONTENT_URL}/nobody@example.com", headers=superuser_headers)
    assert r.status_code == 404
