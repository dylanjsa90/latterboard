import time
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from app.core import google
from app.core.config import settings
from app.database import SessionLocal
from app.models import User, UserIdentity

LOGIN_URL = "/api/v1/login/google"
SIGNUP_URL = "/api/v1/users/google"
TEST_TOKEN_URL = "/api/v1/login/test-token"
CLIENT_ID = "test-client.apps.googleusercontent.com"

MAYA = google.GoogleIdentity(
    sub="google-sub-maya", email="maya@gmail.com", email_verified=True, name="Maya Ortiz"
)


@pytest.fixture
def fake_google(monkeypatch):
    """Google sign-in turned on, with credentials that name the identity they prove."""
    monkeypatch.setattr(settings, "GOOGLE_CLIENT_ID", CLIENT_ID)
    identities: dict[str, google.GoogleIdentity] = {"maya": MAYA}

    def verify(credential: str) -> google.GoogleIdentity:
        if credential not in identities:
            raise google.InvalidGoogleToken("unknown")
        return identities[credential]

    monkeypatch.setattr(google, "verify_google_id_token", verify)
    return identities


def _profile(**overrides):
    return {
        "credential": "maya",
        "username": "maya",
        "display_name": "Maya Ortiz",
        "birth_year": 1995,
        **overrides,
    }


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _identities(user_id: int) -> list[UserIdentity]:
    db = SessionLocal()
    try:
        return db.query(UserIdentity).filter(UserIdentity.user_id == user_id).all()
    finally:
        db.close()


@pytest.mark.usefixtures("fake_google")
def test_new_google_user_needs_profile(client):
    r = client.post(LOGIN_URL, json={"credential": "maya"})
    assert r.status_code == 200
    assert r.json() == {
        "status": "needs_profile",
        "email": "maya@gmail.com",
        "name": "Maya Ortiz",
    }


@pytest.mark.usefixtures("fake_google")
def test_sign_up_creates_linked_account(client):
    r = client.post(SIGNUP_URL, json=_profile(location="Lisbon"))
    assert r.status_code == 201
    me = client.post(TEST_TOKEN_URL, headers=_bearer(r.json()["access_token"]))
    assert me.status_code == 200
    body = me.json()
    assert (body["email"], body["username"], body["birth_year"], body["location"]) == (
        "maya@gmail.com",
        "maya",
        1995,
        "Lisbon",
    )
    [identity] = _identities(body["id"])
    assert (identity.provider, identity.subject) == ("google", "google-sub-maya")


@pytest.mark.usefixtures("fake_google")
def test_returning_google_user_signs_in(client):
    client.post(SIGNUP_URL, json=_profile())
    r = client.post(LOGIN_URL, json={"credential": "maya"})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "signed_in"
    assert body["token_type"] == "bearer"
    me = client.post(TEST_TOKEN_URL, headers=_bearer(body["access_token"]))
    assert me.json()["username"] == "maya"


def test_returning_user_matched_by_sub_not_email(client, fake_google):
    client.post(SIGNUP_URL, json=_profile())
    # Same Google account after its address changed.
    fake_google["maya"] = google.GoogleIdentity(
        sub=MAYA.sub, email="maya@work.example", email_verified=True, name=None
    )
    r = client.post(LOGIN_URL, json={"credential": "maya"})
    assert r.json()["status"] == "signed_in"


def test_existing_password_account_is_linked(client, fake_google):
    fake_google["first"] = google.GoogleIdentity(
        sub="google-sub-first",
        # Differently cased from the seeded account's address.
        email=settings.DEFAULT_USER.upper(),
        email_verified=True,
        name="First",
    )
    r = client.post(LOGIN_URL, json={"credential": "first"})
    assert r.json()["status"] == "signed_in"
    me = client.post(TEST_TOKEN_URL, headers=_bearer(r.json()["access_token"])).json()
    assert me["email"] == settings.DEFAULT_USER
    assert len(_identities(me["id"])) == 1

    # Signing in again doesn't link twice, and the password still works.
    client.post(LOGIN_URL, json={"credential": "first"})
    assert len(_identities(me["id"])) == 1
    r = client.post(
        "/api/v1/login/access-token",
        data={"username": settings.DEFAULT_USER, "password": settings.DEFAULT_USER_PASSWORD},
    )
    assert r.status_code == 200


def test_unverified_email_is_refused(client, fake_google):
    fake_google["unverified"] = google.GoogleIdentity(
        sub="google-sub-x", email=settings.DEFAULT_USER, email_verified=False, name=None
    )
    assert client.post(LOGIN_URL, json={"credential": "unverified"}).status_code == 403
    r = client.post(SIGNUP_URL, json=_profile(credential="unverified"))
    assert r.status_code == 403


