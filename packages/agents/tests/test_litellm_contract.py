"""Contract test against a real LiteLLM proxy (ADR 0004's specific risk:
"Structured-output mode ... must be verified to pass through LiteLLM
correctly for each provider used ... Contract tests against the real
proxy, not just against a direct provider call, are required").

Requires a real `ANTHROPIC_API_KEY` in the environment — skipped entirely
otherwise, since there is no meaningful mock for "does the real proxy pass
structured output through correctly." Retry-with-feedback's *bookkeeping*
(attempt counting, ledger rows) is covered deterministically in
`test_litellm_adapter.py`; what only a real call can prove is that a
trivial structured-output request actually round-trips.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import openai
import pytest
from aic_agents.litellm_adapter import LiteLLMAdapter
from aic_agents.port import ModelTier
from pydantic import BaseModel
from sqlalchemy.orm import Session, sessionmaker
from testcontainers.core.container import DockerContainer
from testcontainers.core.waiting_utils import wait_for_logs

_CONFIG_PATH = (
    Path(__file__).resolve().parent.parent.parent.parent
    / "infra"
    / "litellm"
    / "litellm_config.yaml"
)
_MASTER_KEY = "sk-aic-contract-test"


class _ArithmeticAnswer(BaseModel):
    answer: int


@pytest.fixture(scope="session")
def anthropic_api_key() -> str:
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        pytest.skip("ANTHROPIC_API_KEY not set — needed for the real LiteLLM contract test")
    return key


@pytest.fixture(scope="session")
def litellm_base_url(anthropic_api_key: str) -> Iterator[str]:
    container = (
        DockerContainer("ghcr.io/berriai/litellm:main-stable")
        .with_command("--config /app/config.yaml --port 4000")
        .with_env("ANTHROPIC_API_KEY", anthropic_api_key)
        .with_env("LITELLM_MASTER_KEY", _MASTER_KEY)
        .with_volume_mapping(str(_CONFIG_PATH), "/app/config.yaml", "ro")
        .with_exposed_ports(4000)
    )
    with container:
        wait_for_logs(container, "Uvicorn running", timeout=60)
        host = container.get_container_host_ip()
        port = container.get_exposed_port(4000)
        yield f"http://{host}:{port}"


async def test_structured_output_round_trips_through_the_real_proxy(
    litellm_base_url: str, session_factory: sessionmaker[Session]
) -> None:
    client = openai.AsyncOpenAI(api_key=_MASTER_KEY, base_url=litellm_base_url)
    adapter = LiteLLMAdapter(client=client, session_factory=session_factory)

    result = await adapter.complete_structured(
        tier=ModelTier.CHEAP,
        agent_role="contract-test",
        system="You do simple arithmetic and respond only via the required tool.",
        user="What is 2 + 2?",
        response_model=_ArithmeticAnswer,
    )

    assert result.answer == 4
