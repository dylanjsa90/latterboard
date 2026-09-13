"""Word/cipher races and sudoku co-op matches (see app/game/match_modes.py)."""

import random

import pytest

from app.crud.match_puzzle import match_puzzle as crud_match_puzzle
from app.database import SessionLocal
from app.game import match_modes
from app.game.puzzle_seed_data import SUDOKU_VARIANTS, generate_sudoku
from app.models.match import Match, MatchPuzzle

BASE = "/api/v1/matches"
ANSWER = "aback"
WRONG = ["abase", "abate", "abbey"]


def _start_match(client, inviter_headers, opponent_headers, game: str) -> int:
    r = client.post(
        f"{BASE}/invite",
        json={"opponent_username": "opponent", "game": game},
        headers=inviter_headers,
    )
    assert r.status_code == 201
    match_id = r.json()["id"]
    r = client.post(f"{BASE}/{match_id}/accept", headers=opponent_headers)
    assert r.status_code == 200
    return match_id


def _puzzle(match_id: int) -> tuple[dict, dict]:
    db = SessionLocal()
    try:
        row = db.query(MatchPuzzle).filter(MatchPuzzle.match_id == match_id).one()
        return row.data, row.state
    finally:
        db.close()


def _set_puzzle(match_id: int, data: dict, state: dict | None = None) -> None:
    db = SessionLocal()
    try:
        row = db.query(MatchPuzzle).filter(MatchPuzzle.match_id == match_id).one()
        row.data = data
        if state is not None:
            row.state = state
        db.commit()
    finally:
        db.close()


def _detail(client, headers, match_id: int) -> dict:
    r = client.get(f"{BASE}/{match_id}", headers=headers)
    assert r.status_code == 200
    return r.json()


def _leaderboard(client, game: str) -> dict[str, int]:
    r = client.get(f"/api/v1/scores/leaderboard/{game}/all-time")
    return {e["username"]: e["score"] for e in r.json()}


def _word_guess(client, headers, match_id: int, word: str):
    return client.post(f"{BASE}/{match_id}/word/guess", json={"word": word}, headers=headers)


def _sudoku_move(client, headers, match_id: int, index: int, value: int):
    return client.post(
        f"{BASE}/{match_id}/sudoku/move", json={"index": index, "value": value}, headers=headers
    )


def _wrong_value(solution: list[int], index: int) -> int:
    return solution[index] % 9 + 1


# --- rules ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("progress", "expected"),
    [
        ({1: (2, True), 2: (1, False)}, (False, None)),  # 2 can still tie
        ({1: (2, True), 2: (2, False)}, (True, 1)),  # 2 can no longer match 1
        ({1: (2, True), 2: (2, True)}, (True, None)),  # same count: draw
        ({1: (3, True), 2: (2, True)}, (True, 2)),
        ({1: (6, False), 2: (3, False)}, (False, None)),  # 2 may still solve
        ({1: (6, False), 2: (6, True)}, (True, 2)),
        ({1: (6, False), 2: (6, False)}, (True, None)),  # both out: draw
    ],
)
def test_race_outcome(progress, expected):
    assert match_modes.race_outcome(progress, max_attempts=6) == expected


def test_generated_sudoku_is_valid_and_new():
    board = generate_sudoku(random.Random(1))
    puzzle, solution = board["puzzle"], board["solution"]

    rows = [solution[r * 9 : r * 9 + 9] for r in range(9)]
    cols = [solution[c::9] for c in range(9)]
    boxes = [
        [solution[(br * 3 + r) * 9 + bc * 3 + c] for r in range(3) for c in range(3)]
        for br in range(3)
        for bc in range(3)
    ]
    for group in rows + cols + boxes:
        assert sorted(group) == list(range(1, 10))
    assert all(given in (0, answer) for given, answer in zip(puzzle, solution, strict=True))
    assert puzzle.count(0) == SUDOKU_VARIANTS[0]["puzzle"].count(0)
    assert puzzle not in [variant["puzzle"] for variant in SUDOKU_VARIANTS]


