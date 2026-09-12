from datetime import date, datetime, time, timedelta

import pytest

from app.core.config import settings
from app.crud.puzzle import puzzle as crud_puzzle
from app.crud.user import user as crud_user
from app.database import SessionLocal
from app.game.puzzles import grade_word, seeded_memory_deck, today
from app.models.puzzle import PuzzleAttempt

BASE = "/api/v1/puzzles"


def _puzzle_answer(game: str, puzzle_id: str, key: str):
    puzzle_date = date.fromisoformat(puzzle_id.split("-", 1)[1])
    db = SessionLocal()
    try:
        return crud_puzzle.get_by_game_date(db, game, puzzle_date).data[key]
    finally:
        db.close()


def _attempt_row(user_email: str, puzzle_id: str) -> PuzzleAttempt | None:
    db = SessionLocal()
    try:
        user = crud_user.get_user_by_email(db, user_email)
        return (
            db.query(PuzzleAttempt)
            .filter(PuzzleAttempt.user_id == user.id, PuzzleAttempt.puzzle_id == puzzle_id)
            .first()
        )
    finally:
        db.close()


# --- word -------------------------------------------------------------


def test_get_word_puzzle(client):
    r = client.get(f"{BASE}/word")
    assert r.status_code == 200
    data = r.json()
    assert data["puzzle_id"].startswith("word-")
    assert data["word_length"] == 5
    assert data["max_attempts"] == 6
    assert data["initial_guess"] == "ADORE"
    assert len(data["initial_grade"]) == 5


def test_anonymous_word_puzzle_is_yesterdays(client):
    yesterday = (today() - timedelta(days=1)).isoformat()
    r = client.get(f"{BASE}/word")
    assert r.status_code == 200
    assert r.json()["puzzle_id"] == f"word-{yesterday}"


