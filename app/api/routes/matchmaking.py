"""Automatic matchmaking: players queue for a game and are paired with whoever else is
waiting for it.

Each game's queue is a Redis sorted set of user ids, scored by when the player last
joined. Clients re-join periodically to stay queued; entries older than
MATCHMAKING_QUEUE_TTL_SECONDS are dropped, so a player who closes the tab leaves the
queue on their own.
"""

import time

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.orm import Session

from app.api import deps
from app.api.match_start import notify_match_started, start_match
from app.api.routes.matches import SUPPORTED_GAMES
from app.core.config import settings
from app.crud.match import match as crud_match
from app.models import User
from app.redis import r as redis
from app.schemas.matchmaking import MatchmakingStatus

router = APIRouter(prefix="/matchmaking", tags=["matchmaking"])

QUEUE_PREFIX = "mm:queue"

# Pairs the caller with the longest-waiting other player, or queues them if nobody else
# is waiting. A script so two players joining at once can't both pop the same opponent.
# KEYS[1]: the queue. ARGV: caller id, now, stale cutoff. Returns the opponent id or nil.
PAIR_OR_ENQUEUE = """
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', ARGV[3])
for _, id in ipairs(redis.call('ZRANGE', KEYS[1], 0, -1)) do
  if id ~= ARGV[1] then
    redis.call('ZREM', KEYS[1], id, ARGV[1])
    return id
  end
end
redis.call('ZADD', KEYS[1], ARGV[2], ARGV[1])
return false
"""


def queue_key(game: str) -> str:
    return f"{QUEUE_PREFIX}:{game}"


def _require_supported(game: str) -> None:
    if game not in SUPPORTED_GAMES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unsupported game")


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
    others = [queue_key(g) for g in SUPPORTED_GAMES if g != game]
    async with redis.pipeline(transaction=False) as pipe:
        for key in others:
            pipe.zrem(key, current_user.id)
        await pipe.execute()

    now = time.time()
    opponent_id = await redis.eval(
        PAIR_OR_ENQUEUE,
        1,
        queue_key(game),
        str(current_user.id),
        str(now),
        str(now - settings.MATCHMAKING_QUEUE_TTL_SECONDS),
    )
    if opponent_id is None:
        return _queued()

    opponent = db.get(User, int(opponent_id))
    if opponent is None or not opponent.is_active:
        # The waiting account went away while queued; wait for someone else instead.
        await redis.zadd(queue_key(game), {str(current_user.id): now})
        return _queued()

    obj = start_match(db, waiting=opponent, joining=current_user, game=game)
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
    await redis.zrem(queue_key(game), current_user.id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
