from datetime import date, datetime, timedelta

import pytest

from app.core.config import settings
from app.crud.game_score import game_score as crud_score
from app.crud.user import user as crud_user
from app.database import SessionLocal
from app.game.puzzles import day_start, today
from app.models.game_score import GameScore

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


@pytest.mark.usefixtures("submitted_score")
def test_leaderboard_alltime_populated(client, second_user_and_headers):
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


@pytest.mark.usefixtures("submitted_score", "second_user_and_headers")
def test_leaderboard_rank_order(client):
    r = client.get(f"{BASE}/leaderboard/{GAME}/all-time")
    entries = r.json()
    scores = [e["score"] for e in entries]
    assert scores == sorted(scores, reverse=True)
    assert entries[0]["rank"] == 1


def test_leaderboard_best_per_user(client, auth_headers):
    client.post(f"{BASE}/", json={"game": GAME, "score": 100}, headers=auth_headers)
    client.post(f"{BASE}/", json={"game": GAME, "score": 200}, headers=auth_headers)
    r = client.get(f"{BASE}/leaderboard/{GAME}/all-time")
    entries = r.json()
    usernames = [e["username"] for e in entries]
    assert len(usernames) == len(set(usernames)), "Each user should appear only once"
    assert entries[0]["score"] == 200


@pytest.mark.usefixtures("submitted_score")
def test_leaderboard_alltime_limit(client):
    r = client.get(f"{BASE}/leaderboard/{GAME}/all-time?limit=1")
    assert r.status_code == 200
    assert len(r.json()) == 1


@pytest.mark.usefixtures("submitted_score")
def test_leaderboard_daily_defaults_today(client):
    r = client.get(f"{BASE}/leaderboard/{GAME}/daily")
    assert r.status_code == 200
    assert len(r.json()) >= 1


def test_leaderboard_daily_past_date(client):
    r = client.get(f"{BASE}/leaderboard/{GAME}/daily?day=2020-01-01")
    assert r.status_code == 200
    assert r.json() == []


@pytest.mark.usefixtures("submitted_score")
def test_leaderboard_monthly_defaults(client):
    r = client.get(f"{BASE}/leaderboard/{GAME}/monthly")
    assert r.status_code == 200
    assert len(r.json()) >= 1


def test_leaderboard_monthly_past(client):
    r = client.get(f"{BASE}/leaderboard/{GAME}/monthly?year=2020&month=1")
    assert r.status_code == 200
    assert r.json() == []


# --- Pacific days -------------------------------------------------------------

# 03:00 UTC on Aug 1 is 8pm on Jul 31 in Los Angeles.
JUL_31_EVENING = datetime(2026, 8, 1, 3)


def _score_at(created_at: datetime) -> None:
    """Store a score for the seeded user as if submitted at `created_at` (naive UTC)."""
    db = SessionLocal()
    try:
        user = crud_user.get_user_by_email(db, settings.DEFAULT_USER)
        db.add(GameScore(user_id=user.id, game=GAME, score=50, created_at=created_at))
        db.commit()
    finally:
        db.close()


def test_an_evening_score_counts_toward_its_pacific_day():
    _score_at(JUL_31_EVENING)
    db = SessionLocal()
    try:
        user_id = crud_user.get_user_by_email(db, settings.DEFAULT_USER).id
        assert crud_score.get_daily_play_count(db, user_id, GAME, date(2026, 7, 31)) == 1
        assert crud_score.get_daily_play_count(db, user_id, GAME, date(2026, 8, 1)) == 0
    finally:
        db.close()


def test_both_play_counts_cover_the_whole_pacific_day(client, auth_headers):
    _score_at(day_start(today()) + timedelta(hours=23))
    r = client.get(f"{BASE}/me/{GAME}/plays-today", headers=auth_headers)
    assert r.json()["used"] == 1
    r = client.get(f"{BASE}/me/{GAME}/daily-count", headers=auth_headers)
    assert r.json() == {"count": 1}


def test_daily_leaderboard_uses_pacific_days(client):
    _score_at(JUL_31_EVENING)
    assert len(client.get(f"{BASE}/leaderboard/{GAME}/daily?day=2026-07-31").json()) == 1
    assert client.get(f"{BASE}/leaderboard/{GAME}/daily?day=2026-08-01").json() == []


def test_monthly_leaderboard_uses_pacific_months(client):
    _score_at(JUL_31_EVENING)
    r = client.get(f"{BASE}/leaderboard/{GAME}/monthly?year=2026&month=7")
    assert len(r.json()) == 1
    r = client.get(f"{BASE}/leaderboard/{GAME}/monthly?year=2026&month=8")
    assert r.json() == []
