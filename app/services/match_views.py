"""Matches as the responses the routes return and the websocket frames carry.

`app/crud/match.py` reads and writes the rows; this turns them into schemas, loading
the players each one names in a single query rather than one per player or guess.
"""

from collections.abc import Iterable

from sqlalchemy.orm import Session

from app.crud.match import match as crud_match
from app.crud.match_puzzle import match_puzzle as crud_match_puzzle
from app.crud.user import user as crud_user
from app.game import match_modes
from app.models.match import Match, MatchGuess, MatchPuzzle
from app.models.user import User
from app.schemas.match import (
    ActiveMatch,
    LetterResultOut,
    MatchDetail,
    MatchGuessResult,
    MatchHistory,
    MatchHistoryItem,
    MatchPublic,
    PendingInvite,
)


def to_public(
    db: Session, match: Match, users: dict[int, User] | None = None
) -> MatchPublic:
    """`users` may hold players the caller already loaded; the rest are fetched."""
    users = _with_players(db, match, users or {})
    inviter = users.get(match.inviter_id)
    invitee = users.get(match.invitee_id)
    return MatchPublic(
        id=match.id,
        game=match.game,
        status=match.status,
        inviter_username=inviter.username if inviter else "",
        invitee_username=invitee.username if invitee else "",
        # `is_bot` is nullable, so coerce: null means a human account.
        inviter_is_bot=bool(inviter and inviter.is_bot),
        invitee_is_bot=bool(invitee and invitee.is_bot),
        current_turn_username=_username_or_none(users, match.current_turn_user_id),
        winner_username=_username_or_none(users, match.winner_id),
        max_guesses=match.max_guesses,
        created_at=match.created_at,
        started_at=match.started_at,
        completed_at=match.completed_at,
    )


def to_detail(db: Session, match: Match, viewer_id: int) -> MatchDetail:
    guesses = crud_match.get_guesses(db, match.id)
    # Guessers are always players, so one query covers the match and its guesses.
    users = crud_user.get_by_ids(
        db,
        [
            match.inviter_id,
            match.invitee_id,
            match.current_turn_user_id,
            match.winner_id,
            *(g.user_id for g in guesses),
        ],
    )
    public = to_public(db, match, users)
    return MatchDetail(
        **public.model_dump(),
        target_word=(
            match.target_word
            if match.game == "wordle" and match.status == "completed"
            else None
        ),
        guesses=[
            _guess_out(match, g, users.get(g.user_id), g.word, public) for g in guesses
        ],
        race=(
            crud_match_puzzle.race_detail(db, match, viewer_id)
            if match.game in match_modes.RACE_GAMES
            else None
        ),
        sudoku=(
            crud_match_puzzle.sudoku_detail(db, match)
            if match.game == match_modes.SUDOKU_COOP
            else None
        ),
    )


def guess_result(
    db: Session, match: Match, user: User, guess: MatchGuess, typed: str
) -> MatchGuessResult:
    """The guess `user` just made. `typed` is the word as sent, whose letters label the
    result; the row keeps it lowercased."""
    public = to_public(db, match, {user.id: user})
    return _guess_out(match, guess, user, typed, public)


def _guess_out(
    match: Match,
    guess: MatchGuess,
    guesser: User | None,
    letters: str,
    public: MatchPublic,
) -> MatchGuessResult:
    return MatchGuessResult(
        match_id=match.id,
        turn_number=guess.turn_number,
        username=guesser.username if guesser else "",
        word=guess.word,
        result=[
            LetterResultOut(letter=letter, status=status)
            for letter, status in zip(letters, guess.result, strict=True)
        ],
        correct=guess.is_correct,
        status=match.status,
        current_turn_username=public.current_turn_username,
        winner_username=public.winner_username,
    )


def history(db: Session, user_id: int, *, skip: int, limit: int) -> MatchHistory:
    rows, total = crud_match.get_finished(db, user_id, skip=skip, limit=limit)
    opponents = crud_user.get_by_ids(db, (_opponent_id(m, user_id) for m in rows))
    coop_puzzles = crud_match_puzzle.get_by_matches(
        db, [m.id for m in rows if m.game == match_modes.SUDOKU_COOP]
    )
    return MatchHistory(
        items=[_history_item(m, user_id, opponents, coop_puzzles) for m in rows],
        total=total,
        record=crud_match.get_record(db, user_id),
    )


