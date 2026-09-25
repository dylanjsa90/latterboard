"""Driving the computer-controlled opponent's moves.

The bot only ever acts as a consequence of a human's HTTP request, inside that
request's `BackgroundTasks`. That choice does a lot of work:

- Background tasks are awaited inside the ASGI request/response cycle, which
  uvicorn's graceful shutdown tracks and waits on. A detached `asyncio.create_task`
  is not tracked, so it would be cancelled silently at loop teardown — and under
  `fastapi dev` the reloader SIGTERMs the child on every file save, stranding a
  match each time.
- FastAPI exits `yield` dependencies *before* sending the response and runs
  background tasks after, so `get_db` has already committed and closed by the time
  the bot opens its own session. No overlapping transactions to reason about.
- The sync `TestClient` runs background tasks to completion before returning, so
  with `BOT_TURN_DELAY_SECONDS=0` the bot's frames arrive in a deterministic order
  and the tests need no special casing.

Only `match_id` crosses into the task. ORM objects from the request's session are
detached by the time it runs.
"""

import asyncio
import logging
import random
from typing import Any

from fastapi import BackgroundTasks
from sqlalchemy.orm import Session

from app.api.match_play import apply_race_guess, apply_wordle_guess, wordle_move_error
from app.core.config import settings
from app.crud.match import match as crud_match
from app.crud.match_puzzle import MoveRejected
from app.crud.match_puzzle import match_puzzle as crud_match_puzzle
from app.database import SessionLocal
from app.game import bot, match_modes
from app.models import Match, MatchGuess, MatchPuzzle, User

logger = logging.getLogger(__name__)

BOT_GAMES = frozenset({"wordle", match_modes.WORD_RACE, match_modes.CIPHER_RACE})


def bot_participant(db: Session, match: Match) -> User | None:
    """The bot playing in this match, or None if both players are human."""
    for user_id in (match.inviter_id, match.invitee_id):
        user = db.get(User, user_id)
        if user is not None and user.is_bot:
            return user
    return None


def schedule_bot_turn(background_tasks: BackgroundTasks, db: Session, match: Match) -> None:
    """Queue the bot's reply, to run once the human's response has been sent.

    Checks for a bot here rather than inside the task: most matches are between two
    people, and queueing a task that sleeps only to discover it has nothing to do
    would hold a connection open for every move in every human match.
    """
    if match.game not in BOT_GAMES or bot_participant(db, match) is None:
        return
    background_tasks.add_task(run_bot_turn, match.id)


# Background tasks run after the response is sent but before the connection is
# released, so a playout at full pace would hold one open for half a minute — and
# leave a player who has finished their race waiting that long to hear the result.
CATCH_UP_PACE = 0.25


async def _think(pace: float = 1.0) -> None:
    """Pause before moving, so the bot reads as thinking rather than as a machine."""
    delay = pace * (
        settings.BOT_TURN_DELAY_SECONDS
        + random.random() * settings.BOT_TURN_DELAY_JITTER_SECONDS
    )
    if delay > 0:
        await asyncio.sleep(delay)


async def run_bot_turn(match_id: int) -> None:
    """Play one bot move in `match_id`, if one is owed.

    Never raises. An exception escaping a background task reaches no handler, and
    a bot that fails must not take the match down with it.
    """
    await _think()
    # Opened after the pause, so a pooled connection isn't held while sleeping.
    db = SessionLocal()
    try:
        match = crud_match.get(db, match_id)
        if match is None or match.game not in BOT_GAMES:
            return
        player = bot_participant(db, match)
        if player is None:
            return
        if match.game == "wordle":
            await _play_wordle(db, match, player)
        else:
            await _play_race(db, match, player)
    except MoveRejected as exc:
        # A legitimate outcome, not a failure: the human's move ended the match or
        # closed out the bot's side while it was thinking.
        logger.info("Bot move declined in match %s: %s", match_id, exc)
    except Exception:
        logger.exception("Bot turn failed for match %s", match_id)
    finally:
        db.close()


async def _play_wordle(db: Session, match: Match, player: User) -> None:
    # Re-checked rather than assumed: the state may have moved while we paused.
    if wordle_move_error(match, player) is not None:
        return
    history: bot.WordHistory = [
        (row.word, list(row.result))
        for row in db.query(MatchGuess)
        .filter(MatchGuess.match_id == match.id)
        .order_by(MatchGuess.turn_number)
        .all()
    ]
    # The board is shared, so the human's guesses are information the bot is
    # entitled to — exactly as the human can see the bot's.
    word = bot.choose_word(history, skill=settings.BOT_SKILL)
    await apply_wordle_guess(db, match, player, word)


def _race_history(row: MatchPuzzle, player_id: int) -> list[dict[str, Any]]:
    """The bot's own guesses. JSON object keys are strings, so the id is too."""
    guesses: list[dict[str, Any]] = row.state["guesses"][str(player_id)]
    return guesses


def _next_race_guess(match: Match, own_guesses: list[dict[str, Any]]) -> str | list[int]:
    """Only ever the bot's own history. Race boards are private, and the opponent's
    grades without their letters say nothing useful anyway."""
    if match.game == match_modes.WORD_RACE:
        word_history: bot.WordHistory = [
            (entry["guess"], list(entry["result"])) for entry in own_guesses
        ]
        return bot.choose_word(word_history, skill=settings.BOT_SKILL)
    cipher_history: bot.CipherHistory = [
        (list(entry["guess"]), entry["result"]) for entry in own_guesses
    ]
    return bot.choose_cipher(cipher_history, skill=settings.BOT_SKILL)


async def _play_race(db: Session, match: Match, player: User) -> None:
    """One guess per human guess, so both sides' attempt counts stay level — which
    is what makes "fewest attempts wins" a fair contest. A bot racing ahead on its
    own clock would end the match out from under a still-thinking human.

    The exception is when the human has finished and the bot hasn't: nothing else
    will prompt the bot again, and `race_outcome` can't decide the match until the
    bot's side is settled. Then it plays itself out.
    """
    opponent_id = match.invitee_id if player.id == match.inviter_id else match.inviter_id

    for move in range(match.max_guesses + 1):
        if match.status != "in_progress":
            return
        row = crud_match_puzzle.get_by_match(db, match.id)
        if crud_match_puzzle.race_finished_in(row, match, player.id):
            return
        if move > 0:
            # Only reached while playing out against a finished opponent, so keep
            # it brisk: they are waiting on this to learn the result.
            await _think(CATCH_UP_PACE)

        guess = _next_race_guess(match, _race_history(row, player.id))
        await apply_race_guess(db, match, player, guess)

        if not crud_match_puzzle.race_finished(db, match, opponent_id):
            return
