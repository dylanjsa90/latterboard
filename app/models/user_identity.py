from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base_class import Base


class UserIdentity(Base):
    """An outside account (today only Google) that signs in as a user.

    A table rather than columns on `user`: `create_all` builds a new table with its
    unique constraint, while `init_db.ensure_user_columns` can only add bare nullable
    columns to `user`.
    """

    __tablename__: str = "user_identity"
    __table_args__ = (UniqueConstraint("provider", "subject"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("user.id", ondelete="CASCADE"), index=True, nullable=False
    )
    provider: Mapped[str] = mapped_column(String(16), nullable=False)
    # The provider's stable account ID (Google's `sub`), never the email, which can change.
    subject: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
