"""Rules every Vocab Challenger entry must meet. Pure functions over VocabWord: the tests
and the pipeline both call them, so a word is judged the same way before and after it ships.
"""

import re
from collections.abc import Sequence
from difflib import SequenceMatcher

from app.game.vocab import with_blank
from app.game.vocab_words import DIFFICULTIES, POS, VocabWord

OPTION_COUNT = 4
# Ids below this may lack practice and have 3 room options (removed in Task 10).
LEGACY_COUNT = 46
# difflib ratio at or above which two sentences count as the same.
SIMILAR = 0.8
LIMITS: dict[str, tuple[int, int]] = {
    "word": (2, 30),
    "pronunciation": (3, 40),
    "definition": (20, 120),
    "example": (20, 140),
    "nuance": (20, 160),
    "prompt": (20, 140),
    "option": (20, 120),
    "reason": (15, 140),
    "synonym": (2, 30),
}
SYNONYM_COUNT = (2, 4)
MAX_ALTERNATIVES = 3

_SLUG = re.compile(r"^[a-z]+(?:-[a-z]+)*$")
_PARTS = re.compile(r"[ '\-]")


def _exact_count(sentence: str, word: str) -> int:
    return len(re.findall(rf"\b{re.escape(word)}\b", sentence, re.I))


def has_exact_word(sentence: str, word: str) -> bool:
    """The word appears whole, which is what the room's blank substitution needs."""
    return _exact_count(sentence, word) > 0


def uses_word(sentence: str, word: str) -> bool:
    """Some token starts with the word's stem, so inflections count."""
    stem = word[: max(4, len(word) - 2)]
    return bool(re.search(rf"\b{re.escape(stem)}", sentence, re.I))


def _normal(text: str) -> str:
    return " ".join(text.casefold().split()).rstrip(".!?")


def too_close(a: str, b: str) -> bool:
    matcher = SequenceMatcher(None, _normal(a), _normal(b), autojunk=False)
    return matcher.quick_ratio() >= SIMILAR and matcher.ratio() >= SIMILAR


def _text(slug: str, name: str, limit: str, value: str) -> list[str]:
    if not value:
        return [f"{slug}: {name} is empty"]
    if value != value.strip():
        return [f"{slug}: {name} has leading or trailing spaces"]
    low, high = LIMITS[limit]
    if len(value) < low:
        return [f"{slug}: {name}: {len(value)} characters (min {low})"]
    if len(value) > high:
        return [f"{slug}: {name}: {len(value)} characters (max {high})"]
    return []


def _usage_options(
    slug: str, label: str, word: str, options: Sequence[str], reasons: Sequence[str]
) -> list[str]:
    """Checks shared by the room question and the practice question."""
    errors: list[str] = []
    if len(reasons) != len(options):
        errors.append(f"{slug}: {label}reasons must line up with options")
    if len({o.casefold() for o in options}) != len(options):
        errors.append(f"{slug}: {label}options repeat")
    for i, option in enumerate(options):
        errors += _text(slug, f"{label}options[{i}]", "option", option)
        if not uses_word(option, word):
            errors.append(f"{slug}: {label}options[{i}] does not use {word}")
    for i, reason in enumerate(reasons):
        errors += _text(slug, f"{label}reasons[{i}]", "reason", reason)
    return errors