# --- invites ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("game", "max_guesses"), [("word_race", 6), ("cipher_race", 8), ("sudoku_coop", 3)]
)
def test_puzzle_matches_start_without_turns(
    client, inviter_headers, opponent_headers, game, max_guesses
):
    match_id = _start_match(client, inviter_headers, opponent_headers, game)
    detail = _detail(client, inviter_headers, match_id)
    assert detail["status"] == "in_progress"
    assert detail["current_turn_username"] is None
    assert detail["max_guesses"] == max_guesses
    assert detail["target_word"] is None


def test_unsupported_game_rejected(client, inviter_headers):
    r = client.post(
        f"{BASE}/invite",
        json={"opponent_username": "opponent", "game": "memory_race"},
        headers=inviter_headers,
    )
    assert r.status_code == 400


def test_moves_only_accepted_on_their_own_game(
    client, inviter_headers, opponent_headers, auth_headers
):
    match_id = _start_match(client, inviter_headers, opponent_headers, "word_race")

    r = client.post(f"{BASE}/{match_id}/guess", json={"word": WRONG[0]}, headers=inviter_headers)
    assert r.status_code == 400
    r = client.post(
        f"{BASE}/{match_id}/cipher/guess", json={"attempt": [0, 1, 2, 3]}, headers=inviter_headers
    )
    assert r.status_code == 400
    assert _sudoku_move(client, inviter_headers, match_id, 0, 1).status_code == 400
    assert _word_guess(client, inviter_headers, match_id, "zzzzz").status_code == 400
    assert _word_guess(client, auth_headers, match_id, WRONG[0]).status_code == 403


# --- races ------------------------------------------------------------------


def test_word_race_fewer_guesses_wins(client, inviter_headers, opponent_headers):
    match_id = _start_match(client, inviter_headers, opponent_headers, "word_race")
    _set_puzzle(match_id, {"answer": ANSWER})

    # Guessing order doesn't matter: the opponent goes first here.
    assert _word_guess(client, opponent_headers, match_id, WRONG[0]).json()["correct"] is False
    assert _word_guess(client, inviter_headers, match_id, WRONG[1]).status_code == 200
    r = _word_guess(client, inviter_headers, match_id, ANSWER)
    assert r.json()["correct"] is True
    # The opponent has used 1 guess, so they could still tie on their 2nd.
    assert r.json()["status"] == "in_progress"

    r = _word_guess(client, opponent_headers, match_id, WRONG[2])
    data = r.json()
    assert data["status"] == "completed"
    assert data["winner_username"] == "inviter"
    assert data["answer"] == ANSWER
    assert _leaderboard(client, "word_race") == {
        "inviter": match_modes.race_score(2, 6),
        "opponent": 0,
    }


def test_word_race_equal_guesses_is_a_draw(client, inviter_headers, opponent_headers):
    match_id = _start_match(client, inviter_headers, opponent_headers, "word_race")
    _set_puzzle(match_id, {"answer": ANSWER})

    assert _word_guess(client, inviter_headers, match_id, ANSWER).json()["status"] == "in_progress"
    data = _word_guess(client, opponent_headers, match_id, ANSWER).json()
    assert data["status"] == "completed"
    assert data["winner_username"] is None
    score = match_modes.race_score(1, 6)
    assert _leaderboard(client, "word_race") == {"inviter": score, "opponent": score}


def test_finished_racer_cannot_keep_guessing(client, inviter_headers, opponent_headers):
    match_id = _start_match(client, inviter_headers, opponent_headers, "word_race")
    _set_puzzle(match_id, {"answer": ANSWER})
    _word_guess(client, inviter_headers, match_id, ANSWER)

    assert _word_guess(client, inviter_headers, match_id, WRONG[0]).status_code == 409


def test_race_hides_opponent_guesses_until_over(client, inviter_headers, opponent_headers):
    match_id = _start_match(client, inviter_headers, opponent_headers, "word_race")
    _set_puzzle(match_id, {"answer": ANSWER})
    graded = _word_guess(client, inviter_headers, match_id, WRONG[0]).json()["result"]

    race = _detail(client, opponent_headers, match_id)["race"]
    assert race["you"]["guesses"] == []
    assert race["opponent"]["username"] == "inviter"
    assert race["opponent"]["guesses"] == [{"guess": None, "result": graded, "correct": False}]
    assert race["answer"] is None
    assert _detail(client, inviter_headers, match_id)["race"]["you"]["guesses"][0]["guess"] == WRONG[0]

    _word_guess(client, inviter_headers, match_id, ANSWER)
    _word_guess(client, opponent_headers, match_id, WRONG[1])
    _word_guess(client, opponent_headers, match_id, WRONG[2])

    race = _detail(client, opponent_headers, match_id)["race"]
    assert [g["guess"] for g in race["opponent"]["guesses"]] == [WRONG[0], ANSWER]
    assert race["opponent"]["solved"] is True
    assert race["you"]["finished"] is False
    assert race["answer"] == ANSWER


