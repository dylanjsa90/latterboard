import asyncio
import json
import logging
import re
from uuid import uuid4

from fastapi import WebSocket
from redis import asyncio as aioredis

from app.core.config import settings

logger = logging.getLogger("uvicorn")

# Every worker publishes to and subscribes from this one channel, so a broadcast
# issued on any worker reaches clients held by all of them.
BROADCAST_CHANNEL = "ws:broadcast"

# Each worker keeps its own per-topic tally under ws:presence:{topic}:{worker_id}, so a
# topic's real total is the sum across workers. Keys carry a TTL and are refreshed on
# every heartbeat, which is what evicts the tally of a worker that died without cleanup.
PRESENCE_PREFIX = "ws:presence"

# Mirrors PRESENCE_PREFIX but holds each worker's set of connected usernames per topic
# under ws:usernames:{topic}:{worker_id}, so the full list is the union across workers.
USERNAMES_PREFIX = "ws:usernames"


def _escape_glob(value: str) -> str:
    """Neutralise glob metacharacters so a topic name can't widen a SCAN match."""
    return re.sub(r"([\\*?\[\]])", r"\\\1", value)


class ConnectionManager:
    """Tracks the websockets held by this process and fans messages out over Redis."""

    def __init__(self) -> None:
        self._topics: dict[str, dict[WebSocket, str]] = {}
        self._redis: aioredis.Redis | None = None
        self._pubsub = None
        self._listener_task: asyncio.Task | None = None
        self._heartbeat_task: asyncio.Task | None = None
        self._worker_id = uuid4().hex
        # Topics we have a presence key for, so we know which ones to delete once we
        # no longer hold any connection on them.
        self._published_topics: set[str] = set()

    async def start(self) -> None:
        """Subscribe to the broadcast channel and start the heartbeat. Call from lifespan."""
        self._redis = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
        self._pubsub = self._redis.pubsub(ignore_subscribe_messages=True)
        await self._pubsub.subscribe(BROADCAST_CHANNEL)
        self._listener_task = asyncio.create_task(self._listen())
        self._heartbeat_task = asyncio.create_task(self._heartbeat())
        logger.info("ConnectionManager started")

    async def stop(self) -> None:
        for task in (self._listener_task, self._heartbeat_task):
            if task is not None:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        self._listener_task = None
        self._heartbeat_task = None
        if self._pubsub is not None:
            await self._pubsub.aclose()
            self._pubsub = None
        if self._redis is not None:
            # Retire our presence keys now rather than leaving them to expire, so a
            # clean shutdown doesn't overstate the count for a heartbeat or three.
            self._topics.clear()
            await self._sync_presence()
            await self._redis.aclose()
            self._redis = None
        logger.info("ConnectionManager stopped")

    async def connect(self, websocket: WebSocket, topic: str, username: str) -> None:
        """Accept the handshake and subscribe the connection to a topic."""
        await websocket.accept()
        await self.join(websocket, topic, username)

    async def join(self, websocket: WebSocket, topic: str, username: str) -> None:
        """Subscribe an already-accepted connection to an additional topic."""
        self._topics.setdefault(topic, {})[websocket] = username
        logger.info("Websocket joined topic %s (%d on topic)", topic, len(self._topics[topic]))
        await self._sync_presence()

    async def disconnect(self, websocket: WebSocket, topic: str) -> None:
        """Unsubscribe the connection from a topic. Safe to call more than once."""
        connections = self._topics.get(topic)
        if connections is None:
            return
        connections.pop(websocket, None)
        if not connections:
            del self._topics[topic]
        await self._sync_presence()

    async def count(self, topic: str) -> int:
        """Total active connections on a topic, summed across every worker.

        A worker that dies without cleanup keeps inflating this until its presence keys
        expire, so treat the number as accurate to within one heartbeat interval.
        """
        if self._redis is None:
            raise RuntimeError("ConnectionManager.start() has not been called")
        pattern = f"{PRESENCE_PREFIX}:{_escape_glob(topic)}:*"
        keys = [key async for key in self._redis.scan_iter(match=pattern)]
        if not keys:
            return 0
        return sum(int(value) for value in await self._redis.mget(keys) if value)

    async def usernames(self, topic: str) -> list[str]:
        """Distinct usernames currently connected to a topic, unioned across every worker.

        Subject to the same staleness window as count(): a worker that dies without
        cleanup keeps its usernames listed until its key expires.
        """
        if self._redis is None:
            raise RuntimeError("ConnectionManager.start() has not been called")
        pattern = f"{USERNAMES_PREFIX}:{_escape_glob(topic)}:*"
        keys = [key async for key in self._redis.scan_iter(match=pattern)]
        if not keys:
            return []
        return sorted(await self._redis.sunion(keys))

    def _presence_key(self, topic: str) -> str:
        return f"{PRESENCE_PREFIX}:{topic}:{self._worker_id}"

    def _usernames_key(self, topic: str) -> str:
        return f"{USERNAMES_PREFIX}:{topic}:{self._worker_id}"

    async def _sync_presence(self) -> None:
        """Write this worker's per-topic counts and usernames to Redis, dropping topics
        it has left.

        Never raises: presence is a read-only convenience, and a Redis blip must not
        take down a live connection by propagating out of connect/disconnect.
        """
        if self._redis is None:
            return
        try:
            ttl = settings.WS_HEARTBEAT_SECONDS * 3
            async with self._redis.pipeline(transaction=False) as pipe:
                for topic, connections in self._topics.items():
                    pipe.set(self._presence_key(topic), len(connections), ex=ttl)
                    usernames_key = self._usernames_key(topic)
                    pipe.delete(usernames_key)
                    distinct_usernames = set(connections.values())
                    if distinct_usernames:
                        pipe.sadd(usernames_key, *distinct_usernames)
                        pipe.expire(usernames_key, ttl)
                for topic in self._published_topics - set(self._topics):
                    pipe.delete(self._presence_key(topic))
                    pipe.delete(self._usernames_key(topic))
                await pipe.execute()
            self._published_topics = set(self._topics)
        except Exception:
            logger.exception("Failed to sync presence counts")

    async def disconnect_all(self, websocket: WebSocket) -> None:
        """Remove a connection from every topic it currently holds. Safe to call more than once."""
        for topic in [t for t, conns in self._topics.items() if websocket in conns]:
            await self.disconnect(websocket, topic)

    async def send_to_user(self, user_id: int, message: dict) -> None:
        """Deliver a message only to the given user's connection(s)."""
        await self.broadcast(message, topic=f"user:{user_id}")

    async def broadcast(self, message: dict, topic: str | None = None) -> None:
        """Publish a message to one topic, or to every active connection when topic is None."""
        if self._redis is None:
            raise RuntimeError("ConnectionManager.start() has not been called")
        await self._redis.publish(
            BROADCAST_CHANNEL, json.dumps({"topic": topic, "message": message})
        )

    async def _listen(self) -> None:
        """Deliver messages published by any worker to this worker's connections."""
        try:
            async for event in self._pubsub.listen():
                try:
                    payload = json.loads(event["data"])
                    await self._deliver(payload["message"], payload["topic"])
                except (ValueError, KeyError):
                    logger.exception("Discarding malformed broadcast payload")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.exception("Broadcast listener stopped", e)
    async def _heartbeat(self) -> None:
        """Send an app-level ping so clients (and we) can tell a live socket from a stale one."""
        try:
            while True:
                await asyncio.sleep(settings.WS_HEARTBEAT_SECONDS)
                await self._deliver({"type": "heartbeat"}, None)
                # Refresh the TTL on our presence keys, and pick up any counts the
                # heartbeat's own pruning just changed.
                await self._sync_presence()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Heartbeat stopped")

    async def _deliver(self, message: dict, topic: str | None) -> None:
        """Write to this worker's sockets, dropping any that fail."""
        if topic is None:
            targets = list(self._topics.items())
        else:
            targets = [(topic, self._topics.get(topic, set()))]

        for name, connections in targets:
            for websocket in list(connections):
                try:
                    await websocket.send_json(message)
                except Exception:
                    logger.warning("Dropping unreachable websocket on topic %s", name)
                    await self.disconnect(websocket, name)


manager = ConnectionManager()
