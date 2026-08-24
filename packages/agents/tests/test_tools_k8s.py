from __future__ import annotations

import base64
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from aic_agents.tools.k8s import (
    GET_DEPLOYMENT_HISTORY,
    GET_POD_EVENTS,
    GET_SERVICE_DEPENDENCIES,
    DeploymentHistoryInput,
    PodEventsInput,
    ServiceDependenciesInput,
    build_specs,
    load_investigator_credentials,
)
from aic_common.clock import FixedClock
from aic_database.models import Deployment, ServiceDependency
from aic_domain.enums import EvidenceStatus
from sqlalchemy.orm import Session, sessionmaker

T0 = datetime(2026, 8, 24, 12, 0, 0, tzinfo=UTC)


async def test_get_deployment_history_reads_real_postgres_rows_newest_first(
    session_factory: sessionmaker[Session],
) -> None:
    service = f"payment-service-{id(session_factory)}"
    with session_factory() as session:
        session.add(
            Deployment(
                service=service,
                version="v41",
                image_tag="payment-service:dev",
                config_diff={},
                deployed_at=T0 - timedelta(hours=2),
                deployed_by="tester",
            )
        )
        session.add(
            Deployment(
                service=service,
                version="v42",
                image_tag="payment-service:dev",
                config_diff={"DB_POOL_SIZE": "20 -> 3"},
                deployed_at=T0 - timedelta(minutes=5),
                deployed_by="tester",
            )
        )
        session.commit()

    specs = build_specs(
        session_factory=session_factory,
        k8s_client=httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200))),
        namespace="aic-demo",
    )
    result = await specs[GET_DEPLOYMENT_HISTORY].invoke(
        FixedClock(T0), DeploymentHistoryInput(service=service)
    )

    assert result.status == EvidenceStatus.OK
    assert [d["version"] for d in result.data] == ["v42", "v41"]
    assert result.data[0]["config_diff"] == {"DB_POOL_SIZE": "20 -> 3"}


async def test_get_service_dependencies_reads_real_postgres_rows(
    session_factory: sessionmaker[Session],
) -> None:
    suffix = id(session_factory)
    with session_factory() as session:
        session.add(ServiceDependency(service=f"checkout-{suffix}", depends_on=f"payment-{suffix}"))
        session.commit()

    specs = build_specs(
        session_factory=session_factory,
        k8s_client=httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200))),
        namespace="aic-demo",
    )
    result = await specs[GET_SERVICE_DEPENDENCIES].invoke(
        FixedClock(T0), ServiceDependenciesInput()
    )

    assert result.status == EvidenceStatus.OK
    assert {"service": f"checkout-{suffix}", "depends_on": f"payment-{suffix}"} in result.data


async def test_get_pod_events_calls_the_real_k8s_events_endpoint(
    session_factory: sessionmaker[Session],
) -> None:
    captured: dict[str, Any] = {}

    def _handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["params"] = dict(request.url.params)
        return httpx.Response(
            200,
            json={
                "items": [
                    {
                        "reason": "OOMKilled",
                        "message": "container was OOMKilled",
                        "involvedObject": {"name": "payment-service-abc"},
                        "type": "Warning",
                        "lastTimestamp": T0.isoformat(),
                        "count": 1,
                    }
                ]
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(_handler), base_url="https://k8s.test")
    specs = build_specs(session_factory=session_factory, k8s_client=client, namespace="aic-demo")

    result = await specs[GET_POD_EVENTS].invoke(FixedClock(T0), PodEventsInput())

    assert result.status == EvidenceStatus.OK
    assert captured["path"] == "/api/v1/namespaces/aic-demo/events"
    assert captured["params"]["fieldSelector"] == "involvedObject.kind=Pod"
    assert result.data[0]["reason"] == "OOMKilled"
    await client.aclose()


def _write_fake_kubectl(tmp_path: Path, *, server: str, ca_data_b64: str, token: str) -> Path:
    script = tmp_path / "fake-kubectl"
    script.write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        "args = sys.argv[1:]\n"
        "if args[:2] == ['config', 'view']:\n"
        "    jsonpath = args[-1]\n"
        f"    print({ca_data_b64!r} if 'certificate-authority-data' in jsonpath else {server!r})\n"
        "elif args[:2] == ['create', 'token']:\n"
        f"    print({token!r})\n"
        "else:\n"
        "    raise SystemExit(f'unexpected args: {args}')\n"
    )
    script.chmod(0o755)
    return script


def test_load_investigator_credentials_mints_a_scoped_token_not_the_admin_credential(
    tmp_path: Path,
) -> None:
    ca_bytes = b"fake-ca-cert-bytes"
    ca_data_b64 = base64.b64encode(ca_bytes).decode()
    kubectl = _write_fake_kubectl(
        tmp_path,
        server="https://127.0.0.1:12345",
        ca_data_b64=ca_data_b64,
        token="sa-token-for-aic-investigator",
    )

    credentials = load_investigator_credentials(
        context="kind-aic-demo",
        namespace="aic-demo",
        service_account="aic-investigator",
        kubectl=str(kubectl),
    )

    assert credentials.server == "https://127.0.0.1:12345"
    assert credentials.token == "sa-token-for-aic-investigator"
    assert credentials.namespace == "aic-demo"
    assert credentials.ca_cert_path.read_bytes() == ca_bytes


@pytest.mark.parametrize("bad_args", [["get", "secrets"]])
def test_fake_kubectl_rejects_unexpected_invocations(tmp_path: Path, bad_args: list[str]) -> None:
    """Guards the test double itself: if `load_investigator_credentials` ever
    starts shelling out with different arguments, this fails loudly instead
    of silently returning stale values."""
    import subprocess

    kubectl = _write_fake_kubectl(tmp_path, server="x", ca_data_b64="eA==", token="t")
    result = subprocess.run([str(kubectl), *bad_args], capture_output=True, text=True)
    assert result.returncode != 0
