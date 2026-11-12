import redis.asyncio as redis

from app.core.config import settings

# The shared async client for one-off commands (matchmaking queues). The websocket
# ConnectionManager keeps its own, since its pub/sub needs different socket settings.
redis_client = redis.Redis.from_url(settings.REDIS_URL, decode_responses=True)
