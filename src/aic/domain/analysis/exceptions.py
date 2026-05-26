"""Analysis domain exceptions."""


class AnalysisError(Exception):
    """Base exception for analysis domain errors."""

    pass


class AnalysisGenerationError(AnalysisError):
    """Raised when analysis generation fails."""

    def __init__(self, message: str, cause: Exception | None = None):
        self.cause = cause
        super().__init__(message)


class AnalysisNotFoundError(AnalysisError):
    """Raised when an analysis cannot be found."""

    pass
