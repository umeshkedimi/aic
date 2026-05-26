"""Time utilities for consistent datetime handling."""

from datetime import UTC, datetime


def utc_now() -> datetime:
    """Get current UTC datetime with timezone info."""
    return datetime.now(UTC)


def format_iso(dt: datetime) -> str:
    """Format datetime as ISO 8601 string."""
    return dt.isoformat()


def parse_iso(s: str) -> datetime:
    """Parse ISO 8601 string to datetime."""
    return datetime.fromisoformat(s)
