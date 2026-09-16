"""Move selection for the computer-controlled opponent.

Pure logic: no FastAPI/pydantic/SQLAlchemy imports, mirroring `app/game/wordle.py`
and `app/game/match_modes.py`. Every entry point takes an optional
`rng: random.Random`, like `wordle.select_word`, so games can be replayed exactly
in tests.

Both games are solved the same way: keep the answers still consistent with every
piece of feedback so far, rank them, then pick one. The interesting part is that
the bot is deliberately *held back* — the cipher has only 360 possible answers
against 8 attempts, and the word list doubles as the candidate set, so an
unhandicapped solver wins almost every time and reads as a machine. `skill` is
what keeps it human: see `_top_slice`.
"""

import random
from collections import Counter
from itertools import permutations

from app.game import wordle
from app.game.puzzles import cipher_feedback
from app.lib.word_list import word_list

# 0.0 = careless, 1.0 = always plays the best candidate it knows.
DEFAULT_SKILL = 0.75

CIPHER_DIGIT_RANGE = 6
CIPHER_LENGTH = 4

# A guess and the feedback it drew back.
WordHistory = list[tuple[str, list[str]]]
CipherHistory = list[tuple[list[int], dict[str, int]]]

_WORDS: tuple[str, ...] = tuple(sorted({w.lower() for w in word_list}))
# The answer is always `random.sample(range(6), 4)`, so only these 360 codes are
# possible and the bot never wastes an attempt on one with a repeated digit.
_CIPHER_CODES: tuple[tuple[int, ...], ...] = tuple(
    permutations(range(CIPHER_DIGIT_RANGE), CIPHER_LENGTH)
)


def _top_slice(count: int, skill: float) -> int:
    """How many of the ranked candidates the bot is willing to pick from.

    `count ** (1 - skill)`, which behaves well at both ends: skill 1.0 always
    takes the single best candidate, skill 0.0 picks uniformly at random, and the
    slice narrows on its own as candidates are eliminated — so the bot gets more
    decisive as a game closes in, the way a person does.
    """
    skill = min(max(skill, 0.0), 1.0)
    if count <= 1:
        return max(count, 0)
    # Annotated because `float ** float` is Any to mypy (it can yield complex).
    width: int = round(float(count) ** (1.0 - skill))
    return max(1, min(count, width))


def _slips(skill: float, rng: random.Random) -> bool:
    """Whether to throw away this guess on a word that can't be the answer.

    People do this — they play a word to test letters, not to win. At the default
    skill it happens about one guess in twenty, and never at skill 1.0.
    """
    return rng.random() < (1.0 - min(max(skill, 0.0), 1.0)) * 0.2


# --- words (wordle, word_race) ------------------------------------------------


def word_candidates(history: WordHistory) -> list[str]:
    """Every word that would have produced exactly this feedback.

    Grades the candidate *as if it were the answer* with the real
    `wordle.evaluate_guess`, rather than reimplementing the rules — which is what
    keeps duplicate-letter handling correct here for free.
    """
    return [
        word
        for word in _WORDS
        if all(wordle.evaluate_guess(word, guess) == grades for guess, grades in history)
    ]


def _rank_words(candidates: list[str]) -> list[str]:
    """Most informative first: how common each letter is *at that position* among
    the words still in play. Repeats are discounted because a second `e` confirms
    much less than a new letter does."""
    columns: list[Counter[str]] = [
        Counter() for _ in range(wordle.WORD_LENGTH)
    ]
    for word in candidates:
        for position, letter in enumerate(word):
            columns[position][letter] += 1

    def score(word: str) -> int:
        seen: set[str] = set()
        total = 0
        for position, letter in enumerate(word):
            value = columns[position][letter]
            if letter in seen:
                value //= 2
            seen.add(letter)
            total += value
        return total

    # The word itself breaks ties, so a given candidate set always ranks the same.
    return sorted(candidates, key=lambda word: (-score(word), word))


def choose_word(
    history: WordHistory,
    *,
    skill: float = DEFAULT_SKILL,
    rng: random.Random | None = None,
) -> str:
    """The bot's next 5-letter guess. Always a word from the list, so it passes
    `wordle.is_valid_word`."""
    chooser = rng or random.Random()
    candidates = word_candidates(history)
    if not candidates:
        # Contradictory feedback should be impossible, but this runs in a
        # background task where raising would strand the match.
        return chooser.choice(_WORDS)

    if history and len(candidates) > 1 and _slips(skill, chooser):
        ruled_out = set(_WORDS) - set(candidates)
        if ruled_out:
            return chooser.choice(sorted(ruled_out))

    ranked = _rank_words(candidates)
    return chooser.choice(ranked[: _top_slice(len(ranked), skill)])


# --- cipher (cipher_race) -----------------------------------------------------


def _cipher_consistent(
    code: tuple[int, ...], attempt: list[int], feedback: dict[str, int]
) -> bool:
    actual = cipher_feedback(attempt, list(code))
    return bool(
        actual["exact"] == feedback["exact"] and actual["close"] == feedback["close"]
    )


def cipher_candidates(history: CipherHistory) -> list[list[int]]:
    """Every code that would have produced exactly this exact/close feedback."""
    return [
        list(code)
        for code in _CIPHER_CODES
        if all(
            _cipher_consistent(code, attempt, feedback) for attempt, feedback in history
        )
    ]


def choose_cipher(
    history: CipherHistory,
    *,
    skill: float = DEFAULT_SKILL,
    rng: random.Random | None = None,
) -> list[int]:
    """The bot's next 4-digit attempt.

    Picks at random among the codes still possible instead of running Knuth's
    minimax. That is the point: minimax cracks all 360 codes in about 4 attempts
    every single time, which no person does. Guessing a consistent code lands
    around 5 of the allowed 8 — and is a fraction of the code.
    """
    chooser = rng or random.Random()
    candidates = cipher_candidates(history)
    if not candidates:
        return list(chooser.choice(_CIPHER_CODES))

    if history and len(candidates) > 1 and _slips(skill, chooser):
        possible = {tuple(code) for code in candidates}
        ruled_out = [list(code) for code in _CIPHER_CODES if code not in possible]
        if ruled_out:
            return chooser.choice(ruled_out)

    return chooser.choice(candidates)
