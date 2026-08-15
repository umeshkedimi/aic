"""Deploys payment-service at a given config and records a real `Deployment`
row (design doc §1.2, §5).

This is the mechanism behind the signature scenario's "bad deploy": a real
`kubectl apply` changing payment-service's pod spec, immediately followed by
a real row in AIC's `deployment` table describing what changed — not a
fixture standing in for a redeploy. `get_deployment_history` (T7) reads this
table for real later.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from getpass import getuser

from aic_common.logging import configure_logging, get_logger
from aic_database.models import Deployment
from aic_database.session import (
    DatabaseSettings,
    create_database_engine,
    create_session_factory,
    session_scope,
)
from sqlalchemy import select
from sqlalchemy.orm import Session

logger = get_logger(__name__)

NAMESPACE = "aic-demo"
SERVICE = "payment-service"
IMAGE = "payment-service:dev"
POOL_SIZE_KEY = "DB_POOL_SIZE"

APPLY_TIMEOUT_SECONDS = 30
ROLLOUT_TIMEOUT_SECONDS = 90


@dataclass(frozen=True)
class DeploySpec:
    version: str
    pool_size: int


PRESETS: dict[str, DeploySpec] = {
    "good": DeploySpec(version="v41", pool_size=20),
    "bad": DeploySpec(version="v42", pool_size=3),
}


def render_manifest(spec: DeploySpec) -> str:
    return f"""\
apiVersion: apps/v1
kind: Deployment
metadata:
  name: {SERVICE}
  namespace: {NAMESPACE}
  labels:
    app: {SERVICE}
spec:
  replicas: 1
  selector:
    matchLabels:
      app: {SERVICE}
  template:
    metadata:
      labels:
        app: {SERVICE}
        version: "{spec.version}"
    spec:
      containers:
        - name: {SERVICE}
          image: {IMAGE}
          imagePullPolicy: IfNotPresent
          ports:
            - containerPort: 8000
          env:
            - name: DATABASE_URL
              value: "postgresql://payment:payment@postgres:5432/payment"
            - name: {POOL_SIZE_KEY}
              value: "{spec.pool_size}"
            - name: SERVICE_VERSION
              value: "{spec.version}"
          readinessProbe:
            httpGet:
              path: /health
              port: 8000
            initialDelaySeconds: 3
            periodSeconds: 5
"""


def apply_manifest(manifest: str, *, kubectl: str = "kubectl") -> None:
    subprocess.run(
        [kubectl, "apply", "-n", NAMESPACE, "-f", "-"],
        input=manifest,
        text=True,
        check=True,
        timeout=APPLY_TIMEOUT_SECONDS,
    )


def wait_for_rollout(*, kubectl: str = "kubectl") -> None:
    subprocess.run(
        [
            kubectl,
            "-n",
            NAMESPACE,
            "rollout",
            "status",
            f"deployment/{SERVICE}",
            f"--timeout={ROLLOUT_TIMEOUT_SECONDS}s",
        ],
        check=True,
        timeout=ROLLOUT_TIMEOUT_SECONDS + 10,
    )


def _previous_pool_size(session: Session) -> int | None:
    stmt = (
        select(Deployment)
        .where(Deployment.service == SERVICE)
        .order_by(Deployment.deployed_at.desc())
        .limit(1)
    )
    row = session.execute(stmt).scalar_one_or_none()
    if row is None:
        return None
    previous: int | None = row.config_diff.get(POOL_SIZE_KEY, {}).get("to")
    return previous


def record_deployment(spec: DeploySpec, *, database_url: str | None = None) -> uuid.UUID:
    """Insert a real `Deployment` row for this redeploy, diffed against the
    most recent prior deployment of the same service."""
    url = database_url or os.environ["AIC_DATABASE_URL"]
    settings = DatabaseSettings(url=url)
    engine = create_database_engine(settings)
    session_factory = create_session_factory(engine)
    try:
        with session_scope(session_factory) as session:
            previous_pool_size = _previous_pool_size(session)
            config_diff = {POOL_SIZE_KEY: {"from": previous_pool_size, "to": spec.pool_size}}
            deployment = Deployment(
                service=SERVICE,
                version=spec.version,
                image_tag=IMAGE,
                config_diff=config_diff,
                deployed_at=datetime.now(UTC),
                deployed_by=getuser(),
            )
            session.add(deployment)
            session.flush()
            deployment_id: uuid.UUID = deployment.id
    finally:
        engine.dispose()

    logger.info(
        "toy_ops.deployment_recorded",
        deployment_id=str(deployment_id),
        service=SERVICE,
        version=spec.version,
        pool_size=spec.pool_size,
    )
    return deployment_id


def deploy(spec: DeploySpec, *, kubectl: str = "kubectl", record: bool = True) -> None:
    manifest = render_manifest(spec)
    apply_manifest(manifest, kubectl=kubectl)
    wait_for_rollout(kubectl=kubectl)
    if record:
        record_deployment(spec)


def _parse_args(argv: list[str]) -> DeploySpec:
    parser = argparse.ArgumentParser(description="Deploy payment-service at a given config.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--preset", choices=sorted(PRESETS))
    group.add_argument("--pool-size", type=int)
    parser.add_argument("--version", default=None, help="Required together with --pool-size.")
    args = parser.parse_args(argv)

    if args.preset is not None:
        return PRESETS[args.preset]

    if args.version is None:
        parser.error("--version is required when using --pool-size")
    return DeploySpec(version=args.version, pool_size=args.pool_size)


def main(argv: list[str] | None = None) -> int:
    configure_logging()
    spec = _parse_args(sys.argv[1:] if argv is None else argv)
    deploy(spec)
    print(f"payment-service deployed: version={spec.version} pool_size={spec.pool_size}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
