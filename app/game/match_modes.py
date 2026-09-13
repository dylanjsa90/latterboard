"""Pure rules for the puzzle-based match modes that sit alongside the turn-based
"wordle" match:

- word_race / cipher_race: both players get the same private puzzle and play it at
  the same time on their own boards. Whoever solves it in fewer attempts wins.
- sudoku_coop: both players fill in one shared board against a shared mistake limit.

No FastAPI/pydantic/SQLAlchemy imports here, mirroring app/game/wordle.py.
"""

from typing import Any

from app.game import wordle
from app.game.puzzle_seed_data import generate_cipher_digits, generate_sudoku
from app.game.puzzles import CIPHER_MAX_ATTEMPTS, WORD_MAX_ATTEMPTS, cipher_feedback
from app.schemas.puzzle import Grade

WORD_RACE = "word_race"
CIPHER_RACE = "cipher_race"
SUDOKU_COOP = "sudoku_coop"

RACE_GAMES = frozenset({WORD_RACE, CIPHER_RACE})
PUZZLE_MATCH_GAMES = RACE_GAMES | {SUDOKU_COOP}

# Stored in Match.max_guesses: attempts per player in a race, the team's mistake
# limit in co-op.
MAX_GUESSES = {
    WORD_RACE: WORD_MAX_ATTEMPTS,
    CIPHER_RACE: CIPHER_MAX_ATTEMPTS,
    SUDOKU_COOP: 3,
}


def new_puzzle(game: str) -> dict[str, Any]:
    """A fresh private puzzle, answer included, for one match."""
    if game == WORD_RACE or game == "wordle":
        return {"answer": wordle.select_word()}
    if game == CIPHER_RACE:
        return {"digits": generate_cipher_digits()}
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


def grade_race_guess(
    puzzle: dict[str, Any], guess: str | list[int]
) -> tuple[list[Grade] | dict[str, Any], bool]:
    """Feedback for a race guess (word grades or cipher exact/close) and whether it
    solves the puzzle."""
    if isinstance(guess, str):
        grades = wordle.evaluate_guess(puzzle["answer"], guess)
        return grades, all(grade == "correct" for grade in grades)
    feedback = cipher_feedback(guess, puzzle["digits"])
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
