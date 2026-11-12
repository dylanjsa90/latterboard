"""What the server writes to its log (stdout, which journald or Docker collects)."""

import logging
from collections.abc import Iterator

import pytest
from loguru import logger
from pydantic import ValidationError

from app.core.config import Settings, settings
from app.init_logger import setup_loguru


def _capture() -> Iterator[list[str]]:
    messages: list[str] = []
    sink_id = logger.add(messages.append, format="{name}:{function} | {message}")
    yield messages
    logger.remove(sink_id)


@pytest.fixture
def log_messages() -> Iterator[list[str]]:
    """Messages loguru emits during the test. Request it after `client`, whose startup
    runs setup_loguru, which removes every sink."""
    yield from _capture()


@pytest.fixture
def configured_logging() -> Iterator[list[str]]:
    """Messages emitted after setup_loguru, configured as the server runs it."""
    setup_loguru()
    yield from _capture()


def _uvicorn_websocket_line(path: str) -> None:
    # Uvicorn's own line for an accepted websocket, with the path and query string.
    logging.getLogger("uvicorn.error").info(
        '%s - "WebSocket %s" [accepted]', "203.0.113.7:51234", path
    )


def test_url_token_is_not_logged(configured_logging):
    _uvicorn_websocket_line("/ws/wordle?token=eyJhbGciOi.secret-part.sig&x=1")

    logged = "".join(configured_logging)
    assert "WebSocket /ws/wordle?token=" in logged
    assert "secret-part" not in logged
    assert "&x=1" in logged


def test_uvicorn_lines_are_logged_once(configured_logging):
    _uvicorn_websocket_line("/ws/wordle")

    assert len([m for m in configured_logging if "WebSocket /ws/wordle" in m]) == 1


def test_stdlib_lines_name_their_caller(configured_logging):
    logging.getLogger("some.library").warning("from the stdlib")

    (line,) = [m for m in configured_logging if "from the stdlib" in m]
    assert line.startswith("tests.test_logging:test_stdlib_lines_name_their_caller")


def test_websocket_join_log_names_the_topic(client, auth_headers, log_messages):
    with client.websocket_connect("/ws/wordle", headers=auth_headers):
        pass

    assert any("Websocket joined topic wordle (1 on topic)" in m for m in log_messages)


def test_log_level_setting_filters_stdout(monkeypatch, capsys):
    monkeypatch.setattr(settings, "LOG_LEVEL", "WARNING")
    setup_loguru()
    try:
        logger.info("quiet line")
        logger.warning("loud line")
        logger.complete()
        out = capsys.readouterr().out
    finally:
        # The sink holds pytest's capture stream; don't leave it for later tests.
        logger.remove()

    assert "loud line" in out
    assert "quiet line" not in out


def test_unknown_log_level_is_refused():
    with pytest.raises(ValidationError, match="LOG_LEVEL"):
        Settings(LOG_LEVEL="verbose")
