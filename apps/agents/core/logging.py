import sys
import os
from loguru import logger


_LOG_FMT = (
    "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
    "<level>{level: <8}</level> | "
    "<cyan>{name}</cyan>:<cyan>{line}</cyan> | "
    "{message}"
)


def setup_logging() -> None:
    log_level = os.getenv("LOG_LEVEL", "INFO").upper()

    logger.remove()
    logger.add(sys.stderr, format=_LOG_FMT, level=log_level, colorize=True, enqueue=True)

    log_file = os.getenv("LOG_FILE")
    if log_file:
        logger.add(
            log_file,
            format=_LOG_FMT,
            level=log_level,
            rotation="10 MB",
            retention="7 days",
            colorize=False,
            enqueue=True,
        )
