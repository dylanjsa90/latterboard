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
    # Named per side rather than "opponent", because this model has no viewer:
    # `crud_match.to_public` builds one object that both players receive.
    inviter_is_bot: bool = False
    invitee_is_bot: bool = False
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


class ActiveMatch(BaseModel):
    """One of the viewer's open matches, for switching between them."""

    id: int
    game: str
    status: Literal["pending_invite", "in_progress"]
    opponent_username: str
    opponent_is_bot: bool = False
    # The viewer's side of the invite: an inviter waits on a pending one, an invitee answers.
    role: Literal["inviter", "invitee"]
    # True when the match is waiting on the viewer: their turn, a race they haven't
    # finished, any co-op board, or an invite they haven't answered.
    your_move: bool
    created_at: datetime
    started_at: datetime | None


class PendingInvite(BaseModel):
    id: int
    game: str
    from_username: str
    created_at: datetime
    expires_at: datetime


# --- history ------------------------------------------------------------------


class MatchHistoryItem(BaseModel):
    id: int
    game: str
    opponent_username: str
    # Solo games stay in the list but are left out of `MatchRecord`.
    opponent_is_bot: bool = False
    # Competitive games are won/lost/draw from the viewer's side; co-op is solved/failed.
    result: Literal["won", "lost", "draw", "solved", "failed"]
    completed_at: datetime


class MatchRecord(BaseModel):
    """The viewer's competitive results over every finished match (co-op excluded)."""

    won: int
    lost: int
    drawn: int


class MatchHistory(BaseModel):
    items: list[MatchHistoryItem]
    total: int
    record: MatchRecord