def test_authenticated_word_puzzle_is_todays(client, auth_headers):
    r = client.get(f"{BASE}/word", headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["puzzle_id"] == f"word-{today().isoformat()}"


def test_seeding_covers_yesterday():
    yesterday = today() - timedelta(days=1)
    db = SessionLocal()
    try:
        for game in ("word", "sudoku", "cipher"):
            row = crud_puzzle.get_by_game_date(db, game, yesterday)
            assert row is not None
            assert row.variant_index == 0
    finally:
        db.close()


def test_word_guess_correct(client):
    puzzle = client.get(f"{BASE}/word").json()
    answer = _puzzle_answer("word", puzzle["puzzle_id"], "answer")

    r = client.post(
        f"{BASE}/word/guess",
        json={"puzzle_id": puzzle["puzzle_id"], "guess": answer, "attempt_count": 1},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["won"] is True
    assert data["lost"] is False
    assert data["grades"] == ["correct"] * 5


def test_word_guess_lost_reveals_answer(client):
    puzzle = client.get(f"{BASE}/word").json()
    answer = _puzzle_answer("word", puzzle["puzzle_id"], "answer")
    wrong = "ZZZZZ" if answer != "ZZZZZ" else "YYYYY"

    r = client.post(
        f"{BASE}/word/guess",
        json={"puzzle_id": puzzle["puzzle_id"], "guess": wrong, "attempt_count": 6},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["won"] is False
    assert data["lost"] is True
    assert data["answer"] == answer


def test_word_hint(client):
    puzzle = client.get(f"{BASE}/word").json()
    answer = _puzzle_answer("word", puzzle["puzzle_id"], "answer")

    r = client.post(f"{BASE}/word/hint", json={"puzzle_id": puzzle["puzzle_id"]})
    assert r.status_code == 200
    assert r.json()["letter"] == answer[0]


def test_word_guess_invalid_length_rejected(client):
    r = client.post(
        f"{BASE}/word/guess",
        json={"puzzle_id": "word-0", "guess": "AB", "attempt_count": 1},
    )
    assert r.status_code == 422


def _word_guess(client, headers, puzzle_id: str, guess: str, attempt_count: int):
    r = client.post(
        f"{BASE}/word/guess",
        headers=headers,
        json={"puzzle_id": puzzle_id, "guess": guess, "attempt_count": attempt_count},
    )
    assert r.status_code == 200


def test_word_puzzle_resumes_signed_in_guesses(client, auth_headers):
    puzzle = client.get(f"{BASE}/word", headers=auth_headers).json()
    assert puzzle["guesses"] == []
    answer = _puzzle_answer("word", puzzle["puzzle_id"], "answer")
    wrong = "ZZZZZ" if answer != "ZZZZZ" else "YYYYY"
    _word_guess(client, auth_headers, puzzle["puzzle_id"], wrong, 2)

    data = client.get(f"{BASE}/word", headers=auth_headers).json()
    assert data["guesses"] == [{"guess": wrong, "grades": grade_word(wrong, answer)}]
    assert (data["won"], data["lost"], data["answer"]) == (False, False, None)


@pytest.mark.parametrize("won", [True, False])
def test_word_puzzle_reports_finished_game(client, auth_headers, won):
    puzzle = client.get(f"{BASE}/word", headers=auth_headers).json()
    answer = _puzzle_answer("word", puzzle["puzzle_id"], "answer")
    guess = answer if won else ("ZZZZZ" if answer != "ZZZZZ" else "YYYYY")
    _word_guess(client, auth_headers, puzzle["puzzle_id"], guess, 6)

    data = client.get(f"{BASE}/word", headers=auth_headers).json()
    assert [g["guess"] for g in data["guesses"]] == [guess.upper()]
    assert data["won"] is won
    assert data["lost"] is not won
    assert data["answer"] == (None if won else answer)


def test_anonymous_word_puzzle_has_no_saved_guesses(client, auth_headers):
    # A signed-in player's progress on yesterday's word stays theirs.
    yesterday_id = f"word-{(today() - timedelta(days=1)).isoformat()}"
    _word_guess(client, auth_headers, yesterday_id, "ZZZZZ", 2)

    data = client.get(f"{BASE}/word").json()
    assert data["puzzle_id"] == yesterday_id
    assert data["guesses"] == []


# --- sudoku -------------------------------------------------------------


def test_get_sudoku_puzzle(client):
    r = client.get(f"{BASE}/sudoku")
    assert r.status_code == 200
    data = r.json()
    assert data["puzzle_id"].startswith("sudoku-")
    assert len(data["puzzle"]) == 81


def test_sudoku_move_correct_and_incorrect(client):
    puzzle = client.get(f"{BASE}/sudoku").json()
    solution = _puzzle_answer("sudoku", puzzle["puzzle_id"], "solution")
    board = puzzle["puzzle"]

    r = client.post(
        f"{BASE}/sudoku/move",
        json={
            "puzzle_id": puzzle["puzzle_id"],
            "index": 2,
            "value": solution[2],
            "board": board,
        },
    )
    assert r.status_code == 200
    assert r.json()["correct"] is True
    assert r.json()["completed"] is False

    wrong_value = 1 if solution[2] != 1 else 2
    r = client.post(
        f"{BASE}/sudoku/move",
        json={
            "puzzle_id": puzzle["puzzle_id"],
            "index": 2,
            "value": wrong_value,
            "board": board,
        },
    )
    assert r.status_code == 200
    assert r.json()["correct"] is False


def test_sudoku_move_completion(client):
    puzzle = client.get(f"{BASE}/sudoku").json()
    solution = _puzzle_answer("sudoku", puzzle["puzzle_id"], "solution")
    almost_solved = list(solution)
    almost_solved[80] = 0

    r = client.post(
        f"{BASE}/sudoku/move",
        json={
            "puzzle_id": puzzle["puzzle_id"],
            "index": 80,
            "value": solution[80],
            "board": almost_solved,
        },
    )
    assert r.status_code == 200
    assert r.json()["correct"] is True
    assert r.json()["completed"] is True


def test_sudoku_hint(client):
    puzzle = client.get(f"{BASE}/sudoku").json()
    solution = _puzzle_answer("sudoku", puzzle["puzzle_id"], "solution")

    r = client.post(
        f"{BASE}/sudoku/hint",
        json={"puzzle_id": puzzle["puzzle_id"], "index": 0, "board": puzzle["puzzle"]},
    )
    assert r.status_code == 200
    assert r.json()["value"] == solution[0]


# --- memory -------------------------------------------------------------


def test_get_memory_puzzle(client):
    r = client.get(f"{BASE}/memory")
    assert r.status_code == 200
    data = r.json()
    assert data["puzzle_id"].startswith("memory-")
    assert data["size"] == 12


def test_memory_reveal_matches_seeded_deck(client):
    puzzle = client.get(f"{BASE}/memory").json()
    db = SessionLocal()
    try:
        symbols = crud_puzzle.get_by_game_variant(db, "memory", 0).data["symbols"]
    finally:
        db.close()
    expected_deck = seeded_memory_deck(puzzle["puzzle_id"], symbols)

    r = client.post(f"{BASE}/memory/reveal", json={"puzzle_id": puzzle["puzzle_id"], "index": 0})
    assert r.status_code == 200
    assert r.json()["symbol"] == expected_deck[0]


def test_memory_reveal_out_of_range_rejected(client):
    puzzle = client.get(f"{BASE}/memory").json()
    r = client.post(f"{BASE}/memory/reveal", json={"puzzle_id": puzzle["puzzle_id"], "index": 99})
    assert r.status_code == 400


# --- cipher -------------------------------------------------------------


def test_get_cipher_puzzle(client):
    r = client.get(f"{BASE}/cipher")
    assert r.status_code == 200
    data = r.json()
    assert data["puzzle_id"].startswith("cipher-")
    assert data["slots"] == 4
    assert data["max_attempts"] == 8
    assert len(data["initial_attempt"]) == 4


def test_cipher_attempt_win(client):
    puzzle = client.get(f"{BASE}/cipher").json()
    digits = _puzzle_answer("cipher", puzzle["puzzle_id"], "digits")

    r = client.post(
        f"{BASE}/cipher/attempt", json={"puzzle_id": puzzle["puzzle_id"], "attempt": digits}
    )
    assert r.status_code == 200
    data = r.json()
    assert data["exact"] == 4
    assert data["won"] is True


def test_cipher_attempt_out_of_range_rejected(client):
    r = client.post(
        f"{BASE}/cipher/attempt", json={"puzzle_id": "cipher-0", "attempt": [0, 1, 2, 9]}
    )
    assert r.status_code == 422


# --- generation past the seeded bank ---------------------------------------


def test_cipher_generates_when_bank_exhausted(client):
    # The seeded bank starts yesterday, so an earlier date has no row yet and
    # must be generated on the fly instead of 404ing.
    past = (today() - timedelta(days=30)).isoformat()

    r = client.post(f"{BASE}/cipher/attempt", json={"puzzle_id": f"cipher-{past}", "attempt": [0, 1, 2, 3]})
    assert r.status_code == 200

    db = SessionLocal()
    try:
        row = crud_puzzle.get_by_game_date(db, "cipher", date.fromisoformat(past))
    finally:
        db.close()
    assert row is not None
    assert sorted(row.data["digits"]) == sorted(set(row.data["digits"]))
    assert len(row.data["digits"]) == 4


def test_sudoku_generates_when_bank_exhausted(client):
    # The seeded bank starts yesterday, so an earlier date has no row yet.
    past = (today() - timedelta(days=30)).isoformat()

    r = client.post(
        f"{BASE}/sudoku/move",
        json={"puzzle_id": f"sudoku-{past}", "index": 0, "value": 1, "board": [0] * 81},
    )
    assert r.status_code == 200

    db = SessionLocal()
    try:
        row = crud_puzzle.get_by_game_date(db, "sudoku", date.fromisoformat(past))
    finally:
        db.close()
    assert row is not None
    assert len(row.data["solution"]) == 81


def test_future_puzzle_rejected(client):
    tomorrow = (today() + timedelta(days=1)).isoformat()

    r = client.post(
        f"{BASE}/word/guess",
        json={"puzzle_id": f"word-{tomorrow}", "guess": "CRANE", "attempt_count": 6},
    )
    assert r.status_code == 404


# --- attempt tracking -----------------------------------------------------


def test_unauthenticated_play_does_not_record_attempt(client):
    puzzle = client.get(f"{BASE}/word").json()
    client.post(f"{BASE}/word/hint", json={"puzzle_id": puzzle["puzzle_id"]})
    assert _attempt_row(settings.DEFAULT_USER, puzzle["puzzle_id"]) is None


def test_authenticated_play_records_attempt(client, auth_headers):
    puzzle = client.get(f"{BASE}/word").json()
    answer = _puzzle_answer("word", puzzle["puzzle_id"], "answer")

    r = client.post(
        f"{BASE}/word/guess",
        json={"puzzle_id": puzzle["puzzle_id"], "guess": answer, "attempt_count": 1},
        headers=auth_headers,
    )
    assert r.status_code == 200

    row = _attempt_row(settings.DEFAULT_USER, puzzle["puzzle_id"])
    assert row is not None
    assert row.attempt_count == 1
    assert row.won is True
    assert row.completed is True


# --- stats -----------------------------------------------------------------

EMPTY_STATS = {
    "total_solved": 0,
    "today_solved": 0,
    "streak": 0,
    "best_streak": 0,
    "games": [],
    "recent": [],
}


def _completed_attempt(
    user_email: str, game: str, days_ago: int, *, won: bool = True, attempt_count: int = 1
) -> None:
    """Store a finished attempt as if it were completed `days_ago` days back, at noon UTC."""
    solved_on = today() - timedelta(days=days_ago)
    db = SessionLocal()
    try:
        user = crud_user.get_user_by_email(db, user_email)
        db.add(
            PuzzleAttempt(
                user_id=user.id,
                game=game,
                puzzle_id=f"{game}-{solved_on.isoformat()}",
                attempt_count=attempt_count,
                won=won,
                completed=True,
                completed_at=datetime.combine(solved_on, time(12)),
            )
        )
        db.commit()
    finally:
        db.close()


def test_stats_unauthenticated(client):
    r = client.get(f"{BASE}/me/stats")
    assert r.status_code == 401


def test_stats_empty_for_new_player(client, auth_headers):
    r = client.get(f"{BASE}/me/stats", headers=auth_headers)
    assert r.status_code == 200
    data = r.json()
    assert data == EMPTY_STATS


def test_stats_after_solving_a_puzzle(client, auth_headers):
    puzzle = client.get(f"{BASE}/cipher").json()
    digits = _puzzle_answer("cipher", puzzle["puzzle_id"], "digits")

    r = client.post(
        f"{BASE}/cipher/attempt",
        json={"puzzle_id": puzzle["puzzle_id"], "attempt": digits},
        headers=auth_headers,
    )
    assert r.status_code == 200
    assert r.json()["won"] is True

    r = client.get(f"{BASE}/me/stats", headers=auth_headers)
    assert r.status_code == 200
    data = r.json()
    assert data["total_solved"] == 1
    assert data["today_solved"] == 1
    assert data["streak"] == 1
    assert len(data["recent"]) == 1
    assert data["recent"][0]["game"] == "cipher"
    assert data["recent"][0]["result"] == 1
    assert data["best_streak"] == 1
    assert data["games"] == [{"game": "cipher", "played": 1, "won": 1}]


def test_stats_only_count_completed_attempts(client, auth_headers):
    puzzle = client.get(f"{BASE}/word").json()
    client.post(
        f"{BASE}/word/hint",
        json={"puzzle_id": puzzle["puzzle_id"]},
        headers=auth_headers,
    )

    r = client.get(f"{BASE}/me/stats", headers=auth_headers)
    assert r.status_code == 200
    assert r.json() == EMPTY_STATS


def test_stats_best_streak_and_per_game_counts(client, auth_headers):
    # Current run: today and yesterday. Longest run: three to five days ago.
    for days_ago in (0, 1, 3, 4, 5):
        _completed_attempt(settings.DEFAULT_USER, "sudoku", days_ago)
    _completed_attempt(settings.DEFAULT_USER, "word", 0, won=False, attempt_count=6)

    r = client.get(f"{BASE}/me/stats", headers=auth_headers)
    assert r.status_code == 200
    data = r.json()
    assert data["streak"] == 2
    assert data["best_streak"] == 3
    assert data["games"] == [
        {"game": "sudoku", "played": 5, "won": 5},
        {"game": "word", "played": 1, "won": 0},
    ]


# --- history ---------------------------------------------------------------


def test_history_unauthenticated(client):
    r = client.get(f"{BASE}/me/history")
    assert r.status_code == 401


def test_history_lists_completed_attempts_newest_first(client, auth_headers):
    _completed_attempt(settings.DEFAULT_USER, "cipher", 2, attempt_count=5)
    _completed_attempt(settings.DEFAULT_USER, "word", 1, won=False, attempt_count=6)
    _completed_attempt(settings.DEFAULT_USER, "sudoku", 0, attempt_count=48)
    # A puzzle that's only been started isn't history yet.
    puzzle = client.get(f"{BASE}/word", headers=auth_headers).json()
    client.post(
        f"{BASE}/word/hint", json={"puzzle_id": puzzle["puzzle_id"]}, headers=auth_headers
    )

    r = client.get(f"{BASE}/me/history", headers=auth_headers)
    assert r.status_code == 200
    data = r.json()
    assert data["total"] == 3
    assert [(i["game"], i["won"], i["attempt_count"]) for i in data["items"]] == [
        ("sudoku", True, 48),
        ("word", False, 6),
        ("cipher", True, 5),
    ]


def test_history_pages(client, auth_headers):
    for days_ago in range(3):
        _completed_attempt(settings.DEFAULT_USER, "cipher", days_ago)

    first = client.get(f"{BASE}/me/history?limit=2", headers=auth_headers).json()
    rest = client.get(f"{BASE}/me/history?skip=2&limit=2", headers=auth_headers).json()

    assert first["total"] == rest["total"] == 3
    assert [i["puzzle_id"] for i in first["items"] + rest["items"]] == [
        f"cipher-{(today() - timedelta(days=n)).isoformat()}" for n in range(3)
    ]


def test_history_rejects_out_of_range_limit(client, auth_headers):
    for limit in (0, 101):
        r = client.get(f"{BASE}/me/history?limit={limit}", headers=auth_headers)
        assert r.status_code == 422
