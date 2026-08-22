"""Deterministic severity rule table (design doc §1.4 TRIAGE row: "severity
comes from a rule table... checkout-service + prod + >=2 correlated signals
-> SEV2"). No LLM in this path: classifying from labels the correlator
already produced is a lookup, not a judgment call.

Design call the doc doesn't fully pin down (documented here rather than
blocking on it, per CLAUDE.local.md's guidance): the one worked example
names `checkout-service` — the scenario's customer-facing edge — explicitly,
implying severity should weight *which* service is affected, not just
environment and signal count. The table below keys on `(service,
environment)` with signal-count bands per key, and falls back to a
conservative environment-only default for any service not explicitly
catalogued, so a future service can never hit an unhandled case.
"""

from __future__ import annotations

from aic_common.config import Environment

from aic_domain.enums import Severity

# Ordered highest-severity-first; the first threshold `signal_count` meets
# or exceeds wins. Every band list must end with a threshold of 1 so a
# match always exists for any signal_count >= 1.
_SeverityBands = list[tuple[int, Severity]]

_DEFAULT_BANDS: dict[Environment, _SeverityBands] = {
    Environment.PROD: [(2, Severity.SEV3), (1, Severity.SEV4)],
    Environment.STAGING: [(1, Severity.SEV4)],
    Environment.LOCAL: [(1, Severity.SEV4)],
}

_SEVERITY_TABLE: dict[str, dict[Environment, _SeverityBands]] = {
    "checkout-service": {
        Environment.PROD: [(3, Severity.SEV1), (2, Severity.SEV2), (1, Severity.SEV3)],
        Environment.STAGING: [(2, Severity.SEV3), (1, Severity.SEV4)],
        Environment.LOCAL: [(1, Severity.SEV4)],
    },
}


def assess_severity(service: str, environment: Environment, signal_count: int) -> Severity:
    """Look up severity for a correlated incident. Pure, zero-I/O, exhaustive
    over the closed `Severity`/`Environment` value sets."""
    if signal_count < 1:
        raise ValueError("signal_count must be >= 1")

    bands = _SEVERITY_TABLE.get(service, _DEFAULT_BANDS)[environment]
    for threshold, severity in bands:
        if signal_count >= threshold:
            return severity
    raise AssertionError(
        f"no severity band matched signal_count={signal_count}"
    )  # pragma: no cover
