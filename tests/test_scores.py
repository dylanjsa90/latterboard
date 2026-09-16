import pytest

from app.core.config import settings

BASE = "/api/v1/scores"
GAME = "snake"


@pytest.fixture
def submitted_score(client, auth_headers):
    r = client.post(f"{BASE}/", json={"game": GAME, "score": 100}, headers=auth_headers)
    assert r.status_code == 201
    return r.json()


@pytest.fixture
def second_user_and_headers(client):
    payload = {
        "email": "player2@example.com",
        "username": "player2",
        "password": "secret123",
        "display_name": "Player Two",
        "birth_year": 1990,
    }
    r = client.post("/api/v1/users/", json=payload)
    assert r.status_code == 201
    r = client.post(
        "/api/v1/login/access-token",
        data={"username": "player2@example.com", "password": "secret123"},
    )
    assert r.status_code == 200
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def test_submit_score(client, auth_headers):
    r = client.post(f"{BASE}/", json={"game": GAME, "score": 40}, headers=auth_headers)
    assert r.status_code == 201
    data = r.json()
    assert data["game"] == GAME
    assert data["score"] == 40
    assert "id" in data
    assert "created_at" in data


def test_submit_score_unauthenticated(client):
    r = client.post(f"{BASE}/", json={"game": GAME, "score": 10})
    assert r.status_code == 401


def test_submit_score_rejects_unreachable_snake_scores(client, auth_headers):
    # Negative, off the 10-point step, and past a full board.
    for score in (-10, 42, 4000):
        r = client.post(
            f"{BASE}/", json={"game": GAME, "score": score}, headers=auth_headers
        )
        assert r.status_code == 422, score


def test_submit_score_accepts_a_full_snake_board(client, auth_headers):
    r = client.post(f"{BASE}/", json={"game": GAME, "score": 3990}, headers=auth_headers)
    assert r.status_code == 201


def test_submit_score_other_games_only_need_non_negative_scores(client, auth_headers):
    r = client.post(f"{BASE}/", json={"game": "other", "score": 42}, headers=auth_headers)
    assert r.status_code == 201
    r = client.post(f"{BASE}/", json={"game": "other", "score": -1}, headers=auth_headers)
    assert r.status_code == 422


def test_rejected_scores_do_not_use_a_play(client, auth_headers):
    client.post(f"{BASE}/", json={"game": GAME, "score": 42}, headers=auth_headers)
    r = client.get(f"{BASE}/me/{GAME}/plays-today", headers=auth_headers)
    assert r.json()["used"] == 0


def test_daily_play_limit(client, auth_headers):
    # Use a separate game slug so we don't exhaust the main GAME quota used by other tests
    limit_game = "snake_limit_test"
    limit = settings.MAX_DAILY_PLAYS_PER_GAME
    for _ in range(limit):
        r = client.post(
            f"{BASE}/", json={"game": limit_game, "score": 1}, headers=auth_headers
        )
        assert r.status_code == 201
    r = client.post(
        f"{BASE}/", json={"game": limit_game, "score": 1}, headers=auth_headers
    )
    assert r.status_code == 429


def test_my_scores(client, auth_headers, submitted_score):
    r = client.get(f"{BASE}/me/{GAME}", headers=auth_headers)
    assert r.status_code == 200
    ids = [s["id"] for s in r.json()]
    assert submitted_score["id"] in ids


def test_my_scores_unauthenticated(client):
    r = client.get(f"{BASE}/me/{GAME}")
    assert r.status_code == 401


@pytest.mark.usefixtures("submitted_score")
def test_my_plays_today(client, auth_headers):
    r = client.get(f"{BASE}/me/{GAME}/plays-today", headers=auth_headers)
    assert r.status_code == 200
    assert r.json() == {"used": 1, "limit": settings.MAX_DAILY_PLAYS_PER_GAME}


@pytest.mark.usefixtures("submitted_score")
def test_my_plays_today_counts_only_that_game(client, auth_headers):
    r = client.get(f"{BASE}/me/other/plays-today", headers=auth_headers)
    assert r.json()["used"] == 0


def test_my_plays_today_unauthenticated(client):
    r = client.get(f"{BASE}/me/{GAME}/plays-today")
    assert r.status_code == 401


def test_leaderboard_alltime_empty(client):
    r = client.get(f"{BASE}/leaderboard/nonexistent_game/all-time")
    assert r.status_code == 200
    assert r.json() == []


def test_leaderboard_alltime_populated(
    client, submitted_score, second_user_and_headers
):
    client.post(
        f"{BASE}/", json={"game": GAME, "score": 990}, headers=second_user_and_headers
    )
    r = client.get(f"{BASE}/leaderboard/{GAME}/all-time")
    assert r.status_code == 200
    entries = r.json()
    assert len(entries) >= 2
    for entry in entries:
        assert "rank" in entry
        assert "username" in entry
        assert "score" in entry
        assert "achieved_at" in entry


def test_leaderboard_rank_order(client, submitted_score, second_user_and_headers):
    r = client.get(f"{BASE}/leaderboard/{GAME}/all-time")
    entries = r.json()
    scores = [e["score"] for e in entries]
    assert scores == sorted(scores, reverse=True)
    assert entries[0]["rank"] == 1


def test_leaderboard_best_per_user(client, auth_headers):
    r = client.get(f"{BASE}/leaderboard/{GAME}/all-time")
    entries = r.json()
    usernames = [e["username"] for e in entries]
    assert len(usernames) == len(set(usernames)), "Each user should appear only once"


def test_leaderboard_alltime_limit(client, submitted_score):
    r = client.get(f"{BASE}/leaderboard/{GAME}/all-time?limit=1")
    assert r.status_code == 200
    assert len(r.json()) == 1


def test_leaderboard_daily_defaults_today(client, submitted_score):
    r = client.get(f"{BASE}/leaderboard/{GAME}/daily")
    assert r.status_code == 200
    assert len(r.json()) >= 1


def test_leaderboard_daily_past_date(client):
    r = client.get(f"{BASE}/leaderboard/{GAME}/daily?day=2020-01-01")
    assert r.status_code == 200
    assert r.json() == []


def test_leaderboard_monthly_defaults(client, submitted_score):
    r = client.get(f"{BASE}/leaderboard/{GAME}/monthly")
    assert r.status_code == 200
    assert len(r.json()) >= 1


def test_leaderboard_monthly_past(client):
    r = client.get(f"{BASE}/leaderboard/{GAME}/monthly?year=2020&month=1")
    assert r.status_code == 200
    assert r.json() == []
