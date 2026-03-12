"""
Logging configuration utilities.

This module centralises logging configuration for the application.
The ``init_logging`` function should be called at startup to
configure the root logger and any third‑party loggers used by the
application. By default, logs are emitted to stdout with timestamps
and log levels, but the configuration can be customised easily.
"""

import logging
from typing import Optional


def init_logging(level: str = "INFO") -> None:
    """Initialise basic logging configuration.

    The root logger and common third‑party loggers (e.g. for
    ``sqlalchemy``) are configured to emit messages at the specified
    level. Log messages include timestamps, the logger name and the
    severity level.

    Parameters
    ----------
    level: str
        The minimum severity of messages to emit. Valid values are
        ``DEBUG``, ``INFO``, ``WARNING``, ``ERROR`` and ``CRITICAL``.
    """

    numeric_level: Optional[int] = getattr(logging, level.upper(), None)
    if not isinstance(numeric_level, int):
        raise ValueError(f"Invalid log level: {level}")

    # Configure the root logger
    logging.basicConfig(
        level=numeric_level,
        format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Reduce verbosity of noisy libraries
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.INFO)