from datetime import datetime, timezone
from io import BytesIO

import pytest
from PIL import Image

from app.lib.avatar import AVATAR_MAX_BYTES

BASE = "/api/v1/users"

NEW_USER = {
    "email": "newuser@example.com",
    "username": "newuser",
    "password": "secret123",
    "display_name": "New User",
    "birth_year": 1995,
    "location": "Portland, OR",
}


def _login(client, email: str, password: str = "secret123") -> dict[str, str]:
    r = client.post(
        "/api/v1/login/access-token", data={"username": email, "password": password}
    )
    assert r.status_code == 200
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.fixture
def created_user(client):
    r = client.post(f"{BASE}/", json=NEW_USER)
    assert r.status_code == 201
    return r.json()


@pytest.fixture
def created_headers(client, created_user):
    return _login(client, created_user["email"])


def _jpeg_with_exif(size=(400, 300)) -> bytes:
    exif = Image.Exif()
    exif[0x010F] = "SecretCam"  # Make
    out = BytesIO()
    Image.new("RGB", size, "red").save(out, "JPEG", exif=exif)
    return out.getvalue()


def test_create_user(client):
    payload = {
        "email": "create_test@example.com",
        "username": "create_test",
        "password": "pw",
        "display_name": "  Create   Test ",
        "birth_year": 1990,
    }
    r = client.post(f"{BASE}/", json=payload)
    assert r.status_code == 201
    data = r.json()
    assert data["email"] == payload["email"]
    assert data["username"] == "create_test"
    assert data["display_name"] == "Create Test"
    assert data["birth_year"] == 1990
    assert data["location"] is None
    assert data["avatar_url"] is None
    assert "id" in data


def test_create_user_lowercases_username(client):
    r = client.post(
        f"{BASE}/", json={**NEW_USER, "username": "MixedCase", "email": "m@example.com"}
    )
    assert r.status_code == 201
    assert r.json()["username"] == "mixedcase"


@pytest.mark.parametrize(
    "username", ["ab", "has space", "someone@example.com", "x" * 21, "dash-name"]
)
def test_create_user_rejects_bad_usernames(client, username):
    r = client.post(f"{BASE}/", json={**NEW_USER, "username": username})
    assert r.status_code == 422


@pytest.mark.parametrize("missing", ["display_name", "birth_year"])
def test_create_user_requires_profile_fields(client, missing):
    payload = {key: value for key, value in NEW_USER.items() if key != missing}
    r = client.post(f"{BASE}/", json=payload)
    assert r.status_code == 422


def test_create_user_must_be_13(client):
    this_year = datetime.now(timezone.utc).year
    r = client.post(f"{BASE}/", json={**NEW_USER, "birth_year": this_year - 12})
    assert r.status_code == 422
    assert "13 or older" in str(r.json()["detail"])
    r = client.post(f"{BASE}/", json={**NEW_USER, "birth_year": this_year - 13})
    assert r.status_code == 201


@pytest.mark.usefixtures("created_user")
def test_create_user_duplicate_email(client):
    duplicate = {**NEW_USER, "username": "other_name"}
    r = client.post(f"{BASE}/", json=duplicate)
    assert r.status_code == 409


@pytest.mark.usefixtures("created_user")
def test_create_user_duplicate_username_any_case(client):
    r = client.post(
        f"{BASE}/", json={**NEW_USER, "email": "else@example.com", "username": "NewUser"}
    )
    assert r.status_code == 409
    assert r.json()["detail"] == "That username is taken."


def test_birth_year_is_only_visible_to_its_owner(client, auth_headers, created_user):
    assert "birth_year" not in client.get(
        f"{BASE}/{created_user['id']}", headers=auth_headers
    ).json()
    for listed in client.get(f"{BASE}/", headers=auth_headers).json():
        assert "birth_year" not in listed
    own = client.post("/api/v1/login/test-token", headers=_login(client, NEW_USER["email"]))
    assert own.json()["birth_year"] == NEW_USER["birth_year"]


def test_list_users(client, auth_headers, created_user):
    r = client.get(f"{BASE}/", headers=auth_headers)
    assert r.status_code == 200
    ids = [u["id"] for u in r.json()]
    assert created_user["id"] in ids


def test_list_users_unauthenticated(client):
    r = client.get(f"{BASE}/")
    assert r.status_code == 401