def entry_errors(w: VocabWord, *, strict: bool = False) -> list[str]:
    s = w.slug
    legacy = w.id < LEGACY_COUNT and not strict
    errors: list[str] = []
    if not _SLUG.match(w.slug):
        errors.append(f"{s}: slug must match ^[a-z]+(?:-[a-z]+)*$")
    parts = _PARTS.split(w.word)
    if not all(p.isalpha() for p in parts):
        errors.append(
            f"{s}: word must be letters joined by spaces, hyphens or apostrophes"
        )
    if w.pos not in POS:
        errors.append(f"{s}: pos must be one of {', '.join(POS)}")
    if w.difficulty not in DIFFICULTIES:
        errors.append(f"{s}: difficulty must be one of {', '.join(DIFFICULTIES)}")
    if not (w.pronunciation.startswith("/") and w.pronunciation.endswith("/")):
        errors.append(f"{s}: pronunciation must be between slashes")
    for name, value in (
        ("word", w.word),
        ("pronunciation", w.pronunciation),
        ("definition", w.definition),
        ("example", w.example),
        ("nuance", w.nuance),
        ("prompt", w.prompt),
    ):
        errors += _text(s, name, name, value)

    low, high = SYNONYM_COUNT
    if not low <= len(w.synonyms) <= high:
        errors.append(f"{s}: synonyms: expected {low}-{high}, got {len(w.synonyms)}")
    if len({x.casefold() for x in w.synonyms}) != len(w.synonyms):
        errors.append(f"{s}: synonyms repeat")
    if w.word.casefold() in {x.casefold() for x in w.synonyms}:
        errors.append(f"{s}: synonyms include the word")
    for i, synonym in enumerate(w.synonyms):
        errors += _text(s, f"synonyms[{i}]", "synonym", synonym)

    if len(w.alternatives) > MAX_ALTERNATIVES:
        errors.append(
            f"{s}: alternatives: at most {MAX_ALTERNATIVES}, got {len(w.alternatives)}"
        )
    if len(set(w.alternatives)) != len(w.alternatives):
        errors.append(f"{s}: alternatives repeat")
    if w.slug in w.alternatives:
        errors.append(f"{s}: alternatives include the word itself")

    expected = 3 if legacy and len(w.options) == 3 else OPTION_COUNT
    if len(w.options) != expected:
        errors.append(f"{s}: options: expected {OPTION_COUNT}, got {len(w.options)}")
    errors += _usage_options(s, "", w.word, w.options, w.reasons)
    if w.options and _exact_count(w.options[0], w.word) != 1:
        errors.append(f'{s}: options[0] must contain "{w.word}" exactly once')
    if (
        w.options
        and _exact_count(w.options[0], w.word) == 1
        and uses_word(with_blank(w.options[0], w.word), w.word)
    ):
        errors.append(f"{s}: options[0] still shows a form of {w.word} after blanking")
    if not uses_word(w.example, w.word):
        errors.append(f"{s}: example does not use {w.word}")
    if any(too_close(w.example, o) for o in w.options):
        errors.append(f"{s}: example is too close to the room question")

    p = w.practice
    if p is None:
        if not legacy:
            errors.append(f"{s}: practice is required")
        return errors
    if len(p.options) != OPTION_COUNT:
        errors.append(
            f"{s}: practice.options: expected {OPTION_COUNT}, got {len(p.options)}"
        )
    if len(p.reasons) != OPTION_COUNT:
        errors.append(
            f"{s}: practice.reasons: expected {OPTION_COUNT}, got {len(p.reasons)}"
        )
    if not 0 <= p.answer < OPTION_COUNT:
        errors.append(f"{s}: practice.answer must be 0-{OPTION_COUNT - 1}")
    errors += _text(s, "practice.prompt", "prompt", p.prompt)
    errors += _usage_options(s, "practice.", w.word, p.options, p.reasons)
    if too_close(p.prompt, w.prompt):
        errors.append(f"{s}: practice.prompt is too close to the room question")
    for i, option in enumerate(p.options):
        if any(too_close(option, o) for o in w.options):
            errors.append(
                f"{s}: practice.options[{i}] is too close to the room question"
            )
        if too_close(option, w.example):
            errors.append(f"{s}: practice.options[{i}] is too close to the example")
    return errors


def list_errors(words: Sequence[VocabWord]) -> list[str]:
    errors: list[str] = []
    for w in words:
        errors += entry_errors(w)
    if [w.id for w in words] != list(range(len(words))):
        errors.append("ids must run from 0 without gaps")
    slugs: set[str] = set()
    names: set[str] = set()
    for w in words:
        if w.slug in slugs:
            errors.append(f"{w.slug}: duplicate slug {w.slug}")
        slugs.add(w.slug)
        if w.word.casefold() in names:
            errors.append(f"{w.slug}: duplicate word {w.word}")
        names.add(w.word.casefold())
    for w in words:
        for a in w.alternatives:
            if a not in slugs:
                errors.append(f"{w.slug}: alternative {a} is not a shipped word")
    return errors
