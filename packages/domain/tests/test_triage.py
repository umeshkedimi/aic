import pytest
from aic_common.config import Environment
from aic_domain.enums import Severity
from aic_domain.triage import assess_severity


@pytest.mark.parametrize(
    ("service", "environment", "signal_count", "expected"),
    [
        # Design doc's own worked example.
        ("checkout-service", Environment.PROD, 2, Severity.SEV2),
        ("checkout-service", Environment.PROD, 3, Severity.SEV1),
        ("checkout-service", Environment.PROD, 5, Severity.SEV1),
        ("checkout-service", Environment.PROD, 1, Severity.SEV3),
        ("checkout-service", Environment.STAGING, 2, Severity.SEV3),
        ("checkout-service", Environment.STAGING, 1, Severity.SEV4),
        ("checkout-service", Environment.LOCAL, 1, Severity.SEV4),
        ("checkout-service", Environment.LOCAL, 10, Severity.SEV4),
        # A service not in the catalog falls back to the conservative
        # environment-only default rather than raising.
        ("some-future-service", Environment.PROD, 2, Severity.SEV3),
        ("some-future-service", Environment.PROD, 1, Severity.SEV4),
        ("some-future-service", Environment.STAGING, 5, Severity.SEV4),
        ("some-future-service", Environment.LOCAL, 1, Severity.SEV4),
    ],
)
def test_assess_severity_rule_table(
    service: str, environment: Environment, signal_count: int, expected: Severity
) -> None:
    assert assess_severity(service, environment, signal_count) == expected


def test_assess_severity_rejects_zero_signals() -> None:
    with pytest.raises(ValueError, match="signal_count must be >= 1"):
        assess_severity("checkout-service", Environment.PROD, 0)
