from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api import deps
from app.connection_manager import manager
from app.core.config import settings
from app.crud.match import match as crud_match
from app.crud.user import user as crud_user
from app.game import wordle
from app.models import User
from app.schemas.match import (
    MatchDetail,
    MatchGuessCreate,
    MatchGuessResult,
    MatchInviteCreate,
    MatchPublic,
    PendingInvite,
)

router = APIRouter(prefix="/matches", tags=["matches"])

SUPPORTED_GAMES = {"wordle"}


def _get_match_or_404(db: Session, match_id: int):
    obj = crud_match.get(db, match_id)
    if obj is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Match not found")
    return crud_match.expire_if_needed(db, obj)


def _require_participant(match, user: User) -> None:
    if user.id not in (match.inviter_id, match.invitee_id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not a participant")


@router.post("/invite", response_model=MatchPublic, status_code=status.HTTP_201_CREATED)
async def create_invite(
    invite_in: MatchInviteCreate,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
):
    if invite_in.game not in SUPPORTED_GAMES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unsupported game")

    opponent = crud_user.get_user_by_username(db, invite_in.opponent_username)
    if opponent is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Opponent not found")
    if opponent.id == current_user.id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cannot invite yourself")

    existing = crud_match.get_open_match_between(db, current_user.id, opponent.id, invite_in.game)
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="An open match already exists"
        )

    new_match = crud_match.create_invite(
        db,
        inviter_id=current_user.id,
        invitee_id=opponent.id,
        game=invite_in.game,
        max_guesses=settings.MATCH_MAX_GUESSES,
        expiry_minutes=settings.MATCH_INVITE_EXPIRY_MINUTES,
    )

    await manager.send_to_user(
        opponent.id,
        {
            "type": "invite_received",
            "match_id": new_match.id,
            "from_username": current_user.username,
            "game": new_match.game,
            "created_at": new_match.created_at.isoformat(),
            "expires_at": new_match.invite_expires_at.isoformat(),
        },
    )
    return crud_match.to_public(db, new_match)


@router.post("/{match_id}/accept", response_model=MatchPublic)
async def accept_invite(
    match_id: int,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
):
    obj = _get_match_or_404(db, match_id)
    if current_user.id != obj.invitee_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not your invite")
    if obj.status == "expired":
        raise HTTPException(status_code=status.HTTP_410_GONE, detail="Invite expired")
    if obj.status != "pending_invite":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Invite is not pending")

    obj = crud_match.accept_invite(db, obj)
    public = crud_match.to_public(db, obj)

    for user_id in (obj.inviter_id, obj.invitee_id):
        await manager.send_to_user(
            user_id,
            {
                "type": "match_started",
                "match_id": obj.id,
                "opponent_username": (
                    public.invitee_username
                    if user_id == obj.inviter_id
                    else public.inviter_username
                ),
                "current_turn_username": public.current_turn_username,
                "max_guesses": obj.max_guesses,
            },
        )
    return public


@router.post("/{match_id}/decline", response_model=MatchPublic)
async def decline_invite(
    match_id: int,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
):
    obj = _get_match_or_404(db, match_id)
    if current_user.id != obj.invitee_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not your invite")
    if obj.status != "pending_invite":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Invite is not pending")

    obj = crud_match.decline_invite(db, obj)
    await manager.send_to_user(obj.inviter_id, {"type": "invite_declined", "match_id": obj.id})
    return crud_match.to_public(db, obj)


@router.post("/{match_id}/cancel", response_model=MatchPublic)
async def cancel_invite(
    match_id: int,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
):
    obj = _get_match_or_404(db, match_id)
    if current_user.id != obj.inviter_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not your invite")
    if obj.status != "pending_invite":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Invite is not pending")

    obj = crud_match.cancel_invite(db, obj)
    await manager.send_to_user(obj.invitee_id, {"type": "invite_cancelled", "match_id": obj.id})
    return crud_match.to_public(db, obj)


@router.get("/pending", response_model=list[PendingInvite])
def pending_invites(
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
):
    invites = crud_match.get_pending_invites(db, current_user.id)
    return [crud_match.to_pending_invite(db, m) for m in invites]


@router.get("/{match_id}", response_model=MatchDetail)
def get_match(
    match_id: int,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
):
    obj = _get_match_or_404(db, match_id)
    _require_participant(obj, current_user)
    return crud_match.to_detail(db, obj)


@router.post("/{match_id}/guess", response_model=MatchGuessResult)
async def submit_guess(
    match_id: int,
    guess_in: MatchGuessCreate,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
):
    obj = _get_match_or_404(db, match_id)
    _require_participant(obj, current_user)
    if obj.status != "in_progress":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Match is not in progress")
    if obj.current_turn_user_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Not your turn")
    if not wordle.is_valid_word(guess_in.word):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Not a valid word")

    result = crud_match.submit_guess(db, obj, current_user, guess_in.word)

    await manager.broadcast(
        {
            "type": "opponent_guessed",
            "match_id": result.match_id,
            "username": result.username,
            "turn_number": result.turn_number,
            "word": result.word,
            "result": [r.model_dump() for r in result.result],
            "correct": result.correct,
        },
        topic=f"match:{match_id}",
    )

    if result.status == "completed":
        refreshed = crud_match.get(db, match_id)
        await manager.broadcast(
            {
                "type": "match_completed",
                "match_id": match_id,
                "winner_username": result.winner_username,
                "target_word": refreshed.target_word,
                "reason": "solved" if result.winner_username else "exhausted",
            },
            topic=f"match:{match_id}",
        )
    else:
        await manager.broadcast(
            {
                "type": "your_turn",
                "match_id": match_id,
                "current_turn_username": result.current_turn_username,
            },
            topic=f"match:{match_id}",
        )

    return result
