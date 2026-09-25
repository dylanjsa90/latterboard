import re
from datetime import datetime, timezone
from typing import Annotated, Literal

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    EmailStr,
    Field,
    computed_field,
)

from app.core.config import settings

# `User.is_bot` is nullable, so accounts predating the column read back as None.
# Null means human, and the wire only ever carries a real boolean.
BotFlag = Annotated[bool, BeforeValidator(lambda value: bool(value))]

USERNAME_PATTERN = re.compile(r"[a-z0-9_]{3,20}")
DISPLAY_NAME_MAX = 40
LOCATION_MAX = 60
MIN_AGE = 13


def _handle(value: str) -> str:
    # Lowercased so "Maya" can't pass for "maya".
    value = value.strip().lower()
    if not USERNAME_PATTERN.fullmatch(value):
        raise ValueError("Usernames are 3–20 letters, numbers, or underscores.")
    return value


def _display_name(value: str) -> str:
    value = " ".join(value.split())
    if not value:
        raise ValueError("Enter your name.")
    if len(value) > DISPLAY_NAME_MAX:
        raise ValueError(f"Names can be up to {DISPLAY_NAME_MAX} characters.")
    return value


def _location(value: str | None) -> str | None:
    if value is None:
        return None
    value = " ".join(value.split())
    if len(value) > LOCATION_MAX:
        raise ValueError(f"Locations can be up to {LOCATION_MAX} characters.")
    return value or None


def _birth_year(value: int) -> int:
    this_year = datetime.now(timezone.utc).year
    if not 1900 <= value <= this_year:
        raise ValueError("Enter the year you were born.")
    if this_year - value < MIN_AGE:
        raise ValueError(f"You must be {MIN_AGE} or older to create an account.")
    return value


Handle = Annotated[str, AfterValidator(_handle)]
DisplayName = Annotated[str, AfterValidator(_display_name)]
Location = Annotated[str | None, AfterValidator(_location)]
BirthYear = Annotated[int, AfterValidator(_birth_year)]


def avatar_url(user_id: int, version: str | None) -> str | None:
    """The photo's URL, versioned so it can be cached forever; None without a photo."""
    if not version:
        return None
    return f"{settings.API_V1_STR}/users/{user_id}/avatar?v={version}"


class UserBase(BaseModel):
    email: EmailStr
    # Unvalidated on the way out: accounts made before handles have their email here.
    username: str


class UserCreate(UserBase):
    username: Handle
    password: str
    display_name: DisplayName
    birth_year: BirthYear
    location: Location = None


class ProfileCreate(BaseModel):
    """The profile every new account starts with, however the player signs in."""

    username: Handle
    display_name: DisplayName
    birth_year: BirthYear
    location: Location = None


class GoogleCredential(BaseModel):
    # The ID token Google's sign-in button handed the browser.
    credential: str


class GoogleSignUp(ProfileCreate, GoogleCredential):
    """A new Google player's profile, sent with the same credential again.

    Google's ID tokens last an hour, long enough to fill in the profile, so the
    account isn't made (and no token of ours issued) until the profile is valid.
    """


class GoogleLogin(BaseModel):
    """`signed_in` carries a token; `needs_profile` means sign up with `/users/google`."""

    status: Literal["signed_in", "needs_profile"]
    access_token: str | None = None
    token_type: str | None = None
    # Suggestions for the profile step when `needs_profile`.
    email: str | None = None
    name: str | None = None


class UserUpdate(BaseModel):
    email: EmailStr | None = None
    username: Handle | None = None
    password: str | None = None
    is_active: bool | None = None


class ProfileUpdate(BaseModel):
    """What a player may change about themselves from the edit-profile page."""

    display_name: DisplayName | None = None
    username: Handle | None = None
    birth_year: BirthYear | None = None
    # Sending "" or null clears it.
    location: Location = None


class AvatarFields(BaseModel):
    """The avatar column plus the versioned URL derived from it.

    Subclasses redeclare `id` when they need it serialized differently.
    """

    id: int
    avatar_version: str | None = Field(default=None, exclude=True)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def avatar_url(self) -> str | None:
        return avatar_url(self.id, self.avatar_version)


class UserPublic(UserBase, AvatarFields):
    is_active: bool
    is_bot: BotFlag = False
    display_name: str | None = None
    location: str | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class UserPrivate(UserPublic):
    """The signed-in player's own account, including what nobody else sees."""

    birth_year: int | None = None


class PlayerPublic(AvatarFields):
    """How other players see someone: no email, no birth year."""

    id: int = Field(exclude=True)
    username: str
    is_bot: BotFlag = False
    display_name: str | None = None
    location: str | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


# JSON payload containing access token
class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


# Contents of JWT token
class TokenPayload(BaseModel):
    sub: str | None = None


class NewPassword(BaseModel):
    token: str
    new_password: str = Field(min_length=8, max_length=128)


class Message(BaseModel):
    message: str
