from __future__ import annotations

import asyncio

import pytest
from aic_agents.tools.base import ToolResult, ToolSpec, default_digest
from aic_common.clock import FixedClock
from aic_domain.enums import EvidenceStatus
from pydantic import BaseModel


class _Input(BaseModel):
    query: str = "the-query"


def _spec(call: object, *, max_concurrency: int = 4, key: str = "test-key") -> ToolSpec[_Input]:
    return ToolSpec(
        name="test.tool",
        source="test",
        input_model=_Input,
        timeout_seconds=0.2,
        rate_limit_key=key,
        rate_limit_max_concurrency=max_concurrency,
        call=call,  # type: ignore[arg-type]
        render_query=lambda i: i.query,
    )


def test_default_digest_truncates_large_payloads() -> None:
    huge = {"data": "x" * 10_000}
    rendered = default_digest(huge)
    assert rendered.endswith("...(truncated)")
    assert len(rendered) < 5_000


def test_default_digest_leaves_small_payloads_untouched() -> None:
    small = {"a": 1}
    assert default_digest(small) == '{"a": 1}'


async def test_invoke_returns_ok_result_on_success() -> None:
    async def _call(_input_data: _Input) -> object:
        return {"ok": True}

    spec = _spec(_call)
    result = await spec.invoke(FixedClock(), _Input())

    assert isinstance(result, ToolResult)
    assert result.status == EvidenceStatus.OK
    assert result.data == {"ok": True}
    assert result.query == "the-query"
    assert result.error_class is None


async def test_invoke_returns_error_result_on_exception_never_raises() -> None:
    async def _call(_input_data: _Input) -> object:
        raise RuntimeError("boom")

    spec = _spec(_call)
    result = await spec.invoke(FixedClock(), _Input())

    assert result.status == EvidenceStatus.ERROR
    assert result.error_class == "RuntimeError"
    assert result.error_message == "boom"
    assert result.data is None


async def test_invoke_returns_error_result_on_timeout() -> None:
    async def _call(_input_data: _Input) -> object:
        await asyncio.sleep(10)
        return {"unreachable": True}

    spec = _spec(_call)
    result = await spec.invoke(FixedClock(), _Input())

    assert result.status == EvidenceStatus.ERROR
    assert result.error_class == "TimeoutError"


async def test_rate_limiter_bounds_concurrent_calls_per_key() -> None:
    key = f"key-{id(object())}"
    in_flight = 0
    max_observed = 0

    async def _call(_input_data: _Input) -> object:
        nonlocal in_flight, max_observed
        in_flight += 1
        max_observed = max(max_observed, in_flight)
        await asyncio.sleep(0.05)
        in_flight -= 1
        return None

    spec = _spec(_call, max_concurrency=2, key=key)
    await asyncio.gather(*(spec.invoke(FixedClock(), _Input()) for _ in range(5)))

    assert max_observed <= 2


@pytest.mark.parametrize("key", ["shared-key-test"])
async def test_rate_limiter_is_shared_across_toolspecs_naming_the_same_key(key: str) -> None:
    in_flight = 0
    max_observed = 0

    async def _call(_input_data: _Input) -> object:
        nonlocal in_flight, max_observed
        in_flight += 1
        max_observed = max(max_observed, in_flight)
        await asyncio.sleep(0.05)
        in_flight -= 1
        return None

    spec_a = _spec(_call, max_concurrency=1, key=key)
    spec_b = _spec(_call, max_concurrency=1, key=key)
    await asyncio.gather(
        spec_a.invoke(FixedClock(), _Input()), spec_b.invoke(FixedClock(), _Input())
    )

    assert max_observed <= 1
