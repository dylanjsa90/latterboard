from collections.abc import Generator

import jwt
from fastapi import Depends, HTTPException, Query, WebSocket, WebSocketException, status
from fastapi.security import OAuth2PasswordBearer
from jwt.exceptions import InvalidTokenError
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.core import security
from app.core.config import settings
from app.database import SessionLocal
from app.models.user import User
from app.schemas.user import TokenPayload

reusable_oauth2 = OAuth2PasswordBearer(
    tokenUrl=f"{settings.API_V1_STR}/login/access-token"
)
optional_oauth2 = OAuth2PasswordBearer(
    tokenUrl=f"{settings.API_V1_STR}/login/access-token", auto_error=False
)


def get_db() -> Generator[Session, None, None]:
    try:
        db = SessionLocal()
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _access_token_user_id(token: str) -> int | None:
    """The user id a valid access token names, or None for anything else.

    A reset token, or any token whose subject isn't a number, is None here, so it
    can't reach `db.get` (where a non-numeric id fails on Postgres).
    """
    try:
        payload = jwt.decode(
            token, settings.SECRET_KEY, algorithms=[security.ALGORITHM]
        )
        token_data = TokenPayload(**payload)
    except (InvalidTokenError, ValidationError):
        return None
    # Tokens issued before the purpose claim have none; they are access tokens.
    if token_data.purpose not in (None, security.ACCESS_PURPOSE):
        return None
    return token_data.sub


def get_current_user(
    db: Session = Depends(get_db), token: str = Depends(reusable_oauth2)
) -> User:
    user_id = _access_token_user_id(token)
    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if not user.is_active:
        raise HTTPException(status_code=400, detail="Inactive user")
    return user


def get_optional_current_user(
    db: Session = Depends(get_db), token: str | None = Depends(optional_oauth2)
) -> User | None:
    """Like get_current_user, but returns None instead of raising when no
    token is presented or it doesn't check out, for routes usable both
    anonymously and (with tracking) while logged in.
    """
    if not token:
        return None
    user_id = _access_token_user_id(token)
    if user_id is None:
        return None
    user = db.get(User, user_id)
    if not user or not user.is_active:
        return None
    return user


def get_current_user_ws(
    websocket: WebSocket, token: str | None = Query(default=None)
) -> User:
    """Authenticate a websocket handshake, rejecting it if the token doesn't check out.

    The browser WebSocket API can't set request headers, so the token normally arrives
    as a ?token= query param; non-browser clients may send an Authorization header
    instead, which takes precedence. Failures close the handshake with 1008 rather than
    raising HTTPException, which has no meaning before a socket is accepted.
    """
    header = websocket.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        token = header[7:]
    if not token:
        raise WebSocketException(
            code=status.WS_1008_POLICY_VIOLATION, reason="Missing token"
        )
    user_id = _access_token_user_id(token)
    if user_id is None:
        raise WebSocketException(
            code=status.WS_1008_POLICY_VIOLATION,
            reason="Could not validate credentials",
        )
    # Deliberately not Depends(get_db): a websocket dependency's session would stay
    # checked out of the pool for the whole life of the connection.
    db = SessionLocal()
    try:
        user = db.get(User, user_id)
        if not user or not user.is_active:
            raise WebSocketException(
                code=status.WS_1008_POLICY_VIOLATION,
                reason="Could not validate credentials",
            )
        return user
    finally:
        db.close()


def get_current_active_superuser(
    current_user: User = Depends(get_current_user),
) -> User:
    if not current_user.is_superuser:
        raise HTTPException(
            status_code=403, detail="The user doesn't have enough privileges"
        )
    return current_user
