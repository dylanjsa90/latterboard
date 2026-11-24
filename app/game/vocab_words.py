"""The Vocab Challenger word list: one canonical JSON file per word in vocab_entries/.

Entries load once at import, never at request time. A word's id is permanent (saved
wordbooks store it) and so is its slug, so never renumber, rename or delete a file.
In the room question options[0] is the correct usage and reasons line up with options,
so nothing here may reach a client before a round resolves. Practice is page-only and
never used in a room. New words arrive only through app/vocab_pipeline approvals.
"""

import json
import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from types import MappingProxyType
from typing import Any, Literal

Difficulty = Literal["Foundation", "Intermediate", "Advanced"]
Pos = Literal["noun", "verb", "adjective", "adverb"]

DIFFICULTIES: tuple[Difficulty, ...] = ("Foundation", "Intermediate", "Advanced")
POS: tuple[Pos, ...] = ("noun", "verb", "adjective", "adverb")
ENTRIES_DIR = Path(__file__).with_name("vocab_entries")
FIELDS = (
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
    "prompt",
    "options",
    "reasons",
    "practice",
)
PRACTICE_FIELDS = ("prompt", "options", "answer", "reasons")


@dataclass(frozen=True)
class Practice:
    prompt: str
    options: tuple[str, ...]
    # Index of the correct option, stored as shown.
    answer: int
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class VocabWord:
    id: int
    slug: str
    word: str
    pos: Pos
    # US IPA, as shown under the word of the day.
    pronunciation: str
    definition: str
    difficulty: Difficulty
    example: str
    synonyms: tuple[str, ...]
    nuance: str
    # Slugs of shipped words the context question offers as distractors.
    alternatives: tuple[str, ...]
    # The scenario shown above the usage question.
    prompt: str
    # Four sentences using the word; options[0] is the one that uses it correctly.
    options: tuple[str, ...]
    # Why each option is right or wrong, aligned with options.
    reasons: tuple[str, ...]
    # Page-only practice question; never used in a room.
    practice: Practice


_UNSPLIT = str.maketrans(
    {
        "œ": "oe", "Œ": "OE", "æ": "ae", "Æ": "AE", "ø": "o", "Ø": "O",
        "ß": "ss", "ẞ": "SS", "ð": "d", "Ð": "D", "þ": "th", "Þ": "TH",
        "ł": "l", "Ł": "L",
    }
)  # fmt: skip


def slugify(word: str) -> str:
    folded = unicodedata.normalize("NFKD", word.translate(_UNSPLIT))
    letters = "".join(c for c in folded if not unicodedata.combining(c))
    return re.sub(r"[^a-z]+", "-", letters.lower()).strip("-")


def entry_filename(word_id: int, slug: str) -> str:
    return f"{word_id:05d}-{slug}.json"


def _check_keys(where: str, data: Mapping[str, Any], expected: tuple[str, ...]) -> None:
    extra = sorted(set(data) - set(expected))
    missing = [k for k in expected if k not in data]
    if extra:
        raise ValueError(f"{where}: unexpected keys: {', '.join(extra)}")
    if missing:
        raise ValueError(f"{where}: missing keys: {', '.join(missing)}")


def word_from_json(data: Mapping[str, Any]) -> VocabWord:
    _check_keys("entry", data, FIELDS)
    raw = data["practice"]
    if raw is None:
        raise ValueError("practice is required")
    _check_keys("practice", raw, PRACTICE_FIELDS)
    practice = Practice(
        prompt=raw["prompt"],
        options=tuple(raw["options"]),
        answer=raw["answer"],
        reasons=tuple(raw["reasons"]),
    )
    return VocabWord(
        id=data["id"],
        slug=data["slug"],
        word=data["word"],
        pos=data["pos"],
        pronunciation=data["pronunciation"],
        definition=data["definition"],
        difficulty=data["difficulty"],
        example=data["example"],
        synonyms=tuple(data["synonyms"]),
        nuance=data["nuance"],
        alternatives=tuple(data["alternatives"]),
        prompt=data["prompt"],
        options=tuple(data["options"]),
        reasons=tuple(data["reasons"]),
        practice=practice,
    )


def word_to_json(w: VocabWord) -> dict[str, Any]:
    p = w.practice
    return {
        "id": w.id,
        "slug": w.slug,
        "word": w.word,
        "pos": w.pos,
        "pronunciation": w.pronunciation,
        "definition": w.definition,
        "difficulty": w.difficulty,
        "example": w.example,
        "synonyms": list(w.synonyms),
        "nuance": w.nuance,
        "alternatives": list(w.alternatives),
        "prompt": w.prompt,
        "options": list(w.options),
        "reasons": list(w.reasons),
        "practice": {
            "prompt": p.prompt,
            "options": list(p.options),
            "answer": p.answer,
            "reasons": list(p.reasons),
        },
    }


def dump_entry(data: Mapping[str, Any]) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def load_words(directory: Path = ENTRIES_DIR) -> tuple[VocabWord, ...]:
    """Every entry in `directory`, ordered by id. Raises ValueError on a malformed file,
    a file name that isn't entry_filename(id, slug), a duplicate id or slug or an id gap."""
    by_id: dict[int, tuple[str, VocabWord]] = {}
    by_slug: dict[str, str] = {}
    for path in sorted(directory.glob("*.json")):
        try:
            w = word_from_json(json.loads(path.read_text(encoding="utf-8")))
            if path.name != entry_filename(w.id, w.slug):
                raise ValueError(f"should be named {entry_filename(w.id, w.slug)}")
        except (ValueError, TypeError, KeyError) as e:
            raise ValueError(f"{path.name}: {e}") from e
        if w.id in by_id:
            raise ValueError(f"duplicate id {w.id}: {by_id[w.id][0]}, {path.name}")
        if w.slug in by_slug:
            raise ValueError(f"duplicate slug {w.slug}: {by_slug[w.slug]}, {path.name}")
        by_id[w.id] = (path.name, w)
        by_slug[w.slug] = path.name
    for i in range(len(by_id)):
        if i not in by_id:
            raise ValueError(f"ids must run from 0 without gaps; missing {i}")
    return tuple(by_id[i][1] for i in range(len(by_id)))


WORDS: tuple[VocabWord, ...] = load_words()
BY_SLUG: Mapping[str, VocabWord] = MappingProxyType({w.slug: w for w in WORDS})


LEGACY_DAILY_COUNT = 46
DAILY_ANCHOR = date(2026, 12, 6)  # toordinal() % 46 == 0


def daily_id(day: date, count: int) -> int:
    """The word id for a day. Before the anchor the original 46-word rotation holds; from
    the anchor on, ids go one per day in order, so appending words never moves a past day."""
    n = (day - DAILY_ANCHOR).days
    if n < 0:
        return day.toordinal() % LEGACY_DAILY_COUNT
    return n % count


def daily_runway(today: date, count: int) -> int:
    """Days left before the schedule wraps to id 0 again; negative once it has."""
    return (DAILY_ANCHOR - today).days + count - 1


def daily_word(day: date) -> VocabWord:
    """The word of the day for a calendar date (callers pass the Pacific day)."""
    return WORDS[daily_id(day, len(WORDS))]
