from aic_common.errors import ConflictError


class IllegalTransition(ConflictError):
    """Raised when an event is not valid for the incident's current status."""

    def __init__(self, current: str, event: str) -> None:
        super().__init__(f"cannot apply event {event!r} while incident is {current!r}")
        self.current = current
        self.event = event
