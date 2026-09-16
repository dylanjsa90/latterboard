"""Automatic matchmaking: players queue for a game and are paired with whoever else is
waiting for it.

Each game's queue is a Redis sorted set of user ids, scored by when the player last
joined. Clients re-join periodically to stay queued; entries older than
MATCHMAKING_QUEUE_TTL_SECONDS are dropped, so a player who closes the tab leaves the
queue on their own.

A player nobody joins is given the computer to play after
MATCHMAKING_BOT_WAIT_SECONDS. That needs a *first* join time, which the sorted set
cannot supply — its score is deliberately the player's last join, so re-joining
refreshes it and the stale sweep can tell a closed tab from a patient one. So each
queue has a companion hash holding when each player first joined, written with
HSETNX and cleared everywhere the player leaves the queue.
"""

import time

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.orm import Session

from app.api import deps
from app.api.match_start import notify_match_started, start_match
from app.api.routes.matches import SUPPORTED_GAMES
from app.core.config import settings
from app.crud.match import match as crud_match
from app.crud.user import user as crud_user
from app.game import match_modes
from app.models import User
from app.redis import r as redis
from app.schemas.matchmaking import MatchmakingStatus

router = APIRouter(prefix="/matchmaking", tags=["matchmaking"])

QUEUE_PREFIX = "mm:queue"
WAITING_SINCE_PREFIX = "mm:waiting_since"

# Games the computer opponent can play. Sudoku co-op is excluded: it is cooperative
# and free-form, so there is no opponent to stand in for.
BOT_GAMES = frozenset({"wordle", match_modes.WORD_RACE, match_modes.CIPHER_RACE})

# Pairs the caller with the longest-waiting other player, or queues them if nobody else
# is waiting. A script so two players joining at once can't both pop the same opponent.
# KEYS: the queue, the waiting-since hash. ARGV: caller id, now, stale cutoff, and
# how long an idle waiting-since hash may live.
# Returns {opponent id or '', the caller's first-join time or ''}. Empty strings rather
# than false because a false inside a Lua table truncates the reply array.
PAIR_OR_ENQUEUE = """
local queue, since = KEYS[1], KEYS[2]
local me, now, cutoff, ttl = ARGV[1], ARGV[2], ARGV[3], ARGV[4]

for _, id in ipairs(redis.call('ZRANGEBYSCORE', queue, '-inf', cutoff)) do
  redis.call('ZREM', queue, id)
  redis.call('HDEL', since, id)
end

for _, id in ipairs(redis.call('ZRANGE', queue, 0, -1)) do
  if id ~= me then
    redis.call('ZREM', queue, id, me)
    redis.call('HDEL', since, id, me)
    return {id, ''}
  end
end

redis.call('ZADD', queue, now, me)
redis.call('HSETNX', since, me, now)
-- Refreshed on every join, so only an abandoned queue's hash ever expires. Entries
-- are deleted on pairing, leaving and the stale sweep; this just bounds the damage
-- if the process dies between those.
redis.call('EXPIRE', since, ttl)
return {'', redis.call('HGET', since, me) or now}
"""


def queue_key(game: str) -> str:
    return f"{QUEUE_PREFIX}:{game}"


def waiting_since_key(game: str) -> str:
    return f"{WAITING_SINCE_PREFIX}:{game}"


async def _dequeue(game: str, user_id: int) -> None:
    """Leave a queue, clearing both the sorted set and the waiting-since hash."""
    async with redis.pipeline(transaction=False) as pipe:
        pipe.zrem(queue_key(game), user_id)
        pipe.hdel(waiting_since_key(game), str(user_id))
        await pipe.execute()


def _require_supported(game: str) -> None:
    if game not in SUPPORTED_GAMES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unsupported game")


def _bot_for(db: Session, game: str, waited: float) -> User | None:
    """The computer opponent, once the player has waited long enough for one."""
    if not settings.BOT_OPPONENT_ENABLED or game not in BOT_GAMES:
        return None
    if waited < settings.MATCHMAKING_BOT_WAIT_SECONDS:
        return None
    return crud_user.get_bot(db)


def _queued() -> MatchmakingStatus:
    return MatchmakingStatus(
        status="queued", queue_ttl_seconds=settings.MATCHMAKING_QUEUE_TTL_SECONDS
    )


@router.post("/{game}", response_model=MatchmakingStatus)
async def join_queue(
    game: str,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
) -> MatchmakingStatus:
    """Queue for a game, or start a match with a player already waiting for it.

    Re-joining refreshes the caller's place in the queue. A player queues for one game
    at a time: joining one leaves every other game's queue.
    """
    _require_supported(game)
    async with redis.pipeline(transaction=False) as pipe:
        for other in (g for g in SUPPORTED_GAMES if g != game):
            pipe.zrem(queue_key(other), current_user.id)
            # Clear the other game's wait too, or returning to it later would count
            # the abandoned wait and hand the player a bot straight away.
            pipe.hdel(waiting_since_key(other), str(current_user.id))
        await pipe.execute()

    now = time.time()
    opponent_id, waiting_since = await redis.eval(
        PAIR_OR_ENQUEUE,
        2,
        queue_key(game),
        waiting_since_key(game),
        str(current_user.id),
        str(now),
        str(now - settings.MATCHMAKING_QUEUE_TTL_SECONDS),
        str(settings.MATCHMAKING_QUEUE_TTL_SECONDS * 10),
    )

    # `start_match` makes the waiting player the inviter, who takes the first turn
    # in wordle — so which side waited decides who opens.
    if not opponent_id:
        opponent = _bot_for(db, game, waited=now - float(waiting_since or now))
        if opponent is None:
            return _queued()
        # The bot is in no queue, so only the caller needs removing.
        await _dequeue(game, current_user.id)
        # Nobody came, so the caller is the one who waited: they open against the
        # computer.
        waiting, joining = current_user, opponent
    else:
        opponent = db.get(User, int(opponent_id))
        if opponent is None or not opponent.is_active:
            # The waiting account went away while queued; wait for someone else.
            await redis.zadd(queue_key(game), {str(current_user.id): now})
            return _queued()
        # The player already in the queue waited; the caller has just joined.
        waiting, joining = opponent, current_user

    obj = start_match(db, waiting=waiting, joining=joining, game=game)
    public = crud_match.to_public(db, obj)
    await notify_match_started(obj, public)
    return MatchmakingStatus(
        status="matched",
        match=public,
        queue_ttl_seconds=settings.MATCHMAKING_QUEUE_TTL_SECONDS,
    )


@router.delete("/{game}", status_code=status.HTTP_204_NO_CONTENT)
async def leave_queue(
    game: str,
    current_user: User = Depends(deps.get_current_user),
) -> Response:
    """Leave a game's queue. Safe to call when not queued."""
    _require_supported(game)
    await _dequeue(game, current_user.id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
