"""The event loop watchdog in `app/core/loop_lag.py`."""

import asyncio
import contextlib
import time

from loguru import logger

from app.core.loop_lag import watch_loop_lag


async def _run_watchdog(block_seconds: float) -> None:
    watchdog = asyncio.create_task(watch_loop_lag(interval=0.01, warn_after=0.1))
    await asyncio.sleep(0.02)
    time.sleep(block_seconds)  # holds the loop, as a sync database call would
    await asyncio.sleep(0.05)
    watchdog.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await watchdog


def _messages_while(block_seconds: float) -> list[str]:
    messages: list[str] = []
    sink_id = logger.add(messages.append, format="{message}")
    try:
        asyncio.run(_run_watchdog(block_seconds))
    finally:
        logger.remove(sink_id)
    return messages


def test_a_blocked_loop_is_logged():
    messages = _messages_while(0.3)
    assert any(m.startswith("Event loop stalled for") for m in messages)


def test_a_free_loop_logs_nothing():
    assert not any("stalled" in m for m in _messages_while(0))
