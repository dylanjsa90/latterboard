"""Rendering and sending email: password recovery and match invites."""

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import emails  # type: ignore[import-untyped]
from jinja2 import Template
from loguru import logger
from markupsafe import escape

from app.core.config import settings
from app.game.puzzles import day_of


@dataclass
class EmailData:
    html_content: str
    subject: str


def render_email_template(*, template_name: str, context: dict[str, Any]) -> str:
    template_str = (
        Path(__file__).parents[1] / "email-templates" / "build" / template_name
    ).read_text()
    html_content = Template(template_str).render(context)
    return html_content


def send_email(
    *,
    email_to: str,
    subject: str = "",
    html_content: str = "",
) -> None:
    assert settings.emails_enabled, "no provided configuration for email variables"
    message = emails.Message(
        subject=subject,
        html=html_content,
        mail_from=(settings.EMAILS_FROM_NAME, settings.EMAILS_FROM_EMAIL),
    )
    smtp_options = {"host": settings.SMTP_HOST, "port": settings.SMTP_PORT}
    if settings.SMTP_TLS:
        smtp_options["tls"] = True
    elif settings.SMTP_SSL:
        smtp_options["ssl"] = True
    if settings.SMTP_USER:
        smtp_options["user"] = settings.SMTP_USER
    if settings.SMTP_PASSWORD:
        smtp_options["password"] = settings.SMTP_PASSWORD
    response = message.send(to=email_to, smtp=smtp_options)
    logger.info(f"send email result: {response}")


def generate_reset_password_email(email_to: str, email: str, token: str) -> EmailData:
    project_name = settings.PROJECT_NAME
    subject = f"{project_name} - Password recovery for user {email}"
    link = f"{settings.FRONTEND_HOST}/reset-password?token={token}"
    html_content = render_email_template(
        template_name="reset_password.html",
        context={
            "project_name": settings.PROJECT_NAME,
            "username": email,
            "email": email_to,
            "valid_hours": settings.EMAIL_RESET_TOKEN_EXPIRE_HOURS,
            "link": link,
        },
    )
    return EmailData(html_content=html_content, subject=subject)


def generate_match_invite_email(
    *,
    inviter_username: str,
    game_title: str,
    message: str | None,
    link: str,
    expires_at: datetime,
) -> EmailData:
    # Collapsed to one line so a username can't add headers to the subject.
    inviter = " ".join(inviter_username.split())
    subject = f"{inviter} invited you to play {game_title}"
    expires_on = day_of(expires_at)
    html_content = render_email_template(
        template_name="match_invite.html",
        # Players write the username and message, and render_email_template doesn't
        # autoescape, so escape them here.
        context={
            "project_name": settings.PROJECT_NAME,
            "inviter_username": escape(inviter),
            "game_title": escape(game_title),
            "message": escape(message) if message else None,
            "link": link,
            "expires_on": f"{expires_on:%B} {expires_on.day}",
        },
    )
    return EmailData(html_content=html_content, subject=subject)
