"""The one place that calls into wall-clock time.

Code that needs "now" takes a Clock instead of calling datetime.now()
directly, so tests can inject a FixedClock instead of sleeping or freezing
global time.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol


class Clock(Protocol):
    def now(self) -> datetime: ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


class FixedClock:
    def __init__(self, fixed: datetime) -> None:
        self._fixed = fixed

    def now(self) -> datetime:
        return self._fixed