@pytest.mark.usefixtures("fake_google")
def test_bad_credential_is_401(client):
    assert client.post(LOGIN_URL, json={"credential": "forged"}).status_code == 401
    assert client.post(SIGNUP_URL, json=_profile(credential="forged")).status_code == 401


@pytest.mark.usefixtures("fake_google")
def test_google_sign_in_off_without_client_id(client, monkeypatch):
    monkeypatch.setattr(settings, "GOOGLE_CLIENT_ID", "")
    assert client.post(LOGIN_URL, json={"credential": "maya"}).status_code == 404
    assert client.post(SIGNUP_URL, json=_profile()).status_code == 404


@pytest.mark.usefixtures("fake_google")
def test_inactive_user_is_refused(client):
    client.post(SIGNUP_URL, json=_profile())
    db = SessionLocal()
    try:
        db.query(User).filter(User.username == "maya").update({"is_active": False})
        db.commit()
    finally:
        db.close()
    assert client.post(LOGIN_URL, json={"credential": "maya"}).status_code == 400


@pytest.mark.usefixtures("fake_google")
def test_sign_up_taken_username_is_409(client):
    r = client.post(SIGNUP_URL, json=_profile(username=settings.DEFAULT_USER_USERNAME))
    assert r.status_code == 409
    assert "username" in r.json()["detail"]


@pytest.mark.usefixtures("fake_google")
def test_sign_up_twice_is_409(client):
    client.post(SIGNUP_URL, json=_profile())
    r = client.post(SIGNUP_URL, json=_profile(username="maya2"))
    assert r.status_code == 409
    assert "email" in r.json()["detail"]


@pytest.mark.usefixtures("fake_google")
def test_sign_up_under_13_is_422(client):
    r = client.post(SIGNUP_URL, json=_profile(birth_year=time.gmtime().tm_year - 5))
    assert r.status_code == 422


@pytest.mark.usefixtures("fake_google")
def test_google_only_user_has_no_usable_password(client):
    client.post(SIGNUP_URL, json=_profile())
    r = client.post(
        "/api/v1/login/access-token", data={"username": "maya@gmail.com", "password": ""}
    )
    assert r.status_code in (401, 422)


@pytest.mark.usefixtures("fake_google")
def test_deleting_user_deletes_identity(client):
    token = client.post(SIGNUP_URL, json=_profile()).json()["access_token"]
    user_id = client.post(TEST_TOKEN_URL, headers=_bearer(token)).json()["id"]
    db = SessionLocal()
    try:
        db.delete(db.get(User, user_id))
        db.commit()
    finally:
        db.close()
    assert _identities(user_id) == []


# The real check, against tokens signed by a throwaway key standing in for Google's.


@pytest.fixture
def google_key(monkeypatch):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    monkeypatch.setattr(settings, "GOOGLE_CLIENT_ID", CLIENT_ID)
    monkeypatch.setattr(
        google._jwks,
        "get_signing_key_from_jwt",
        lambda _token: SimpleNamespace(key=key.public_key()),
    )
    return key


def _id_token(key, **overrides) -> str:
    now = int(time.time())
    claims = {
        "iss": "https://accounts.google.com",
        "aud": CLIENT_ID,
        "sub": "google-sub-maya",
        "email": "maya@gmail.com",
        "email_verified": True,
        "name": "Maya Ortiz",
        "iat": now,
        "exp": now + 3600,
        **overrides,
    }
    return jwt.encode(claims, key, algorithm="RS256")


def test_verify_accepts_google_token(google_key):
    assert google.verify_google_id_token(_id_token(google_key)) == MAYA


@pytest.mark.parametrize(
    "overrides",
    [
        {"aud": "someone-elses-app.apps.googleusercontent.com"},
        {"iss": "https://evil.example.com"},
        {"exp": int(time.time()) - 60},
        {"email": None},
    ],
    ids=["wrong-audience", "wrong-issuer", "expired", "no-email"],
)
def test_verify_rejects_bad_token(google_key, overrides):
    with pytest.raises(google.InvalidGoogleToken):
        google.verify_google_id_token(_id_token(google_key, **overrides))


@pytest.mark.usefixtures("google_key")
def test_verify_rejects_token_signed_by_another_key():
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    with pytest.raises(google.InvalidGoogleToken):
        google.verify_google_id_token(_id_token(other))


@pytest.mark.usefixtures("google_key")
def test_verify_rejects_hs256_token():
    forged = jwt.encode(
        {"iss": "accounts.google.com", "aud": CLIENT_ID, "sub": "x", "email": "x@y.z"},
        "secret",
        algorithm="HS256",
    )
    with pytest.raises(google.InvalidGoogleToken):
        google.verify_google_id_token(forged)
