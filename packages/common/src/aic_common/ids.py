"""UUIDv7 (RFC 9562) generation.

Time-ordered UUIDs keep primary-key indexes append-mostly instead of randomly
scattered, which matters once tables like ``incident_event`` grow large. The
stdlib does not ship ``uuid.uuid7`` until 3.14, and this workspace targets
3.13, so it is implemented directly here rather than pulling in a dependency
for ~15 lines of bit-twiddling.
"""

from __future__ import annotations

import os
import time
import uuid

_VERSION_BITS = 0x7 << 76
_VARIANT_BITS = 0b10 << 62
_RAND_A_MASK = 0x0FFF
_RAND_B_MASK = (1 << 62) - 1


def new_id() -> uuid.UUID:
    """Generate a fresh, time-ordered UUIDv7."""
    unix_ts_ms = int(time.time() * 1000)
    rand_bytes = os.urandom(10)
    rand_a = int.from_bytes(rand_bytes[0:2], "big") & _RAND_A_MASK
    rand_b = int.from_bytes(rand_bytes[2:10], "big") & _RAND_B_MASK

    uuid_int = (unix_ts_ms << 80) | _VERSION_BITS | (rand_a << 64) | _VARIANT_BITS | rand_b
    return uuid.UUID(int=uuid_int)


def new_id_str() -> str:
    """Generate a fresh UUIDv7 as its canonical string form."""
    return str(new_id())
