"""Wires the real tool adapters (httpx clients, Postgres session factory,
the minted `aic-investigator` K8s credential) into one `ToolRegistry` the
investigation graph's `gather` node looks up tools by name from.

Tests don't use this: they build their own `dict[str, ToolSpec[Any]]`
with fake `call` functions against the real input schemas, so graph/node
logic is tested without a live Prometheus/Loki/K8s/Postgres. This module
is exercised by `apps/aic-investigator` (T7) against the real stack.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx
from sqlalchemy.orm import Session, sessionmaker

from aic_agents.tools import k8s, knowledge, loki, prometheus
from aic_agents.tools.base import ToolSpec
from aic_agents.tools.k8s import InvestigatorK8sCredentials
from aic_agents.tools.loki import LokiSettings
from aic_agents.tools.prometheus import PrometheusSettings


@dataclass(slots=True)
class ToolRegistry:
    specs: dict[str, ToolSpec[Any]]
    _prometheus_client: httpx.AsyncClient
    _loki_client: httpx.AsyncClient
    _k8s_client: httpx.AsyncClient

    async def aclose(self) -> None:
        await self._prometheus_client.aclose()
        await self._loki_client.aclose()
        await self._k8s_client.aclose()


def build_registry(
    *,
    prometheus_settings: PrometheusSettings,
    loki_settings: LokiSettings,
    k8s_credentials: InvestigatorK8sCredentials,
    session_factory: sessionmaker[Session],
) -> ToolRegistry:
    prometheus_client = httpx.AsyncClient(base_url=prometheus_settings.base_url)
    loki_client = httpx.AsyncClient(base_url=loki_settings.base_url)
    k8s_client = httpx.AsyncClient(
        base_url=k8s_credentials.server,
        verify=str(k8s_credentials.ca_cert_path),
        headers={"Authorization": f"Bearer {k8s_credentials.token}"},
    )

    specs: dict[str, ToolSpec[Any]] = {}
    specs.update(prometheus.build_specs(prometheus_client, prometheus_settings))
    specs.update(loki.build_specs(loki_client, loki_settings))
    specs.update(
        k8s.build_specs(
            session_factory=session_factory,
            k8s_client=k8s_client,
            namespace=k8s_credentials.namespace,
        )
    )
    specs.update(knowledge.build_specs())

    return ToolRegistry(
        specs=specs,
        _prometheus_client=prometheus_client,
        _loki_client=loki_client,
        _k8s_client=k8s_client,
    )
