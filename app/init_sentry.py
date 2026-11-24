import re
from collections.abc import Callable

import sentry_sdk
from loguru import logger
from sentry_sdk.integrations.logging import LoggingIntegration
from sentry_sdk.integrations.loguru import LoguruIntegration
from sentry_sdk.transport import Transport
from sentry_sdk.types import Event, Hint

from app.core.config import settings
from app.init_logger import redact_tokens

# POST /password-recovery/{email} puts an address in the path.
_EMAIL = re.compile(r"[^/@\s?&=]+@[^/\s?&]+")


def _scrub_url(url: str) -> str:
    return _EMAIL.sub("[email]", redact_tokens(url))


def _redact_keys(
    section: object, keys: tuple[str, ...], redact: Callable[[str], str] = redact_tokens
) -> None:
    if isinstance(section, dict):
        for key in keys:
            if isinstance(section.get(key), str):
                section[key] = redact(section[key])


def scrub_event(event: Event, _hint: Hint) -> Event:
    """Redacts `token=` query values (the websocket's JWT), emails in URLs, and a
    guest room's `X-Guest-Token` header before an event leaves."""
    _redact_keys(event.get("request"), ("query_string",))
    _redact_keys(event.get("request"), ("url",), _scrub_url)
    _redact_keys(event.get("logentry"), ("message", "formatted"))
    headers = (event.get("request") or {}).get("headers")
    if isinstance(headers, dict):
        for name in headers:
            if name.lower() == "x-guest-token":
                headers[name] = "[Filtered]"
    breadcrumbs = event.get("breadcrumbs")
    if isinstance(breadcrumbs, dict):
        for crumb in breadcrumbs.get("values", []):
            _redact_keys(crumb, ("message",))
    return event


def setup_sentry(transport: type[Transport] | None = None) -> bool:
    """Reports errors to Sentry when SENTRY_DSN is set; returns whether it did.

    Unhandled route and websocket errors (5xx only) come from the FastAPI
    integration, and `logger.error` / `logger.exception` from the loguru one.
    """
    if not settings.SENTRY_DSN:
        return False
    sentry_sdk.init(
        dsn=settings.SENTRY_DSN,
        environment=settings.APP_ENV,
        # Keeps IPs, cookies, and the Authorization header out; user ids are set
        # explicitly by the auth dependencies.
        send_default_pii=False,
        traces_sample_rate=0,
        # Frame locals include the raw ASGI scope, with the Authorization header and
        # the ?token= query, so they stay out (as loguru's `diagnose` does).
        include_local_variables=False,
        before_send=scrub_event,
        # Just the message: loguru's default format starts with a timestamp, which
        # would make every logged error its own issue.
        integrations=[
            LoguruIntegration(event_format="{message}", breadcrumb_format="{message}")
        ],
        # stdlib logging already flows into loguru (InterceptHandler); Sentry's own
        # stdlib hook would report each of those errors a second time.
        disabled_integrations=[LoggingIntegration()],
        transport=transport,
    )
    logger.info(f"Reporting errors to Sentry ({settings.APP_ENV})")
    return True
