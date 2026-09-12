from datetime import date, timedelta

from app.core.config import settings
from app.crud.puzzle import puzzle as crud_puzzle
from app.crud.user import user as crud_user
from app.database import SessionLocal
from app.game.puzzles import seeded_memory_deck, today
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


def test_stats_unauthenticated(client):
    r = client.get(f"{BASE}/me/stats")
    assert r.status_code == 401


def test_stats_empty_for_new_player(client, auth_headers):
    r = client.get(f"{BASE}/me/stats", headers=auth_headers)
    assert r.status_code == 200
    data = r.json()
    assert data == {"total_solved": 0, "today_solved": 0, "streak": 0, "recent": []}


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


def test_stats_only_count_completed_attempts(client, auth_headers):
    puzzle = client.get(f"{BASE}/word").json()
    client.post(
        f"{BASE}/word/hint",
        json={"puzzle_id": puzzle["puzzle_id"]},
        headers=auth_headers,
    )

    r = client.get(f"{BASE}/me/stats", headers=auth_headers)
    assert r.status_code == 200
    assert r.json() == {"total_solved": 0, "today_solved": 0, "streak": 0, "recent": []}
