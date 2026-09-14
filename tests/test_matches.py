from app.database import SessionLocal
from app.models.match import Match

BASE = "/api/v1/matches"
KNOWN_WORD = "aback"


def _set_target_word(match_id: int, word: str) -> None:
    db = SessionLocal()
    try:
        m = db.get(Match, match_id)
        m.target_word = word
        db.add(m)
        db.commit()
    finally:
        db.close()


def test_invite_visible_to_opponent(client, inviter_headers, opponent_headers):
    r = client.post(
        f"{BASE}/invite", json={"opponent_username": "opponent"}, headers=inviter_headers
    )
    assert r.status_code == 201
    match_id = r.json()["id"]

    r = client.get(f"{BASE}/pending", headers=opponent_headers)
    assert r.status_code == 200
    ids = [inv["id"] for inv in r.json()]
    assert match_id in ids


def test_invite_unknown_opponent(client, inviter_headers):
    r = client.post(
        f"{BASE}/invite", json={"opponent_username": "nobody"}, headers=inviter_headers
    )
    assert r.status_code == 404


def test_invite_self(client, inviter_headers):
    r = client.post(
        f"{BASE}/invite", json={"opponent_username": "inviter"}, headers=inviter_headers
    )
    assert r.status_code == 400


def test_accept_starts_match_with_inviter_turn(client, inviter_headers, opponent_headers):
    r = client.post(
        f"{BASE}/invite", json={"opponent_username": "opponent"}, headers=inviter_headers
    )
    match_id = r.json()["id"]
    inviter_username = r.json()["inviter_username"]

    r = client.post(f"{BASE}/{match_id}/accept", headers=opponent_headers)
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "in_progress"
    assert data["current_turn_username"] == inviter_username


def test_guess_out_of_turn_rejected(client, inviter_headers, opponent_headers):
    r = client.post(
        f"{BASE}/invite", json={"opponent_username": "opponent"}, headers=inviter_headers
    )
    match_id = r.json()["id"]
    client.post(f"{BASE}/{match_id}/accept", headers=opponent_headers)

    # opponent (invitee) tries to guess even though inviter goes first
    r = client.post(f"{BASE}/{match_id}/guess", json={"word": "abbey"}, headers=opponent_headers)
    assert r.status_code == 409


def test_invalid_word_rejected(client, inviter_headers, opponent_headers):
    r = client.post(
        f"{BASE}/invite", json={"opponent_username": "opponent"}, headers=inviter_headers
    )
    match_id = r.json()["id"]
    client.post(f"{BASE}/{match_id}/accept", headers=opponent_headers)

    r = client.post(f"{BASE}/{match_id}/guess", json={"word": "zzzzz"}, headers=inviter_headers)
    assert r.status_code == 400


def test_forced_win_completes_match_and_records_score(
    client, inviter_headers, opponent_headers
):
    r = client.post(
        f"{BASE}/invite", json={"opponent_username": "opponent"}, headers=inviter_headers
    )
    match_id = r.json()["id"]
    inviter_username = r.json()["inviter_username"]
    client.post(f"{BASE}/{match_id}/accept", headers=opponent_headers)

    _set_target_word(match_id, KNOWN_WORD)

    r = client.post(
        f"{BASE}/{match_id}/guess", json={"word": KNOWN_WORD}, headers=inviter_headers
    )
    assert r.status_code == 200
    data = r.json()
    assert data["correct"] is True
    assert data["status"] == "completed"

    r = client.get(f"{BASE}/{match_id}", headers=inviter_headers)
    assert r.status_code == 200
    detail = r.json()
    assert detail["status"] == "completed"
    assert detail["winner_username"] == inviter_username
    assert detail["target_word"] == KNOWN_WORD

    r = client.get("/api/v1/scores/leaderboard/wordle_vs/all-time")
    assert r.status_code == 200
    usernames = [e["username"] for e in r.json()]
    assert inviter_username in usernames


def test_decline_then_further_actions_rejected(client, inviter_headers, opponent_headers):
    r = client.post(
        f"{BASE}/invite", json={"opponent_username": "opponent"}, headers=inviter_headers
    )
    match_id = r.json()["id"]

    r = client.post(f"{BASE}/{match_id}/decline", headers=opponent_headers)
    assert r.status_code == 200
    assert r.json()["status"] == "declined"

    r = client.post(f"{BASE}/{match_id}/accept", headers=opponent_headers)
    assert r.status_code == 409


def test_cancel_by_inviter(client, inviter_headers, opponent_headers):
    r = client.post(
        f"{BASE}/invite", json={"opponent_username": "opponent"}, headers=inviter_headers
    )
    match_id = r.json()["id"]

    r = client.post(f"{BASE}/{match_id}/cancel", headers=inviter_headers)
    assert r.status_code == 200
    assert r.json()["status"] == "cancelled"


