"""Warns when the event loop stalls.

Production runs one Uvicorn worker, so its one event loop serves every request,
websocket, heartbeat and the Redis listener. Anything blocking on it (a database
call in an `async def`, say) freezes all of them at once, and nothing else would
show it. This sleeps on the loop and logs when it wakes up late.
"""

import asyncio

from loguru import logger

INTERVAL_SECONDS = 0.5
WARN_AFTER_SECONDS = 0.1


async def watch_loop_lag(
    interval: float = INTERVAL_SECONDS, warn_after: float = WARN_AFTER_SECONDS
) -> None:
    """Run until cancelled, warning whenever a sleep overruns by more than `warn_after`."""
    loop = asyncio.get_running_loop()
    while True:
        started = loop.time()
        await asyncio.sleep(interval)
        lag = loop.time() - started - interval
        if lag > warn_after:
            logger.warning("Event loop stalled for {:.0f} ms", lag * 1000)
