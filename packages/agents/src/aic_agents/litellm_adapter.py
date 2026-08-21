"""LiteLLM-backed `LLMPort` (ADR 0004).

Structured output is obtained via forced tool-calling, not `response_format`
JSON mode — Anthropic models exposed through LiteLLM's OpenAI-compatible
endpoint reliably support tool-use, and using it directly (with
`tool_choice` forced to the one tool describing `response_model`'s schema)
is the version of ADR 0004's "Anthropic tool-use ... must be verified to
pass through LiteLLM correctly" risk that most needs the contract test.

Retry-with-feedback: on a missing tool call or a schema-validation failure,
the invalid response plus a description of the error is appended to the
conversation and the model is asked again — capped at 2 attempts total
(design doc §1.14). Every attempt, success or failure, is written to the
`llm_call` ledger before this function returns or raises.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from typing import Any, cast
from uuid import UUID

import openai
from aic_common.errors import ExternalServiceError
from aic_database.models import LLMCall
from aic_database.session import session_scope
from aic_domain.enums import LLMCallStatus
from openai.types.chat import ChatCompletionMessageParam, ChatCompletionToolUnionParam
from pydantic import BaseModel, ValidationError
from sqlalchemy.orm import Session, sessionmaker

from aic_agents.port import LLMStructuredOutputError, ModelTier

_MAX_ATTEMPTS = 2


@dataclass(slots=True)
class _CompletionResult:
    content_json: str | None
    model: str | None
    input_tokens: int | None
    output_tokens: int | None
    cost_usd: float | None


def _prompt_hash(system: str, user: str) -> str:
    return hashlib.sha256(f"{system}\n{user}".encode()).hexdigest()


def _elapsed_ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


class LiteLLMAdapter:
    """Implements `LLMPort` against a real LiteLLM proxy via the
    OpenAI-compatible `AsyncOpenAI` client."""

    def __init__(
        self,
        *,
        client: openai.AsyncOpenAI,
        session_factory: sessionmaker[Session],
        timeout_seconds: float = 30.0,
    ) -> None:
        self._client = client
        self._session_factory = session_factory
        self._timeout_seconds = timeout_seconds

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
        prompt_hash = _prompt_hash(system, user)
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        last_error = "no attempts were made"

        for attempt in range(1, _MAX_ATTEMPTS + 1):
            started = time.monotonic()
            try:
                result = await self._complete_once(
                    tier=tier, messages=messages, response_model=response_model
                )
            except Exception as exc:
                self._record(
                    incident_id=incident_id,
                    agent_role=agent_role,
                    tier=tier,
                    model=None,
                    prompt_hash=prompt_hash,
                    attempt=attempt,
                    status=LLMCallStatus.ERROR,
                    error=str(exc),
                    latency_ms=_elapsed_ms(started),
                )
                raise ExternalServiceError(f"LiteLLM completion call failed: {exc}") from exc

            latency_ms = _elapsed_ms(started)

            if result.content_json is None:
                last_error = f"model did not call the required {response_model.__name__} tool"
                self._record(
                    incident_id=incident_id,
                    agent_role=agent_role,
                    tier=tier,
                    model=result.model,
                    prompt_hash=prompt_hash,
                    attempt=attempt,
                    status=LLMCallStatus.VALIDATION_FAILED,
                    error=last_error,
                    input_tokens=result.input_tokens,
                    output_tokens=result.output_tokens,
                    latency_ms=latency_ms,
                )
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            f"Your previous response was invalid: {last_error}. Call the "
                            f"{response_model.__name__} tool with valid arguments."
                        ),
                    }
                )
                continue

            try:
                parsed = response_model.model_validate_json(result.content_json)
            except (ValidationError, json.JSONDecodeError) as exc:
                last_error = str(exc)
                self._record(
                    incident_id=incident_id,
                    agent_role=agent_role,
                    tier=tier,
                    model=result.model,
                    prompt_hash=prompt_hash,
                    attempt=attempt,
                    status=LLMCallStatus.VALIDATION_FAILED,
                    error=last_error,
                    input_tokens=result.input_tokens,
                    output_tokens=result.output_tokens,
                    latency_ms=latency_ms,
                )
                messages.append({"role": "assistant", "content": result.content_json})
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            f"Your previous response failed schema validation: {last_error}. "
                            f"Call the {response_model.__name__} tool again with valid arguments "
                            "that satisfy the schema."
                        ),
                    }
                )
                continue

            self._record(
                incident_id=incident_id,
                agent_role=agent_role,
                tier=tier,
                model=result.model,
                prompt_hash=prompt_hash,
                attempt=attempt,
                status=LLMCallStatus.OK,
                input_tokens=result.input_tokens,
                output_tokens=result.output_tokens,
                cost_usd=result.cost_usd,
                latency_ms=latency_ms,
            )
            return parsed

        raise LLMStructuredOutputError(
            f"structured output failed validation after {_MAX_ATTEMPTS} attempts: {last_error}"
        )

    async def _complete_once(
        self, *, tier: ModelTier, messages: list[dict[str, Any]], response_model: type[BaseModel]
    ) -> _CompletionResult:
        tool_name = response_model.__name__
        tool = {
            "type": "function",
            "function": {
                "name": tool_name,
                "description": f"Respond with a {tool_name} object matching the given schema.",
                "parameters": response_model.model_json_schema(),
            },
        }
        raw = await self._client.chat.completions.with_raw_response.create(
            model=tier.value,
            messages=cast("list[ChatCompletionMessageParam]", messages),
            tools=[cast("ChatCompletionToolUnionParam", tool)],
            tool_choice={"type": "function", "function": {"name": tool_name}},
            timeout=self._timeout_seconds,
        )
        completion = raw.parse()
        cost_header = raw.headers.get("x-litellm-response-cost")
        cost_usd = float(cost_header) if cost_header is not None else None

        usage = completion.usage
        message = completion.choices[0].message
        content_json: str | None = None
        if message.tool_calls:
            first_call = message.tool_calls[0]
            # Always a function-tool call in practice: `tool` above only
            # ever declares type="function", and tool_choice forces that
            # one tool — the custom-tool arm of this union can't occur.
            if hasattr(first_call, "function"):
                content_json = first_call.function.arguments

        return _CompletionResult(
            content_json=content_json,
            model=completion.model,
            input_tokens=usage.prompt_tokens if usage else None,
            output_tokens=usage.completion_tokens if usage else None,
            cost_usd=cost_usd,
        )

    def _record(
        self,
        *,
        incident_id: UUID | None,
        agent_role: str,
        tier: ModelTier,
        model: str | None,
        prompt_hash: str,
        attempt: int,
        status: LLMCallStatus,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        cost_usd: float | None = None,
        latency_ms: int | None = None,
        error: str | None = None,
    ) -> None:
        with session_scope(self._session_factory) as session:
            session.add(
                LLMCall(
                    incident_id=incident_id,
                    agent_role=agent_role,
                    tier=tier.value,
                    model=model,
                    prompt_hash=prompt_hash,
                    attempt=attempt,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    cost_usd=cost_usd,
                    latency_ms=latency_ms,
                    status=status,
                    error=error,
                )
            )
