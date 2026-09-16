"""Starting a match once both players are known. Accepted invites, matchmaking, and
claimed invite links all go through here, so clients get the same frame from each."""

from sqlalchemy.orm import Session

from app.connection_manager import manager
from app.core.config import settings
from app.crud.match import match as crud_match
from app.game import match_modes
from app.models import Match, User
from app.schemas.match import MatchPublic


def max_guesses_for(game: str) -> int:
    return settings.MATCH_MAX_GUESSES if game == "wordle" else match_modes.MAX_GUESSES[game]


def start_match(db: Session, waiting: User, joining: User, game: str) -> Match:
    """Start a match between two players, reusing an open one they already share.

    `waiting` becomes the inviter, so they take the first turn in turn-based games.
    """
    obj = crud_match.get_open_match_between(db, waiting.id, joining.id, game)
    if obj is None:
        obj = crud_match.create_invite(
            db,
            inviter_id=waiting.id,
            invitee_id=joining.id,
            game=game,
            max_guesses=max_guesses_for(game),
            expiry_minutes=settings.MATCH_INVITE_EXPIRY_MINUTES,
        )
    if obj.status == "pending_invite":
        obj = crud_match.accept_invite(db, obj)
    return obj


async def notify_match_started(obj: Match, public: MatchPublic) -> None:
    """Tell both players the match has started, each naming the other as opponent."""
    for user_id in (obj.inviter_id, obj.invitee_id):
        await manager.send_to_user(
            user_id,
            {
                "type": "match_started",
                "match_id": obj.id,
                "game": obj.game,
                "opponent_username": (
                    public.invitee_username
                    if user_id == obj.inviter_id
                    else public.inviter_username
                ),
                "current_turn_username": public.current_turn_username,
                "max_guesses": obj.max_guesses,
            },
        )
