"""Word Duel: Your Pick (`word_pick`): a word race where each player picks the word
the other must guess."""

import pytest

from app.game import match_modes
from tests.helpers import match_detail, start_match

BASE = "/api/v1/matches"
GAME = match_modes.WORD_PICK
# The word the inviter picks (so the opponent must guess it), and the reverse.
FOR_OPPONENT = "aback"
FOR_INVITER = "crane"
WRONG = ["abase", "abate", "abbey"]


def _pick(client, headers, match_id: int, word: str):
    return client.post(
        f"{BASE}/{match_id}/word_pick/pick", json={"word": word}, headers=headers
    )


def _guess(client, headers, match_id: int, word: str):
    return client.post(
        f"{BASE}/{match_id}/word_pick/guess", json={"word": word}, headers=headers
    )


def _picked_match(client, inviter_headers, opponent_headers) -> int:
    match_id = start_match(client, inviter_headers, opponent_headers, GAME)
    assert _pick(client, inviter_headers, match_id, FOR_OPPONENT).status_code == 200
    r = _pick(client, opponent_headers, match_id, FOR_INVITER)
    assert r.json() == {"match_id": match_id, "word": FOR_INVITER, "both_picked": True}
    return match_id


def _leaderboard(client) -> dict[str, int]:
    r = client.get(f"/api/v1/scores/leaderboard/{GAME}/all-time")
    return {e["username"]: e["score"] for e in r.json()}


def test_each_player_guesses_the_word_the_other_picked(
    client, inviter_headers, opponent_headers
):
    match_id = _picked_match(client, inviter_headers, opponent_headers)

    # The inviter's answer is the opponent's pick, so their own pick is wrong for them.
    r = _guess(client, inviter_headers, match_id, FOR_OPPONENT)
    assert r.json()["correct"] is False
    # The opponent solves in 1 after the inviter missed in 1, which decides it.
    data = _guess(client, opponent_headers, match_id, FOR_OPPONENT).json()
    assert data["correct"] is True
    assert data["status"] == "completed"
    assert data["winner_username"] == "opponent"
    assert data["answer"] == FOR_OPPONENT
    assert _leaderboard(client) == {
        "opponent": match_modes.race_score(1, 6),
        "inviter": 0,
    }


def test_guessing_waits_for_both_picks(client, inviter_headers, opponent_headers):
    match_id = start_match(client, inviter_headers, opponent_headers, GAME)
    assert _guess(client, inviter_headers, match_id, WRONG[0]).status_code == 409

    r = _pick(client, inviter_headers, match_id, FOR_OPPONENT)
    assert r.json()["both_picked"] is False
    # The opponent's word has been picked, but they haven't picked the inviter's.
    assert _guess(client, opponent_headers, match_id, WRONG[0]).status_code == 409
    assert _guess(client, inviter_headers, match_id, WRONG[0]).status_code == 409


def test_a_player_picks_only_once(client, inviter_headers, opponent_headers):
    match_id = start_match(client, inviter_headers, opponent_headers, GAME)
    _pick(client, inviter_headers, match_id, FOR_OPPONENT)

    assert _pick(client, inviter_headers, match_id, FOR_INVITER).status_code == 409
    race = match_detail(client, inviter_headers, match_id)["race"]
    assert race["your_pick"] == FOR_OPPONENT


@pytest.mark.parametrize("word", ["zzzzz", "dulia", "abc", "toolong"])
def test_picks_must_be_answer_words(client, inviter_headers, opponent_headers, word):
    """Valid guesses that aren't answer words ("dulia") are refused too."""
    match_id = start_match(client, inviter_headers, opponent_headers, GAME)
    assert _pick(client, inviter_headers, match_id, word).status_code in (400, 422)


def test_picks_are_lowercased(client, inviter_headers, opponent_headers):
    match_id = start_match(client, inviter_headers, opponent_headers, GAME)
    assert _pick(client, inviter_headers, match_id, "ABACK").json()["word"] == "aback"


def test_pick_is_only_for_word_pick_matches(client, inviter_headers, opponent_headers):
    match_id = start_match(client, inviter_headers, opponent_headers, "word_race")
    assert _pick(client, inviter_headers, match_id, FOR_OPPONENT).status_code == 400
    assert _guess(client, inviter_headers, match_id, WRONG[0]).status_code == 400


