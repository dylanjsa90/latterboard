from datetime import timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session

from app.api import deps, google_sign_in
from app.core import security
from app.core.config import settings
from app.crud import user as crud_user  # noqa: F401
from app.models import User
from app.schemas.user import (
    GoogleCredential,
    GoogleLogin,
    Message,
    NewPassword,
    Token,
    UserPrivate,
    UserUpdate,
)
from app.utils import (
    generate_password_reset_token,
    generate_reset_password_email,
    send_email,
    verify_password_reset_token,
)

router = APIRouter(tags=["login"])


@router.post("/login/access-token")
def login_access_token(
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(deps.get_db),
) -> Token:
    """
    OAuth2 compatible token login, get an access token for future requests
    """
    user = crud_user.authenticate(
        db=db, email=form_data.username, password=form_data.password
    )
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
        )
    elif not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Inactive user"
        )
    access_token_expires = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    return Token(
        access_token=security.create_access_token(
            user.id, expires_delta=access_token_expires
        )
    )


@router.post("/login/google", response_model_exclude_none=True)
def login_google(body: GoogleCredential, db: Session = Depends(deps.get_db)) -> GoogleLogin:
    """
    Sign in with a Google ID token. An account with the same verified email is
    linked on first use; with no account at all, the player signs up through
    `POST /users/google` instead.
    """
    identity = google_sign_in.verified_identity(body.credential)
    user = crud_user.get_user_by_identity(
        db, google_sign_in.PROVIDER, identity.sub
    )
    linked = user is not None
    if user is None:
        user = crud_user.get_user_by_email_any_case(db, identity.email)
    if user is None:
        return GoogleLogin(
            status="needs_profile", email=identity.email, name=identity.name
        )
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Inactive user"
        )
    if not linked:
        crud_user.link_identity(db, user, google_sign_in.PROVIDER, identity.sub)
    return GoogleLogin(
        status="signed_in",
        access_token=google_sign_in.access_token(user),
        token_type="bearer",
    )


@router.post("/login/test-token", response_model=UserPrivate)
def test_token(current_user: User = Depends(deps.get_current_user)) -> User:
    """
    Test access token
    """
    return current_user


@router.post("/password-recovery/{email}")
def recover_password(email: str, db: Session = Depends(deps.get_db)) -> Any:
    """
    Password Recovery
    """
    user = crud_user.get_user_by_email(db=db, email=email)

    # Always return the same response to prevent email enumeration attacks
    # Only send email if user actually exists
    if user:
        password_reset_token = generate_password_reset_token(email=email)
        email_data = generate_reset_password_email(
            email_to=user.email, email=email, token=password_reset_token
        )
        send_email(
            email_to=user.email,
            subject=email_data.subject,
            html_content=email_data.html_content,
        )
    return JSONResponse(
        {"message": "If that email is registered, we sent a password recovery link"}
    )


@router.post("/reset-password/")
def reset_password(body: NewPassword, db: Session = Depends(deps.get_db)) -> Message:
    """
    Reset password
    """
    email = verify_password_reset_token(token=body.token)
    if not email:
        raise HTTPException(status_code=400, detail="Invalid token")
    user = crud_user.get_user_by_email(db=db, email=email)
    if not user:
        # Don't reveal that the user doesn't exist - use same error as invalid token
        raise HTTPException(status_code=400, detail="Invalid token")
    elif not user.is_active:
        raise HTTPException(status_code=400, detail="Inactive user")
    user_in_update = UserUpdate(password=body.new_password)
    crud_user.update_user(
        db=db,
        user=user,
        user_in=user_in_update,
    )
    return Message(message="Password updated successfully")


@router.post(
    "/password-recovery-html-content/{email}",
    dependencies=[Depends(deps.get_current_active_superuser)],
    response_class=HTMLResponse,
)
def recover_password_html_content(
    email: str, db: Session = Depends(deps.get_db)
) -> Any:
    """
    HTML Content for Password Recovery
    """
    user = crud_user.get_user_by_email(db=db, email=email)

    if not user:
        raise HTTPException(
            status_code=404,
            detail="The user with this username does not exist in the system.",
        )
    password_reset_token = generate_password_reset_token(email=email)
    email_data = generate_reset_password_email(
        email_to=user.email, email=email, token=password_reset_token
    )

    return HTMLResponse(
        content=email_data.html_content, headers={"subject:": email_data.subject}
    )
