from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, LargeBinary, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base_class import Base


class UserAvatar(Base):
    """A player's profile photo, already re-encoded to a 256px WebP.

    Kept out of `user` so loading a user never drags the image bytes along, and so
    `create_all` can add it to existing databases (it never alters existing tables).
    """

    __tablename__: str = "user_avatar"

    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("user.id", ondelete="CASCADE"), primary_key=True
    )
    image: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    content_type: Mapped[str] = mapped_column(String(32), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )
