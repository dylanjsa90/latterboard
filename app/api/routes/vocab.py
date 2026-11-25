"""Vocab Challenger: solo sessions and duels addressed by invite code, plus each
player's wordbook. Rooms aren't matches. Each request applies whatever transition is
due, and when that or the request changes the room, a `room_changed` frame on the
socket topic `vocab:{code}` tells its players to refetch `GET /vocab/rooms/{code}`.

Nothing here runs on a timer: clients refetch at a round's deadline themselves, and the
frame goes out from the request's `BackgroundTasks`, never a detached task (see the
`app/services/bot_turn.py` docstring for why).

`GET /vocab/daily-word` (the home page's word of the day), `GET /vocab/entries` (every
shipped word, for webcade's word pages), `POST /vocab/guest-rooms` and
`POST /vocab/words/{id}/report` are public. A guest room is a solo room started signed out; its own `GET` and `actions`
take the `X-Guest-Token` header its create call returned, in place of a bearer.
"""

import functools
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
    Query,
    Request,
    Response,
    status,
)
from sqlalchemy.orm import Session

from app.api import deps
from app.connection_manager import manager
from app.core.rate_limit import allow, network_of
from app.crud.vocab import crud_vocab
from app.game import puzzles, vocab, vocab_words
from app.game.daily_archive import archive_days
from app.game.vocab_public import public_entries
from app.game.vocab_words import WORDS, daily_word
from app.models import User
from app.schemas.vocab import (
    DailyWord,
    DailyWordDay,
    DailyWordHistory,
    GuestRoomSnapshot,
    ReviewCreate,
    RoomAction,
    RoomCreate,
    RoomSnapshot,
    VocabEntries,
    Wordbook,
    WordReport,
    WordReportCreate,
)

router = APIRouter(prefix="/vocab", tags=["vocab"])

RoomCode = Annotated[str, Path(pattern="^[A-Fa-f0-9]{6}$")]

GUEST_HEADER = "X-Guest-Token"
GuestToken = Annotated[str, Header(alias=GUEST_HEADER, min_length=32, max_length=64)]
OptionalGuestToken = Annotated[
    str | None, Header(alias=GUEST_HEADER, min_length=32, max_length=64)
]

# Only creating a guest room is limited: per network (a class behind one school NAT
# fits), then site-wide. Read at call time, so tests can lower them.
GUEST_LIMIT_PREFIX = "rl:vocab-guest"
GUEST_WINDOW_SECONDS = 3600
GUEST_ROOMS_PER_NETWORK_PER_HOUR = 60
GUEST_ROOMS_PER_HOUR = 2_000
GUEST_NETWORK_LIMITED = "Too many guest games from your network. Sign in to keep playing, or try again later."
GUEST_SITE_LIMITED = "Guest play is busy right now. Sign in to keep playing, or try again in a few minutes."

# Reports are open to guests too, so they're limited the same way.
REPORT_LIMIT_PREFIX = "rl:vocab-report"
REPORT_WINDOW_SECONDS = 3600
REPORTS_PER_NETWORK_PER_HOUR = 20
REPORTS_PER_HOUR = 500
REPORT_LIMITED = "Too many reports right now. Please try again later."


@contextmanager
def _http_errors() -> Iterator[None]:
    try:
        yield
    except vocab.VocabError as e:
        raise HTTPException(status_code=e.status, detail=e.message)


def _player_id(user: User) -> str:
    # Room state is JSON, so player ids are strings.
    return str(user.id)


async def _guest_room_allowed(request: Request) -> None:
    host = request.client.host if request.client else None
    network_key = f"{GUEST_LIMIT_PREFIX}:net:{network_of(host)}"
    if not await allow(
        network_key, GUEST_ROOMS_PER_NETWORK_PER_HOUR, GUEST_WINDOW_SECONDS
    ):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=GUEST_NETWORK_LIMITED
        )
    if not await allow(
        f"{GUEST_LIMIT_PREFIX}:all", GUEST_ROOMS_PER_HOUR, GUEST_WINDOW_SECONDS
    ):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=GUEST_SITE_LIMITED
        )


async def _report_allowed(request: Request) -> None:
    host = request.client.host if request.client else None
    keys_and_limits = (
        (f"{REPORT_LIMIT_PREFIX}:net:{network_of(host)}", REPORTS_PER_NETWORK_PER_HOUR),
        (f"{REPORT_LIMIT_PREFIX}:all", REPORTS_PER_HOUR),
    )
    for key, limit in keys_and_limits:
        if not await allow(key, limit, REPORT_WINDOW_SECONDS):
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=REPORT_LIMITED
            )


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
    dependencies=[Depends(_guest_room_allowed)],
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
    """Today's word, for the Pacific day (`puzzles.today()`) every daily feature uses.
    Public: the home page shows it."""
    day = puzzles.today()
    w = daily_word(day)
    return DailyWord(
        id=w.id,
        date=day.isoformat(),
        slug=w.slug,
        word=w.word,
        pronunciation=w.pronunciation,
        pos=w.pos,
        definition=w.definition,
        example=w.example,
    )


# Public lists that change at most daily; CDNs and the build may cache them briefly.
PUBLIC_CACHE = "public, max-age=300"


@router.get("/daily-word/history", response_model=DailyWordHistory)
def get_daily_word_history() -> Response:
    """The word of each archived day through today, oldest first. Public. Today is the
    server's Pacific day, never one from the request, so no later day's word appears."""
    history = DailyWordHistory(
        days=[
            DailyWordDay(date=day.isoformat(), word_id=w.id, slug=w.slug)
            for day in archive_days(puzzles.today())
            for w in [daily_word(day)]
        ]
    )
    return Response(
        history.model_dump_json(),
        media_type="application/json",
        headers={"Cache-Control": PUBLIC_CACHE},
    )


@functools.cache
def _entries_body() -> bytes:
    # Serialized once per process: the list is public and grows to megabytes.
    return public_entries(vocab_words.WORDS).model_dump_json().encode()


@router.get("/entries", response_model=VocabEntries)
def get_vocab_entries() -> Response:
    """Every shipped word in id order, for webcade's word pages. Public."""
    return Response(
        _entries_body(),
        media_type="application/json",
        headers={"Cache-Control": PUBLIC_CACHE},
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


@router.post(
    "/words/{word_id}/report",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(_report_allowed)],
)
def report_word(
    word_id: int,
    body: WordReportCreate,
    db: Session = Depends(deps.get_db),
    user: User | None = Depends(deps.get_optional_current_user),
) -> None:
    """Report a problem with a word's entry. Open to guests; a signed-in reporter is
    recorded so a follow-up is possible."""
    crud_vocab.report(db, _known_word(word_id), user.id if user else None, body)


@router.get("/reports", response_model=list[WordReport])
def list_reports(
    limit: int = Query(200, ge=1, le=1000),
    db: Session = Depends(deps.get_db),
    _: User = Depends(deps.get_current_active_superuser),
) -> list[WordReport]:
    """Recent word reports, newest first (superuser only)."""
    return crud_vocab.reports(db, limit)
