"""Live Postgres parity checks for physical-artifact cleanup schedule state."""

from __future__ import annotations

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
        yield store
    finally:
        await store.close()


async def test_postgres_cursor_lease_is_restart_safe_and_owner_checked(
    runtime_store: PostgresRuntimeApiStore,
) -> None:
    now = datetime(2026, 7, 26, 12, tzinfo=UTC)
    first = PostgresArtifactCleanupScheduleStore(store=runtime_store)
    assert await first.acquire_lease(
        owner_id="cleanup_worker_one",
        now=now,
        expires_at=now + timedelta(seconds=30),
    )
    assert await first.advance_cursor(
        owner_id="cleanup_worker_one", expected=None, next_cursor="org_cleanup_a"
    )
    assert not await first.advance_cursor(
        owner_id="cleanup_worker_two",
        expected="org_cleanup_a",
        next_cursor="org_cleanup_b",
    )
    await first.release_lease(owner_id="cleanup_worker_one")

    restarted = PostgresArtifactCleanupScheduleStore(store=runtime_store)
    assert await restarted.load_cursor() == "org_cleanup_a"
    assert await restarted.acquire_lease(
        owner_id="cleanup_worker_two",
        now=now + timedelta(seconds=1),
        expires_at=now + timedelta(seconds=31),
    )
    assert await restarted.advance_cursor(
        owner_id="cleanup_worker_two",
        expected="org_cleanup_a",
        next_cursor="org_cleanup_b",
    )
