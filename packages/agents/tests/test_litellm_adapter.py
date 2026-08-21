"""Retry-with-feedback orchestration (design doc §1.14, max 2 attempts) and
the `llm_call` ledger write path.

These tests fake `LiteLLMAdapter._complete_once` — the one seam that talks
to the real OpenAI-compatible client — rather than the LLM itself: getting
a real model to reliably emit malformed tool-call arguments on demand isn't
deterministic, and the thing this test suite is about is AIC's own retry
bookkeeping, not model behavior. Whether structured output actually
round-trips through the real LiteLLM proxy is proven separately by
`test_litellm_contract.py`, against the real proxy.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any
from uuid import UUID, uuid4

import pytest
from aic_agents.litellm_adapter import LiteLLMAdapter, _CompletionResult
from aic_agents.port import LLMStructuredOutputError, ModelTier
from aic_database.models import LLMCall
from aic_domain.enums import LLMCallStatus
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker


class _Verdict(BaseModel):
    confidence: float
    statement: str


def _adapter(session_factory: sessionmaker[Session]) -> LiteLLMAdapter:
    import openai

    return LiteLLMAdapter(
        client=openai.AsyncOpenAI(api_key="unused-in-these-tests", base_url="http://unused"),
        session_factory=session_factory,
    )


def _stub_results(
    adapter: LiteLLMAdapter, results: Sequence[_CompletionResult]
) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    remaining = list(results)

    async def fake_complete_once(**kwargs: Any) -> _CompletionResult:
        calls.append(kwargs)
        return remaining.pop(0)

    adapter._complete_once = fake_complete_once  # type: ignore[method-assign]
    return calls


def _ledger_rows(session_factory: sessionmaker[Session], agent_role: str) -> list[LLMCall]:
    """Filtered by `agent_role`: `postgres_url` is session-scoped, so a
    plain `SELECT *` would also see rows from every other test in this
    file — a unique `agent_role` per test is this suite's isolation key."""
    with session_factory() as session:
        return list(
            session.execute(
                select(LLMCall).where(LLMCall.agent_role == agent_role).order_by(LLMCall.attempt)
            )
            .scalars()
            .all()
        )


async def test_valid_first_response_needs_only_one_attempt(
    session_factory: sessionmaker[Session],
) -> None:
    role = f"digest-{uuid4().hex[:8]}"
    adapter = _adapter(session_factory)
    _stub_results(
        adapter,
        [
            _CompletionResult(
                content_json='{"confidence": 0.9, "statement": "pool exhaustion"}',
                model="claude-haiku-4-5-20251001",
                input_tokens=100,
                output_tokens=20,
                cost_usd=0.001,
            )
        ],
    )

    result = await adapter.complete_structured(
        tier=ModelTier.CHEAP,
        agent_role=role,
        system="sys",
        user="usr",
        response_model=_Verdict,
    )

    assert result == _Verdict(confidence=0.9, statement="pool exhaustion")
    rows = _ledger_rows(session_factory, role)
    assert [r.status for r in rows] == [LLMCallStatus.OK]
    assert rows[0].attempt == 1
    assert rows[0].cost_usd == pytest.approx(0.001)


async def test_malformed_first_response_retries_once_and_succeeds(
    session_factory: sessionmaker[Session],
) -> None:
    role = f"digest-{uuid4().hex[:8]}"
    adapter = _adapter(session_factory)
    calls = _stub_results(
        adapter,
        [
            _CompletionResult(
                content_json='{"confidence": "not-a-number", "statement": "x"}',
                model="claude-haiku-4-5-20251001",
                input_tokens=100,
                output_tokens=15,
                cost_usd=None,
            ),
            _CompletionResult(
                content_json='{"confidence": 0.7, "statement": "corrected"}',
                model="claude-haiku-4-5-20251001",
                input_tokens=140,
                output_tokens=18,
                cost_usd=0.0012,
            ),
        ],
    )

    result = await adapter.complete_structured(
        tier=ModelTier.CHEAP,
        agent_role=role,
        system="sys",
        user="usr",
        response_model=_Verdict,
    )

    assert result.statement == "corrected"
    rows = _ledger_rows(session_factory, role)
    assert [r.status for r in rows] == [LLMCallStatus.VALIDATION_FAILED, LLMCallStatus.OK]
    assert [r.attempt for r in rows] == [1, 2]
    # The second call must actually carry the validation-error feedback.
    second_call_messages = calls[1]["messages"]
    assert "failed schema validation" in second_call_messages[-1]["content"]


