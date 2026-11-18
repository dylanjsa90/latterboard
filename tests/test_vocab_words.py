import pytest

from app.game.vocab_words import (
    BY_SLUG,
    ENTRIES_DIR,
    WORDS,
    dump_entry,
    entry_filename,
    load_words,
    slugify,
    word_to_json,
)
from tests.vocab_helpers import entry, write_entries


def test_shipped_files_are_canonical():
    files = sorted(ENTRIES_DIR.glob("*.json"))
    assert len(files) == len(WORDS) >= 46
    for path, w in zip(files, WORDS, strict=True):
        assert path.name == entry_filename(w.id, w.slug)
        assert path.read_text(encoding="utf-8") == dump_entry(word_to_json(w)), (
            path.name
        )


def test_original_words_keep_their_ids_and_text():
    assert [w.slug for w in WORDS[:3]] == ["discerning", "equivocal", "pragmatic"]
    assert WORDS[45].slug == "unequivocal" and BY_SLUG["latent"].id == 44
    assert WORDS[1].synonyms == ("ambiguous", "inconclusive", "unclear")
    assert WORDS[0].pronunciation == "/dɪˈsɜːr.nɪŋ/"
    assert WORDS[1].options[0].startswith("The committee offered an equivocal response")
    assert all(w.slug == w.word for w in WORDS[:46])
    assert all(w.practice is None for w in WORDS[:46])


def test_slugify():
    assert [
        slugify(w) for w in ("Equivocal", "naïve", "ad hoc", "laissez-faire", "o'clock")
    ] == ["equivocal", "naive", "ad-hoc", "laissez-faire", "o-clock"]


def test_loader_rejects_bad_directories(tmp_path):
    write_entries(tmp_path / "ok", [entry(0, "alpha"), entry(1, "beta")])
    assert [w.slug for w in load_words(tmp_path / "ok")] == ["alpha", "beta"]
    write_entries(
        tmp_path / "dup", [entry(0, "alpha"), entry(1, "beta"), entry(1, "gamma")]
    )
    with pytest.raises(
        ValueError, match="duplicate id 1: 00001-beta.json, 00001-gamma.json"
    ):
        load_words(tmp_path / "dup")
    write_entries(tmp_path / "gap", [entry(0, "alpha"), entry(2, "gamma")])
    with pytest.raises(ValueError, match="missing 1"):
        load_words(tmp_path / "gap")
    (tmp_path / "name").mkdir()
    (tmp_path / "name" / "00000-alfa.json").write_text(dump_entry(entry(0, "alpha")))
    with pytest.raises(ValueError, match="should be named 00000-alpha.json"):
        load_words(tmp_path / "name")
    write_entries(tmp_path / "keys", [{**entry(0, "alpha"), "status": "pending"}])
    with pytest.raises(ValueError, match="unexpected keys: status"):
        load_words(tmp_path / "keys")
