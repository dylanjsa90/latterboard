"""Pure rules for the puzzle-based match modes that sit alongside the turn-based
"wordle" match:

- word_race / cipher_race: both players get the same private puzzle and play it at
  the same time on their own boards. Whoever solves it in fewer attempts wins.
- word_pick: a word race where each player picks the word the other must guess.
  Guessing opens once both have picked.
- sudoku_coop: both players fill in one shared board against a shared mistake limit.

No FastAPI/pydantic/SQLAlchemy imports here, mirroring app/game/wordle.py.
"""

from typing import Any

from app.game import wordle
from app.game.puzzle_seed_data import generate_cipher_digits, generate_sudoku
from app.game.puzzles import (
    CIPHER_MAX_ATTEMPTS,
    WORD_MAX_ATTEMPTS,
    CipherFeedback,
    cipher_feedback,
)
from app.game.word_list import word_list
from app.schemas.puzzle import Grade

WORD_RACE = "word_race"
CIPHER_RACE = "cipher_race"
SUDOKU_COOP = "sudoku_coop"
WORD_PICK = "word_pick"

RACE_GAMES = frozenset({WORD_RACE, CIPHER_RACE, WORD_PICK})
PUZZLE_MATCH_GAMES = RACE_GAMES | {SUDOKU_COOP}

# Stored in Match.max_guesses: attempts per player in a race, the team's mistake
# limit in co-op.
MAX_GUESSES = {
    WORD_RACE: WORD_MAX_ATTEMPTS,
    WORD_PICK: WORD_MAX_ATTEMPTS,
    CIPHER_RACE: CIPHER_MAX_ATTEMPTS,
    SUDOKU_COOP: 3,
}


def new_puzzle(game: str) -> dict[str, Any]:
    """A fresh private puzzle, answer included, for one match."""
    if game == WORD_RACE or game == "wordle":
        return {"answer": wordle.select_word()}
    if game == CIPHER_RACE:
        return {"digits": generate_cipher_digits()}
    if game == WORD_PICK:
        # Filled in by the players: each player's id maps to the word they must guess.
        return {"answers": {}}
    if game == SUDOKU_COOP:
        return generate_sudoku()
    raise ValueError(f"No puzzle generator for game {game!r}")


def initial_state(
    game: str, player_ids: tuple[int, int], puzzle: dict[str, Any]
) -> dict[str, Any]:
    """The match's starting state. Player ids are strings because JSON keys are."""
    if game == SUDOKU_COOP:
        return {"board": list(puzzle["puzzle"]), "mistakes": 0, "outcome": None}
    return {"guesses": {str(player_id): [] for player_id in player_ids}}


_ANSWER_WORDS = frozenset(word.lower() for word in word_list)
# Sorted, for players searching the words they may pick.
PICKABLE_WORDS = sorted(_ANSWER_WORDS)


def is_pickable_word(word: str) -> bool:
    """Whether a player may pick `word` for their opponent: answer words only, so
    nobody is handed an obscure word they could never be expected to find."""
    return word.lower() in _ANSWER_WORDS


def race_answer(puzzle: dict[str, Any], player_id: int) -> str | list[int] | None:
    """The answer `player_id` is racing towards: the shared one, or in word_pick the
    word their opponent picked (None until they have)."""
    if "answers" in puzzle:
        answer: str | None = puzzle["answers"].get(str(player_id))
        return answer
    shared: str | list[int] = (
        puzzle["answer"] if "answer" in puzzle else puzzle["digits"]
    )
    return shared


def race_ready(puzzle: dict[str, Any]) -> bool:
    """Whether guessing is open: always, except a word_pick race until both words
    have been picked."""
    return "answers" not in puzzle or len(puzzle["answers"]) == 2


def grade_race_guess(
    answer: str | list[int], guess: str | list[int]
) -> tuple[list[Grade] | CipherFeedback, bool]:
    """Feedback for a race guess (word grades or cipher exact/close) against the
    player's answer, and whether it solves it."""
    if isinstance(guess, str):
        assert isinstance(answer, str)
        grades = wordle.evaluate_guess(answer, guess)
        return grades, all(grade == "correct" for grade in grades)
    assert isinstance(answer, list)
    feedback = cipher_feedback(guess, answer)
    return feedback, bool(feedback["won"])


def race_outcome(
    progress: dict[int, tuple[int, bool]], max_attempts: int
) -> tuple[bool, int | None]:
    """Whether a two-player race is decided and, if so, who won (None for a draw).

    `progress` maps each player to (attempts used, solved). A player who solves in n
    attempts wins as soon as the other has used n attempts without solving, since
    the best the other can still do is n + 1. Solving in the same number of attempts,
    or both running out, is a draw.
    """

    def best_possible(attempts: int, solved: bool) -> tuple[int, bool]:
        """The fewest attempts this player can still finish in, and whether that's final."""
        if solved:
            return attempts, True
        if attempts >= max_attempts:
            return max_attempts + 1, True  # out of attempts: worse than any solve
        return attempts + 1, False

    (a, a_progress), (b, b_progress) = progress.items()
    a_best, a_final = best_possible(*a_progress)
    b_best, b_final = best_possible(*b_progress)
    if a_final and b_final:
        return True, a if a_best < b_best else b if b_best < a_best else None
    if a_final and b_best > a_best:
        return True, a
    if b_final and a_best > b_best:
        return True, b
    return False, None


def race_score(attempts: int, max_attempts: int) -> int:
    """Score for solving a race: 100, plus 100 for every attempt left unused."""
    return 100 * (max_attempts - attempts + 1)


def sudoku_coop_score(mistakes: int, max_mistakes: int) -> int:
    """Score each co-op player gets for a solved board: 100 per mistake left unused."""
    return 100 * (max_mistakes - mistakes)
