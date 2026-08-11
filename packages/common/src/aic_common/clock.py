"""Injectable clock so time-dependent code is deterministically testable."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Protocol, runtime_checkable


@runtime_checkable
class Clock(Protocol):
    """Anything that can report the current UTC time."""

    def now(self) -> datetime: ...


class SystemClock:
    """Wall-clock time. The default in production code."""

    def now(self) -> datetime:
        return datetime.now(UTC)


class FixedClock:
    """A clock that only advances when told to. For tests."""

    def __init__(self, initial: datetime | None = None) -> None:
        self._current = initial if initial is not None else datetime.now(UTC)
        if self._current.tzinfo is None:
            raise ValueError("FixedClock requires a timezone-aware datetime")

    def now(self) -> datetime:
        return self._current

    def advance(self, delta: timedelta) -> None:
        self._current = self._current + delta

    def set(self, moment: datetime) -> None:
        if moment.tzinfo is None:
            raise ValueError("FixedClock requires a timezone-aware datetime")
        self._current = moment
