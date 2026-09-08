import pytest

from app.database import SessionLocal
from app.models.match import Match

BASE = "/api/v1/matches"
KNOWN_WORD = "aback"


def _signup_and_login(client, email: str, username: str, password: str = "secret123"):
    r = client.post(
        "/api/v1/users/",
        json={"email": email, "username": username, "password": password},
    )
    assert r.status_code == 201
    r = client.post(
        "/api/v1/login/access-token",
        data={"username": email, "password": password},
    )
    assert r.status_code == 200
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.fixture
def inviter_headers(client):
    return _signup_and_login(client, "inviter@example.com", "inviter")


@pytest.fixture
def opponent_headers(client):
    return _signup_and_login(client, "opponent@example.com", "opponent")


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