def test_get_user(client, auth_headers, created_user):
    r = client.get(f"{BASE}/{created_user['id']}", headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["email"] == NEW_USER["email"]


def test_get_user_unauthenticated(client, created_user):
    r = client.get(f"{BASE}/{created_user['id']}")
    assert r.status_code == 401


def test_get_user_not_found(client, auth_headers):
    r = client.get(f"{BASE}/99999", headers=auth_headers)
    assert r.status_code == 404


def test_update_user(client, auth_headers):
    r = client.post("/api/v1/login/test-token", headers=auth_headers)
    user_id = r.json()["id"]
    r = client.put(
        f"{BASE}/{user_id}", json={"username": "updated_name"}, headers=auth_headers
    )
    assert r.status_code == 200
    assert r.json()["username"] == "updated_name"


def test_update_user_forbidden(client, auth_headers, created_user):
    r = client.put(
        f"{BASE}/{created_user['id']}",
        json={"username": "hacked"},
        headers=auth_headers,
    )
    assert r.status_code == 403


def test_update_me(client, created_headers):
    r = client.patch(
        f"{BASE}/me",
        json={"display_name": "Maya Chen", "username": "Maya_C", "location": "Lisbon"},
        headers=created_headers,
    )
    assert r.status_code == 200
    data = r.json()
    assert (data["display_name"], data["username"], data["location"]) == (
        "Maya Chen",
        "maya_c",
        "Lisbon",
    )
    assert data["birth_year"] == NEW_USER["birth_year"]

    # Location clears; a null name or handle is ignored rather than wiping them.
    r = client.patch(
        f"{BASE}/me",
        json={"location": "", "display_name": None, "username": None},
        headers=created_headers,
    )
    data = r.json()
    assert data["location"] is None
    assert (data["display_name"], data["username"]) == ("Maya Chen", "maya_c")


def test_update_me_keeping_own_username(client, created_headers):
    r = client.patch(f"{BASE}/me", json={"username": "NEWUSER"}, headers=created_headers)
    assert r.status_code == 200


def test_update_me_username_taken(client, created_headers):
    r = client.patch(
        f"{BASE}/me", json={"username": "first_user"}, headers=created_headers
    )
    assert r.status_code == 409
    assert r.json()["detail"] == "That username is taken."


def test_update_me_validates(client, created_headers):
    this_year = datetime.now(timezone.utc).year
    for body in ({"username": "no spaces"}, {"birth_year": this_year - 5}):
        assert client.patch(f"{BASE}/me", json=body, headers=created_headers).status_code == 422


def test_update_me_unauthenticated(client):
    assert client.patch(f"{BASE}/me", json={"location": "x"}).status_code == 401


def test_players_lookup(client, auth_headers, created_user):
    r = client.get(
        f"{BASE}/players",
        params={"usernames": ["NewUser", "nobody_here", "first_user"]},
        headers=auth_headers,
    )
    assert r.status_code == 200
    players = {player["username"]: player for player in r.json()}
    assert set(players) == {"newuser", "first_user"}
    assert players["newuser"] == {
        "username": "newuser",
        "is_bot": False,
        "display_name": "New User",
        "location": "Portland, OR",
        "avatar_url": None,
        "created_at": created_user["created_at"],
    }


def test_players_lookup_unauthenticated(client):
    assert client.get(f"{BASE}/players", params={"usernames": ["x"]}).status_code == 401


def test_avatar_upload_is_reencoded_and_public(client, auth_headers, created_user, created_headers):
    r = client.put(
        f"{BASE}/me/avatar",
        files={"file": ("me.jpg", _jpeg_with_exif(), "image/jpeg")},
        headers=created_headers,
    )
    assert r.status_code == 200
    url = r.json()["avatar_url"]
    assert url.startswith(f"{BASE}/{created_user['id']}/avatar?v=")

    served = client.get(url)  # no bearer: <img> can't send one
    assert served.status_code == 200
    assert served.headers["content-type"] == "image/webp"
    assert "immutable" in served.headers["cache-control"]
    image = Image.open(BytesIO(served.content))
    assert (image.format, image.size) == ("WEBP", (256, 256))
    assert not image.getexif()

    players = client.get(
        f"{BASE}/players", params={"usernames": ["newuser"]}, headers=auth_headers
    ).json()
    assert players[0]["avatar_url"] == url


def test_avatar_rejects_non_images(client, created_headers):
    r = client.put(
        f"{BASE}/me/avatar",
        files={"file": ("me.jpg", b"not an image", "image/jpeg")},
        headers=created_headers,
    )
    assert r.status_code == 422


def test_avatar_rejects_large_files(client, created_headers):
    r = client.put(
        f"{BASE}/me/avatar",
        files={"file": ("me.jpg", b"0" * (AVATAR_MAX_BYTES + 1), "image/jpeg")},
        headers=created_headers,
    )
    assert r.status_code == 413


def test_avatar_remove(client, created_headers):
    url = client.put(
        f"{BASE}/me/avatar",
        files={"file": ("me.jpg", _jpeg_with_exif(), "image/jpeg")},
        headers=created_headers,
    ).json()["avatar_url"]
    assert client.delete(f"{BASE}/me/avatar", headers=created_headers).status_code == 204
    assert client.get(url).status_code == 404
    own = client.post("/api/v1/login/test-token", headers=created_headers).json()
    assert own["avatar_url"] is None


def test_deleting_user_removes_avatar(client, created_user, created_headers):
    url = client.put(
        f"{BASE}/me/avatar",
        files={"file": ("me.jpg", _jpeg_with_exif(), "image/jpeg")},
        headers=created_headers,
    ).json()["avatar_url"]
    r = client.delete(f"{BASE}/{created_user['id']}", headers=created_headers)
    assert r.status_code == 204
    assert client.get(url).status_code == 404


def test_delete_user(client, auth_headers):
    r = client.post("/api/v1/login/test-token", headers=auth_headers)
    user_id = r.json()["id"]
    r = client.delete(f"{BASE}/{user_id}", headers=auth_headers)
    assert r.status_code == 204


def test_delete_user_not_found(client, auth_headers):
    r = client.delete(f"{BASE}/99999", headers=auth_headers)
    assert r.status_code == 404


def test_delete_user_unauthenticated(client, created_user):
    r = client.delete(f"{BASE}/{created_user['id']}")
    assert r.status_code == 401


def test_delete_user_forbidden(client, auth_headers, created_user):
    r = client.delete(f"{BASE}/{created_user['id']}", headers=auth_headers)
    assert r.status_code == 403


def test_responses_never_include_password_hash(client, auth_headers, created_user):
    bodies = [
        created_user,
        client.get(f"{BASE}/{created_user['id']}", headers=auth_headers).json(),
        *client.get(f"{BASE}/", headers=auth_headers).json(),
        client.post("/api/v1/login/test-token", headers=auth_headers).json(),
    ]
    for body in bodies:
        assert "hashed_password" not in body
