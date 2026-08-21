"""`LLMPort` — the one seam investigation/triage/learning code talks to for
LLM access (ADR 0004). The concrete adapter behind it is LiteLLM
(`aic_agents.litellm_adapter.LiteLLMAdapter`); callers depend only on this
Protocol, so swapping the gateway or bypassing it entirely for a test is a
constructor-argument change, not a rewrite.

`ModelTier` values are literally the `model_name` aliases LiteLLM's
`litellm_config.yaml` registers — per-agent-role model routing ("cheap
model for digest/title/scribe, frontier model for synthesize") is a config
change there, never a model string picked in application code.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Protocol
from uuid import UUID

from aic_common.errors import ExternalServiceError
from pydantic import BaseModel


class ModelTier(StrEnum):
    """Design doc §1.4: cheap tier for digest/title/assess/scribe, frontier
    tier for synthesize. Values are LiteLLM `model_name` aliases, not real
    provider model ids — see `infra/litellm/litellm_config.yaml`."""

    CHEAP = "aic-cheap"
    FRONTIER = "aic-frontier"


class LLMStructuredOutputError(ExternalServiceError):
    """A structured-output call never produced a schema-valid response
    within the retry-with-feedback budget (design doc §1.14, max 2
    attempts). Each attempt is still recorded in the `llm_call` ledger
    before this is raised."""


class LLMPort(Protocol):
    async def complete_structured[T: BaseModel](
        self,
        *,
        tier: ModelTier,
        agent_role: str,
        system: str,
        user: str,
        response_model: type[T],
        incident_id: UUID | None = None,
    ) -> T:
        """Issue a structured-output completion and return a validated
        `response_model` instance.

        On a schema-validation failure, retries once with the validation
        error fed back to the model (max 2 attempts total); raises
        `LLMStructuredOutputError` if both attempts fail. Every attempt is
        persisted to the `llm_call` ledger, success or failure.
        """
        ...
