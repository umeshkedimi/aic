"""ID generation utilities.

Uses ULID for sortable, unique identifiers that are URL-safe
and contain embedded timestamps.
"""

from uuid import UUID

from ulid import ULID


def generate_id() -> UUID:
    """Generate a new ULID as UUID.

    ULIDs are:
    - Lexicographically sortable
    - Contain embedded timestamps
    - URL-safe
    - Compatible with UUID storage
    """
    return ULID().to_uuid()


def generate_incident_id() -> UUID:
    """Generate a new incident ID."""
    return generate_id()


def generate_analysis_id() -> UUID:
    """Generate a new analysis ID."""
    return generate_id()


def generate_document_id() -> UUID:
    """Generate a new document ID."""
    return generate_id()


def ulid_to_timestamp(ulid_uuid: UUID) -> float:
    """Extract timestamp from a ULID-based UUID.

    Returns Unix timestamp in seconds.
    """
    ulid = ULID.from_uuid(ulid_uuid)
    return ulid.timestamp
