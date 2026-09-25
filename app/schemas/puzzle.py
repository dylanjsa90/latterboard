from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

Grade = Literal["correct", "present", "absent"]


class PuzzleCreate(BaseModel):
    game: str
    variant_index: int
    date: date
    data: dict[str, Any]


class PuzzleUpdate(BaseModel):
    data: dict[str, Any] | None = None


class PuzzleAttemptCreate(BaseModel):
    user_id: int
    game: str
    puzzle_id: str


class PuzzleAttemptUpdate(BaseModel):
    attempt_count: int | None = None
    won: bool | None = None
    completed: bool | None = None


class RecentPuzzleAttempt(BaseModel):
    game: str
    solved_on: date
    solved_at: datetime
    result: int


class GamePuzzleStats(BaseModel):
    game: str
    played: int
    won: int


class PuzzleStats(BaseModel):
    total_solved: int
    today_solved: int
    streak: int
    best_streak: int
    games: list[GamePuzzleStats]
    recent: list[RecentPuzzleAttempt]


class PuzzleHistoryItem(BaseModel):
    game: str
    puzzle_id: str
    won: bool
    attempt_count: int
    completed_at: datetime


class PuzzleHistory(BaseModel):
    items: list[PuzzleHistoryItem]
    total: int


# --- word ---------------------------------------------------------------


class WordGuessPublic(BaseModel):
    guess: str
    grades: list[Grade]


class WordPuzzlePublic(BaseModel):
    puzzle_id: str
    word_length: int
    max_attempts: int
    initial_guess: str
    initial_grade: list[Grade]
    # The signed-in player's progress on this puzzle; empty for anonymous visitors.
    guesses: list[WordGuessPublic] = Field(default_factory=list)
    won: bool = False
    lost: bool = False
    answer: str | None = None


class WordHintRequest(BaseModel):
    puzzle_id: str


class WordHint(BaseModel):
    letter: str


class WordGuessCreate(BaseModel):
    puzzle_id: str
    guess: str = Field(min_length=5, max_length=5)
    attempt_count: int = Field(ge=0)

    @field_validator("guess")
    @classmethod
    def guess_is_alpha(cls, value: str) -> str:
        if not value.isalpha():
            raise ValueError("guess must contain only letters")
        return value.upper()


class WordGuessResult(BaseModel):
    grades: list[Grade]
    won: bool
    lost: bool
    answer: str | None = None


# --- sudoku ---------------------------------------------------------------

SUDOKU_CELLS = 81


class SudokuPuzzlePublic(BaseModel):
    puzzle_id: str
    puzzle: list[int] = Field(min_length=SUDOKU_CELLS, max_length=SUDOKU_CELLS)


class SudokuMoveCreate(BaseModel):
    puzzle_id: str
    index: int = Field(ge=0, lt=SUDOKU_CELLS)
    value: int = Field(ge=1, le=9)
    board: list[int] = Field(min_length=SUDOKU_CELLS, max_length=SUDOKU_CELLS)


class SudokuMoveResult(BaseModel):
    correct: bool
    completed: bool


class SudokuHintCreate(BaseModel):
    puzzle_id: str
    index: int = Field(ge=0, lt=SUDOKU_CELLS)
    board: list[int] = Field(min_length=SUDOKU_CELLS, max_length=SUDOKU_CELLS)


class SudokuHintResult(BaseModel):
    value: int
    completed: bool


# --- memory ---------------------------------------------------------------


class MemoryPuzzlePublic(BaseModel):
    puzzle_id: str
    size: int


class MemoryRevealCreate(BaseModel):
    puzzle_id: str
    index: int = Field(ge=0)


class MemoryRevealResult(BaseModel):
    symbol: str


# --- cipher ---------------------------------------------------------------


class CipherFeedback(BaseModel):
    exact: int
    close: int
    won: bool


class CipherPuzzlePublic(BaseModel):
    puzzle_id: str
    slots: int
    max_attempts: int
    initial_attempt: list[int]
    initial_feedback: CipherFeedback


class CipherAttempt(BaseModel):
    attempt: list[int] = Field(min_length=4, max_length=4)

    @field_validator("attempt")
    @classmethod
    def attempt_digits_in_range(cls, value: list[int]) -> list[int]:
        if any(v < 0 or v > 5 for v in value):
            raise ValueError("attempt values must be between 0 and 5")
        return value


class CipherAttemptCreate(CipherAttempt):
    puzzle_id: str
