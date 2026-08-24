from __future__ import annotations

from datetime import UTC, datetime

from aic_agents.tools.knowledge import SEARCH, SearchInput, build_specs
from aic_common.clock import FixedClock
from aic_domain.enums import EvidenceStatus

T0 = datetime(2026, 8, 24, 12, 0, 0, tzinfo=UTC)


async def test_search_is_callable_and_returns_no_hits_until_t12() -> None:
    specs = build_specs()
    result = await specs[SEARCH].invoke(FixedClock(T0), SearchInput(query="db pool exhaustion"))

    assert result.status == EvidenceStatus.OK
    assert result.data == []
