"""PR 7.2 — connector rollup tests for ``UsageRollupLoop``."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from agent_runtime.persistence.records import (
    RuntimeModelCallUsageRecord,
    RuntimeRunUsageRecord,
    UsageDailyConnectorRow,
)
from runtime_adapters.in_memory.async_runtime_api_store import (
    AsyncInMemoryRuntimeApiStore,
)
from runtime_adapters.in_memory.runtime_api_store import InMemoryRuntimeApiStore
from runtime_worker.usage_rollup_loop import UsageRollupLoop


def _seed_run(
    store: InMemoryRuntimeApiStore,
    *,
    org_id: str,
    user_id: str,
    run_id: str,
    completed_at: datetime,
    input_tokens: int = 100,
    output_tokens: int = 50,
) -> None:
    store.run_usage[run_id] = RuntimeRunUsageRecord(
        id=run_id,
        org_id=org_id,
        user_id=user_id,
        conversation_id=f"conv-{run_id}",
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


def _seed_call(
    store: InMemoryRuntimeApiStore,
    *,
    org_id: str,
    run_id: str,
    connector_slug: str | None,
    created_at: datetime,
    input_tokens: int = 50,
    output_tokens: int = 25,
) -> None:
    store.model_call_usage.append(
        RuntimeModelCallUsageRecord(
            id=f"{run_id}-{len(store.model_call_usage)}",
            org_id=org_id,
            run_id=run_id,
            conversation_id=f"conv-{run_id}",
            trace_id=f"trace-{run_id}",
            model_provider="openai",
            model_name="gpt-5.4-mini",
            connector_slug=connector_slug,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cached_input_tokens=0,
            total_tokens=input_tokens + output_tokens,
            duration_ms=500,
            created_at=created_at,
        )
    )


class TestConnectorRollup:
    @pytest.mark.asyncio
    async def test_rollup_aggregates_per_connector(self) -> None:
        store = InMemoryRuntimeApiStore()
        async_store = AsyncInMemoryRuntimeApiStore(store)
        # Use real-now so the loop's `datetime.now(timezone.utc)` window
        # encompasses our seeded `completed_at`.
        now = datetime.now(timezone.utc) - timedelta(seconds=1)
        # Two calls in same day on slack, one on notion, plus an
        # unattributed planning call.
        _seed_run(store, org_id="org_a", user_id="u1", run_id="r1", completed_at=now)
        _seed_call(
            store,
            org_id="org_a",
            run_id="r1",
            connector_slug=None,
            created_at=now,
            input_tokens=10,
            output_tokens=5,
        )
        _seed_call(
            store,
            org_id="org_a",
            run_id="r1",
            connector_slug="slack",
            created_at=now,
            input_tokens=20,
            output_tokens=10,
        )
        _seed_call(
            store,
            org_id="org_a",
            run_id="r1",
            connector_slug="slack",
            created_at=now,
            input_tokens=20,
            output_tokens=10,
        )
        _seed_call(
            store,
            org_id="org_a",
            run_id="r1",
            connector_slug="notion",
            created_at=now,
            input_tokens=15,
            output_tokens=7,
        )

        loop = UsageRollupLoop(
            persistence=async_store,  # type: ignore[arg-type]
            interval_seconds=3600,
            late_arrival_minutes=0,
            backfill_days=0,
        )
        await loop.refresh(span_days=1)

        rows = await async_store.query_connector_daily_usage(
            org_id="org_a", start_day=now - timedelta(days=1), end_day=now
        )
        slugs = {row.connector_slug: row for row in rows}
        # 'slack', 'notion', '' (unattributed) buckets.
        assert set(slugs.keys()) == {"slack", "notion", ""}
        assert slugs["slack"].input_tokens == 40
        assert slugs["slack"].output_tokens == 20
        assert slugs["notion"].input_tokens == 15
        assert slugs[""].input_tokens == 10

    @pytest.mark.asyncio
    async def test_rollup_distinct_users(self) -> None:
        store = InMemoryRuntimeApiStore()
        async_store = AsyncInMemoryRuntimeApiStore(store)
        # Use real-now so the loop's `datetime.now(timezone.utc)` window
        # encompasses our seeded `completed_at`.
        now = datetime.now(timezone.utc) - timedelta(seconds=1)
        _seed_run(
            store,
            org_id="org_a",
            user_id="u1",
            run_id="r1",
            completed_at=now,
        )
        _seed_run(
            store,
            org_id="org_a",
            user_id="u2",
            run_id="r2",
            completed_at=now,
        )
        _seed_call(
            store,
            org_id="org_a",
            run_id="r1",
            connector_slug="slack",
            created_at=now,
        )
        _seed_call(
            store,
            org_id="org_a",
            run_id="r2",
            connector_slug="slack",
            created_at=now,
        )

        loop = UsageRollupLoop(
            persistence=async_store,  # type: ignore[arg-type]
            interval_seconds=3600,
            late_arrival_minutes=0,
            backfill_days=0,
        )
        await loop.refresh(span_days=1)

        rows = await async_store.query_connector_daily_usage(
            org_id="org_a", start_day=now - timedelta(days=1), end_day=now
        )
        slack = next(row for row in rows if row.connector_slug == "slack")
        assert slack.runs_count == 2
        assert slack.distinct_users == 2

    @pytest.mark.asyncio
    async def test_rollup_idempotent(self) -> None:
        # Running the loop twice must produce identical rows.
        store = InMemoryRuntimeApiStore()
        async_store = AsyncInMemoryRuntimeApiStore(store)
        # Use real-now so the loop's `datetime.now(timezone.utc)` window
        # encompasses our seeded `completed_at`.
        now = datetime.now(timezone.utc) - timedelta(seconds=1)
        _seed_run(store, org_id="org_a", user_id="u1", run_id="r1", completed_at=now)
        _seed_call(
            store,
            org_id="org_a",
            run_id="r1",
            connector_slug="slack",
            created_at=now,
            input_tokens=10,
            output_tokens=5,
        )
        loop = UsageRollupLoop(
            persistence=async_store,  # type: ignore[arg-type]
            interval_seconds=3600,
            late_arrival_minutes=0,
            backfill_days=0,
        )
        await loop.refresh(span_days=1)
        first_rows = await async_store.query_connector_daily_usage(
            org_id="org_a", start_day=now - timedelta(days=1), end_day=now
        )
        await loop.refresh(span_days=1)
        second_rows = await async_store.query_connector_daily_usage(
            org_id="org_a", start_day=now - timedelta(days=1), end_day=now
        )
        # Same shape: same number of bucket rows, same totals.
        assert len(first_rows) == len(second_rows)
        first_totals = {r.connector_slug: r.total_tokens for r in first_rows}
        second_totals = {r.connector_slug: r.total_tokens for r in second_rows}
        assert first_totals == second_totals


class TestReconciliation:
    @pytest.mark.asyncio
    async def test_per_call_total_matches_run_total(self) -> None:
        # Reconciliation invariant: for any (org, period), the sum of
        # by_connector totals equals the sum of run-level totals when
        # every call belongs to a run we count. This guards against the
        # rollup loop drifting from the truth.
        store = InMemoryRuntimeApiStore()
        async_store = AsyncInMemoryRuntimeApiStore(store)
        # Use real-now so the loop's `datetime.now(timezone.utc)` window
        # encompasses our seeded `completed_at`.
        now = datetime.now(timezone.utc) - timedelta(seconds=1)
        _seed_run(
            store,
            org_id="org_a",
            user_id="u1",
            run_id="r1",
            completed_at=now,
            input_tokens=100,
            output_tokens=50,
        )
        # Two calls summing to the run total (typical: planning call +
        # follow-up after one tool fires).
        _seed_call(
            store,
            org_id="org_a",
            run_id="r1",
            connector_slug=None,
            created_at=now,
            input_tokens=40,
            output_tokens=20,
        )
        _seed_call(
            store,
            org_id="org_a",
            run_id="r1",
            connector_slug="slack",
            created_at=now,
            input_tokens=60,
            output_tokens=30,
        )
        loop = UsageRollupLoop(
            persistence=async_store,  # type: ignore[arg-type]
            interval_seconds=3600,
            late_arrival_minutes=0,
            backfill_days=0,
        )
        await loop.refresh(span_days=1)
        rows: list[UsageDailyConnectorRow] = list(
            await async_store.query_connector_daily_usage(
                org_id="org_a", start_day=now - timedelta(days=1), end_day=now
            )
        )
        run_totals = (
            store.run_usage["r1"].input_tokens + store.run_usage["r1"].output_tokens
        )
        connector_totals = sum(row.input_tokens + row.output_tokens for row in rows)
        assert connector_totals == run_totals
