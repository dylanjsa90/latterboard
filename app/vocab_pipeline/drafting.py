"""Pure drafting rules: how many words of each difficulty to ask for, which proposals to
keep, and how a model's output becomes a shipped entry's JSON. No I/O."""

import hashlib
from collections.abc import Iterable, Mapping
from fractions import Fraction
from typing import Any, cast

from app.game.vocab_words import DIFFICULTIES, POS, Difficulty, slugify
from app.vocab_pipeline.store import Draft

TARGET_MIX: Mapping[Difficulty, float] = {
    "Foundation": 0.3,
    "Intermediate": 0.4,
    "Advanced": 0.3,
}


def allocate(
    counts: Mapping[Difficulty, int],
    n: int,
    mix: Mapping[Difficulty, float] = TARGET_MIX,
) -> dict[Difficulty, int]:
    """Split `n` new words across difficulties so the list moves toward `mix`: each
    difficulty's shortfall from its target, scaled to sum to `n`, largest remainders
    rounding up (ties in DIFFICULTIES order)."""
    total = sum(counts.values()) + n
    # Fractions keep 0.3 * 146 from flooring a whole number down by a hair.
    deficit = {
        d: max(Fraction(0), Fraction(str(mix[d])) * total - counts[d])
        for d in DIFFICULTIES
    }
    owed = sum(deficit.values())
    if owed == 0:
        return dict.fromkeys(DIFFICULTIES, 0)
    shares = {d: deficit[d] * n / owed for d in DIFFICULTIES}
    out = {d: int(shares[d]) for d in DIFFICULTIES}
    by_remainder = sorted(
        DIFFICULTIES, key=lambda d: (-(shares[d] - out[d]), DIFFICULTIES.index(d))
    )
    for d in by_remainder[: n - sum(out.values())]:
        out[d] += 1
    return out


def select_candidates(
    proposed: Iterable[Mapping[str, str]],
    quota: Mapping[Difficulty, int],
    known: set[str],
) -> list[Draft]:
    """Queued drafts for the proposed words that are new, have a part of speech the game
    supports, and fit their difficulty's remaining quota."""
    left = dict(quota)
    seen = set(known)
    drafts: list[Draft] = []
    for p in proposed:
        slug = slugify(p["word"])
        # An unknown difficulty has no quota, so .get skips it.
        difficulty = cast(Difficulty, p["difficulty"])
        if slug in seen or p["pos"] not in POS or left.get(difficulty, 0) <= 0:
            continue
        seen.add(slug)
        left[difficulty] -= 1
        drafts.append(
            {
                "slug": slug,
                "word": p["word"].strip(),
                "pos": p["pos"],
                "difficulty": difficulty,
                "source": "generated",
                "status": "queued",
                "attempts": 0,
                "batch_id": None,
                "errors": [],
                "note": "",
                "entry": None,
            }
        )
    return drafts


def practice_slot(slug: str) -> int:
    """Where the correct practice option sits, so it isn't always A."""
    return int(hashlib.sha256(slug.encode()).hexdigest(), 16) % 4


def _strip(value: Any) -> Any:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        return [_strip(v) for v in value]
    if isinstance(value, dict):
        return {k: _strip(v) for k, v in value.items()}
    return value


def _practice(slug: str, question: Mapping[str, Any]) -> dict[str, Any]:
    """The practice question with its correct option (first in the output) rotated to
    practice_slot(slug), its reason moving with it."""
    options = question["options"]
    slot = practice_slot(slug)
    n = len(options)
    rotated = [options[(i - slot) % n] for i in range(n)]
    return {
        "prompt": question["prompt"],
        "options": [o["sentence"] for o in rotated],
        "answer": slot,
        "reasons": [o["reason"] for o in rotated],
    }


def entry_from_output(d: Draft, output: Mapping[str, Any]) -> dict[str, Any]:
    """A shipped entry without its id, from a DRAFT_SCHEMA output. Raises ValueError if
    the model drafted another word; other problems are left for validation to report."""
    out = _strip(output)
    slug = d["slug"]
    if slugify(out["word"]) != slug:
        raise ValueError(f"model wrote {out['word']}, not {slug}")
    room = out["room"]["options"]
    return {
        "slug": slug,
        "word": out["word"],
        "pos": out["pos"],
        "pronunciation": out["pronunciation"],
        "definition": out["definition"],
        "difficulty": out["difficulty"],
        "example": out["example"],
        "synonyms": out["synonyms"],
        "nuance": out["nuance"],
        "alternatives": list(dict.fromkeys(slugify(a) for a in out["alternatives"])),
        "prompt": out["room"]["prompt"],
        "options": [o["sentence"] for o in room],
        "reasons": [o["reason"] for o in room],
        "practice": _practice(slug, out["practice"]),
    }


def backfill_entry(
    current: Mapping[str, Any], output: Mapping[str, Any]
) -> dict[str, Any]:
    """`current` with the practice question from a BACKFILL_SCHEMA output, plus its
    fourth room option when it has only three. Nothing else changes."""
    out = _strip(output)
    built = {**current, "practice": _practice(current["slug"], out["practice"])}
    if len(current["options"]) == 3:
        extra = out["extra_option"]
        if extra is None:
            raise ValueError("needs a fourth room option")
        built["options"] = [*current["options"], extra["sentence"]]
        built["reasons"] = [*current["reasons"], extra["reason"]]
    return built
