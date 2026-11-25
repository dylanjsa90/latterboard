from datetime import date as date_
from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base_class import Base


class Puzzle(Base):
    __tablename__: str = "puzzle"
    __table_args__ = (UniqueConstraint("game", "date", name="uq_puzzle_game_date"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    game: Mapped[str] = mapped_column(String, nullable=False, index=True)
    variant_index: Mapped[int] = mapped_column(Integer, nullable=False)
    date: Mapped[date_] = mapped_column(Date, nullable=False, index=True)
    data: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)


class PuzzleAttempt(Base):
    __tablename__: str = "puzzle_attempt"
    # One row per player per puzzle, which also indexes lookups by the pair.
    __table_args__ = (UniqueConstraint("user_id", "puzzle_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("user.id"), nullable=False, index=True
    )
    game: Mapped[str] = mapped_column(String, nullable=False, index=True)
    puzzle_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    won: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    completed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # Moves in play order, excluding cipher's fixed starter: word guesses, cipher attempts as
    # digit strings (app.game.puzzles.encode_cipher), or the sudoku cells filled, as
    # indices ("40"); the server rebuilds the board from them.
    guesses: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
