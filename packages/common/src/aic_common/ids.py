"""Time-ordered IDs for every persisted row.

UUIDv7 sorts by creation time, so primary-key indexes grow append-only
instead of taking random-insert hits the way UUIDv4 does at incident-event
volume.
"""

from uuid import UUID

from uuid6 import uuid7


def new_id() -> UUID:
    return uuid7()
