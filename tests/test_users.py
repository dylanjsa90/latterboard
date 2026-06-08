import pytest

BASE = "/api/v1/users"

NEW_USER = {
    "email": "newuser@example.com",
    "username": "newuser",
    "password": "secret123",
}


@pytest.fixture(scope="module")
def created_user(client):
    """Create a user once for this module, delete on teardown."""
    r = client.post(f"{BASE}/", json=NEW_USER)
    assert r.status_code == 201
    user = r.json()
    yield user
    client.delete(f"{BASE}/{user['id']}")


def test_create_user(client):
    payload = {"email": "create_test@example.com", "username": "create_test", "password": "pw"}
    r = client.post(f"{BASE}/", json=payload)
    assert r.status_code == 201
    data = r.json()
    assert data["email"] == payload["email"]
    assert data["username"] == payload["username"]
    assert "id" in data
    client.delete(f"{BASE}/{data['id']}")


def test_create_user_duplicate_email(client, created_user):
    duplicate = {**NEW_USER, "username": "other_name"}
    r = client.post(f"{BASE}/", json=duplicate)
    assert r.status_code == 409


def test_list_users(client, created_user):
    r = client.get(f"{BASE}/")
    assert r.status_code == 200
    ids = [u["id"] for u in r.json()]
    assert created_user["id"] in ids


def test_get_user(client, created_user):
    r = client.get(f"{BASE}/{created_user['id']}")
    assert r.status_code == 200
    assert r.json()["email"] == NEW_USER["email"]


def test_get_user_not_found(client):
    r = client.get(f"{BASE}/99999")
    assert r.status_code == 404


def test_update_user(client, created_user):
    r = client.put(f"{BASE}/{created_user['id']}", json={"username": "updated_name"})
    assert r.status_code == 200
    assert r.json()["username"] == "updated_name"


def test_delete_user(client):
    r = client.post(f"{BASE}/", json={"email": "todelete@example.com", "username": "todelete", "password": "pw"})
    assert r.status_code == 201
    user_id = r.json()["id"]
    r = client.delete(f"{BASE}/{user_id}")
    assert r.status_code == 204


def test_delete_user_not_found(client):
    r = client.delete(f"{BASE}/99999")
    assert r.status_code == 404
