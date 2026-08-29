"""`k8s.get_deployment_history`, `k8s.get_service_dependencies`,
`k8s.get_pod_events` (design doc §1.9).

Only `get_pod_events` actually calls the Kubernetes API — deployment
history and service dependencies are real data, but the real data already
lives in AIC's own Postgres (`deployment`/`service_dependency`, written by
`apps/toy-ops`'s deploy script and `demo-seed` respectively), so those two
tools are Postgres reads, not K8s API calls. Grouping all three under the
`k8s.*` tool namespace follows the design doc's own naming, not the
storage layer.

Privilege separation for `get_pod_events` (design doc §1.11 intent,
T2's `infra/kind/rbac.yaml`): T7 runs the investigation graph as a host
process, like aic-ingest/aic-correlator/aic-triage (T4/T6), not as an
in-cluster pod — so the RBAC comment's "T7 will reference aic-investigator
via serviceAccountName" doesn't literally apply. Instead,
`load_investigator_credentials` mints a short-lived Bearer token for the
read-only `aic-investigator` ServiceAccount (`kubectl create token`) using
the operator's own kubeconfig once, at startup, then every actual
investigative K8s read uses only that scoped token — never the operator's
own admin credential. Even a compromised/prompt-injected tool call
reaching this credential can only get/list/watch pods/events/deployments/
replicasets in the `aic-demo` namespace (see `infra/kind/rbac.yaml`); it
cannot patch or delete anything — `aic_agents.execution` (T10) is the
*other* side of that boundary, minted for `aic-executor` instead and
never imported here.

The actual token-minting mechanics live in `aic_agents.k8s_auth`, shared
with `aic_agents.execution`'s executor credential loader (T10) — the two
differ only in which ServiceAccount name they mint for.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
from aic_database.models import Deployment, ServiceDependency
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from aic_agents.k8s_auth import ServiceAccountCredentials, mint_service_account_credentials
from aic_agents.tools.base import ToolSpec

GET_DEPLOYMENT_HISTORY = "k8s.get_deployment_history"
GET_SERVICE_DEPENDENCIES = "k8s.get_service_dependencies"
GET_POD_EVENTS = "k8s.get_pod_events"

InvestigatorK8sCredentials = ServiceAccountCredentials


class DeploymentHistoryInput(BaseModel):
    service: str
    limit: int = Field(default=10, ge=1, le=100)


class ServiceDependenciesInput(BaseModel):
    """No parameters: reads the whole static table. Still a declared
    schema, per §1.9, rather than an untyped no-arg tool."""


class PodEventsInput(BaseModel):
    limit: int = Field(default=50, ge=1, le=500)


def load_investigator_credentials(
    *,
    context: str,
    namespace: str,
    service_account: str = "aic-investigator",
    token_duration: str = "1h",
    kubectl: str = "kubectl",
) -> InvestigatorK8sCredentials:
    """Mint the `aic-investigator` ServiceAccount's own credentials from the
    operator's kubeconfig. Every subsequent K8s read uses only what's
    returned here. Thin wrapper over `aic_agents.k8s_auth`'s shared minter —
    see that module's docstring for why the mechanics live there."""
    return mint_service_account_credentials(
        context=context,
        namespace=namespace,
        service_account=service_account,
        token_duration=token_duration,
        kubectl=kubectl,
    )


async def _get_deployment_history(
    session_factory: sessionmaker[Session], input_data: DeploymentHistoryInput
) -> Any:
    def _read() -> list[dict[str, Any]]:
        with session_factory() as session:
            rows = (
                session.execute(
                    select(Deployment)
                    .where(Deployment.service == input_data.service)
                    .order_by(Deployment.deployed_at.desc())
                    .limit(input_data.limit)
                )
                .scalars()
                .all()
            )
            return [
                {
                    "id": str(row.id),
                    "service": row.service,
                    "version": row.version,
                    "image_tag": row.image_tag,
                    "config_diff": row.config_diff,
                    "deployed_at": row.deployed_at.isoformat(),
                    "deployed_by": row.deployed_by,
                }
                for row in rows
            ]

    return await asyncio.to_thread(_read)


async def _get_service_dependencies(
    session_factory: sessionmaker[Session], _input_data: ServiceDependenciesInput
) -> Any:
    def _read() -> list[dict[str, Any]]:
        with session_factory() as session:
            rows = session.execute(select(ServiceDependency)).scalars().all()
            return [{"service": row.service, "depends_on": row.depends_on} for row in rows]

    return await asyncio.to_thread(_read)


async def _get_pod_events(
    client: httpx.AsyncClient, namespace: str, input_data: PodEventsInput
) -> Any:
    response = await client.get(
        f"/api/v1/namespaces/{namespace}/events",
        params={"fieldSelector": "involvedObject.kind=Pod", "limit": input_data.limit},
    )
    response.raise_for_status()
    body = response.json()
    items = body.get("items", [])
    return [
        {
            "reason": item.get("reason"),
            "message": item.get("message"),
            "involved_object": item.get("involvedObject", {}).get("name"),
            "type": item.get("type"),
            "last_timestamp": item.get("lastTimestamp"),
            "count": item.get("count"),
        }
        for item in items
    ]


def build_specs(
    *,
    session_factory: sessionmaker[Session],
    k8s_client: httpx.AsyncClient,
    namespace: str,
) -> dict[str, ToolSpec[Any]]:
    return {
        GET_DEPLOYMENT_HISTORY: ToolSpec(
            name=GET_DEPLOYMENT_HISTORY,
            source="postgres",
            input_model=DeploymentHistoryInput,
            timeout_seconds=5.0,
            rate_limit_key="aic-postgres",
            rate_limit_max_concurrency=8,
            call=lambda input_data: _get_deployment_history(session_factory, input_data),
            render_query=lambda input_data: f"service={input_data.service}",
        ),
        GET_SERVICE_DEPENDENCIES: ToolSpec(
            name=GET_SERVICE_DEPENDENCIES,
            source="postgres",
            input_model=ServiceDependenciesInput,
            timeout_seconds=5.0,
            rate_limit_key="aic-postgres",
            rate_limit_max_concurrency=8,
            call=lambda input_data: _get_service_dependencies(session_factory, input_data),
            render_query=lambda _input_data: None,
        ),
        GET_POD_EVENTS: ToolSpec(
            name=GET_POD_EVENTS,
            source="k8s",
            input_model=PodEventsInput,
            timeout_seconds=10.0,
            rate_limit_key="k8s-api",
            rate_limit_max_concurrency=4,
            call=lambda input_data: _get_pod_events(k8s_client, namespace, input_data),
            render_query=lambda _input_data: "involvedObject.kind=Pod",
        ),
    }
