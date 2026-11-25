"""Vocab Challenger I/O. Times are epoch milliseconds (see app/game/vocab.py)."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

Difficulty = Literal["Foundation", "Intermediate", "Advanced"]


class RoomCreate(BaseModel):
    mode: Literal["solo", "duel"]


class RoomAction(BaseModel):
    action: Literal["ready", "answer", "next", "rematch"]
    # The rematch generation the client is looking at; an answer for an older one is stale.
    generation: int
    round: int | None = None
    choice: int | None = None


class ReviewCreate(BaseModel):
    known: bool


class Question(BaseModel):
    """A round's question. Before it resolves, `correct` through `nuance` are null, and
    so are `id`, `word`, `pos` and `definition` for a context question (the word is the
    answer)."""

    id: int | None
    word: str | None
    pos: str | None
    definition: str | None
    difficulty: Difficulty
    kind: Literal["usage", "context"]
    prompt: str
    options: list[str]
    correct: int | None = None
    reasons: list[str] | None = None
    example: str | None = None
    synonyms: str | None = None
    nuance: str | None = None


class RevealedQuestion(Question):
    id: int
    word: str
    pos: str
    definition: str
    correct: int
    reasons: list[str]
    example: str
    synonyms: str
    nuance: str


class Seat(BaseModel):
    id: Literal["me", "opponent"]
    name: str
    ready: bool
    # When the player last polled the room.
    seen: int
    answered: bool
    score: int


class RoundAnswer(BaseModel):
    name: str
    # Null when the player didn't answer that round.
    choice: int | None = None
    points: int | None = None
    correct: bool | None = None
    ms: int | None = None


class HistoryRound(BaseModel):
    question: RevealedQuestion
    answers: list[RoundAnswer]


class GameView(BaseModel):
    code: str
    mode: Literal["solo", "duel"]
    phase: Literal["lobby", "question", "feedback", "results"]
    # Zero-based.
    round: int
    generation: int
    deadline: int
    # Orders replies: a client ignores a snapshot older than the one it has.
    revision: int
    rematch_requested: bool
    players: list[Seat]
    question: Question
    # The viewer's choice this round, if they've answered.
    mine: int | None
    history: list[HistoryRound]


class RoomSnapshot(BaseModel):
    game: GameView
    server_now: int


class GuestRoomSnapshot(RoomSnapshot):
    """A new guest room and the token that opens it, returned once."""

    guest_token: str


class SavedWord(BaseModel):
    id: int
    word: str
    pos: str
    definition: str
    difficulty: Difficulty
    example: str
    synonyms: str
    nuance: str
    due: int
    # 0 (just missed or just saved) to 6.
    level: int


class Wordbook(BaseModel):
    saved: list[SavedWord]
    word_count: int


class DailyWord(BaseModel):
    """The word of the day: only what the home card shows, never a question's answer."""

    id: int
    # The Pacific date it's the word for, YYYY-MM-DD.
    date: str
    slug: str
    word: str
    pronunciation: str
    pos: str
    definition: str
    example: str


class DailyWordDay(BaseModel):
    # A Pacific date, YYYY-MM-DD.
    date: str
    word_id: int
    slug: str


class DailyWordHistory(BaseModel):
    """Every archived day through today, oldest first; never a later day."""

    days: list[DailyWordDay]


class PracticeQuestion(BaseModel):
    """A word page's own usage question, never one a room asks."""

    prompt: str
    options: list[str]
    answer: int
    reasons: list[str]


class VocabEntry(BaseModel):
    """A shipped word as its public page shows it: no room question's text."""

    id: int
    slug: str
    word: str
    pos: str
    pronunciation: str
    definition: str
    difficulty: Difficulty
    example: str
    synonyms: list[str]
    nuance: str
    # Slugs of other shipped words.
    alternatives: list[str]
    practice: PracticeQuestion


class VocabEntries(BaseModel):
    entries: list[VocabEntry]


ReportReason = Literal[
    "wrong_definition", "wrong_answer", "confusing", "inappropriate", "other"
]


class WordReportCreate(BaseModel):
    """A player's report of a problem with a word's entry."""

    reason: ReportReason
    # Which question was on screen when they reported it.
    kind: Literal["usage", "context"] | None = None
    note: str = Field(default="", max_length=500)


class WordReport(BaseModel):
    """A stored report, as the owner reads it (superuser only)."""

    id: int
    word_id: int
    word: str
    reason: str
    kind: str | None
    note: str
    # Null for a guest.
    user_id: int | None
    # Naive UTC.
    created_at: datetime
