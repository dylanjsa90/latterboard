"""Vocab Challenger rooms and wordbooks over HTTP (app/api/routes/vocab.py)."""

import time
from datetime import timedelta
from typing import Any

from app.crud.vocab import crud_vocab
from app.database import SessionLocal
from app.game import vocab
from app.models import User, VocabRoom
from app.utils import utcnow

BASE = "/api/v1/vocab"


def _state(code: str) -> vocab.Game:
    db = SessionLocal()
    try:
        row = db.get(VocabRoom, code)
        assert row is not None
        return row.state  # type: ignore[return-value]
    finally:
        db.close()


def _set_state(code: str, **changes: Any) -> None:
    db = SessionLocal()
    try:
        row = db.get(VocabRoom, code)
        assert row is not None
        row.state = {**row.state, **changes}
        db.commit()
    finally:
        db.close()


def _key(code: str) -> int:
    correct: int = vocab.full_question(_state(code))["correct"]
    return correct


def _create(client, headers, mode: str = "duel") -> dict:
    r = client.post(f"{BASE}/rooms", json={"mode": mode}, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()["game"]


def _act(client, headers, game: dict, action: str, **extra):
    body = {"action": action, "generation": game["generation"], **extra}
    return client.post(
        f"{BASE}/rooms/{game['code']}/actions", json=body, headers=headers
    )


def _get(client, headers, code: str) -> dict:
    r = client.get(f"{BASE}/rooms/{code}", headers=headers)
    assert r.status_code == 200, r.text
    return r.json()["game"]


def _start_duel(client, inviter_headers, opponent_headers) -> dict:
    game = _create(client, inviter_headers)
    r = client.post(
        f"{BASE}/rooms/{game['code'].lower()}/join", headers=opponent_headers
    )
    assert r.status_code == 200, r.text
    assert _act(client, inviter_headers, game, "ready").status_code == 200
    r = _act(client, opponent_headers, game, "ready")
    assert r.status_code == 200
    return r.json()["game"]


def test_duel_plays_five_rounds_then_needs_both_for_a_rematch(
    client, inviter_headers, opponent_headers
):
    game = _create(client, inviter_headers)
    assert game["phase"] == "lobby"
    assert [p["name"] for p in game["players"]] == ["inviter"]
    r = client.post(f"{BASE}/rooms/{game['code']}/join", headers=opponent_headers)
    assert [(p["id"], p["name"]) for p in r.json()["game"]["players"]] == [
        ("opponent", "inviter"),
        ("me", "opponent"),
    ]

    assert (
        _act(client, inviter_headers, game, "ready").json()["game"]["phase"] == "lobby"
    )
    snapshot = _act(client, opponent_headers, game, "ready").json()
    assert snapshot["game"]["phase"] == "question"
    assert abs(snapshot["server_now"] - time.time() * 1000) < 5000
    code = game["code"]

    for r in range(vocab.ROUNDS):
        key = _key(code)
        mine = _act(
            client, inviter_headers, game, "answer", round=r, choice=key
        ).json()["game"]
        # The opponent hasn't answered, so nothing is revealed yet.
        assert mine["phase"] == "question"
        assert mine["mine"] == key
        assert mine["question"]["correct"] is None
        assert [p["answered"] for p in mine["players"]] == [True, False]
        wrong = (key + 1) % len(mine["question"]["options"])
        theirs = _act(client, opponent_headers, game, "answer", round=r, choice=wrong)
        resolved = theirs.json()["game"]
        assert resolved["phase"] == "feedback"
        assert resolved["question"]["correct"] == key
        assert resolved["history"][-1]["answers"][0]["correct"] is True
        # Feedback ends at its deadline, observed on the next poll.
        _set_state(code, deadline=0)
        _get(client, inviter_headers, code)

    results = _get(client, opponent_headers, code)
    assert results["phase"] == "results"
    assert len(results["history"]) == vocab.ROUNDS
    scores = {p["name"]: p["score"] for p in results["players"]}
    assert scores["opponent"] == 0
    assert 500 <= scores["inviter"] <= 525

    after = _act(client, inviter_headers, results, "rematch").json()["game"]
    assert after["phase"] == "results"
    assert after["rematch_requested"] is True
    fresh = _act(client, opponent_headers, results, "rematch").json()["game"]
    assert fresh["phase"] == "lobby"
    assert fresh["generation"] == 1
    assert fresh["revision"] > results["revision"]


def test_round_without_answers_resolves_on_a_poll_after_its_deadline(
    client, inviter_headers, opponent_headers
):
    game = _start_duel(client, inviter_headers, opponent_headers)
    _set_state(game["code"], deadline=0)
    view = _get(client, opponent_headers, game["code"])
    assert view["phase"] == "feedback"
    assert view["history"][0]["answers"] == [
        {
            "name": "inviter",
            "choice": None,
            "points": None,
            "correct": None,
            "ms": None,
        },
        {
            "name": "opponent",
            "choice": None,
            "points": None,
            "correct": None,
            "ms": None,
        },
    ]
    r = _act(client, inviter_headers, game, "answer", round=0, choice=0)
    assert r.status_code == 409


def test_context_question_hides_the_word_until_it_resolves(client, auth_headers):
    game = _create(client, auth_headers, "solo")
    _set_state(game["code"], round=1)
    question = _get(client, auth_headers, game["code"])["question"]
    assert question["kind"] == "context"
    assert question["word"] is None
    assert question["id"] is None
    assert "_____" in question["prompt"]


def test_solo_waits_for_next(client, auth_headers):
    game = _create(client, auth_headers, "solo")
    assert game["phase"] == "question"
    assert _act(client, auth_headers, game, "next").status_code == 409
    key = _key(game["code"])
    answered = _act(client, auth_headers, game, "answer", round=0, choice=key).json()[
        "game"
    ]
    assert answered["phase"] == "feedback"
    nxt = _act(client, auth_headers, game, "next").json()["game"]
    assert (nxt["phase"], nxt["round"]) == ("question", 1)


def test_room_access_errors(client, auth_headers, inviter_headers, opponent_headers):
    game = _start_duel(client, inviter_headers, opponent_headers)
    code = game["code"]
    assert client.get(f"{BASE}/rooms/{code}").status_code == 401
    r = client.get(f"{BASE}/rooms/{code}", headers=auth_headers)
    assert r.status_code == 403
    assert "participant" in r.json()["detail"]
    assert (
        client.post(f"{BASE}/rooms/{code}/join", headers=auth_headers).status_code
        == 409
    )
    assert client.get(f"{BASE}/rooms/XYZ", headers=auth_headers).status_code == 422
    missing = "ABCDEF" if code != "ABCDEF" else "FEDCBA"
    assert (
        client.get(f"{BASE}/rooms/{missing}", headers=auth_headers).status_code == 404
    )

    solo = _create(client, inviter_headers, "solo")
    r = client.post(f"{BASE}/rooms/{solo['code']}/join", headers=opponent_headers)
    assert r.status_code == 403

    _set_state(code, created=vocab.now_ms() - vocab.ROOM_LIFETIME_MS - 1)
    assert (
        client.get(f"{BASE}/rooms/{code}", headers=inviter_headers).status_code == 410
    )

    r = _act(client, inviter_headers, solo, "teleport")
    assert r.status_code == 422


def test_invalid_answers_are_rejected(client, auth_headers):
    game = _create(client, auth_headers, "solo")
    r = _act(client, auth_headers, game, "answer", round=0, choice=99)
    assert r.status_code == 400
    r = _act(
        client, auth_headers, {**game, "generation": 1}, "answer", round=0, choice=0
    )
    assert r.status_code == 409
    r = _act(client, auth_headers, game, "answer", round=0, choice=0)
    assert r.status_code == 200
    r = _act(client, auth_headers, game, "answer", round=0, choice=0)
    assert r.status_code == 409


def test_simultaneous_answers_both_land(client, inviter_headers, opponent_headers):
    game = _start_duel(client, inviter_headers, opponent_headers)
    code = game["code"]
    db = SessionLocal()
    try:
        opponent = db.query(User).filter(User.username == "opponent").one()
        # This session now holds the room as it was before the inviter's answer...
        stale_row = db.get(VocabRoom, code)
        assert stale_row is not None
        r = _act(client, inviter_headers, game, "answer", round=0, choice=0)
        assert r.status_code == 200
        assert stale_row.state["players"][0]["answers"] == {}
        # ...so its write hits a stale version and must be replayed, not overwrite it.
        snapshot, _ = crud_vocab.mutate(
            db,
            code,
            str(opponent.id),
            lambda g, now: vocab.apply_action(
                g,
                str(opponent.id),
                "answer",
                now,
                choice=1,
                round_index=0,
                generation=0,
            ),
        )
    finally:
        db.close()

    assert snapshot.game.phase == "feedback"
    players = _state(code)["players"]
    assert [p["answers"]["0"]["choice"] for p in players] == [0, 1]


def _join_room_topic(ws, code: str) -> None:
    assert ws.receive_json()["type"] == "connected"
    ws.send_json({"type": "join_room", "code": code.lower()})
    assert ws.receive_json() == {"type": "joined_room", "code": code}


def _assert_no_frame_before_echo(ws) -> None:
    """Frames reach sockets in the order they were published, so if a lobby echo sent
    now comes back first, nothing was published for this socket before it."""
    ws.send_text("echo")
    assert ws.receive_json()["type"] == "message"


def test_room_changed_reaches_players_only_for_news(
    client, auth_headers, inviter_headers, opponent_headers
):
    game = _create(client, inviter_headers)
    code = game["code"]
    with (
        client.websocket_connect("/ws/lobby", headers=inviter_headers) as host_ws,
        client.websocket_connect("/ws/lobby", headers=auth_headers) as stranger_ws,
    ):
        _join_room_topic(host_ws, code)
        assert stranger_ws.receive_json()["type"] == "connected"
        stranger_ws.send_json({"type": "join_room", "code": code})
        stranger_ws.send_json({"type": "join_room", "code": "nope"})
        # The host's own GET only records `seen`: no news.
        _get(client, inviter_headers, code)
        _assert_no_frame_before_echo(host_ws)
        assert stranger_ws.receive_json()["type"] == "message"

        r = client.post(f"{BASE}/rooms/{code}/join", headers=opponent_headers)
        assert host_ws.receive_json() == {
            "type": "room_changed",
            "code": code,
            "revision": r.json()["game"]["revision"],
        }
        _act(client, inviter_headers, game, "ready")
        assert host_ws.receive_json()["type"] == "room_changed"
        game = _act(client, opponent_headers, game, "ready").json()["game"]
        assert host_ws.receive_json()["revision"] == game["revision"]

        r = _act(client, opponent_headers, game, "answer", round=0, choice=0)
        game = r.json()["game"]
        assert host_ws.receive_json()["revision"] == game["revision"]

        # A GET after the deadline resolves the round, which is news.
        _set_state(code, deadline=0)
        view = _get(client, opponent_headers, code)
        assert view["phase"] == "feedback"
        assert host_ws.receive_json()["revision"] == view["revision"]

        host_ws.send_json({"type": "leave_room", "code": code})
        _act(client, opponent_headers, game, "ready")  # rejected: nothing changes
        _get(client, opponent_headers, code)
        _assert_no_frame_before_echo(host_ws)
        # The stranger was never subscribed, so only its own echoes ever arrived.
        _assert_no_frame_before_echo(stranger_ws)


def _backdate(code: str, days: float) -> None:
    """Make a room `days` old, in both its rules' clock and its row's created_at."""
    _set_state(code, created=vocab.now_ms() - int(days * 24 * 60 * 60 * 1000))
    db = SessionLocal()
    try:
        row = db.get(VocabRoom, code)
        assert row is not None
        row.created_at = utcnow() - timedelta(days=days)
        db.commit()
    finally:
        db.close()


def test_creating_a_room_deletes_rooms_a_week_past_expiry(client, inviter_headers):
    stale = _create(client, inviter_headers)["code"]
    recent = _create(client, inviter_headers)["code"]
    _backdate(stale, 8.5)
    _backdate(recent, 2)

    _create(client, inviter_headers, "solo")

    r = client.get(f"{BASE}/rooms/{stale}", headers=inviter_headers)
    assert r.status_code == 404
    # Expired, but recently enough that its link still says so.
    r = client.get(f"{BASE}/rooms/{recent}", headers=inviter_headers)
    assert r.status_code == 410


def test_wordbook_save_review_remove(client, inviter_headers, opponent_headers):
    assert client.put(f"{BASE}/words/3", headers=inviter_headers).status_code == 204
    assert client.put(f"{BASE}/words/3", headers=inviter_headers).status_code == 204
    book = client.get(f"{BASE}/words", headers=inviter_headers).json()
    assert book["word_count"] == len(vocab.WORDS)
    assert [(w["id"], w["word"], w["level"]) for w in book["saved"]] == [
        (3, vocab.WORDS[3].word, 0)
    ]
    saved_at = book["saved"][0]["due"]
    assert abs(saved_at - time.time() * 1000) < 5000

    r = client.post(
        f"{BASE}/words/3/review", json={"known": True}, headers=inviter_headers
    )
    assert r.status_code == 204
    word = client.get(f"{BASE}/words", headers=inviter_headers).json()["saved"][0]
    assert word["level"] == 1
    assert abs(word["due"] - saved_at - 86_400_000) < 5000

    client.post(
        f"{BASE}/words/3/review", json={"known": False}, headers=inviter_headers
    )
    word = client.get(f"{BASE}/words", headers=inviter_headers).json()["saved"][0]
    assert word["level"] == 0

    assert client.get(f"{BASE}/words", headers=opponent_headers).json()["saved"] == []
    r = client.post(
        f"{BASE}/words/3/review", json={"known": True}, headers=opponent_headers
    )
    assert r.status_code == 404
    assert client.delete(f"{BASE}/words/3", headers=opponent_headers).status_code == 204
    assert (
        len(client.get(f"{BASE}/words", headers=inviter_headers).json()["saved"]) == 1
    )

    assert client.delete(f"{BASE}/words/3", headers=inviter_headers).status_code == 204
    assert client.get(f"{BASE}/words", headers=inviter_headers).json()["saved"] == []


def test_wordbook_rejects_unknown_words_and_guests(client, auth_headers):
    assert (
        client.put(f"{BASE}/words/{len(vocab.WORDS)}", headers=auth_headers).status_code
        == 400
    )
    assert client.put(f"{BASE}/words/-1", headers=auth_headers).status_code == 400
    assert client.get(f"{BASE}/words").status_code == 401
