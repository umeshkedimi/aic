"""Base error hierarchy shared across all AIC packages and services.

Package- and domain-specific errors (e.g. ``IllegalTransition`` in
``aic_domain``) should subclass these rather than raising bare exceptions, so
callers at any layer can catch ``AICError`` and get a predictable shape.
"""

from __future__ import annotations


class AICError(Exception):
    """Base class for every error raised intentionally by AIC code."""


class NotFoundError(AICError):
    """A requested entity does not exist."""


class ValidationError(AICError):
    """Input failed validation before any state was changed."""


class IllegalStateError(AICError):
    """An operation was attempted that the current state does not allow."""


class ConfigurationError(AICError):
    """Required configuration is missing or invalid."""


class ExternalServiceError(AICError):
    """A call to an external system (HTTP, DB, LLM, Kafka, ...) failed.

    Distinct from the above: this always represents a failure whose cause is
    outside this process, which matters for retry/backoff decisions at the
    call site.
    """
