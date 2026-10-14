import logging
import sys

from loguru import logger


class InterceptHandler(logging.Handler):
    def emit(self, record: logging.LogRecord):
        try:
            level = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno

        frame, depth = logging.currentframe(), 2
        while frame.f_code.co_filename == logging.__file__:
            frame = frame.f_back
            depth += 1

        logger.opt(depth=depth, exception=record.exc_info).log(
            level, record.getMessage()
        )


def setup_loguru():
    # Remove default handler
    logger.remove()

    # Add console sink with custom formatting and thread-safe queue
    logger.add(
        sys.stdout,
        format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | {name}:{function}:{line} - {message}",
        level="INFO",
        enqueue=True,
        backtrace=True,
        diagnose=True,
    )

    # Optional: Add file output with rotation and retention
    logger.add(
        "app.log",
        rotation="10 MB",
        retention="20 days",
        level="INFO",
        enqueue=True,
        serialize=False,  # Set to True if you want JSON logs
    )

    # Intercept standard library logging
    logging.basicConfig(handlers=[InterceptHandler()], level=0, force=True)

    # Intercept Uvicorn loggers
    for _logger in ("uvicorn", "uvicorn.access", "uvicorn.error", "fastapi"):
        logging_logger = logging.getLogger(_logger)
        logging_logger.handlers = [InterceptHandler()]
