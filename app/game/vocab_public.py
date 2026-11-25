"""Shipped words as their public pages show them (`GET /vocab/entries`): everything but a
room question's prompt, options and reasons."""

from collections.abc import Sequence

from app.game import vocab_words
from app.game.vocab_words import VocabWord
from app.schemas.vocab import PracticeQuestion, VocabEntries, VocabEntry


def _alternatives(w: VocabWord) -> list[str]:
    """`w`'s alternatives that are other shipped words, in order, without repeats."""
    seen: list[str] = []
    for slug in w.alternatives:
        if slug != w.slug and slug in vocab_words.BY_SLUG and slug not in seen:
            seen.append(slug)
    return seen


def public_entries(words: Sequence[VocabWord]) -> VocabEntries:
    return VocabEntries(
        entries=[
            VocabEntry(
                id=w.id,
                slug=w.slug,
                word=w.word,
                pos=w.pos,
                pronunciation=w.pronunciation,
                definition=w.definition,
                difficulty=w.difficulty,
                example=w.example,
                synonyms=list(w.synonyms),
                nuance=w.nuance,
                alternatives=_alternatives(w),
                practice=PracticeQuestion(
                    prompt=w.practice.prompt,
                    options=list(w.practice.options),
                    answer=w.practice.answer,
                    reasons=list(w.practice.reasons),
                ),
            )
            for w in sorted(words, key=lambda w: w.id)
        ]
    )
