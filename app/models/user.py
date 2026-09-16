import logging
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.base_class import Base

if TYPE_CHECKING:
    from app.models.user_avatar import UserAvatar


class User(Base):
    __tablename__: str = "user"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    email: Mapped[str] = mapped_column(String, unique=True, index=True, nullable=False)
    username: Mapped[str] = mapped_column(
        String, unique=True, index=True, nullable=False
    )
    hashed_password: Mapped[str] = mapped_column(String, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )
    is_superuser: Mapped[bool] = mapped_column(Boolean, default=False)

    # Profile fields. Nullable because accounts made before them have none, and
    # `init_db.ensure_user_columns` adds them to existing databases.
    display_name: Mapped[str | None] = mapped_column(String(40), nullable=True)
    # Private: only the player themselves ever sees it (`UserPrivate`).
    birth_year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    location: Mapped[str | None] = mapped_column(String(60), nullable=True)
    # Hash of the current photo, used to version its URL; null when there's no photo.
    avatar_version: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # True for the computer-controlled opponent. Nullable so `ensure_user_columns`
    # can add it to existing databases, so null means "a human" — always read it
    # as `bool(user.is_bot)`, never `is True`.
    is_bot: Mapped[bool | None] = mapped_column(Boolean, nullable=True, index=True)

    avatar: Mapped["UserAvatar | None"] = relationship(
        cascade="all, delete-orphan", uselist=False
    )
