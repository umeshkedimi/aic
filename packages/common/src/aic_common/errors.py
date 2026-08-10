"""Base error taxonomy shared across services.

Every domain/infra error type in the platform derives from one of these so
API and worker error handlers can map on type, not on string matching.
"""


class AicError(Exception):
    """Base for all errors raised by AIC code (as opposed to library errors)."""


class NotFoundError(AicError):
    """A requested entity does not exist."""


class ConflictError(AicError):
    """The requested operation conflicts with current state (e.g. illegal transition)."""


class ValidationError(AicError):
    """Input failed semantic validation beyond schema shape."""
