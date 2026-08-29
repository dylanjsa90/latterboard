import logging
import os
import pathlib
import sys

# create logs folder if needed
pathlib.Path("logs").mkdir(parents=True, exist_ok=True)

logger = logging.getLogger("uvicorn")
logger.setLevel(logging.INFO)


def config_logging(logger_name="uvicorn"):
    logger = logging.getLogger(logger_name)
    # Create a custom logger
    logger.setLevel(logging.INFO)
    if os.getenv("DEBUG", "0") == "1":
        logger.setLevel(logging.DEBUG)
    
    stdout_handler = logging.StreamHandler(sys.stdout)

    log_format = logging.Formatter(
        fmt="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )
    stdout_handler.setFormatter(log_format)
    logger.addHandler(stdout_handler)

    # Create handlers
    c_handler = logging.StreamHandler()
    f_handler = logging.FileHandler("./logs/logs_error.log")
    f_info_handler = logging.FileHandler("./logs/logs_info.log")
    c_handler.setLevel(logging.INFO)
    f_handler.setLevel(logging.ERROR)
    f_info_handler.setLevel(logging.INFO)

    # Create formatters and add it to handlers
    c_format = logging.Formatter("%(name)s - %(levelname)s - %(message)s")
    f_format = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    c_handler.setFormatter(c_format)
    f_handler.setFormatter(f_format)
    f_info_handler.setFormatter(f_format)

    # Add handlers to the logger
    logger.addHandler(c_handler)
    logger.addHandler(f_handler)
    logger.addHandler(f_info_handler)
