from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.puzzle import SUDOKU_CELLS, CipherFeedback, Grade

multiplayer: Literal["wordle", "word_race", "cipher_race", "sudoku_coop"]

class MatchInviteCreate(BaseModel):
    opponent_username: str
    # "wordle" (turn-based), "word_race", "cipher_race" or "sudoku_coop".
    game: str = "wordle"


class LetterResultOut(BaseModel):
    letter: str
    status: Literal["correct", "present", "absent"]


class MatchGuessCreate(BaseModel):
    word: str = Field(min_length=5, max_length=5)


class MatchPublic(BaseModel):
    id: int
    game: str
    status: str
    inviter_username: str
    invitee_username: str
    current_turn_username: str | None
    winner_username: str | None
    # Attempts per player in a race; the shared mistake limit in sudoku co-op.
    max_guesses: int
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None


class MatchGuessResult(BaseModel):
    match_id: int
    turn_number: int
    username: str
    word: str
    result: list[LetterResultOut]
    correct: bool
    status: str
    current_turn_username: str | None
    winner_username: str | None


# --- races (word_race, cipher_race) ------------------------------------------

# Word grades per letter, or cipher exact/close counts.
RaceResult = list[Grade] | CipherFeedback


class RaceGuessOut(BaseModel):
    # None for the opponent's guesses until the race is over.
    guess: str | list[int] | None
    result: RaceResult
    correct: bool


class RacePlayer(BaseModel):
    username: str
    guesses: list[RaceGuessOut]
    solved: bool
    finished: bool


class RaceDetail(BaseModel):
    you: RacePlayer
    opponent: RacePlayer
    answer: str | list[int] | None


class RaceGuessResult(BaseModel):
    match_id: int
    turn_number: int
    guess: str | list[int]
    result: RaceResult
    correct: bool
    status: str
    winner_username: str | None
    answer: str | list[int] | None


# --- sudoku co-op -----------------------------------------------------------


class SudokuCoopMoveCreate(BaseModel):
    index: int = Field(ge=0, lt=SUDOKU_CELLS)
    value: int = Field(ge=1, le=9)


class SudokuCoopMoveResult(BaseModel):
    match_id: int
    index: int
    value: int
    correct: bool
    mistakes: int
    status: str
    outcome: Literal["solved", "failed"] | None


class SudokuCoopDetail(BaseModel):
    puzzle: list[int]
    # The givens plus every correct placement so far.
    board: list[int]
    mistakes: int
    max_mistakes: int
    outcome: Literal["solved", "failed"] | None
    solution: list[int] | None


class MatchDetail(MatchPublic):
    target_word: str | None
    guesses: list[MatchGuessResult]
    race: RaceDetail | None = None
    sudoku: SudokuCoopDetail | None = None


class PendingInvite(BaseModel):
    id: int
    game: str
    from_username: str
    created_at: datetime
    expires_at: datetime
