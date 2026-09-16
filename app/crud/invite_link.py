import secrets
from datetime import timedelta

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models import MatchInviteLink, User
from app.schemas.invite_link import (
    InviteLinkCreate,
    InviteLinkPreview,
    InviteLinkPublic,
    InviteLinkStatus,
)
from app.utils import utcnow


def invite_url(token: str) -> str:
    return f"{settings.FRONTEND_HOST}/invite/{token}"


class CRUDInviteLink:
    def create(self, db: Session, inviter_id: int, link_in: InviteLinkCreate) -> MatchInviteLink:
        link = MatchInviteLink(
            token=secrets.token_urlsafe(16),
            inviter_id=inviter_id,
            game=link_in.game,
            message=link_in.message,
            channel=link_in.channel,
            recipient_email=link_in.email,
            expires_at=utcnow() + timedelta(days=settings.MATCH_INVITE_LINK_EXPIRY_DAYS),
        )
        db.add(link)
        db.commit()
        db.refresh(link)
        return link

    def count_created_today(self, db: Session, inviter_id: int) -> int:
        midnight = utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        return (
            db.query(MatchInviteLink)
            .filter(
                MatchInviteLink.inviter_id == inviter_id,
                MatchInviteLink.created_at >= midnight,
            )
            .count()
        )

    def get_by_token(self, db: Session, token: str) -> MatchInviteLink | None:
        return db.query(MatchInviteLink).filter(MatchInviteLink.token == token).first()

    def get_open_for(self, db: Session, inviter_id: int) -> list[MatchInviteLink]:
        """The player's links nobody has used, withdrawn, or let run out, newest first."""
        return (
            db.query(MatchInviteLink)
            .filter(
                MatchInviteLink.inviter_id == inviter_id,
                MatchInviteLink.claimed_by_id.is_(None),
                MatchInviteLink.revoked_at.is_(None),
                MatchInviteLink.expires_at > utcnow(),
            )
            .order_by(MatchInviteLink.created_at.desc(), MatchInviteLink.id.desc())
            .all()
        )

    def status(self, link: MatchInviteLink) -> InviteLinkStatus:
        if link.claimed_by_id is not None:
            return "claimed"
        if link.revoked_at is not None:
            return "revoked"
        if link.expires_at < utcnow():
            return "expired"
        return "open"

    def claim(self, db: Session, link: MatchInviteLink, user_id: int) -> bool:
        """Mark the link as used by `user_id`, unless someone else got there first.

        A conditional UPDATE, so two people opening the link at once can't both win it.
        """
        updated = (
            db.query(MatchInviteLink)
            .filter(MatchInviteLink.id == link.id, MatchInviteLink.claimed_by_id.is_(None))
            .update({MatchInviteLink.claimed_by_id: user_id}, synchronize_session=False)
        )
        db.commit()
        db.refresh(link)
        return updated == 1 or link.claimed_by_id == user_id

    def set_match(self, db: Session, link: MatchInviteLink, match_id: int) -> None:
        link.match_id = match_id
        db.add(link)
        db.commit()

    def revoke(self, db: Session, link: MatchInviteLink) -> None:
        link.revoked_at = utcnow()
        db.add(link)
        db.commit()

    def to_public(self, link: MatchInviteLink, *, emailed: bool = False) -> InviteLinkPublic:
        return InviteLinkPublic(
            token=link.token,
            url=invite_url(link.token),
            game=link.game,
            message=link.message,
            channel=link.channel,  # type: ignore[arg-type]
            recipient_email=link.recipient_email,
            created_at=link.created_at,
            expires_at=link.expires_at,
            emailed=emailed,
        )

    def to_preview(self, db: Session, link: MatchInviteLink) -> InviteLinkPreview:
        inviter = db.get(User, link.inviter_id)
        return InviteLinkPreview(
            inviter_username=inviter.username if inviter else "",
            game=link.game,
            message=link.message,
            expires_at=link.expires_at,
            status=self.status(link),
        )


invite_link = CRUDInviteLink()
