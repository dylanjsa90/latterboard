from datetime import timedelta

import app.api.routes.invite_links as invite_links_route
from app.core.config import settings
from app.database import SessionLocal
from app.models import MatchInviteLink
from app.utils import utcnow
from tests.conftest import _signup_and_login

BASE = "/api/v1/invite-links"


def _create(client, headers, **body):
    return client.post(
        f"{BASE}/", json={"game": "wordle", "channel": "text", **body}, headers=headers
    )


def _update_link(token: str, **fields) -> None:
    db = SessionLocal()
    try:
        link = db.query(MatchInviteLink).filter(MatchInviteLink.token == token).one()
        for name, value in fields.items():
            setattr(link, name, value)
        db.commit()
    finally:
        db.close()


def _capture_email(monkeypatch, *, smtp: bool) -> list[dict]:
    sent: list[dict] = []
    monkeypatch.setattr(invite_links_route, "send_email", lambda **kw: sent.append(kw))
    monkeypatch.setattr(settings, "SMTP_HOST", "smtp.example.com" if smtp else None)
    monkeypatch.setattr(settings, "EMAILS_FROM_EMAIL", "games@example.com")
    return sent


def test_create_text_link(client, inviter_headers):
    r = _create(client, inviter_headers, message="  Bet you can't beat me  ")
    assert r.status_code == 201
    data = r.json()
    assert data["message"] == "Bet you can't beat me"
    assert data["url"].endswith(f"/invite/{data['token']}")
    assert data["channel"] == "text"
    assert data["recipient_email"] is None
    assert data["emailed"] is False


def test_blank_message_is_dropped(client, inviter_headers):
    r = _create(client, inviter_headers, message="   ")
    assert r.status_code == 201
    assert r.json()["message"] is None


def test_create_requires_sign_in(client):
    assert _create(client, {}).status_code == 401


def test_email_channel_needs_address(client, inviter_headers):
    assert _create(client, inviter_headers, channel="email").status_code == 422


def test_text_channel_rejects_address(client, inviter_headers):
    r = _create(client, inviter_headers, email="friend@example.com")
    assert r.status_code == 422


def test_message_too_long(client, inviter_headers):
    assert _create(client, inviter_headers, message="x" * 281).status_code == 422


def test_unsupported_game(client, inviter_headers):
    assert _create(client, inviter_headers, game="chess").status_code == 400


def test_email_not_sent_without_smtp(client, inviter_headers, monkeypatch):
    sent = _capture_email(monkeypatch, smtp=False)
    r = _create(client, inviter_headers, channel="email", email="friend@example.com")
    assert r.status_code == 201
    assert r.json()["emailed"] is False
    assert r.json()["recipient_email"] == "friend@example.com"
    assert sent == []


def test_email_sent_with_escaped_message(client, inviter_headers, monkeypatch):
    sent = _capture_email(monkeypatch, smtp=True)
    r = _create(
        client,
        inviter_headers,
        channel="email",
        email="friend@example.com",
        message="<b>rematch</b>",
    )
    assert r.status_code == 201
    assert r.json()["emailed"] is True
    assert len(sent) == 1
    assert sent[0]["email_to"] == "friend@example.com"
    assert sent[0]["subject"] == "inviter invited you to play Word Duel"
    html = sent[0]["html_content"]
    assert "&lt;b&gt;rematch&lt;/b&gt;" in html
    assert "<b>rematch</b>" not in html
    assert f"/invite/{r.json()['token']}" in html


def test_daily_limit(client, inviter_headers, monkeypatch):
    monkeypatch.setattr(settings, "MATCH_INVITE_LINK_DAILY_LIMIT", 2)
    assert _create(client, inviter_headers).status_code == 201
    assert _create(client, inviter_headers).status_code == 201
    assert _create(client, inviter_headers).status_code == 429


def test_preview_is_public(client, inviter_headers):
    token = _create(client, inviter_headers, game="word_race", message="Go").json()["token"]
    r = client.get(f"{BASE}/{token}")
    assert r.status_code == 200
    data = r.json()
    assert data["inviter_username"] == "inviter"
    assert data["game"] == "word_race"
    assert data["message"] == "Go"
    assert data["status"] == "open"


