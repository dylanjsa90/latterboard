"""Pure Wordle game logic: word selection and guess evaluation.

No FastAPI/pydantic/SQLAlchemy imports here so this stays trivially unit-testable
and reusable outside the request/response cycle.
"""

import random
from collections import Counter

from app.lib.word_list import word_list

WORD_LENGTH = 5

_WORD_SET = frozenset(w.lower() for w in word_list)


def select_word(rng: random.Random | None = None) -> str:
    chooser = rng or random
    return chooser.choice(word_list).lower()


def is_valid_word(guess: str) -> bool:
    return len(guess) == WORD_LENGTH and guess.lower() in _WORD_SET


def evaluate_guess(target: str, guess: str) -> list[tuple[str, str]]:
    """Classic two-pass Wordle scoring: exact matches first, then present/absent
    from the remaining letter counts, so duplicate letters are handled correctly.
    """
    target = target.lower()
    guess = guess.lower()
    results: list[str | None] = [None] * len(guess)
    remaining = Counter(target)

    for i, letter in enumerate(guess):
        if i < len(target) and letter == target[i]:
            results[i] = "correct"
            remaining[letter] -= 1

    for i, letter in enumerate(guess):
        if results[i] is not None:
            continue
        if remaining.get(letter, 0) > 0:
            results[i] = "present"
            remaining[letter] -= 1
        else:
            results[i] = "absent"

    return list(zip(guess, results))
