"""Unit tests for B4's usage rollup loop."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from agent_runtime.persistence.records import RuntimeRunUsageRecord
from runtime_adapters.in_memory import (
    AsyncInMemoryRuntimeApiStore,
    InMemoryRuntimeApiStore,
)
from runtime_worker.usage_rollup_loop import UsageRollupLoop


def _seed_run(
    store: InMemoryRuntimeApiStore,
    *,
    run_id: str,
    org_id: str = "org_a",
    user_id: str = "user_1",
    completed_at: datetime,
    input_tokens: int = 100,
    output_tokens: int = 200,
) -> None:
    store.run_usage[run_id] = RuntimeRunUsageRecord(
        id=run_id,
        org_id=org_id,
        user_id=user_id,
        conversation_id="conv-1",
        run_id=run_id,
        model_provider="openai",
        model_name="gpt-5.4-mini",
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cached_input_tokens=0,
        total_tokens=input_tokens + output_tokens,
        chunk_count=1,
        duration_ms=1000,
        started_at=completed_at - timedelta(seconds=1),
        completed_at=completed_at,
        status="completed",
    )


class TestRefresh:
    @pytest.mark.asyncio
    async def test_refresh_writes_user_and_org_rollups(self) -> None:
        sync_store = InMemoryRuntimeApiStore()
        async_store = AsyncInMemoryRuntimeApiStore(sync_store)
        completed = datetime.now(timezone.utc) - timedelta(hours=2)
        _seed_run(
            sync_store,
            run_id="r1",
            user_id="u1",
            completed_at=completed,
            input_tokens=10,
            output_tokens=20,
        )
        _seed_run(
            sync_store,
            run_id="r2",
            user_id="u2",
            completed_at=completed,
            input_tokens=5,
            output_tokens=7,
        )

        loop = UsageRollupLoop(persistence=async_store)
        await loop.refresh(span_days=2)

        # Two distinct users → two user-rollup rows.
        assert len(sync_store.user_daily_usage) == 2
        # One org × one model × one day → one org-rollup row.
        assert len(sync_store.org_daily_usage) == 1
        org_rows = list(sync_store.org_daily_usage.values())
        assert org_rows[0].distinct_users == 2
        assert org_rows[0].runs_count == 2
        assert org_rows[0].input_tokens == 15
        assert org_rows[0].output_tokens == 27

    @pytest.mark.asyncio
    async def test_refresh_idempotent(self) -> None:
        sync_store = InMemoryRuntimeApiStore()
        async_store = AsyncInMemoryRuntimeApiStore(sync_store)
        completed = datetime.now(timezone.utc) - timedelta(hours=1)
        _seed_run(sync_store, run_id="r1", completed_at=completed)

        loop = UsageRollupLoop(persistence=async_store)
        await loop.refresh(span_days=2)

        def _strip_refreshed(rows: dict[object, object]) -> dict[object, object]:
            # ``refreshed_at`` advances per call; the rest of the row is
            # what we expect to be byte-stable across re-runs.
            return {
                key: {
                    field: value
                    for field, value in row.model_dump().items()
                    if field != "refreshed_at"
                }
                for key, row in rows.items()  # type: ignore[attr-defined]
            }

        first_user = _strip_refreshed(sync_store.user_daily_usage)
        first_org = _strip_refreshed(sync_store.org_daily_usage)
        await loop.refresh(span_days=2)
        assert _strip_refreshed(sync_store.user_daily_usage) == first_user
        assert _strip_refreshed(sync_store.org_daily_usage) == first_org

    @pytest.mark.asyncio
    async def test_refresh_skips_runs_outside_window(self) -> None:
        sync_store = InMemoryRuntimeApiStore()
        async_store = AsyncInMemoryRuntimeApiStore(sync_store)
        old = datetime.now(timezone.utc) - timedelta(days=10)
        _seed_run(sync_store, run_id="old", completed_at=old)

        loop = UsageRollupLoop(persistence=async_store)
        await loop.refresh(span_days=2)

        # The 10-day-old run is outside the 2-day refresh window.
        assert sync_store.user_daily_usage == {}
        assert sync_store.org_daily_usage == {}
