"""Friend invites: single-use links a player sends by text or email. Whoever opens one
and signs in (or signs up) starts a match with the player who sent it."""

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Response, status
from sqlalchemy.orm import Session

from app.api import deps
from app.api.match_start import notify_match_started, start_match
from app.api.routes.matches import SUPPORTED_GAMES
from app.core.config import settings
from app.crud.invite_link import invite_link as crud_invite_link
from app.crud.invite_link import invite_url
from app.crud.match import match as crud_match
from app.models import MatchInviteLink, User
from app.schemas.invite_link import (
    InviteLinkCreate,
    InviteLinkPreview,
    InviteLinkPublic,
)
from app.schemas.match import MatchPublic
from app.utils import generate_match_invite_email, send_email, utcnow

router = APIRouter(prefix="/invite-links", tags=["invite-links"])

# Mirrors webcade's src/components/match/games.ts, for the invite email.
GAME_TITLES = {
    "wordle": "Word Duel",
    "word_race": "Word Race",
    "cipher_race": "Cipher Race",
    "sudoku_coop": "Sudoku Co-op",
}


def _get_or_404(db: Session, token: str) -> MatchInviteLink:
    link = crud_invite_link.get_by_token(db, token)
    if link is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invite not found")
    return link


@router.post("/", response_model=InviteLinkPublic, status_code=status.HTTP_201_CREATED)
def create_invite_link(
    link_in: InviteLinkCreate,
    background_tasks: BackgroundTasks,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
) -> InviteLinkPublic:
    """Create a link to share. For `email`, also send it when SMTP is configured."""
    if link_in.game not in SUPPORTED_GAMES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unsupported game")
    if (
        crud_invite_link.count_created_today(db, current_user.id)
        >= settings.MATCH_INVITE_LINK_DAILY_LIMIT
    ):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="You've sent today's limit of invites. Try again tomorrow.",
        )

    link = crud_invite_link.create(db, current_user.id, link_in)
    emailed = False
    if link.recipient_email and settings.emails_enabled:
        email = generate_match_invite_email(
            inviter_username=current_user.username,
            game_title=GAME_TITLES.get(link.game, link.game),
            message=link.message,
            link=invite_url(link.token),
            expires_at=link.expires_at,
        )
        background_tasks.add_task(
            send_email,
            email_to=link.recipient_email,
            subject=email.subject,
            html_content=email.html_content,
        )
        emailed = True
    return crud_invite_link.to_public(link, emailed=emailed)


@router.get("/mine", response_model=list[InviteLinkPublic])
def my_invite_links(
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
) -> list[InviteLinkPublic]:
    """Your links that are still waiting to be used, newest first."""
    links = crud_invite_link.get_open_for(db, current_user.id)
    return [crud_invite_link.to_public(link) for link in links]


@router.get("/{token}", response_model=InviteLinkPreview)
def preview_invite_link(token: str, db: Session = Depends(deps.get_db)) -> InviteLinkPreview:
    """Who sent the invite and what for. Public, so it can show before sign-in."""
    return crud_invite_link.to_preview(db, _get_or_404(db, token))


@router.post("/{token}/claim", response_model=MatchPublic)
async def claim_invite_link(
    token: str,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
) -> MatchPublic:
    """Start the match with the link's sender. Claiming your own claimed link again
    returns the same match."""
    link = _get_or_404(db, token)
    if link.inviter_id == current_user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="This is your own invite link"
        )
    if link.claimed_by_id not in (None, current_user.id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Someone else already used this invite"
        )
    if link.match_id is not None:
        claimed = crud_match.get(db, link.match_id)
        if claimed is not None:
            return crud_match.to_public(db, claimed)
    if link.revoked_at is not None:
        raise HTTPException(status_code=status.HTTP_410_GONE, detail="This invite was withdrawn")
    if link.expires_at < utcnow():
        raise HTTPException(status_code=status.HTTP_410_GONE, detail="This invite has expired")
    inviter = db.get(User, link.inviter_id)
    if inviter is None or not inviter.is_active:
        raise HTTPException(
            status_code=status.HTTP_410_GONE, detail="This invite is no longer available"
        )
    if not crud_invite_link.claim(db, link, current_user.id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Someone else already used this invite"
        )

    obj = start_match(db, waiting=inviter, joining=current_user, game=link.game)
    crud_invite_link.set_match(db, link, obj.id)
    public = crud_match.to_public(db, obj)
    await notify_match_started(obj, public)
    return public


@router.delete("/{token}", status_code=status.HTTP_204_NO_CONTENT)
def revoke_invite_link(
    token: str,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
) -> Response:
    """Withdraw a link nobody has used yet."""
    link = _get_or_404(db, token)
    if link.inviter_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not your invite")
    if link.claimed_by_id is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="This invite was already used"
        )
    if link.revoked_at is None:
        crud_invite_link.revoke(db, link)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
