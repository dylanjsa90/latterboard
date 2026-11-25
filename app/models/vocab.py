from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base_class import Base


class VocabRoom(Base):
    """A Vocab Challenger solo session or duel, addressed by its six-character invite
    code. The whole room (players, answers, word ids) is one JSON document whose rules
    are in app/game/vocab.py."""

    __tablename__: str = "vocab_room"

    code: Mapped[str] = mapped_column(String(6), primary_key=True)
    state: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    # Optimistic lock, so two players answering at once can't overwrite each other
    # (see CRUDVocab.mutate).
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    __mapper_args__ = {"version_id_col": version}


class VocabSavedWord(Base):
    """A word in a player's wordbook, with its spaced-repetition level and due time."""

    __tablename__: str = "vocab_saved_word"
    __table_args__ = (UniqueConstraint("user_id", "word_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("user.id"), nullable=False, index=True
    )
    # Index into app.game.vocab_words.WORDS, not a foreign key.
    word_id: Mapped[int] = mapped_column(Integer, nullable=False)
    level: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    due: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class VocabWordReport(Base):
    """A player's report that a word's entry is wrong or unsuitable. Words ship without a
    person reviewing each one, so these are how mistakes come back. Guests can report
    too; no address is stored (the network is only a rate-limit key)."""

    __tablename__: str = "vocab_word_report"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # Index into app.game.vocab_words.WORDS, not a foreign key.
    word_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    # Null for a guest, or once the reporter's account is deleted.
    user_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("user.id", ondelete="SET NULL"), nullable=True
    )
    reason: Mapped[str] = mapped_column(String(32), nullable=False)
    # Which question was on screen: "usage" or "context"; null if not sent.
    kind: Mapped[str | None] = mapped_column(String(16), nullable=True)
    note: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    # Naive UTC, like every DateTime column here (app.utils.utcnow).
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
