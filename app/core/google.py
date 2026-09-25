"""Checking the ID token Google's sign-in button gives the browser.

The browser sends us Google's signed JWT (the "credential"); we check its signature
against Google's published keys and that it was issued for our client ID. Nothing
else talks to Google, so there's no client secret and no token exchange.
"""

from dataclasses import dataclass

import jwt

from app.core.config import settings

GOOGLE_CERTS_URL = "https://www.googleapis.com/oauth2/v3/certs"
GOOGLE_ISSUERS = ("accounts.google.com", "https://accounts.google.com")

# Caches Google's keys and refetches them when a token names a key it hasn't seen.
_jwks = jwt.PyJWKClient(GOOGLE_CERTS_URL, cache_keys=True)


class InvalidGoogleToken(Exception):
    """The credential is malformed, expired, forged, or meant for another app."""


@dataclass(frozen=True)
class GoogleIdentity:
    sub: str
    email: str
    email_verified: bool
    name: str | None


def verify_google_id_token(credential: str) -> GoogleIdentity:
    """The Google account a credential proves, or InvalidGoogleToken.

    Routes call this as `google.verify_google_id_token` so tests can replace it.
    """
    try:
        key = _jwks.get_signing_key_from_jwt(credential)
        claims = jwt.decode(
            credential,
            key.key,
            algorithms=["RS256"],
            audience=settings.GOOGLE_CLIENT_ID,
            options={"require": ["exp", "iat", "iss", "aud", "sub"]},
        )
    except jwt.PyJWTError as exc:
        raise InvalidGoogleToken(str(exc)) from exc
    if claims["iss"] not in GOOGLE_ISSUERS or not claims.get("email"):
        raise InvalidGoogleToken("Not a Google sign-in token.")
    return GoogleIdentity(
        sub=claims["sub"],
        email=claims["email"],
        email_verified=claims.get("email_verified") is True,
        name=claims.get("name"),
    )
