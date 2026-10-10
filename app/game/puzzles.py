"""Pure logic for the daily puzzle games (word/sudoku/memory/cipher): daily
variant indexing, cipher feedback, and the seeded memory-deck shuffle. Also the
Pacific calendar day that every daily feature (puzzles, play caps, leaderboards,
streaks, invite limits) rolls over on. No
FastAPI/pydantic/SQLAlchemy imports here so this stays trivially unit-testable,
mirroring app/game/wordle.py. Word grading reuses wordle.evaluate_guess since
both games use the same two-pass scoring algorithm.

Ported from the original puzzle-box Next.js route (app/api/puzzles/route.ts).
"""

import re
from collections import Counter
from datetime import date, datetime, time, timezone
from typing import Literal, TypedDict
from zoneinfo import ZoneInfo

from app.game import wordle

WORD_LENGTH = 5
WORD_STARTER = "ADORE"
CIPHER_STARTER = [0, 2, 3, 1]
WORD_MAX_ATTEMPTS = 6
CIPHER_MAX_ATTEMPTS = 8


PACIFIC = ZoneInfo("America/Los_Angeles")


def today() -> date:
    """Today's date in Los Angeles."""
    return datetime.now(PACIFIC).date()


def day_start(day: date) -> datetime:
    """When `day` begins in Los Angeles, as naive UTC to compare with DateTime columns.

    A day runs from its start up to the next day's: 23 hours when clocks spring
    forward, 25 when they fall back.
    """
    start = datetime.combine(day, time(), tzinfo=PACIFIC)
    return start.astimezone(timezone.utc).replace(tzinfo=None)


def day_of(moment: datetime) -> date:
    """The Los Angeles date of a naive UTC timestamp."""
    return moment.replace(tzinfo=timezone.utc).astimezone(PACIFIC).date()


def dated_puzzle(puzzle_id: str | None, prefix: str) -> date | None:
    """The calendar date in a `{prefix}-YYYY-MM-DD` puzzle_id, or None when it has none."""
    if not puzzle_id:
        return None
    match = re.match(rf"^{re.escape(prefix)}-(\d{{4}}-\d{{2}}-\d{{2}})$", puzzle_id)
    if not match:
        return None
    try:
        return date.fromisoformat(match.group(1))
    except ValueError:
        return None


def puzzle_date(puzzle_id: str | None, prefix: str) -> date:
    """The calendar date a puzzle_id refers to, falling back to today's date
    (Pacific) when puzzle_id is missing or doesn't carry a recognizable date
    (e.g. an old index-based id)."""
    return dated_puzzle(puzzle_id, prefix) or today()


def grade_word(guess: str, answer: str) -> list[Literal["correct", "present", "absent"]]:
    return wordle.evaluate_guess(answer, guess)


class CipherFeedback(TypedDict):
    exact: int
    close: int
    won: bool


def cipher_feedback(attempt: list[int], answer: list[int]) -> CipherFeedback:
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


def encode_cipher(attempt: list[int]) -> str:
    """A cipher attempt as the digit string stored in PuzzleAttempt.guesses."""
    return "".join(str(value) for value in attempt)


def decode_cipher(code: str) -> list[int]:
    return [int(char) for char in code]


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
