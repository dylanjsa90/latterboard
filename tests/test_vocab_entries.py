"""The public word list for webcade's word pages (GET /vocab/entries)."""

from collections.abc import Iterator
from dataclasses import replace
from typing import Any

from app.game import vocab, vocab_words
from app.game.vocab_public import public_entries

BASE = "/api/v1/vocab"
ENTRY_FIELDS = {
    "id",
    "slug",
    "word",
    "pos",
    "pronunciation",
    "definition",
    "difficulty",
    "example",
    "synonyms",
    "nuance",
    "alternatives",
    "practice",
}


def _strings(value: Any) -> Iterator[str]:
    """Every string leaf of a JSON value."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for v in value.values():
            yield from _strings(v)
    elif isinstance(value, list):
        for v in value:
            yield from _strings(v)


def test_entries_are_public_and_cacheable(client):
    r = client.get(f"{BASE}/entries")
    assert r.status_code == 200
    assert r.headers["cache-control"] == "public, max-age=300"
    assert [e["id"] for e in r.json()["entries"]] == [w.id for w in vocab_words.WORDS]


def test_entries_carry_exactly_the_contract_fields(client):
    for e in client.get(f"{BASE}/entries").json()["entries"]:
        assert set(e) == ENTRY_FIELDS
        assert set(e["practice"]) == {"prompt", "options", "answer", "reasons"}
        assert len(e["practice"]["options"]) == 4 == len(e["practice"]["reasons"])
        assert 0 <= e["practice"]["answer"] < 4
        assert e["synonyms"] and all(isinstance(s, str) for s in e["synonyms"])


def test_entries_never_carry_a_room_question(client):
    leaves = set(_strings(client.get(f"{BASE}/entries").json()))
    room = {
        s
        for w in vocab_words.WORDS
        for s in (
            w.prompt,
            *w.options,
            *w.reasons,
            vocab.with_blank(w.options[0], w.word),
        )
    }
    assert leaves.isdisjoint(room)


def test_alternatives_are_slugs_of_other_entries(client):
    entries = client.get(f"{BASE}/entries").json()["entries"]
    slugs = {e["slug"] for e in entries}
    for e in entries:
        assert set(e["alternatives"]) <= slugs - {e["slug"]}
        assert len(e["alternatives"]) == len(set(e["alternatives"]))


def test_public_entries_keeps_only_other_shipped_slugs():
    w0, w1, w2 = vocab_words.WORDS[:3]
    word = replace(w0, alternatives=(w1.slug, "notshipped", w0.slug, w2.slug, w1.slug))
    (entry,) = [e for e in public_entries([word, w1, w2]).entries if e.id == w0.id]
    assert entry.alternatives == [w1.slug, w2.slug]
