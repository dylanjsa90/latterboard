"""What `POST /login/google` and `POST /users/google` share."""

from datetime import timedelta

from fastapi import HTTPException, status

from app.core import google, security
from app.core.config import settings
from app.models import User

PROVIDER = "google"


def verified_identity(credential: str) -> google.GoogleIdentity:
    """The Google account behind a credential, whose email Google has verified.

    404 when Google sign-in isn't configured, 401 for a bad or expired credential,
    403 for an unverified email: the email becomes the account's (and is how an
    existing account gets linked), so it has to belong to whoever signed in.
    """
    if not settings.GOOGLE_CLIENT_ID:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not Found")
    try:
        identity = google.verify_google_id_token(credential)
    except google.InvalidGoogleToken as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Google sign-in expired or didn't go through. Try again.",
        ) from exc
    if not identity.email_verified:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Your Google account's email isn't verified.",
        )
    return identity


def access_token(user: User) -> str:
    """The same session token password sign-in issues."""
    return security.create_access_token(
        user.id, expires_delta=timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    )
