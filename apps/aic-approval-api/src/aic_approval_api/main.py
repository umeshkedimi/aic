"""aic-approval-api (design doc §1.10 APPROVE row, T9): the authenticated
`POST /approvals/{id}/decision` v1 surface. `aic_cli` is the other v1
surface (`aic approve <incident-id>`) — both call the same
`aic_agents.approval.record_decision`, so the approval gate's trust
properties don't depend on which surface a human went through.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Generator
from contextlib import asynccontextmanager
from typing import Literal
from uuid import UUID

import uvicorn
from aic_agents.approval import load_incident_for_action, record_decision
from aic_common.clock import Clock, SystemClock
from aic_common.errors import AuthorizationError, IllegalStateError, NotFoundError
from aic_common.logging import configure_logging, get_logger
from aic_database.models import Action, ApprovalRequest
from aic_database.session import (
    DatabaseSettings,
    create_database_engine,
    create_session_factory,
)
from aic_domain.enums import ApprovalDecisionType
from fastapi import Depends, FastAPI, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from aic_approval_api.config import ApprovalApiSettings, DeciderIdentity

logger = get_logger(__name__)

_bearer_scheme = HTTPBearer(auto_error=False)


class DecisionRequest(BaseModel):
    decision: Literal["approve", "reject"]
    reason: str | None = Field(default=None, max_length=2000)


class DecisionResponse(BaseModel):
    approval_request_id: UUID
    approval_status: str
    incident_id: UUID
    incident_status: str


class ApprovalCard(BaseModel):
    """What a human sees before deciding (design doc §1.4 ACT row: the
    dry-run "attached to the approval card"). `dry_run_result` is whatever
    `aic_agents.execution.dry_run_action` returned when `plan_remediation`
    created this request — `None` if no executor credential was available
    at planning time (see `aic_agents.remediation._try_dry_run`), or an
    `{"error": ...}` payload if the dry run itself failed."""

    approval_request_id: UUID
    incident_id: UUID
    status: str
    quorum: int
    required_roles: list[str]
    expires_at: str
    action_type: str
    target_resource: str
    params: dict[str, object]
    dry_run_result: dict[str, object] | None


def create_app(
    settings: ApprovalApiSettings | None = None,
    session_factory: sessionmaker[Session] | None = None,
    clock: Clock | None = None,
) -> FastAPI:
    """Build the app. Tests inject a `session_factory` bound to a real,
    migrated Postgres testcontainer — this endpoint's whole job is
    real-transaction quorum evaluation, so a mocked session would prove
    nothing about it (same reasoning as `aic_agents.approval`'s own tests).
    """
    settings = settings or ApprovalApiSettings()
    configure_logging(settings.log_level)
    clock = clock or SystemClock()
    owns_engine = session_factory is None
    engine: Engine | None = None

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        nonlocal session_factory, engine
        if owns_engine:
            engine = create_database_engine(DatabaseSettings(url=os.environ["AIC_DATABASE_URL"]))
            session_factory = create_session_factory(engine)
        app.state.session_factory = session_factory
        logger.info("aic_approval_api.started", port=settings.port)
        try:
            yield
        finally:
            if owns_engine and engine is not None:
                engine.dispose()

    app = FastAPI(lifespan=lifespan)

    def _get_session() -> Generator[Session]:
        factory: sessionmaker[Session] = app.state.session_factory
        session = factory()
        try:
            yield session
        finally:
            session.close()

    def _authenticate(
        credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
    ) -> DeciderIdentity:
        if credentials is None or credentials.credentials not in settings.identities:
            raise HTTPException(status_code=401, detail="invalid or missing bearer token")
        return settings.identities[credentials.credentials]

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/approvals/{approval_request_id}")
    async def get_card(
        approval_request_id: UUID,
        _identity: DeciderIdentity = Depends(_authenticate),
        session: Session = Depends(_get_session),
    ) -> ApprovalCard:
        request = session.get(ApprovalRequest, approval_request_id)
        if request is None:
            raise HTTPException(
                status_code=404, detail=f"no approval request {approval_request_id}"
            )
        action = session.get(Action, request.action_id)
        assert action is not None
        incident = load_incident_for_action(session, request.action_id)
        return ApprovalCard(
            approval_request_id=approval_request_id,
            incident_id=incident.id,
            status=request.status,
            quorum=request.quorum,
            required_roles=list(request.required_roles),
            expires_at=request.expires_at.isoformat(),
            action_type=action.action_type,
            target_resource=action.target_resource,
            params=action.params,
            dry_run_result=request.dry_run_result,
        )

    @app.post("/approvals/{approval_request_id}/decision")
    async def decide(
        approval_request_id: UUID,
        body: DecisionRequest,
        identity: DeciderIdentity = Depends(_authenticate),
        session: Session = Depends(_get_session),
    ) -> DecisionResponse:
        assert clock is not None
        try:
            record_decision(
                session,
                approval_request_id,
                decider_id=identity.decider_id,
                decider_roles=frozenset(identity.roles),
                decision=ApprovalDecisionType(body.decision),
                reason=body.reason,
                clock=clock,
            )
        except NotFoundError as exc:
            session.rollback()
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except AuthorizationError as exc:
            session.rollback()
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except IllegalStateError as exc:
            session.rollback()
            raise HTTPException(status_code=409, detail=str(exc)) from exc

        request = session.get(ApprovalRequest, approval_request_id)
        assert request is not None
        incident = load_incident_for_action(session, request.action_id)
        session.commit()

        logger.info(
            "aic_approval_api.decision_recorded",
            approval_request_id=str(approval_request_id),
            decider_id=identity.decider_id,
            decision=body.decision,
            approval_status=request.status,
            incident_status=incident.status.value,
        )
        return DecisionResponse(
            approval_request_id=approval_request_id,
            approval_status=request.status,
            incident_id=incident.id,
            incident_status=incident.status.value,
        )

    return app


def main() -> None:
    settings = ApprovalApiSettings()
    uvicorn.run(create_app(settings=settings), host="0.0.0.0", port=settings.port)


if __name__ == "__main__":
    main()