def _history_item(
    match: Match,
    viewer_id: int,
    opponents: dict[int, User],
    coop_puzzles: dict[int, MatchPuzzle],
) -> MatchHistoryItem:
    opponent = opponents.get(_opponent_id(match, viewer_id))
    if match.game == match_modes.SUDOKU_COOP:
        # Co-op completes with no winner; the puzzle row says how it ended.
        result = coop_puzzles[match.id].state["outcome"]
    elif match.winner_id is None:
        result = "draw"
    else:
        result = "won" if match.winner_id == viewer_id else "lost"
    return MatchHistoryItem(
        id=match.id,
        game=match.game,
        opponent_username=opponent.username if opponent else "",
        opponent_is_bot=bool(opponent and opponent.is_bot),
        result=result,
        # Completed matches always carry completed_at.
        completed_at=match.completed_at or match.created_at,
    )


def active_items(db: Session, user_id: int) -> list[ActiveMatch]:
    """The player's open matches as rail entries, in a fixed number of queries.

    The rail polls this on every match-list change, so opponents and race puzzles
    are loaded in one query each rather than per match.
    """
    matches = crud_match.get_active(db, user_id)
    opponents = crud_user.get_by_ids(db, (_opponent_id(m, user_id) for m in matches))
    puzzles = crud_match_puzzle.get_by_matches(
        db,
        [
            m.id
            for m in matches
            if m.status != "pending_invite" and m.game in match_modes.RACE_GAMES
        ],
    )
    return [_active_item(m, user_id, opponents, puzzles) for m in matches]


def _active_item(
    match: Match,
    viewer_id: int,
    opponents: dict[int, User],
    puzzles: dict[int, MatchPuzzle],
) -> ActiveMatch:
    is_inviter = viewer_id == match.inviter_id
    opponent = opponents.get(_opponent_id(match, viewer_id))
    pending = match.status == "pending_invite"
    row = puzzles.get(match.id)
    if pending:
        your_move = not is_inviter
    elif match.game == "wordle":
        your_move = match.current_turn_user_id == viewer_id
    elif match.game in match_modes.RACE_GAMES and row is not None:
        if match_modes.race_ready(row.data):
            your_move = not crud_match_puzzle.race_finished_in(row, match, viewer_id)
        else:
            # word_pick before guessing opens: on you only until you've picked.
            picked = str(_opponent_id(match, viewer_id)) in row.data["answers"]
            your_move = not picked
    else:
        # Co-op has no turns: the shared board can always use another hand.
        your_move = True
    return ActiveMatch(
        id=match.id,
        game=match.game,
        status="pending_invite" if pending else "in_progress",
        opponent_username=opponent.username if opponent else "",
        opponent_is_bot=bool(opponent and opponent.is_bot),
        role="inviter" if is_inviter else "invitee",
        your_move=your_move,
        created_at=match.created_at,
        started_at=match.started_at,
    )


def pending_invites(db: Session, matches: list[Match]) -> list[PendingInvite]:
    inviters = crud_user.get_by_ids(db, (m.inviter_id for m in matches))
    return [
        PendingInvite(
            id=m.id,
            game=m.game,
            from_username=(
                inviters[m.inviter_id].username if m.inviter_id in inviters else ""
            ),
            created_at=m.created_at,
            expires_at=m.invite_expires_at,
        )
        for m in matches
    ]


def _opponent_id(match: Match, viewer_id: int) -> int:
    return match.invitee_id if viewer_id == match.inviter_id else match.inviter_id


def _with_players(db: Session, match: Match, users: dict[int, User]) -> dict[int, User]:
    """`users` plus whichever of the match's people it lacks, loaded in one query."""
    missing: Iterable[int | None] = {
        match.inviter_id,
        match.invitee_id,
        match.current_turn_user_id,
        match.winner_id,
    } - users.keys()
    return {**users, **crud_user.get_by_ids(db, missing)}


def _username_or_none(users: dict[int, User], user_id: int | None) -> str | None:
    found = users.get(user_id) if user_id is not None else None
    return found.username if found else None
