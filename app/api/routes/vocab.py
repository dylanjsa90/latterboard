"""Vocab Challenger: solo sessions and duels addressed by invite code, plus each
player's wordbook. Rooms aren't matches. Each request applies whatever transition is
due, and when that or the request changes the room, a `room_changed` frame on the
socket topic `vocab:{code}` tells its players to refetch `GET /vocab/rooms/{code}`.

Nothing here runs on a timer: clients refetch at a round's deadline themselves, and the
frame goes out from the request's `BackgroundTasks`, never a detached task (see the
`app/services/bot_turn.py` docstring for why).

`GET /vocab/daily-word` (the home page's word of the day) and `POST /vocab/guest-rooms`
are public. A guest room is a solo room started signed out; its own `GET` and `actions`
take the `X-Guest-Token` header its create call returned, in place of a bearer.
"""

import secrets
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Annotated

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    Header,
    HTTPException,
    Path,
    status,
)
from sqlalchemy.orm import Session

from app.api import deps
from app.connection_manager import manager
from app.crud.vocab import crud_vocab
from app.game import puzzles, vocab
from app.game.vocab_words import WORDS, daily_word
from app.models import User
from app.schemas.vocab import (
    DailyWord,
    GuestRoomSnapshot,
    ReviewCreate,
    RoomAction,
    RoomCreate,
    RoomSnapshot,
    Wordbook,
)

router = APIRouter(prefix="/vocab", tags=["vocab"])

RoomCode = Annotated[str, Path(pattern="^[A-Fa-f0-9]{6}$")]

GUEST_HEADER = "X-Guest-Token"
GuestToken = Annotated[str, Header(alias=GUEST_HEADER, min_length=32, max_length=64)]
OptionalGuestToken = Annotated[
    str | None, Header(alias=GUEST_HEADER, min_length=32, max_length=64)
]


@contextmanager
def _http_errors() -> Iterator[None]:
    try:
        yield
    except vocab.VocabError as e:
        raise HTTPException(status_code=e.status, detail=e.message)


def _player_id(user: User) -> str:
    # Room state is JSON, so player ids are strings.
    return str(user.id)


def _room_player_id(
    guest_token: OptionalGuestToken = None,
    user: User | None = Depends(deps.get_optional_current_user),
) -> str:
    """Who is asking about a room: the guest whose token is sent (it wins over a bearer,
    so a tab that has just signed in can still read its unclaimed room), else the
    signed-in user."""
    if guest_token is not None:
        return vocab.guest_player_id(guest_token)
    if user is not None:
        return _player_id(user)
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )


def _mutate(
    db: Session,
    background_tasks: BackgroundTasks,
    code: str,
    player_id: str,
    change: Callable[[vocab.Game, int], None],
) -> RoomSnapshot:
    """Apply `change` to the room and, if that's news, tell everyone watching it."""
    with _http_errors():
        snapshot, news = crud_vocab.mutate(db, code, player_id, change)
    if news:
        background_tasks.add_task(
            manager.broadcast,
            {"type": "room_changed", "code": code, "revision": snapshot.game.revision},
            topic=f"vocab:{code}",
        )
    return snapshot


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


@router.post(
    "/guest-rooms",
    response_model=GuestRoomSnapshot,
    status_code=status.HTTP_201_CREATED,
)
def create_guest_room(db: Session = Depends(deps.get_db)) -> GuestRoomSnapshot:
    """A solo room for a signed-out visitor. The token is returned only here; the room
    keeps a hash of it as the guest's seat id."""
    token = secrets.token_urlsafe(32)
    with _http_errors():
        snapshot = crud_vocab.create_room(
            db, "solo", vocab.guest_player_id(token), vocab.GUEST_NAME
        )
    return GuestRoomSnapshot(**snapshot.model_dump(), guest_token=token)


@router.get("/rooms/{code}", response_model=RoomSnapshot)
def get_room(
    code: RoomCode,
    background_tasks: BackgroundTasks,
    db: Session = Depends(deps.get_db),
    player_id: str = Depends(_room_player_id),
) -> RoomSnapshot:
    """The room as the player sees it. Also records that they're still here, and
    resolves a round whose deadline has passed."""
    return _mutate(
        db,
        background_tasks,
        code.upper(),
        player_id,
        lambda g, now: vocab.touch(g, player_id, now),
    )


@router.post("/rooms/{code}/join", response_model=RoomSnapshot)
def join_room(
    code: RoomCode,
    background_tasks: BackgroundTasks,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
) -> RoomSnapshot:
    """Take the second seat in a duel's lobby. Rejoining a room you're in just reconnects."""
    player_id = _player_id(current_user)

    def change(g: vocab.Game, now: int) -> None:
        vocab.join(g, vocab.new_player(player_id, current_user.username, now), now)

    return _mutate(db, background_tasks, code.upper(), player_id, change)


@router.post("/rooms/{code}/actions", response_model=RoomSnapshot)
def room_action(
    code: RoomCode,
    body: RoomAction,
    background_tasks: BackgroundTasks,
    db: Session = Depends(deps.get_db),
    player_id: str = Depends(_room_player_id),
) -> RoomSnapshot:
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

    return _mutate(db, background_tasks, code.upper(), player_id, change)


@router.post("/rooms/{code}/claim", response_model=RoomSnapshot)
def claim_room(
    code: RoomCode,
    guest_token: GuestToken,
    background_tasks: BackgroundTasks,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
) -> RoomSnapshot:
    """Hand a guest room's seat to the signed-in caller, answers and score kept."""
    player_id = _player_id(current_user)
    guest_id = vocab.guest_player_id(guest_token)

    def change(g: vocab.Game, now: int) -> None:
        vocab.claim(g, guest_id, player_id, current_user.username, now)

    return _mutate(db, background_tasks, code.upper(), player_id, change)


# --- word of the day -----------------------------------------------------


@router.get("/daily-word", response_model=DailyWord)
def get_daily_word() -> DailyWord:
    """Today's word (UTC, the daily puzzles' day). Public: the vocab home is."""
    day = puzzles.today()
    w = daily_word(day)
    return DailyWord(
        id=w.id,
        date=day.isoformat(),
        word=w.word,
        pronunciation=w.pronunciation,
        pos=w.pos,
        definition=w.definition,
        example=w.example,
    )


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
