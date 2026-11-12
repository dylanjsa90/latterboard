import logging
import re
import sys

from loguru import logger

from app.core.config import settings

# The websocket sends its JWT as ?token=..., and Uvicorn logs paths with the query string.
_URL_TOKEN = re.compile(r"(token=)[^&\s\"]+")


class InterceptHandler(logging.Handler):
    """Sends stdlib `logging` records (uvicorn, SQLAlchemy, ...) through loguru."""

    def emit(self, record: logging.LogRecord) -> None:
        level: str | int
        try:
            level = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno

        # Skip this frame and logging's own, so loguru names the code that logged.
        frame, depth = sys._getframe(1), 1
        while frame.f_code.co_filename == logging.__file__ and frame.f_back:
            frame = frame.f_back
            depth += 1

        message = _URL_TOKEN.sub(r"\1[redacted]", record.getMessage())
        logger.opt(depth=depth, exception=record.exc_info).log(level, message)


def setup_loguru() -> None:
    logger.remove()
    # stdout only: journald (systemd) or Docker collects it and handles rotation.
    logger.add(
        sys.stdout,
        format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | {name}:{function}:{line} - {message}",
        level=settings.LOG_LEVEL,
        enqueue=True,
        backtrace=True,
        # Prints local variables in tracebacks, which can include secrets and tokens.
        diagnose=settings.APP_ENV == "local",
    )

    logging.basicConfig(handlers=[InterceptHandler()], level=0, force=True)
    # Uvicorn's loggers have their own handlers, and uvicorn.error also propagates to
    # uvicorn, so each line was written twice. Hand everything to the root handler.
    for name in ("uvicorn", "uvicorn.access", "uvicorn.error", "fastapi"):
        logging.getLogger(name).handlers = []
        logging.getLogger(name).propagate = True
