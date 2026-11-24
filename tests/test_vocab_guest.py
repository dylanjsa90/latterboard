"""Guest rooms: solo practice without an account (app/api/routes/vocab.py)."""

import json

import pytest
import redis as sync_redis
from redis.exceptions import ConnectionError as RedisConnectionError

from app.api.routes import vocab as vocab_routes
from app.core import rate_limit
from app.core.config import settings
from app.game import vocab
from tests.helpers import vocab_key, vocab_state

BASE = "/api/v1/vocab"
GUEST = "X-Guest-Token"


def _guest(client) -> tuple[dict, dict[str, str]]:
    r = client.post(f"{BASE}/guest-rooms")
    assert r.status_code == 201, r.text
    body = r.json()
    return body["game"], {GUEST: body["guest_token"]}


def _act(client, headers, game: dict, action: str, **extra):
    body = {"action": action, "generation": game["generation"], **extra}
    return client.post(
        f"{BASE}/rooms/{game['code']}/actions", json=body, headers=headers
    )


def test_guest_plays_five_rounds_and_a_rematch(client):
    game, guest = _guest(client)
    assert (game["mode"], game["phase"], game["round"]) == ("solo", "question", 0)
    assert game["players"][0]["name"] == "Guest"
    for r in range(vocab.ROUNDS):
        key = vocab_key(game["code"])
        answered = _act(client, guest, game, "answer", round=r, choice=key)
        assert answered.json()["game"]["phase"] == "feedback"
        game = _act(client, guest, game, "next").json()["game"]
    assert game["phase"] == "results"
    assert game["players"][0]["score"] >= 500
    again = _act(client, guest, game, "rematch").json()["game"]
    assert (again["phase"], again["generation"]) == ("question", 1)


def test_guest_token_opens_only_its_own_solo_room(
    client, auth_headers, inviter_headers
):
    game, guest = _guest(client)
    code = game["code"]
    assert client.get(f"{BASE}/rooms/{code}").status_code == 401
    assert (
        client.get(f"{BASE}/rooms/{code}", headers={GUEST: "x" * 43}).status_code == 403
    )
    assert (
        client.get(f"{BASE}/rooms/{code}", headers={GUEST: "short"}).status_code == 422
    )
    duel = client.post(f"{BASE}/rooms", json={"mode": "duel"}, headers=inviter_headers)
    duel_code = duel.json()["game"]["code"]
    assert client.get(f"{BASE}/rooms/{duel_code}", headers=guest).status_code == 403
    assert (
        client.post(f"{BASE}/rooms/{duel_code}/join", headers=guest).status_code == 401
    )
    assert client.post(f"{BASE}/rooms", json={"mode": "solo"}).status_code == 401
    # Only the create call returns the token; the stored room keeps a hash of it.
    assert guest[GUEST] not in json.dumps(vocab_state(code))
    assert guest[GUEST] not in client.get(f"{BASE}/rooms/{code}", headers=guest).text
    # The guest header wins, so a tab that just signed in can still read its room.
    both = client.get(f"{BASE}/rooms/{code}", headers={**guest, **auth_headers})
    assert both.status_code == 200


def test_claim_moves_the_guest_seat_to_the_account(client, auth_headers):
    me = client.post("/api/v1/login/test-token", headers=auth_headers).json()
    game, guest = _guest(client)
    code = game["code"]
    _act(client, guest, game, "answer", round=0, choice=vocab_key(code))
    claim = f"{BASE}/rooms/{code}/claim"
    assert client.post(claim, headers=guest).status_code == 401
    r = client.post(claim, headers={**guest, **auth_headers})
    assert r.status_code == 200, r.text
    claimed = r.json()["game"]
    assert claimed["phase"] == "feedback"
    assert claimed["players"][0]["name"] == me["username"]
    assert claimed["players"][0]["score"] > 0
    assert claimed["history"][0]["answers"][0]["name"] == me["username"]
    assert client.get(f"{BASE}/rooms/{code}", headers=auth_headers).status_code == 200
    assert client.get(f"{BASE}/rooms/{code}", headers=guest).status_code == 403
    assert client.post(claim, headers={**guest, **auth_headers}).status_code == 403


def _clear_guest_limits() -> int:
    """Deletes every guest counter; returns the TTL the network one had, -2 if none."""
    client = sync_redis.Redis.from_url(settings.REDIS_URL)
    ttl = client.ttl(f"{vocab_routes.GUEST_LIMIT_PREFIX}:net:testclient")
    for key in client.scan_iter(match=f"{vocab_routes.GUEST_LIMIT_PREFIX}:*"):
        client.delete(key)
    client.close()
    return int(ttl)


@pytest.fixture(autouse=True)
def fresh_guest_limits():
    """Tests share Redis across runs, so earlier counts would refuse this run's rooms."""
    _clear_guest_limits()
    yield
    _clear_guest_limits()


def test_guest_rooms_are_limited_per_network_then_site_wide(
    client, monkeypatch, auth_headers
):
    monkeypatch.setattr(vocab_routes, "GUEST_ROOMS_PER_NETWORK_PER_HOUR", 2)
    _guest(client)
    _guest(client)
    r = client.post(f"{BASE}/guest-rooms")
    assert (r.status_code, r.json()["detail"]) == (
        429,
        vocab_routes.GUEST_NETWORK_LIMITED,
    )
    # Signed-in rooms are neither counted nor limited.
    solo = client.post(f"{BASE}/rooms", json={"mode": "solo"}, headers=auth_headers)
    assert solo.status_code == 201
    assert 0 < _clear_guest_limits() <= vocab_routes.GUEST_WINDOW_SECONDS

    monkeypatch.setattr(vocab_routes, "GUEST_ROOMS_PER_NETWORK_PER_HOUR", 100)
    monkeypatch.setattr(vocab_routes, "GUEST_ROOMS_PER_HOUR", 1)
    _guest(client)
    r = client.post(f"{BASE}/guest-rooms")
    assert (r.status_code, r.json()["detail"]) == (429, vocab_routes.GUEST_SITE_LIMITED)


def test_guest_rooms_still_open_when_redis_is_down(client, monkeypatch):
    class DownRedis:
        def pipeline(self, *args: object, **kwargs: object) -> None:
            raise RedisConnectionError("down")

    monkeypatch.setattr(rate_limit, "redis", DownRedis())
    _guest(client)
