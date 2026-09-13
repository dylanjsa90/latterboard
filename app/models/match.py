from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base_class import Base


class Match(Base):
    __tablename__: str = "match"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    inviter_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("user.id"), nullable=False, index=True
    )
    invitee_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("user.id"), nullable=False, index=True
    )
    game: Mapped[str] = mapped_column(String, nullable=False, default="wordle", index=True)
    # pending_invite -> in_progress -> completed | declined | cancelled | expired
    status: Mapped[str] = mapped_column(
        String, nullable=False, default="pending_invite", index=True
    )
    target_word: Mapped[str] = mapped_column(String, nullable=False)
    max_guesses: Mapped[int] = mapped_column(Integer, nullable=False)
    current_turn_user_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("user.id"), nullable=True
    )
    winner_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("user.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    invite_expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


Index("ix_match_invitee_status", Match.invitee_id, Match.status)


class MatchGuess(Base):
    __tablename__: str = "match_guess"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    match_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("match.id"), nullable=False, index=True
    )
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("user.id"), nullable=False)
    turn_number: Mapped[int] = mapped_column(Integer, nullable=False)
    word: Mapped[str] = mapped_column(String, nullable=False)
    result: Mapped[list] = mapped_column(JSON, nullable=False)
    is_correct: Mapped[bool] = mapped_column(Boolean, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


Index("ix_matchguess_match_turn", MatchGuess.match_id, MatchGuess.turn_number)


class MatchPuzzle(Base):
    """The private puzzle and live state behind a race or co-op match.

    A table beside `match` rather than new columns on it because there are no
    migrations: create_all adds a missing table to an existing database, never a
    missing column.
    """

    __tablename__: str = "match_puzzle"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    match_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("match.id"), nullable=False, unique=True, index=True
    )
    # The puzzle with its answer; only sent to clients once the match is over.
    data: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    # Each player's guesses (races), or the shared board and mistake count (co-op).
    state: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    # Optimistic lock, so a move based on stale state fails instead of overwriting
    # the other player's (see CRUDMatchPuzzle._apply).
    version: Mapped[int] = mapped_column(Integer, nullable=False)

    __mapper_args__ = {"version_id_col": version}
