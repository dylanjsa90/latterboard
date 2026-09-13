"""Pure logic for the daily puzzle games (word/sudoku/memory/cipher): daily
variant indexing, cipher feedback, and the seeded memory-deck shuffle. No
FastAPI/pydantic/SQLAlchemy imports here so this stays trivially unit-testable,
mirroring app/game/wordle.py. Word grading reuses wordle.evaluate_guess since
both games use the same two-pass scoring algorithm.

Ported from the original puzzle-box Next.js route (app/api/puzzles/route.ts).
"""

import re
from collections import Counter
from datetime import date, datetime, timezone

from app.game import wordle
from app.schemas.puzzle import Grade

WORD_LENGTH = 5
WORD_STARTER = "ADORE"
CIPHER_STARTER = [0, 2, 3, 1]
WORD_MAX_ATTEMPTS = 6
CIPHER_MAX_ATTEMPTS = 8


def today() -> date:
    return datetime.now(timezone.utc).date()


def puzzle_date(puzzle_id: str | None, prefix: str) -> date:
    """The calendar date a puzzle_id refers to, falling back to today's date
    (UTC) when puzzle_id is missing or doesn't carry a recognizable date
    (e.g. an old index-based id)."""
    fallback = today()
    if not puzzle_id:
        return fallback
    match = re.match(rf"^{re.escape(prefix)}-(\d{{4}}-\d{{2}}-\d{{2}})$", puzzle_id)
    if not match:
        return fallback
    try:
        return date.fromisoformat(match.group(1))
    except ValueError:
        return fallback


def grade_word(guess: str, answer: str) -> list[Grade]:
    return wordle.evaluate_guess(answer, guess)


def cipher_feedback(attempt: list[int], answer: list[int]) -> dict:
    exact = 0
    available: Counter[int] = Counter()
    for value, target in zip(attempt, answer, strict=True):
        if value == target:
            exact += 1
        else:
            available[target] += 1
    close = 0
    for value, target in zip(attempt, answer, strict=True):
        if value != target and available[value] > 0:
            close += 1
            available[value] -= 1
    return {"exact": exact, "close": close, "won": exact == len(answer)}


def seeded_memory_deck(puzzle_id: str, symbols: list[str]) -> list[str]:
    """FNV-1a seed derived from puzzle_id, shuffled with a 32-bit LCG Fisher-Yates.
    Bit-for-bit port of the original TS implementation so the same puzzle_id
    always yields the same deck.
    """
    seed = 2166136261
    for char in puzzle_id:
        seed ^= ord(char)
        seed = (seed * 16777619) & 0xFFFFFFFF
    deck = list(symbols) + list(symbols)
    for index in range(len(deck) - 1, 0, -1):
        seed = ((seed * 1664525) + 1013904223) & 0xFFFFFFFF
        swap = seed % (index + 1)
        deck[index], deck[swap] = deck[swap], deck[index]
    return deck
