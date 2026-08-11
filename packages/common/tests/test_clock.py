from datetime import UTC, datetime, timedelta

import pytest
from aic_common.clock import FixedClock, SystemClock


def test_system_clock_returns_timezone_aware_utc_now() -> None:
    clock = SystemClock()
    moment = clock.now()
    assert moment.tzinfo is not None
    assert moment.utcoffset() == timedelta(0)


def test_fixed_clock_does_not_advance_on_its_own() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    clock = FixedClock(start)
    assert clock.now() == start
    assert clock.now() == start


def test_fixed_clock_advance() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    clock = FixedClock(start)
    clock.advance(timedelta(seconds=90))
    assert clock.now() == start + timedelta(seconds=90)


def test_fixed_clock_set() -> None:
    clock = FixedClock(datetime(2026, 1, 1, tzinfo=UTC))
    new_moment = datetime(2026, 6, 1, tzinfo=UTC)
    clock.set(new_moment)
    assert clock.now() == new_moment


def test_fixed_clock_rejects_naive_datetime() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        FixedClock(datetime(2026, 1, 1))


def test_fixed_clock_set_rejects_naive_datetime() -> None:
    clock = FixedClock(datetime(2026, 1, 1, tzinfo=UTC))
    with pytest.raises(ValueError, match="timezone-aware"):
        clock.set(datetime(2026, 1, 1))