def test_cipher_race(client, inviter_headers, opponent_headers):
    match_id = _start_match(client, inviter_headers, opponent_headers, "cipher_race")
    _set_puzzle(match_id, {"digits": [0, 1, 2, 3]})

    r = client.post(
        f"{BASE}/{match_id}/cipher/guess", json={"attempt": [3, 2, 1, 0]}, headers=opponent_headers
    )
    assert r.json()["result"] == {"exact": 0, "close": 4, "won": False}
    r = client.post(
        f"{BASE}/{match_id}/cipher/guess", json={"attempt": [0, 1, 2, 3]}, headers=inviter_headers
    )
    data = r.json()
    assert data["correct"] is True
    assert data["status"] == "completed"
    assert data["winner_username"] == "inviter"
    assert data["answer"] == [0, 1, 2, 3]


def test_race_over_websocket_shares_grades_not_letters(client, inviter_headers, opponent_headers):
    with client.websocket_connect(
        "/ws/word_race", headers=inviter_headers
    ) as inviter_ws, client.websocket_connect(
        "/ws/word_race", headers=opponent_headers
    ) as opponent_ws:
        assert inviter_ws.receive_json()["type"] == "connected"
        assert opponent_ws.receive_json()["type"] == "connected"

        match_id = _start_match(client, inviter_headers, opponent_headers, "word_race")
        assert opponent_ws.receive_json()["type"] == "invite_received"
        assert inviter_ws.receive_json()["game"] == "word_race"
        assert opponent_ws.receive_json()["type"] == "match_started"
        for ws in (inviter_ws, opponent_ws):
            ws.send_json({"type": "join_match", "match_id": match_id})
            assert ws.receive_json()["type"] == "joined_match"
        _set_puzzle(match_id, {"answer": ANSWER})

        graded = _word_guess(client, inviter_headers, match_id, WRONG[0]).json()["result"]
        frame = opponent_ws.receive_json()
        assert frame == {
            "type": "opponent_guessed",
            "match_id": match_id,
            "username": "inviter",
            "turn_number": 1,
            "result": graded,
            "correct": False,
        }

        # The opponent solves in 1, beating the inviter's 2.
        _word_guess(client, inviter_headers, match_id, ANSWER)
        _word_guess(client, opponent_headers, match_id, ANSWER)
        frames = [opponent_ws.receive_json() for _ in range(3)]
        assert frames[-1] == {
            "type": "match_completed",
            "match_id": match_id,
            "winner_username": "opponent",
            "answer": ANSWER,
            "reason": "solved",
        }


# --- sudoku co-op -------------------------------------------------------------


def test_sudoku_coop_players_share_one_board(client, inviter_headers, opponent_headers):
    match_id = _start_match(client, inviter_headers, opponent_headers, "sudoku_coop")
    data, _ = _puzzle(match_id)
    solution = data["solution"]
    sudoku = _detail(client, inviter_headers, match_id)["sudoku"]
    assert sudoku["board"] == sudoku["puzzle"] == data["puzzle"]
    assert sudoku["solution"] is None
    empty = sudoku["puzzle"].index(0)
    given = next(i for i, v in enumerate(sudoku["puzzle"]) if v)

    r = _sudoku_move(client, opponent_headers, match_id, empty, solution[empty])
    assert r.status_code == 200
    assert r.json()["correct"] is True
    assert r.json()["status"] == "in_progress"
    assert _detail(client, inviter_headers, match_id)["sudoku"]["board"][empty] == solution[empty]

    assert _sudoku_move(client, inviter_headers, match_id, empty, solution[empty]).status_code == 409
    assert _sudoku_move(client, inviter_headers, match_id, given, solution[given]).status_code == 409


