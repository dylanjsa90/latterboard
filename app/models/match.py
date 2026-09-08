from datetime import datetime

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
