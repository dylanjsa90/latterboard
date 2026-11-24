"""Fixed-window rate limits in Redis, keyed by network rather than address.

A window's count starts at the first hit and the key expires when the window ends. When
Redis is unreachable every check passes: a limit going quiet is better than the feature
it guards going down.
"""

import ipaddress

from loguru import logger
from redis.exceptions import RedisError

from app.core.redis_client import redis_client as redis


def network_of(host: str | None) -> str:
    """The network a client address belongs to: an IPv4 address as given, an IPv6
    address as its /64 (one device rotates privacy addresses within it), and anything
    that isn't an address as given."""
    if host is None:
        return "unknown"
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return host
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped is not None:
            return str(ip.ipv4_mapped)
        return str(ipaddress.IPv6Network(f"{ip}/64", strict=False))
    return str(ip)


async def allow(key: str, limit: int, window_seconds: int) -> bool:
    """Count one hit against `key` and return whether the window's count is within
    `limit`. True when Redis is down."""
    try:
        async with redis.pipeline(transaction=True) as pipe:
            pipe.set(key, 0, ex=window_seconds, nx=True)
            pipe.incr(key)
            _, count = await pipe.execute()
    except RedisError:
        logger.warning("Rate limit check skipped: Redis is unavailable")
        return True
    return int(count) <= limit