def test_preview_unknown_token(client):
    assert client.get(f"{BASE}/nope").status_code == 404


def test_claim_starts_match(client, inviter_headers, opponent_headers):
    token = _create(client, inviter_headers).json()["token"]
    r = client.post(f"{BASE}/{token}/claim", headers=opponent_headers)
    assert r.status_code == 200
    match = r.json()
    assert match["status"] == "in_progress"
    assert match["inviter_username"] == "inviter"
    assert match["invitee_username"] == "opponent"
    # The sender invited first, so they take the first turn.
    assert match["current_turn_username"] == "inviter"
    assert client.get(f"{BASE}/{token}").json()["status"] == "claimed"


def test_claim_again_returns_same_match(client, inviter_headers, opponent_headers):
    token = _create(client, inviter_headers).json()["token"]
    first = client.post(f"{BASE}/{token}/claim", headers=opponent_headers).json()
    second = client.post(f"{BASE}/{token}/claim", headers=opponent_headers)
    assert second.status_code == 200
    assert second.json()["id"] == first["id"]


def test_claim_by_second_player_conflicts(client, inviter_headers, opponent_headers):
    token = _create(client, inviter_headers).json()["token"]
    client.post(f"{BASE}/{token}/claim", headers=opponent_headers)
    third = _signup_and_login(client, "third@example.com", "third")
    assert client.post(f"{BASE}/{token}/claim", headers=third).status_code == 409


def test_claim_own_link(client, inviter_headers):
    token = _create(client, inviter_headers).json()["token"]
    assert client.post(f"{BASE}/{token}/claim", headers=inviter_headers).status_code == 400


def test_claim_expired(client, inviter_headers, opponent_headers):
    token = _create(client, inviter_headers).json()["token"]
    _update_link(token, expires_at=utcnow() - timedelta(minutes=1))
    assert client.get(f"{BASE}/{token}").json()["status"] == "expired"
    assert client.post(f"{BASE}/{token}/claim", headers=opponent_headers).status_code == 410


def test_claim_reuses_open_match(client, inviter_headers, opponent_headers):
    pending = client.post(
        "/api/v1/matches/invite",
        json={"opponent_username": "opponent", "game": "wordle"},
        headers=inviter_headers,
    ).json()
    token = _create(client, inviter_headers).json()["token"]
    r = client.post(f"{BASE}/{token}/claim", headers=opponent_headers)
    assert r.status_code == 200
    assert r.json()["id"] == pending["id"]
    assert r.json()["status"] == "in_progress"


def test_revoke(client, inviter_headers, opponent_headers):
    token = _create(client, inviter_headers).json()["token"]
    assert client.delete(f"{BASE}/{token}", headers=opponent_headers).status_code == 403
    assert client.delete(f"{BASE}/{token}", headers=inviter_headers).status_code == 204
    assert client.get(f"{BASE}/{token}").json()["status"] == "revoked"
    assert client.post(f"{BASE}/{token}/claim", headers=opponent_headers).status_code == 410


def test_revoke_used_link_conflicts(client, inviter_headers, opponent_headers):
    token = _create(client, inviter_headers).json()["token"]
    client.post(f"{BASE}/{token}/claim", headers=opponent_headers)
    assert client.delete(f"{BASE}/{token}", headers=inviter_headers).status_code == 409


def test_mine_lists_open_links(client, inviter_headers, opponent_headers):
    kept = _create(client, inviter_headers).json()["token"]
    used = _create(client, inviter_headers).json()["token"]
    withdrawn = _create(client, inviter_headers).json()["token"]
    client.post(f"{BASE}/{used}/claim", headers=opponent_headers)
    client.delete(f"{BASE}/{withdrawn}", headers=inviter_headers)

    r = client.get(f"{BASE}/mine", headers=inviter_headers)
    assert r.status_code == 200
    assert [link["token"] for link in r.json()] == [kept]
