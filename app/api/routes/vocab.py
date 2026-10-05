"""Vocab Challenger: solo sessions and duels addressed by invite code, plus each
player's wordbook. Rooms aren't matches and have no socket; clients poll
`GET /vocab/rooms/{code}`, and each request applies whatever transition is due.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, status
from sqlalchemy.orm import Session

from app.api import deps
from app.crud.vocab import crud_vocab
from app.game import vocab
from app.game.vocab_words import WORDS
from app.models import User
from app.schemas.vocab import (
    ReviewCreate,
    RoomAction,
    RoomCreate,
    RoomSnapshot,
    Wordbook,
)

router = APIRouter(prefix="/vocab", tags=["vocab"])

RoomCode = Annotated[str, Path(pattern="^[A-Fa-f0-9]{6}$")]


@contextmanager
def _http_errors() -> Iterator[None]:
    try:
        yield
    except vocab.VocabError as e:
        raise HTTPException(status_code=e.status, detail=e.message)


def _player_id(user: User) -> str:
    # Room state is JSON, so player ids are strings.
    return str(user.id)


def _known_word(word_id: int) -> int:
    if not 0 <= word_id < len(WORDS):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Unknown word."
        )
    return word_id


# --- rooms ---------------------------------------------------------------


@router.post("/rooms", response_model=RoomSnapshot, status_code=status.HTTP_201_CREATED)
def create_room(
    body: RoomCreate,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
) -> RoomSnapshot:
    with _http_errors():
        return crud_vocab.create_room(
            db, body.mode, _player_id(current_user), current_user.username
        )


@router.get("/rooms/{code}", response_model=RoomSnapshot)
def get_room(
    code: RoomCode,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
) -> RoomSnapshot:
    """The room as the player sees it. Also records that they're still here, and
    resolves a round whose deadline has passed."""
    player_id = _player_id(current_user)
    with _http_errors():
        return crud_vocab.mutate(
            db, code.upper(), player_id, lambda g, now: vocab.touch(g, player_id, now)
        )


@router.post("/rooms/{code}/join", response_model=RoomSnapshot)
def join_room(
    code: RoomCode,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
) -> RoomSnapshot:
    """Take the second seat in a duel's lobby. Rejoining a room you're in just reconnects."""
    player_id = _player_id(current_user)

    def change(g: vocab.Game, now: int) -> None:
        vocab.join(g, vocab.new_player(player_id, current_user.username, now), now)

    with _http_errors():
        return crud_vocab.mutate(db, code.upper(), player_id, change)


@router.post("/rooms/{code}/actions", response_model=RoomSnapshot)
def room_action(
    code: RoomCode,
    body: RoomAction,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
) -> RoomSnapshot:
    player_id = _player_id(current_user)

    def change(g: vocab.Game, now: int) -> None:
        vocab.apply_action(
            g,
            player_id,
            body.action,
            now,
            choice=body.choice,
            round_index=body.round,
            generation=body.generation,
        )

    with _http_errors():
        return crud_vocab.mutate(db, code.upper(), player_id, change)


# --- wordbook ------------------------------------------------------------


@router.get("/words", response_model=Wordbook)
def get_wordbook(
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
) -> Wordbook:
    """The player's saved words, soonest due first."""
    return crud_vocab.wordbook(db, current_user.id)


@router.put("/words/{word_id}", status_code=status.HTTP_204_NO_CONTENT)
def save_word(
    word_id: int,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
) -> None:
    crud_vocab.save(db, current_user.id, _known_word(word_id))


@router.delete("/words/{word_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_word(
    word_id: int,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
) -> None:
    crud_vocab.remove(db, current_user.id, _known_word(word_id))


@router.post("/words/{word_id}/review", status_code=status.HTTP_204_NO_CONTENT)
def review_word(
    word_id: int,
    body: ReviewCreate,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
) -> None:
    """Record a recall check: known moves the word up the ladder, a miss resets it."""
    with _http_errors():
        crud_vocab.review(db, current_user.id, _known_word(word_id), body.known)
