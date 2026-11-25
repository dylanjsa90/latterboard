"""Word reports: anyone can report a problem with a word; only a superuser reads them."""

import pytest
import redis as sync_redis

from app.api.routes import vocab as vocab_routes
from app.core.config import settings
from app.game.vocab_words import WORDS

BASE = "/api/v1/vocab"


def _clear_report_limits() -> None:
    client = sync_redis.Redis.from_url(settings.REDIS_URL)
    for key in client.scan_iter(match=f"{vocab_routes.REPORT_LIMIT_PREFIX}:*"):
        client.delete(key)
    client.close()


@pytest.fixture(autouse=True)
def fresh_report_limits():
    """Tests share Redis across runs, so earlier counts would refuse this run's reports."""
    _clear_report_limits()
    yield
    _clear_report_limits()


def test_guests_and_players_report_and_only_a_superuser_reads_them(
    client, inviter_headers, superuser_headers
):
    guest = {"reason": "wrong_answer", "kind": "usage", "note": "  B is right too.  "}
    assert client.post(f"{BASE}/words/1/report", json=guest).status_code == 204
    signed_in = client.post(
        f"{BASE}/words/2/report", json={"reason": "confusing"}, headers=inviter_headers
    )
    assert signed_in.status_code == 204, signed_in.text

    assert client.get(f"{BASE}/reports").status_code == 401
    assert client.get(f"{BASE}/reports", headers=inviter_headers).status_code == 403
    r = client.get(f"{BASE}/reports", headers=superuser_headers)
    assert r.status_code == 200, r.text
    newest, oldest = r.json()
    assert (newest["word_id"], newest["word"], newest["reason"]) == (
        2,
        WORDS[2].word,
        "confusing",
    )
    assert newest["user_id"] is not None and newest["kind"] is None
    assert (oldest["word"], oldest["kind"], oldest["note"]) == (
        WORDS[1].word,
        "usage",
        "B is right too.",
    )
    assert oldest["user_id"] is None


def test_a_report_needs_a_known_word_and_a_valid_body(client):
    unknown = client.post(f"{BASE}/words/{len(WORDS)}/report", json={"reason": "other"})
    assert unknown.status_code == 400
    for body in (
        {},
        {"reason": "spam"},
        {"reason": "other", "kind": "practice"},
        {"reason": "other", "note": "x" * 501},
    ):
        r = client.post(f"{BASE}/words/0/report", json=body)
        assert r.status_code == 422, body


def test_reports_are_limited_per_network_then_site_wide(client, monkeypatch):
    def report():
        return client.post(f"{BASE}/words/0/report", json={"reason": "other"})

    monkeypatch.setattr(vocab_routes, "REPORTS_PER_NETWORK_PER_HOUR", 2)
    assert [report().status_code for _ in range(2)] == [204, 204]
    limited = report()
    assert (limited.status_code, limited.json()["detail"]) == (
        429,
        vocab_routes.REPORT_LIMITED,
    )

    _clear_report_limits()
    monkeypatch.setattr(vocab_routes, "REPORTS_PER_NETWORK_PER_HOUR", 100)
    monkeypatch.setattr(vocab_routes, "REPORTS_PER_HOUR", 1)
    assert [report().status_code for _ in range(2)] == [204, 429]
