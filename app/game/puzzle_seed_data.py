"""Raw content and generators for the daily puzzle games, originally ported
from the puzzle-box Next.js API route (app/api/puzzles/route.ts). Consumed by
CRUDPuzzle to populate the `puzzle` table; nothing here talks to the database
directly.
"""

import random

from app.lib.word_list import word_list


def shuffled_words() -> list[str]:
    """A fresh random ordering of the word list (leaves the shared list untouched)."""
    return random.sample(word_list, len(word_list))


def choose_word() -> str:
    return random.choice(word_list)


CIPHER_DIGIT_RANGE = 6
CIPHER_LENGTH = 4


def generate_cipher_digits() -> list[int]:
    return random.sample(range(CIPHER_DIGIT_RANGE), CIPHER_LENGTH)

_SUDOKU_BASE_PUZZLE = [
    5, 3, 0, 0, 7, 0, 0, 0, 0, 6, 0, 0, 1, 9, 5, 0, 0, 0, 0, 9, 8, 0, 0, 0, 0, 6, 0,
    8, 0, 0, 0, 6, 0, 0, 0, 3, 4, 0, 0, 8, 0, 3, 0, 0, 1, 7, 0, 0, 0, 2, 0, 0, 0, 6,
    0, 6, 0, 0, 0, 0, 2, 8, 0, 0, 0, 0, 4, 1, 9, 0, 0, 5, 0, 0, 0, 0, 8, 0, 0, 7, 9,
]
_SUDOKU_BASE_SOLUTION = [
    5, 3, 4, 6, 7, 8, 9, 1, 2, 6, 7, 2, 1, 9, 5, 3, 4, 8, 1, 9, 8, 3, 4, 2, 5, 6, 7,
    8, 5, 9, 7, 6, 1, 4, 2, 3, 4, 2, 6, 8, 5, 3, 7, 9, 1, 7, 1, 3, 9, 2, 4, 8, 5, 6,
    9, 6, 1, 5, 3, 7, 2, 8, 4, 2, 8, 7, 4, 1, 9, 6, 3, 5, 3, 4, 5, 2, 8, 6, 1, 7, 9,
]


def sudoku_variant(offset: int) -> dict:
    """Relabel the base puzzle's digits by `offset` (mod 9, so any offset is
    valid). Only 9 distinct boards exist this way, so offsets 9 apart are
    identical - fine for reuse across dates once the curated bank runs out.
    """

    def transform(value: int) -> int:
        return ((value + offset - 1) % 9) + 1 if value else 0

    return {
        "puzzle": [transform(v) for v in _SUDOKU_BASE_PUZZLE],
        "solution": [transform(v) for v in _SUDOKU_BASE_SOLUTION],
    }


# One full, pre-solved variant per offset 0-8 (same relabeling trick the
# original route computed on every request) so a Puzzle row is always a
# complete, ready-to-serve definition.
SUDOKU_VARIANTS = [sudoku_variant(offset) for offset in range(9)]

CIPHERS = [
    [2, 4, 0, 5],
    [5, 1, 3, 0],
    [1, 4, 2, 3],
    [3, 0, 5, 2],
    [4, 2, 1, 5],
]

MEMORY_SYMBOLS = ["☀", "✦", "☂", "♬", "☕", "⌁"]