def test_sudoku_coop_shared_mistakes_lose_the_match(client, inviter_headers, opponent_headers):
    match_id = _start_match(client, inviter_headers, opponent_headers, "sudoku_coop")
    data, _ = _puzzle(match_id)
    solution = data["solution"]
    empty = data["puzzle"].index(0)
    wrong = _wrong_value(solution, empty)

    for mistakes, headers in enumerate(
        (inviter_headers, opponent_headers, inviter_headers), start=1
    ):
        r = _sudoku_move(client, headers, match_id, empty, wrong)
        assert r.json()["correct"] is False
        assert r.json()["mistakes"] == mistakes
    assert r.json()["status"] == "completed"
    assert r.json()["outcome"] == "failed"

    assert _sudoku_move(client, opponent_headers, match_id, empty, solution[empty]).status_code == 409
    sudoku = _detail(client, opponent_headers, match_id)["sudoku"]
    assert sudoku["outcome"] == "failed"
    assert sudoku["solution"] == solution
    assert _leaderboard(client, "sudoku_coop") == {"inviter": 0, "opponent": 0}


def test_sudoku_coop_solved_together(client, inviter_headers, opponent_headers):
    match_id = _start_match(client, inviter_headers, opponent_headers, "sudoku_coop")
    data, _ = _puzzle(match_id)
    solution = data["solution"]
    board = list(solution)
    board[0] = board[80] = 0
    _set_puzzle(match_id, data, {"board": board, "mistakes": 1, "outcome": None})

    assert _sudoku_move(client, inviter_headers, match_id, 0, solution[0]).json()["status"] == "in_progress"
    r = _sudoku_move(client, opponent_headers, match_id, 80, solution[80])
    assert r.json()["status"] == "completed"
    assert r.json()["outcome"] == "solved"
    score = match_modes.sudoku_coop_score(1, 3)
    assert _leaderboard(client, "sudoku_coop") == {"inviter": score, "opponent": score}


def test_sudoku_coop_over_websocket(client, inviter_headers, opponent_headers):
    match_id = _start_match(client, inviter_headers, opponent_headers, "sudoku_coop")
    data, _ = _puzzle(match_id)
    solution = data["solution"]
    empty = data["puzzle"].index(0)

    with client.websocket_connect(
        "/ws/sudoku_coop", headers=inviter_headers
    ) as inviter_ws, client.websocket_connect(
        "/ws/sudoku_coop", headers=opponent_headers
    ) as opponent_ws:
        for ws in (inviter_ws, opponent_ws):
            assert ws.receive_json()["type"] == "connected"
            ws.send_json({"type": "join_match", "match_id": match_id})
            assert ws.receive_json()["type"] == "joined_match"

        _sudoku_move(client, inviter_headers, match_id, empty, _wrong_value(solution, empty))
        assert opponent_ws.receive_json() == {
            "type": "mistake",
            "match_id": match_id,
            "username": "inviter",
            "index": empty,
            "value": _wrong_value(solution, empty),
            "mistakes": 1,
            "max_mistakes": 3,
        }

        _sudoku_move(client, opponent_headers, match_id, empty, solution[empty])
        assert inviter_ws.receive_json()["type"] == "mistake"
        assert inviter_ws.receive_json() == {
            "type": "cell_filled",
            "match_id": match_id,
            "username": "opponent",
            "index": empty,
            "value": solution[empty],
        }


def test_simultaneous_sudoku_moves_both_land(client, inviter_headers, opponent_headers):
    match_id = _start_match(client, inviter_headers, opponent_headers, "sudoku_coop")
    data, _ = _puzzle(match_id)
    solution = data["solution"]
    first, second = [i for i, v in enumerate(data["puzzle"]) if v == 0][:2]

    db = SessionLocal()
    try:
        match = db.get(Match, match_id)
        # This session now holds the board as it was before the opponent's move...
        stale_row = crud_match_puzzle.get_by_match(db, match_id)
        r = _sudoku_move(client, opponent_headers, match_id, first, solution[first])
        assert r.status_code == 200
        assert stale_row.state["board"][first] == 0
        # ...so its write hits a stale version and must be replayed, not overwrite it.
        result = crud_match_puzzle.submit_sudoku_move(db, match, second, solution[second])
    finally:
        db.close()

    assert result.correct is True
    board = _puzzle(match_id)[1]["board"]
    assert board[first] == solution[first]
    assert board[second] == solution[second]
