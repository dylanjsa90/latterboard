"""Builders for Vocab Challenger word entries, shared by the word-list tests."""

from collections.abc import Iterable
from pathlib import Path
from typing import Any

from app.game.vocab_words import dump_entry, entry_filename
from app.vocab_pipeline.store import Draft, Store


def entry(word_id: int, word: str, **overrides: Any) -> dict[str, Any]:
    """A shipped entry's JSON dict for a lowercase `word`: an adjective at Foundation
    difficulty with four room options, practice, two synonyms and no alternatives."""
    data: dict[str, Any] = {
        "id": word_id,
        "slug": word,
        "word": word,
        "pos": "adjective",
        "pronunciation": f"/{word}/",
        "definition": f"Having the quality of being {word}.",
        "difficulty": "Foundation",
        "example": f"Nobody doubted the {word} verdict after the vote.",
        "synonyms": [f"{word}ish", f"{word}like"],
        "nuance": f"{word.capitalize()} describes a plain quality, not a degree.",
        "alternatives": [],
        "prompt": f"A writer is choosing the right word for a {word} scene.",
        "options": [
            f"The {word} remark settled the matter quickly.",
            f"The {word} remark was spoken by nobody at all.",
            f"The {word} remark weighed three kilograms.",
            f"The {word} remark turned left at the corner.",
        ],
        "reasons": [
            f"It uses {word} in its ordinary sense.",
            "A remark needs a speaker, so this contradicts itself.",
            "A remark has no weight.",
            "A remark cannot move along a street.",
        ],
        "practice": {
            "prompt": f"Which sentence uses {word} correctly?",
            "options": [
                f"She made a {word} choice at lunch today.",
                f"He {word} the heavy door slowly.",
                f"The {word} ran home before dark.",
                f"They arrived {word} at the station.",
            ],
            "answer": 0,
            "reasons": [
                f"{word.capitalize()} modifies the noun choice.",
                f"{word.capitalize()} is not a verb.",
                f"{word.capitalize()} is not a noun.",
                f"{word.capitalize()} is not an adverb.",
            ],
        },
    }
    data.update(overrides)
    return data


def write_entries(directory: Path, entries: Iterable[dict[str, Any]]) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    for data in entries:
        path = directory / entry_filename(data["id"], data["slug"])
        path.write_text(dump_entry(data), encoding="utf-8")


def pending_draft(word: str, **entry_overrides: Any) -> Draft:
    """A pending, generated draft whose entry is entry(0, word, ...) without its id."""
    proposed = entry(0, word, **entry_overrides)
    del proposed["id"]
    return {
        "slug": word,
        "word": word,
        "pos": "adjective",
        "difficulty": "Foundation",
        "source": "generated",
        "status": "pending",
        "attempts": 1,
        "batch_id": None,
        "errors": [],
        "note": "",
        "entry": proposed,
    }


def make_store(tmp_path: Path, shipped: list[dict[str, Any]]) -> Store:
    write_entries(tmp_path / "entries", shipped)
    return Store(tmp_path / "entries", tmp_path / "vocab")
