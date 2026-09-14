import time

import pytest
import redis as sync_redis

from app.api.routes.matchmaking import QUEUE_PREFIX, queue_key
from app.core.config import settings

BASE = "/api/v1/matchmaking"


@pytest.fixture(autouse=True)
def empty_queues():
    """Tests share Redis and recreate users with the same ids, so a queue left over from
    another test would pair with the wrong player."""
    client = sync_redis.Redis.from_url(settings.REDIS_URL)
    for key in client.scan_iter(match=f"{QUEUE_PREFIX}:*"):
        client.delete(key)
    yield client
    client.close()


def test_first_player_waits_in_queue(client, inviter_headers):
    r = client.post(f"{BASE}/wordle", headers=inviter_headers)
    assert r.status_code == 200
    assert r.json() == {
        "status": "queued",
        "match": None,
        "queue_ttl_seconds": settings.MATCHMAKING_QUEUE_TTL_SECONDS,
    }


def test_second_player_starts_a_match_and_both_are_notified(
    client, inviter_headers, opponent_headers
):
    with client.websocket_connect(
        "/ws/lobby", headers=inviter_headers
    ) as waiting_ws, client.websocket_connect(
        "/ws/lobby", headers=opponent_headers
    ) as joining_ws:
        assert waiting_ws.receive_json()["type"] == "connected"
        assert joining_ws.receive_json()["type"] == "connected"

        assert client.post(f"{BASE}/wordle", headers=inviter_headers).json()["status"] == (
            "queued"
        )
        r = client.post(f"{BASE}/wordle", headers=opponent_headers)
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "matched"
        match = body["match"]
        assert match["status"] == "in_progress"
        assert match["game"] == "wordle"
        assert match["inviter_username"] == "inviter"
        assert match["invitee_username"] == "opponent"

        waiting_frame = waiting_ws.receive_json()
        assert waiting_frame["type"] == "match_started"
        assert waiting_frame["match_id"] == match["id"]
        assert waiting_frame["opponent_username"] == "opponent"
        joining_frame = joining_ws.receive_json()
        assert joining_frame["type"] == "match_started"
        assert joining_frame["opponent_username"] == "inviter"

    # Both players left the queue when paired, so the next one waits.
    assert client.post(f"{BASE}/wordle", headers=inviter_headers).json()["status"] == (
        "queued"
    )


def test_rejoining_never_matches_a_player_with_themselves(client, inviter_headers):
    for _ in range(2):
        assert client.post(f"{BASE}/wordle", headers=inviter_headers).json()["status"] == (
            "queued"
        )


def test_leaving_the_queue(client, inviter_headers, opponent_headers):
    client.post(f"{BASE}/wordle", headers=inviter_headers)
    assert client.delete(f"{BASE}/wordle", headers=inviter_headers).status_code == 204
    assert client.post(f"{BASE}/wordle", headers=opponent_headers).json()["status"] == (
        "queued"
    )


def test_joining_a_game_leaves_other_queues(
    client, inviter_headers, opponent_headers, empty_queues
):
    client.post(f"{BASE}/wordle", headers=inviter_headers)
    client.post(f"{BASE}/word_race", headers=inviter_headers)
    assert empty_queues.zcard(queue_key("wordle")) == 0
    assert client.post(f"{BASE}/wordle", headers=opponent_headers).json()["status"] == (
        "queued"
    )


def test_stale_queue_entries_are_skipped(client, opponent_headers, empty_queues):
    # A player who stopped re-joining (closed tab) must not be matched.
    stale = time.time() - settings.MATCHMAKING_QUEUE_TTL_SECONDS - 1
    empty_queues.zadd(queue_key("wordle"), {"1": stale})
    assert client.post(f"{BASE}/wordle", headers=opponent_headers).json()["status"] == (
        "queued"
    )


def test_unsupported_game_rejected(client, inviter_headers):
    assert client.post(f"{BASE}/chess", headers=inviter_headers).status_code == 400
    assert client.delete(f"{BASE}/chess", headers=inviter_headers).status_code == 400


def test_requires_sign_in(client):
    assert client.post(f"{BASE}/wordle").status_code == 401
