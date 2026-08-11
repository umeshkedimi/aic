"""Process-wide structured JSON logging, built on structlog.

Call :func:`configure_logging` once at process startup (a service's
entrypoint, a worker's main, or an integration test's fixture). Library and
domain code should never call it — only :func:`get_logger`.
"""

from __future__ import annotations

import logging
import sys

import structlog
from structlog.typing import FilteringBoundLogger

from aic_common.config import LogLevel


def configure_logging(level: LogLevel = LogLevel.INFO) -> None:
    """Configure structlog to emit one JSON object per line to stdout."""
    numeric_level = logging.getLevelNamesMapping()[level.value]
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(numeric_level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str) -> FilteringBoundLogger:
    """Return a structured logger bound to ``name`` (typically ``__name__``)."""
    logger: FilteringBoundLogger = structlog.get_logger(name)
    return logger