def test_detail_hides_the_word_picked_for_you_until_over(
    client, inviter_headers, opponent_headers
):
    match_id = start_match(client, inviter_headers, opponent_headers, GAME)
    _pick(client, inviter_headers, match_id, FOR_OPPONENT)

    detail = match_detail(client, opponent_headers, match_id)
    assert detail["race"]["your_pick"] is None
    assert detail["race"]["opponent_picked"] is True
    assert detail["race"]["answer"] is None
    assert FOR_OPPONENT not in str(detail)

    race = match_detail(client, inviter_headers, match_id)["race"]
    assert race["your_pick"] == FOR_OPPONENT
    assert race["opponent_picked"] is False


def test_finished_detail_reveals_each_players_answer(
    client, inviter_headers, opponent_headers
):
    match_id = _picked_match(client, inviter_headers, opponent_headers)
    _guess(client, inviter_headers, match_id, FOR_INVITER)
    _guess(client, opponent_headers, match_id, FOR_OPPONENT)

    inviter = match_detail(client, inviter_headers, match_id)
    assert inviter["status"] == "completed"
    assert inviter["race"]["answer"] == FOR_INVITER
    opponent = match_detail(client, opponent_headers, match_id)
    assert opponent["race"]["answer"] == FOR_OPPONENT


def test_active_matches_wait_on_whoever_hasnt_picked(
    client, inviter_headers, opponent_headers
):
    match_id = start_match(client, inviter_headers, opponent_headers, GAME)

    def your_move(headers) -> bool:
        items = client.get(f"{BASE}/me/active", headers=headers).json()
        return next(i for i in items if i["id"] == match_id)["your_move"]

    assert your_move(inviter_headers) and your_move(opponent_headers)
    _pick(client, inviter_headers, match_id, FOR_OPPONENT)
    assert not your_move(inviter_headers)
    assert your_move(opponent_headers)
    _pick(client, opponent_headers, match_id, FOR_INVITER)
    assert your_move(inviter_headers) and your_move(opponent_headers)


def test_websocket_announces_picks_without_the_word(
    client, inviter_headers, opponent_headers
):
    with (
        client.websocket_connect(f"/ws/{GAME}", headers=inviter_headers) as inviter_ws,
        client.websocket_connect(
            f"/ws/{GAME}", headers=opponent_headers
        ) as opponent_ws,
    ):
        assert inviter_ws.receive_json()["type"] == "connected"
        assert opponent_ws.receive_json()["type"] == "connected"

        match_id = start_match(client, inviter_headers, opponent_headers, GAME)
        assert opponent_ws.receive_json()["type"] == "invite_received"
        assert inviter_ws.receive_json()["type"] == "match_started"
        assert opponent_ws.receive_json()["type"] == "match_started"
        for ws in (inviter_ws, opponent_ws):
            ws.send_json({"type": "join_match", "match_id": match_id})
            assert ws.receive_json()["type"] == "joined_match"

        _pick(client, inviter_headers, match_id, FOR_OPPONENT)
        assert opponent_ws.receive_json() == {
            "type": "word_picked",
            "match_id": match_id,
            "username": "inviter",
            "both_picked": False,
        }
        _pick(client, opponent_headers, match_id, FOR_INVITER)
        assert opponent_ws.receive_json()["both_picked"] is True

        _guess(client, inviter_headers, match_id, FOR_INVITER)
        _guess(client, opponent_headers, match_id, FOR_OPPONENT)
        frames = [opponent_ws.receive_json() for _ in range(3)]
        assert frames[-1] == {
            "type": "match_completed",
            "match_id": match_id,
            "winner_username": None,
            "answer": None,
            "answers": {"inviter": FOR_INVITER, "opponent": FOR_OPPONENT},
            "reason": "draw",
        }


def test_pickable_words_are_the_sorted_answer_words(client, auth_headers):
    words = client.get(f"{BASE}/word_pick/words", headers=auth_headers).json()
    assert words == sorted(words)
    assert len(words) == len(set(words)) > 2000
    assert "aback" in words and "dulia" not in words
    assert all(match_modes.is_pickable_word(word) for word in words)


def test_pickable_words_need_sign_in(client):
    assert client.get(f"{BASE}/word_pick/words").status_code == 401
