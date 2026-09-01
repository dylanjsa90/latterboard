from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class MatchInviteCreate(BaseModel):
    opponent_username: str
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


class MatchDetail(MatchPublic):
    target_word: str | None
    guesses: list[MatchGuessResult]


class PendingInvite(BaseModel):
    id: int
    game: str
    from_username: str
    created_at: datetime
    expires_at: datetime
