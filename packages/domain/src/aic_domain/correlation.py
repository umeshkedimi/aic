"""Dependency-graph correlation grouping (design doc §1.4 CORRELATE row).

Pure and zero-I/O like the rest of `aic_domain`: given the static
`ServiceDependency` edges, deterministically compute which "correlation
group" a service belongs to. Both `aic-ingest` (Kafka partition key, so all
alerts for one group are strictly ordered within a partition — ADR 0002)
and `aic-correlator` (incident grouping) call this same function, so the
two processes can never disagree about which alerts belong together.

`DEFAULT_SERVICE_DEPENDENCIES` is the single source of truth for the
signature scenario's one static edge (`checkout-service depends_on
payment-service`): `aic_database.seed` seeds the `service_dependency` table
from it for `aic-correlator` to read back at startup, and `aic-ingest`
imports it directly since it never touches Postgres (see the design note in
`infra/kind/eventbus/redpanda.yaml`). One Python constant, not two that can
drift.
"""

from __future__ import annotations

from collections.abc import Iterable

from aic_domain.models import ServiceDependency

DEFAULT_SERVICE_DEPENDENCIES: tuple[tuple[str, str], ...] = (
    ("checkout-service", "payment-service"),
)


class ServiceDependencyGraph:
    """Undirected connectivity over `ServiceDependency` edges. Two services
    correlate if they're connected by any chain of `depends_on` edges,
    regardless of direction — a `checkout-service` alert and a
    `payment-service` alert should group together no matter which one
    Alertmanager happens to label the alert with."""

    def __init__(self, edges: Iterable[ServiceDependency]) -> None:
        adjacency: dict[str, set[str]] = {}
        for edge in edges:
            adjacency.setdefault(edge.service, set()).add(edge.depends_on)
            adjacency.setdefault(edge.depends_on, set()).add(edge.service)
        self._adjacency = adjacency

    @classmethod
    def from_pairs(cls, pairs: Iterable[tuple[str, str]]) -> ServiceDependencyGraph:
        return cls(
            ServiceDependency(service=service, depends_on=depends_on)
            for service, depends_on in pairs
        )

    def group_key(self, service: str) -> str:
        """A stable identifier for `service`'s correlation group: every
        service reachable from it via dependency edges, canonicalized as
        the alphabetically-first member so the same group always produces
        the same key regardless of which member is looked up."""
        if service not in self._adjacency:
            return service

        seen = {service}
        frontier = [service]
        while frontier:
            current = frontier.pop()
            for neighbor in self._adjacency.get(current, ()):
                if neighbor not in seen:
                    seen.add(neighbor)
                    frontier.append(neighbor)
        return min(seen)