async def test_missing_tool_call_counts_as_a_validation_failure(
    session_factory: sessionmaker[Session],
) -> None:
    role = f"digest-{uuid4().hex[:8]}"
    adapter = _adapter(session_factory)
    _stub_results(
        adapter,
        [
            _CompletionResult(
                content_json=None,
                model="claude-haiku-4-5-20251001",
                input_tokens=10,
                output_tokens=5,
                cost_usd=None,
            ),
            _CompletionResult(
                content_json='{"confidence": 0.5, "statement": "ok"}',
                model="claude-haiku-4-5-20251001",
                input_tokens=50,
                output_tokens=10,
                cost_usd=0.0005,
            ),
        ],
    )

    result = await adapter.complete_structured(
        tier=ModelTier.CHEAP,
        agent_role=role,
        system="sys",
        user="usr",
        response_model=_Verdict,
    )

    assert result.statement == "ok"
    rows = _ledger_rows(session_factory, role)
    assert [r.status for r in rows] == [LLMCallStatus.VALIDATION_FAILED, LLMCallStatus.OK]


async def test_two_malformed_responses_raise_after_exactly_two_attempts(
    session_factory: sessionmaker[Session],
) -> None:
    role = f"digest-{uuid4().hex[:8]}"
    adapter = _adapter(session_factory)
    _stub_results(
        adapter,
        [
            _CompletionResult(
                content_json='{"confidence": "bad"}',
                model="claude-haiku-4-5-20251001",
                input_tokens=10,
                output_tokens=5,
                cost_usd=None,
            ),
            _CompletionResult(
                content_json='{"confidence": "still-bad"}',
                model="claude-haiku-4-5-20251001",
                input_tokens=10,
                output_tokens=5,
                cost_usd=None,
            ),
        ],
    )

    with pytest.raises(LLMStructuredOutputError):
        await adapter.complete_structured(
            tier=ModelTier.CHEAP,
            agent_role=role,
            system="sys",
            user="usr",
            response_model=_Verdict,
        )

    rows = _ledger_rows(session_factory, role)
    # Exactly two attempts recorded — proves the cap, not a third silent retry.
    statuses = [r.status for r in rows]
    assert statuses == [LLMCallStatus.VALIDATION_FAILED, LLMCallStatus.VALIDATION_FAILED]
    assert [r.attempt for r in rows] == [1, 2]


async def test_ledger_row_carries_the_given_incident_id(
    session_factory: sessionmaker[Session],
) -> None:
    from datetime import UTC, datetime

    from aic_common.config import Environment
    from aic_database.models import Incident

    role = f"synthesize-{uuid4().hex[:8]}"
    with session_factory() as session:
        incident = Incident(
            fingerprint=f"llm-call-test-{role}",
            service="payment-service",
            environment=Environment.PROD,
            created_at=datetime.now(UTC),
        )
        session.add(incident)
        session.commit()
        incident_id: UUID = incident.id

    adapter = _adapter(session_factory)
    _stub_results(
        adapter,
        [
            _CompletionResult(
                content_json='{"confidence": 0.9, "statement": "x"}',
                model="claude-haiku-4-5-20251001",
                input_tokens=10,
                output_tokens=5,
                cost_usd=0.0001,
            )
        ],
    )

    await adapter.complete_structured(
        tier=ModelTier.FRONTIER,
        agent_role=role,
        system="sys",
        user="usr",
        response_model=_Verdict,
        incident_id=incident_id,
    )

    row = _ledger_rows(session_factory, role)[0]
    assert row.incident_id == incident_id
    assert row.tier == ModelTier.FRONTIER.value
    assert row.agent_role == role
