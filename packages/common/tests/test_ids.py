import time
import uuid

from aic_common.ids import new_id, new_id_str


def test_new_id_is_valid_uuid() -> None:
    generated = new_id()
    assert isinstance(generated, uuid.UUID)


def test_new_id_has_version_7_and_rfc_variant() -> None:
    generated = new_id()
    assert generated.version == 7
    assert generated.variant == uuid.RFC_4122


def test_new_id_str_round_trips() -> None:
    as_str = new_id_str()
    assert uuid.UUID(as_str).version == 7


def test_new_id_is_unique() -> None:
    ids = {new_id() for _ in range(10_000)}
    assert len(ids) == 10_000


def test_new_id_is_time_ordered() -> None:
    first = new_id()
    time.sleep(0.005)
    second = new_id()
    # UUIDv7's leading 48 bits are a millisecond timestamp, so later ids sort higher.
    assert first.int < second.int
