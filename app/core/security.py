import hashlib
import hmac
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
from jwt import encode
from pwdlib import PasswordHash
from pwdlib.hashers.argon2 import Argon2Hasher
from pwdlib.hashers.bcrypt import BcryptHasher

from app.core.config import settings

password_hash = PasswordHash(
    (
        Argon2Hasher(),
        BcryptHasher(),
    )
)


ALGORITHM = "HS256"

# The `purpose` claim keeps one kind of token from being used as another.
ACCESS_PURPOSE = "access"
PASSWORD_RESET_PURPOSE = "password_reset"


def create_access_token(subject: str | Any, expires_delta: timedelta) -> str:
    expire = datetime.now(timezone.utc) + expires_delta
    to_encode = {"exp": expire, "sub": str(subject), "purpose": ACCESS_PURPOSE}
    encoded_jwt = encode(to_encode, settings.SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt


def verify_password(
    plain_password: str, hashed_password: str
) -> tuple[bool, str | None]:
    return password_hash.verify_and_update(plain_password, hashed_password)


def get_password_hash(password: str) -> str:
    return password_hash.hash(password)


def password_fingerprint(hashed_password: str) -> str:
    """Ties a reset token to the password it was issued against.

    Any change of password (including the reset itself) changes the hash, so the
    token stops working. It's an HMAC, so the token doesn't carry anything derived
    from the hash that could be checked offline.
    """
    key = settings.SECRET_KEY.encode()
    return hmac.new(key, hashed_password.encode(), hashlib.sha256).hexdigest()


def generate_password_reset_token(email: str, hashed_password: str) -> str:
    delta = timedelta(hours=settings.EMAIL_RESET_TOKEN_EXPIRE_HOURS)
    now = datetime.now(timezone.utc)
    expires = now + delta
    exp = expires.timestamp()
    encoded_jwt = jwt.encode(
        {
            "exp": exp,
            "nbf": now,
            "sub": email,
            "purpose": PASSWORD_RESET_PURPOSE,
            "pwd": password_fingerprint(hashed_password),
        },
        settings.SECRET_KEY,
        algorithm=ALGORITHM,
    )
    return encoded_jwt


def verify_password_reset_token(token: str) -> tuple[str, str] | None:
    """The email and password fingerprint a valid reset token carries."""
    try:
        decoded = jwt.decode(token, settings.SECRET_KEY, algorithms=[ALGORITHM])
    except jwt.InvalidTokenError:
        return None
    if decoded.get("purpose") != PASSWORD_RESET_PURPOSE:
        return None
    email, fingerprint = decoded.get("sub"), decoded.get("pwd")
    if not isinstance(email, str) or not isinstance(fingerprint, str):
        return None
    return email, fingerprint


def password_reset_token_matches(fingerprint: str, hashed_password: str) -> bool:
    return hmac.compare_digest(fingerprint, password_fingerprint(hashed_password))
