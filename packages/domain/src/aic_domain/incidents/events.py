"""IncidentEvent: the audit spine.

One row per state transition (and, later, per evidence/RCA/action
milestone), appended in the same transaction as the status update.
Mirrors docs/design/09-database-design.md `incident.incident_event`.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class ActorType(StrEnum):
    HUMAN = "human"
    AGENT = "agent"
    SYSTEM = "system"
    POLICY = "policy"


class IncidentEvent(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID
    incident_id: UUID
    seq: int
    event_type: str
    actor_type: ActorType
    actor_id: str
    payload: dict[str, Any] = {}
    created_at: datetime
