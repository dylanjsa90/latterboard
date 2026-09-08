import pytest

BASE = "/api/v1/users"

NEW_USER = {
    "email": "newuser@example.com",
    "username": "newuser@example.com",
    "password": "secret123",
}


@pytest.fixture
def created_user(client):
    r = client.post(f"{BASE}/", json=NEW_USER)
    assert r.status_code == 201
    return r.json()


def test_create_user(client):
    payload = {
        "email": "create_test@example.com",
        "username": "create_test@example.com",
        "password": "pw",
    }
    r = client.post(f"{BASE}/", json=payload)
    assert r.status_code == 201
    data = r.json()
    assert data["email"] == payload["email"]
    assert data["username"] == payload["username"]
    assert "id" in data


def test_create_user_duplicate_email(client, created_user):
    duplicate = {**NEW_USER, "username": "other_name"}
    r = client.post(f"{BASE}/", json=duplicate)
    assert r.status_code == 409


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


def test_delete_user(client, auth_headers):
    r = client.post("/api/v1/login/test-token", headers=auth_headers)
    user_id = r.json()["id"]
    r = client.delete(f"{BASE}/{user_id}", headers=auth_headers)
    assert r.status_code == 204


def test_delete_user_not_found(client, auth_headers):
    r = client.delete(f"{BASE}/99999", headers=auth_headers)
    assert r.status_code == 404


def test_delete_user_forbidden(client, auth_headers, created_user):
    r = client.delete(f"{BASE}/{created_user['id']}")
    assert r.status_code == 401
