from datetime import datetime
from typing import Literal, Self

from pydantic import BaseModel, EmailStr, Field, model_validator

INVITE_MESSAGE_MAX = 280

InviteChannel = Literal["email", "text", "link"]


class InviteLinkCreate(BaseModel):
    game: str
    channel: InviteChannel
    message: str | None = Field(default=None, max_length=INVITE_MESSAGE_MAX)
    # Required for "email", where latterboard sends the invite; unused otherwise.
    email: EmailStr | None = None

    @model_validator(mode="after")
    def email_matches_channel(self) -> Self:
        if self.channel == "email" and self.email is None:
            raise ValueError("An email invite needs an email address")
        if self.channel != "email" and self.email is not None:
            raise ValueError("Only email invites take an email address")
        if self.message is not None:
            self.message = self.message.strip() or None
        return self


class InviteLinkPublic(BaseModel):
    """A link as its creator sees it."""

    token: str
    url: str
    game: str
    message: str | None
    channel: InviteChannel
    recipient_email: str | None
    created_at: datetime
    expires_at: datetime
    # False when the server has no SMTP settings, so the player must share it themselves.
    emailed: bool = False


InviteLinkStatus = Literal["open", "claimed", "expired", "revoked"]


class InviteLinkPreview(BaseModel):
    """What anyone holding the link sees before signing in."""

    inviter_username: str
    game: str
    message: str | None
    expires_at: datetime
    status: InviteLinkStatus
