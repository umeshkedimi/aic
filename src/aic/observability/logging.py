"""Structured logging configuration using structlog.

Provides consistent, JSON-formatted logs with automatic context enrichment
including correlation IDs, service name, and environment.
"""

import logging
import sys
from typing import Any

import structlog
from structlog.types import Processor

from aic.config import Settings


def setup_logging(settings: Settings) -> None:
    """Configure structured logging for the application.

    Sets up structlog with JSON output for production and
    colored console output for development.
    """
    shared_processors: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.stdlib.ExtraAdder(),
    ]

    if settings.is_development:
        # Development: colored console output
        processors: list[Processor] = [
            *shared_processors,
            structlog.dev.ConsoleRenderer(colors=True),
        ]
    else:
        # Production: JSON output
        processors = [
            *shared_processors,
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ]

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, settings.log_level)
        ),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=True,
    )

    # Configure standard library logging to use structlog
    logging.basicConfig(
        format="%(message)s",
        level=getattr(logging, settings.log_level),
        handlers=[logging.StreamHandler(sys.stdout)],
        force=True,
    )

    # Quiet noisy loggers
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)


def get_logger(name: str | None = None, **initial_context: Any) -> structlog.BoundLogger:
    """Get a structured logger instance.

    Args:
        name: Logger name (typically __name__)
        **initial_context: Initial context to bind to the logger

    Returns:
        A bound structlog logger
    """
    logger = structlog.get_logger(name)
    if initial_context:
        logger = logger.bind(**initial_context)
    return logger


def bind_contextvars(**context: Any) -> None:
    """Bind context variables for the current async context.

    These will be included in all log messages within this context.
    """
    structlog.contextvars.bind_contextvars(**context)


def clear_contextvars() -> None:
    """Clear all context variables."""
    structlog.contextvars.clear_contextvars()
