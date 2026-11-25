"""The word-of-the-day archive (app/game/daily_archive.py, GET /vocab/daily-word/history)."""

from dataclasses import replace
from datetime import date, timedelta

from app.game import puzzles, vocab_words
from app.game.daily_archive import ARCHIVE_START, archive_days

BASE = "/api/v1/vocab"


def _expected(day: date) -> dict:
    w = vocab_words.daily_word(day)
    return {"date": day.isoformat(), "word_id": w.id, "slug": w.slug}


def _history(client, monkeypatch, today: date) -> dict:
    monkeypatch.setattr(puzzles, "today", lambda: today)
    r = client.get(f"{BASE}/daily-word/history")
    assert r.status_code == 200, r.text
    return r.json()


def test_archive_days_run_from_the_start_through_today():
    assert ARCHIVE_START == date(2026, 12, 6)
    assert ARCHIVE_START <= vocab_words.DAILY_ANCHOR
    assert archive_days(ARCHIVE_START - timedelta(days=1)) == []
    assert archive_days(ARCHIVE_START) == [ARCHIVE_START]
    assert archive_days(ARCHIVE_START + timedelta(days=2)) == [
        ARCHIVE_START + timedelta(days=n) for n in range(3)
    ]


def test_history_lists_every_day_through_today_and_none_after(client, monkeypatch):
    today = ARCHIVE_START + timedelta(days=9)
    days = _history(client, monkeypatch, today)["days"]
    assert days == [_expected(d) for d in archive_days(today)]
    assert days[-1]["date"] == today.isoformat()


def test_history_ends_on_the_word_of_the_day(client, monkeypatch):
    today = ARCHIVE_START + timedelta(days=9)
    last = _history(client, monkeypatch, today)["days"][-1]
    daily = client.get(f"{BASE}/daily-word").json()
    assert (last["word_id"], last["slug"]) == (daily["id"], daily["slug"])


def test_history_is_empty_before_the_archive_starts(client, monkeypatch):
    assert _history(client, monkeypatch, ARCHIVE_START - timedelta(days=1)) == {
        "days": []
    }


def test_history_survives_a_new_word(client, monkeypatch):
    today = ARCHIVE_START + timedelta(days=9)
    before = _history(client, monkeypatch, today)
    words = vocab_words.WORDS
    new = replace(words[-1], id=len(words), slug="zzznew", word="zzznew")
    monkeypatch.setattr(vocab_words, "WORDS", (*words, new))
    assert _history(client, monkeypatch, today) == before


def test_history_is_public_and_cacheable(client, monkeypatch):
    monkeypatch.setattr(puzzles, "today", lambda: ARCHIVE_START)
    r = client.get(f"{BASE}/daily-word/history")
    assert r.status_code == 200
    assert r.headers["cache-control"] == "public, max-age=300"
