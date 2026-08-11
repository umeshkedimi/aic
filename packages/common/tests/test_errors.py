import pytest
from aic_common.errors import (
    AICError,
    ConfigurationError,
    ExternalServiceError,
    IllegalStateError,
    NotFoundError,
    ValidationError,
)


@pytest.mark.parametrize(
    "error_cls",
    [NotFoundError, ValidationError, IllegalStateError, ConfigurationError, ExternalServiceError],
)
def test_every_base_error_subclasses_aic_error(error_cls: type[AICError]) -> None:
    assert issubclass(error_cls, AICError)
    assert issubclass(error_cls, Exception)


def test_aic_error_is_catchable_as_exception() -> None:
    with pytest.raises(AICError):
        raise NotFoundError("incident 123 not found")
