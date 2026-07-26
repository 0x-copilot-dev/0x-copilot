"""Live Postgres parity checks for physical-artifact cleanup schedule state."""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest

from runtime_adapters.postgres.artifact_cleanup_schedule_store import (
    PostgresArtifactCleanupScheduleStore,
)
from runtime_adapters.postgres.runtime_api_store import PostgresRuntimeApiStore


pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(
        not os.environ.get("ARTIFACT_CLEANUP_SCHEDULE_LIVE_TEST_DATABASE_URL"),
        reason=(
            "Set ARTIFACT_CLEANUP_SCHEDULE_LIVE_TEST_DATABASE_URL to a disposable "
            "Postgres database to exercise artifact-cleanup schedule state."
        ),
    ),
]


@pytest.fixture
def database_url() -> str:
    return os.environ["ARTIFACT_CLEANUP_SCHEDULE_LIVE_TEST_DATABASE_URL"]


@pytest.fixture
async def runtime_store(database_url: str) -> AsyncIterator[PostgresRuntimeApiStore]:
    store = PostgresRuntimeApiStore(
        database_url,
        pool_min_size=1,
        pool_max_size=4,
        pool_acquire_timeout_seconds=10.0,
    )
    await store.open()
    try:
        await store.migrate()
        async with store._role_connection("worker") as conn:  # noqa: SLF001
            await conn.execute(
                "DELETE FROM runtime_artifact_cleanup_deferred_tenants "
                "WHERE source = %s",
                ("artifact_cleanup_execution",),
            )
            await conn.execute(
                "DELETE FROM runtime_artifact_cleanup_schedule_state WHERE source = %s",
                ("artifact_cleanup_execution",),
            )
        yield store
    finally:
        await store.close()


async def test_postgres_uses_db_time_and_fences_expiry_takeover(
    runtime_store: PostgresRuntimeApiStore,
) -> None:
    store = PostgresArtifactCleanupScheduleStore(store=runtime_store)
    # Deliberately contradictory caller times prove lease authority belongs to
    # clock_timestamp(), rather than a skewed worker process clock.
    first = await store.acquire_lease(
        owner_id="cleanup_worker_one",
        now=datetime(1970, 1, 1, tzinfo=UTC),
        duration_seconds=0.05,
    )
    assert first is not None
    assert first.expires_at > datetime.now(UTC)
    assert (
        await store.acquire_lease(
            owner_id="cleanup_worker_two",
            now=datetime(2099, 1, 1, tzinfo=UTC),
            duration_seconds=1,
        )
        is None
    )

    await asyncio.sleep(0.08)
    second = await store.acquire_lease(
        owner_id="cleanup_worker_two",
        now=datetime(1970, 1, 1, tzinfo=UTC),
        duration_seconds=1,
    )
    assert second is not None
    assert second.fence_token > first.fence_token
    assert not await store.complete_tenant(
        owner_id="cleanup_worker_one",
        fence_token=first.fence_token,
        expected_cursor=None,
        org_id="org_cleanup_stale",
        now=datetime(2099, 1, 1, tzinfo=UTC),
    )
    assert await store.complete_tenant(
        owner_id="cleanup_worker_two",
        fence_token=second.fence_token,
        expected_cursor=None,
        org_id="org_cleanup_a",
        now=datetime(1970, 1, 1, tzinfo=UTC),
    )
    assert await store.load_cursor() == "org_cleanup_a"
    await store.release_lease(
        owner_id="cleanup_worker_two", fence_token=second.fence_token
    )


async def test_postgres_defer_is_bounded_persistent_and_cleared_on_completion(
    runtime_store: PostgresRuntimeApiStore,
) -> None:
    store = PostgresArtifactCleanupScheduleStore(store=runtime_store)
    now = datetime.now(UTC)
    lease = await store.acquire_lease(
        owner_id="cleanup_worker_defer", now=now, duration_seconds=30
    )
    assert lease is not None
    first = await store.defer_failed_tenant(
        owner_id="cleanup_worker_defer",
        fence_token=lease.fence_token,
        expected_cursor=await store.load_cursor(),
        org_id="org_cleanup_deferred",
        now=now,
        retry_base_seconds=5,
        retry_max_seconds=10,
    )
    assert first is not None
    assert first.failure_count == 1
    assert first.retry_not_before >= first.last_failed_at + timedelta(seconds=4)
    second = await store.defer_failed_tenant(
        owner_id="cleanup_worker_defer",
        fence_token=lease.fence_token,
        expected_cursor="org_cleanup_deferred",
        org_id="org_cleanup_deferred",
        now=now,
        retry_base_seconds=5,
        retry_max_seconds=10,
    )
    assert second is not None
    assert second.failure_count == 2
    assert second.retry_not_before >= second.last_failed_at + timedelta(seconds=9)
    assert await store.complete_tenant(
        owner_id="cleanup_worker_defer",
        fence_token=lease.fence_token,
        expected_cursor="org_cleanup_deferred",
        org_id="org_cleanup_next",
        now=now,
    )
    assert (
        await store.load_deferred_tenant(
            owner_id="cleanup_worker_defer",
            fence_token=lease.fence_token,
            org_id="org_cleanup_deferred",
            now=datetime(2099, 1, 1, tzinfo=UTC),
        )
        is not None
    )
    assert await store.complete_tenant(
        owner_id="cleanup_worker_defer",
        fence_token=lease.fence_token,
        expected_cursor="org_cleanup_next",
        org_id="org_cleanup_deferred",
        now=now,
    )
    assert (
        await store.load_deferred_tenant(
            owner_id="cleanup_worker_defer",
            fence_token=lease.fence_token,
            org_id="org_cleanup_deferred",
            now=now,
        )
        is None
    )