def _win_wordle(client, inviter_headers, opponent_headers) -> int:
    """Plays a wordle match the inviter wins on the first guess."""
    r = client.post(
        f"{BASE}/invite", json={"opponent_username": "opponent"}, headers=inviter_headers
    )
    match_id = r.json()["id"]
    client.post(f"{BASE}/{match_id}/accept", headers=opponent_headers)
    _set_target_word(match_id, KNOWN_WORD)
    r = client.post(
        f"{BASE}/{match_id}/guess", json={"word": KNOWN_WORD}, headers=inviter_headers
    )
    assert r.json()["status"] == "completed"
    return match_id


def _history(client, headers, **params) -> dict:
    r = client.get(f"{BASE}/me/history", params=params, headers=headers)
    assert r.status_code == 200
    return r.json()


def test_history_shows_each_player_their_side(client, inviter_headers, opponent_headers):
    match_id = _win_wordle(client, inviter_headers, opponent_headers)

    mine = _history(client, inviter_headers)
    assert mine["total"] == 1
    assert mine["record"] == {"won": 1, "lost": 0, "drawn": 0}
    [item] = mine["items"]
    assert item["id"] == match_id
    assert item["game"] == "wordle"
    assert item["opponent_username"] == "opponent"
    assert item["result"] == "won"
    assert item["completed_at"]

    theirs = _history(client, opponent_headers)
    assert theirs["record"] == {"won": 0, "lost": 1, "drawn": 0}
    assert theirs["items"][0]["opponent_username"] == "inviter"
    assert theirs["items"][0]["result"] == "lost"


def test_history_leaves_out_unfinished_matches(client, inviter_headers, opponent_headers):
    invite = {"opponent_username": "opponent"}
    client.post(f"{BASE}/invite", json=invite, headers=inviter_headers)
    r = client.post(
        f"{BASE}/invite", json={**invite, "game": "word_race"}, headers=inviter_headers
    )
    client.post(f"{BASE}/{r.json()['id']}/decline", headers=opponent_headers)
    r = client.post(
        f"{BASE}/invite", json={**invite, "game": "cipher_race"}, headers=inviter_headers
    )
    client.post(f"{BASE}/{r.json()['id']}/accept", headers=opponent_headers)

    assert _history(client, inviter_headers) == {
        "items": [],
        "total": 0,
        "record": {"won": 0, "lost": 0, "drawn": 0},
    }


def test_history_pages_newest_first(client, inviter_headers, opponent_headers):
    first = _win_wordle(client, inviter_headers, opponent_headers)
    second = _win_wordle(client, inviter_headers, opponent_headers)

    page = _history(client, inviter_headers, skip=0, limit=1)
    assert page["total"] == 2
    assert [item["id"] for item in page["items"]] == [second]
    # The record covers every match, not just the page.
    assert page["record"]["won"] == 2
    assert [i["id"] for i in _history(client, inviter_headers, skip=1, limit=1)["items"]] == [
        first
    ]


def test_history_requires_sign_in(client):
    assert client.get(f"{BASE}/me/history").status_code == 401


def test_duplicate_invite_conflicts(client, inviter_headers, opponent_headers):
    r = client.post(
        f"{BASE}/invite", json={"opponent_username": "opponent"}, headers=inviter_headers
    )
    assert r.status_code == 201

    r = client.post(
        f"{BASE}/invite", json={"opponent_username": "opponent"}, headers=inviter_headers
    )
    assert r.status_code == 409


def test_invite_and_gameplay_over_websocket(client, inviter_headers, opponent_headers):
    with client.websocket_connect(
        "/ws/wordle", headers=inviter_headers
    ) as inviter_ws, client.websocket_connect(
        "/ws/wordle", headers=opponent_headers
    ) as invitee_ws:
        assert inviter_ws.receive_json()["type"] == "connected"
        assert invitee_ws.receive_json()["type"] == "connected"

        r = client.post(
            f"{BASE}/invite", json={"opponent_username": "opponent"}, headers=inviter_headers
        )
        assert r.status_code == 201
        match_id = r.json()["id"]
        inviter_username = r.json()["inviter_username"]

        invite_frame = invitee_ws.receive_json()
        assert invite_frame["type"] == "invite_received"
        assert invite_frame["match_id"] == match_id

        r = client.post(f"{BASE}/{match_id}/accept", headers=opponent_headers)
        assert r.status_code == 200

        assert inviter_ws.receive_json()["type"] == "match_started"
        assert invitee_ws.receive_json()["type"] == "match_started"

        inviter_ws.send_json({"type": "join_match", "match_id": match_id})
        invitee_ws.send_json({"type": "join_match", "match_id": match_id})
        assert inviter_ws.receive_json()["type"] == "joined_match"
        assert invitee_ws.receive_json()["type"] == "joined_match"

        _set_target_word(match_id, KNOWN_WORD)

        r = client.post(
            f"{BASE}/{match_id}/guess", json={"word": KNOWN_WORD}, headers=inviter_headers
        )
        assert r.status_code == 200

        guessed = invitee_ws.receive_json()
        assert guessed["type"] == "opponent_guessed"
        assert guessed["username"] == inviter_username

        completed = invitee_ws.receive_json()
        assert completed["type"] == "match_completed"
        assert completed["winner_username"] == inviter_username
