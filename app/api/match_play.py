"""Applying a move and telling both players about it.

Sits alongside `app/api/match_start.py` and exists for the same reason: the move
endpoints are no longer the only caller. The computer opponent
(`app/api/bot_turn.py`) plays through here too, so its websocket frames are
identical to a human's rather than a second copy that can drift.

Nothing here raises `HTTPException`. The bot runs inside a background task, where
an `HTTPException` reaches no handler and is simply a traceback — so the guards
return plain values and `MoveRejected` propagates, leaving each caller to decide
what a rejection means. The routes turn it into a 409; the bot shrugs and stops.
"""

from sqlalchemy.orm import Session

from app.connection_manager import manager
from app.crud.match import match as crud_match
from app.crud.match_puzzle import match_puzzle as crud_match_puzzle
from app.models import Match, User
from app.schemas.match import MatchGuessResult, RaceGuessResult


def wordle_move_error(match: Match, user: User) -> str | None:
    """Why this player may not guess right now, or None if they may."""
    if match.status != "in_progress":
        return "Match is not in progress"
    if match.current_turn_user_id != user.id:
        return "Not your turn"
    return None


async def apply_wordle_guess(
    db: Session, match: Match, user: User, word: str
) -> MatchGuessResult:
    """Record a turn-based guess and broadcast it to the match topic."""
    match_id = match.id
    result = crud_match.submit_guess(db, match, user, word)

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
        await manager.broadcast(
            {
                "type": "match_completed",
                "match_id": match_id,
                "winner_username": result.winner_username,
                # `submit_guess` refreshes the row, so this is current; the route
                # used to re-fetch the match here for the same value.
                "target_word": match.target_word,
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


async def apply_race_guess(
    db: Session, match: Match, user: User, guess: str | list[int]
) -> RaceGuessResult:
    """Record a race guess and broadcast it. Raises `MoveRejected` if the match is
    over or this player has already finished."""
    result = crud_match_puzzle.submit_race_guess(db, match, user, guess)

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
