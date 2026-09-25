from app.database import SessionLocal
from app.models import MatchPuzzle, User

BASE = "/api/v1/matches"


def _start(client, inviter_headers, opponent_headers, game: str) -> int:
    r = client.post(
        f"{BASE}/invite",
        json={"opponent_username": "opponent", "game": game},
        headers=inviter_headers,
    )
    match_id = r.json()["id"]
    client.post(f"{BASE}/{match_id}/accept", headers=opponent_headers)
    return match_id


def _active(client, headers) -> dict[int, dict]:
    r = client.get(f"{BASE}/me/active", headers=headers)
    assert r.status_code == 200
    return {item["id"]: item for item in r.json()}


def _use_up_race(match_id: int, username: str) -> None:
    """Fill the player's side of a race with misses, as if they'd run out of attempts."""
    db = SessionLocal()
    try:
        user_id = db.query(User).filter(User.username == username).one().id
        row = db.query(MatchPuzzle).filter(MatchPuzzle.match_id == match_id).one()
        miss = {"guess": "xxxxx", "result": ["absent"] * 5, "correct": False}
        guesses = {**row.state["guesses"], str(user_id): [miss] * 6}
        row.state = {**row.state, "guesses": guesses}
        db.commit()
    finally:
        db.close()


def test_pending_invite_waits_on_invitee(client, inviter_headers, opponent_headers):
    r = client.post(
        f"{BASE}/invite", json={"opponent_username": "opponent"}, headers=inviter_headers
    )
    match_id = r.json()["id"]

    mine = _active(client, inviter_headers)[match_id]
    assert mine["status"] == "pending_invite"
    assert mine["role"] == "inviter"
    assert mine["opponent_username"] == "opponent"
    assert mine["your_move"] is False

    theirs = _active(client, opponent_headers)[match_id]
    assert theirs["role"] == "invitee"
    assert theirs["your_move"] is True


def test_word_duel_follows_the_turn(client, inviter_headers, opponent_headers):
    match_id = _start(client, inviter_headers, opponent_headers, "wordle")
    assert _active(client, inviter_headers)[match_id]["your_move"] is True
    assert _active(client, opponent_headers)[match_id]["your_move"] is False


def test_race_waits_until_you_finish(client, inviter_headers, opponent_headers):
    match_id = _start(client, inviter_headers, opponent_headers, "word_race")
    assert _active(client, inviter_headers)[match_id]["your_move"] is True

    _use_up_race(match_id, "inviter")
    assert _active(client, inviter_headers)[match_id]["your_move"] is False
    assert _active(client, opponent_headers)[match_id]["your_move"] is True


def test_coop_always_wants_a_move(client, inviter_headers, opponent_headers):
    match_id = _start(client, inviter_headers, opponent_headers, "sudoku_coop")
    assert _active(client, inviter_headers)[match_id]["your_move"] is True
    assert _active(client, opponent_headers)[match_id]["your_move"] is True


def test_concurrent_matches_all_listed(client, inviter_headers, opponent_headers):
    ids = {
        _start(client, inviter_headers, opponent_headers, game)
        for game in ("wordle", "word_race", "cipher_race")
    }
    assert set(_active(client, inviter_headers)) == ids


def test_closed_matches_drop_out(client, inviter_headers, opponent_headers):
    r = client.post(
        f"{BASE}/invite", json={"opponent_username": "opponent"}, headers=inviter_headers
    )
    match_id = r.json()["id"]
    client.post(f"{BASE}/{match_id}/decline", headers=opponent_headers)
    assert match_id not in _active(client, inviter_headers)


def test_listing_active_matches_does_not_scale_with_their_number(
    client, inviter_headers, opponent_headers
):
    """The rail polls this endpoint, so opponents and race puzzles load in bulk.

    Guards against a regression to a query per match, which `to_active_item` used to do.
    Both rounds use the same mix of games, so only the match count differs.
    """
    from sqlalchemy import event

    from app.database import engine
    from tests.conftest import _signup_and_login

    def count_queries() -> int:
        queries = 0

        def counter(*_args, **_kwargs):
            nonlocal queries
            queries += 1

        event.listen(engine, "before_cursor_execute", counter)
        try:
            r = client.get(f"{BASE}/me/active", headers=inviter_headers)
            assert r.status_code == 200
        finally:
            event.remove(engine, "before_cursor_execute", counter)
        return queries

    def start_with(opponent_username: str, headers) -> None:
        for game in ("wordle", "word_race", "cipher_race"):
            r = client.post(
                f"{BASE}/invite",
                json={"opponent_username": opponent_username, "game": game},
                headers=inviter_headers,
            )
            assert r.status_code == 201, r.text
            client.post(f"{BASE}/{r.json()['id']}/accept", headers=headers)

    start_with("opponent", opponent_headers)
    three_matches = count_queries()

    third_headers = _signup_and_login(client, "third@example.com", "third")
    start_with("third", third_headers)
    six_matches = count_queries()

    assert len(_active(client, inviter_headers)) == 6
    assert six_matches == three_matches, (
        f"{six_matches} queries for 6 matches vs {three_matches} for 3: "
        "the endpoint is querying per match again"
    )
