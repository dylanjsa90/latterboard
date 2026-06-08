LOGIN_URL = "/api/v1/login/access-token"
TEST_TOKEN_URL = "/api/v1/login/test-token"

# Credentials for the default user seeded by init_db
DEFAULT_EMAIL = "default.user@dev.com"
DEFAULT_PASSWORD = "password"


def test_login_valid_credentials(client):
    r = client.post(LOGIN_URL, data={"username": DEFAULT_EMAIL, "password": DEFAULT_PASSWORD})
    assert r.status_code == 200
    body = r.json()
    assert "access_token" in body
    assert body["token_type"] == "bearer"


def test_login_wrong_password(client):
    r = client.post(LOGIN_URL, data={"username": DEFAULT_EMAIL, "password": "wrong"})
    assert r.status_code == 400


def test_login_unknown_email(client):
    r = client.post(LOGIN_URL, data={"username": "nobody@example.com", "password": "pw"})
    assert r.status_code == 400


def test_test_token_valid(client, auth_headers):
    r = client.post(TEST_TOKEN_URL, headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["email"] == DEFAULT_EMAIL


def test_test_token_missing(client):
    r = client.post(TEST_TOKEN_URL)
    assert r.status_code == 401
