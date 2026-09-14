from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api import deps
from app.connection_manager import manager
from app.core.config import settings
from app.crud.match import match as crud_match
from app.crud.match_puzzle import MoveRejected
from app.crud.match_puzzle import match_puzzle as crud_match_puzzle
from app.crud.user import user as crud_user
from app.game import match_modes, wordle
from app.models import Match, User
from app.schemas.match import (
    MatchDetail,
    MatchGuessCreate,
    MatchGuessResult,
    MatchHistory,
    MatchInviteCreate,
    MatchPublic,
    PendingInvite,
    RaceGuessResult,
    SudokuCoopMoveCreate,
    SudokuCoopMoveResult,
)
from app.schemas.puzzle import CipherAttempt

router = APIRouter(prefix="/matches", tags=["matches"])

SUPPORTED_GAMES = {"wordle", *match_modes.PUZZLE_MATCH_GAMES}


def _get_match_or_404(db: Session, match_id: int):
    obj = crud_match.get(db, match_id)
    if obj is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Match not found")
    return crud_match.expire_if_needed(db, obj)


def _require_participant(match, user: User) -> None:
    if user.id not in (match.inviter_id, match.invitee_id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not a participant")


def _require_game(match: Match, game: str) -> None:
    if match.game != game:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=f"Not a {game} match"
        )


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
        max_guesses=(
            settings.MATCH_MAX_GUESSES
            if invite_in.game == "wordle"
            else match_modes.MAX_GUESSES[invite_in.game]
        ),
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


@router.get("/me/history", response_model=MatchHistory)
def my_match_history(
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
) -> MatchHistory:
    """Your finished matches, newest first, plus your win/loss/draw record."""
    return crud_match.get_history(db, current_user.id, skip=skip, limit=limit)


@router.get("/{match_id}", response_model=MatchDetail)
def get_match(
    match_id: int,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
):
    obj = _get_match_or_404(db, match_id)
    _require_participant(obj, current_user)
    return crud_match.to_detail(db, obj, current_user.id)


@router.post("/{match_id}/guess", response_model=MatchGuessResult)
async def submit_guess(
    match_id: int,
    guess_in: MatchGuessCreate,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
):
    obj = _get_match_or_404(db, match_id)
    _require_participant(obj, current_user)
    _require_game(obj, "wordle")
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


# --- races (word_race, cipher_race) ------------------------------------------


async def _submit_race_guess(
    db: Session, match: Match, user: User, guess: str | list[int]
) -> RaceGuessResult:
    try:
        result = crud_match_puzzle.submit_race_guess(db, match, user, guess)
    except MoveRejected as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    topic = f"match:{match.id}"
    # Grades only: the guess itself would hand the opponent the letters/digits.
    await manager.broadcast(
        {
            "type": "opponent_guessed",
            "match_id": match.id,
            "username": user.username,
            "turn_number": result.turn_number,
            "result": result.model_dump()["result"],
            "correct": result.correct,
        },
        topic=topic,
    )
    if result.status == "completed":
        await manager.broadcast(
            {
                "type": "match_completed",
                "match_id": match.id,
                "winner_username": result.winner_username,
                "answer": result.answer,
                "reason": "solved" if result.winner_username else "draw",
            },
            topic=topic,
        )
    return result


@router.post("/{match_id}/word/guess", response_model=RaceGuessResult)
async def submit_word_race_guess(
    match_id: int,
    guess_in: MatchGuessCreate,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
) -> RaceGuessResult:
    """Guess in a word race. Both players guess whenever they like; fewest guesses wins."""
    obj = _get_match_or_404(db, match_id)
    _require_participant(obj, current_user)
    _require_game(obj, match_modes.WORD_RACE)
    if not wordle.is_valid_word(guess_in.word):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Not a valid word")
    return await _submit_race_guess(db, obj, current_user, guess_in.word.lower())


@router.post("/{match_id}/cipher/guess", response_model=RaceGuessResult)
async def submit_cipher_race_guess(
    match_id: int,
    guess_in: CipherAttempt,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
) -> RaceGuessResult:
    """Guess in a cipher race. Both players guess whenever they like; fewest guesses wins."""
    obj = _get_match_or_404(db, match_id)
    _require_participant(obj, current_user)
    _require_game(obj, match_modes.CIPHER_RACE)
    return await _submit_race_guess(db, obj, current_user, guess_in.attempt)


# --- sudoku co-op -----------------------------------------------------------


@router.post("/{match_id}/sudoku/move", response_model=SudokuCoopMoveResult)
async def submit_sudoku_move(
    match_id: int,
    move_in: SudokuCoopMoveCreate,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
) -> SudokuCoopMoveResult:
    """Fill a cell on the shared board. A wrong value isn't placed and counts toward
    the team's mistake limit; reaching it loses the match for both players."""
    obj = _get_match_or_404(db, match_id)
    _require_participant(obj, current_user)
    _require_game(obj, match_modes.SUDOKU_COOP)
    try:
        result = crud_match_puzzle.submit_sudoku_move(db, obj, move_in.index, move_in.value)
    except MoveRejected as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    topic = f"match:{match_id}"
    move = {
        "match_id": match_id,
        "username": current_user.username,
        "index": result.index,
        "value": result.value,
    }
    if result.correct:
        await manager.broadcast({"type": "cell_filled", **move}, topic=topic)
    else:
        await manager.broadcast(
            {
                "type": "mistake",
                **move,
                "mistakes": result.mistakes,
                "max_mistakes": obj.max_guesses,
            },
            topic=topic,
        )
    if result.status == "completed":
        await manager.broadcast(
            {"type": "match_completed", "match_id": match_id, "outcome": result.outcome},
            topic=topic,
        )
    return result
