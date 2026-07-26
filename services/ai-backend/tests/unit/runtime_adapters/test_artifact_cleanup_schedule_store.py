"""Adversarial durability checks for artifact-cleanup scheduler state."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from runtime_adapters.file.artifact_cleanup_schedule_store import (
    FileArtifactCleanupScheduleStore,
)
from runtime_adapters.in_memory.artifact_cleanup_schedule_store import (
    InMemoryArtifactCleanupScheduleStore,
)


pytestmark = pytest.mark.anyio

NOW = datetime(2026, 7, 26, 12, tzinfo=UTC)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.parametrize(
    "store_factory",
    [
        pytest.param(
            lambda path: InMemoryArtifactCleanupScheduleStore(), id="in-memory"
        ),
        pytest.param(
            lambda path: FileArtifactCleanupScheduleStore(root=path), id="file"
        ),
    ],
)
async def test_expiry_takeover_fences_stale_owner_and_renews_exclusively(
    tmp_path, store_factory
) -> None:  # noqa: ANN001
    store = store_factory(tmp_path)
    first = await store.acquire_lease(owner_id="worker_a", now=NOW, duration_seconds=10)
    assert first is not None
    assert (
        await store.acquire_lease(
            owner_id="worker_b", now=NOW + timedelta(seconds=1), duration_seconds=10
        )
        is None
    )

    # Renewal keeps the same fence generation exclusive past the original
    # expiry. A second owner cannot acquire while that generation is alive.
    renewed = await store.renew_lease(
        owner_id="worker_a",
        fence_token=first.fence_token,
        now=NOW + timedelta(seconds=8),
        duration_seconds=10,
    )
    assert renewed is not None
    assert renewed.fence_token == first.fence_token
    assert (
        await store.acquire_lease(
            owner_id="worker_b", now=NOW + timedelta(seconds=11), duration_seconds=10
        )
        is None
    )

    second = await store.acquire_lease(
        owner_id="worker_b", now=NOW + timedelta(seconds=19), duration_seconds=10
    )
    assert second is not None
    assert second.fence_token > first.fence_token

    # An expired/taken-over worker cannot advance the cursor, defer work, or
    # release the successor's lease.
    assert not await store.complete_tenant(
        owner_id="worker_a",
        fence_token=first.fence_token,
        expected_cursor=None,
        org_id="org_stale",
        now=NOW + timedelta(seconds=19),
    )
    assert (
        await store.defer_failed_tenant(
            owner_id="worker_a",
            fence_token=first.fence_token,
            expected_cursor=None,
            org_id="org_stale",
            now=NOW + timedelta(seconds=19),
            retry_base_seconds=5,
            retry_max_seconds=20,
        )
        is None
    )
    await store.release_lease(
        owner_id="worker_a", fence_token=first.fence_token, now=NOW
    )
    assert (
        await store.renew_lease(
            owner_id="worker_b",
            fence_token=second.fence_token,
            now=NOW + timedelta(seconds=20),
            duration_seconds=10,
        )
        is not None
    )
    assert await store.load_cursor() is None


@pytest.mark.parametrize(
    "store_factory",
    [
        pytest.param(
            lambda path: InMemoryArtifactCleanupScheduleStore(), id="in-memory"
        ),
        pytest.param(
            lambda path: FileArtifactCleanupScheduleStore(root=path), id="file"
        ),
    ],
)
async def test_expired_pass_cannot_overlap_successor_or_release_its_lease(
    tmp_path, store_factory
) -> None:  # noqa: ANN001
    """A paused destructive pass holds its tenant fence beyond scheduler TTL."""

    store = store_factory(tmp_path)
    first = await store.acquire_lease(owner_id="worker_a", now=NOW, duration_seconds=5)
    assert first is not None
    stalled = await store.acquire_tenant_execution(
        owner_id="worker_a",
        fence_token=first.fence_token,
        org_id="org_a",
        now=NOW,
    )
    assert stalled is not None

    # The global lease can be taken over after expiry, but the tenant pass
    # remains exclusive until the stalled owner releases it (or its process
    # dies, which releases the OS/DB lock in durable adapters).
    second = await store.acquire_lease(
        owner_id="worker_b", now=NOW + timedelta(seconds=6), duration_seconds=30
    )
    assert second is not None
    assert second.fence_token > first.fence_token
    assert not await store.validate_tenant_execution(
        execution=stalled, now=NOW + timedelta(seconds=6)
    )
    assert (
        await store.acquire_tenant_execution(
            owner_id="worker_b",
            fence_token=second.fence_token,
            org_id="org_a",
            now=NOW + timedelta(seconds=6),
        )
        is None
    )

    # Expired A is inert: it cannot clear B's current generation.
    await store.release_lease(
        owner_id="worker_a",
        fence_token=first.fence_token,
        now=NOW + timedelta(seconds=6),
    )
    assert (
        await store.renew_lease(
            owner_id="worker_b",
            fence_token=second.fence_token,
            now=NOW + timedelta(seconds=7),
            duration_seconds=30,
        )
        is not None
    )

    await store.release_tenant_execution(execution=stalled)
    successor = await store.acquire_tenant_execution(
        owner_id="worker_b",
        fence_token=second.fence_token,
        org_id="org_a",
        now=NOW + timedelta(seconds=7),
    )
    assert successor is not None
    assert await store.validate_tenant_execution(
        execution=successor, now=NOW + timedelta(seconds=7)
    )
    await store.release_tenant_execution(execution=successor)


@pytest.mark.parametrize(
    "store_factory",
    [
        pytest.param(
            lambda path: InMemoryArtifactCleanupScheduleStore(), id="in-memory"
        ),
        pytest.param(
            lambda path: FileArtifactCleanupScheduleStore(root=path), id="file"
        ),
    ],
)
async def test_deferred_tenant_is_durable_bounded_and_tenant_isolated(
    tmp_path, store_factory
) -> None:  # noqa: ANN001
    store = store_factory(tmp_path)
    lease = await store.acquire_lease(owner_id="worker_a", now=NOW, duration_seconds=60)
    assert lease is not None

    first = await store.defer_failed_tenant(
        owner_id="worker_a",
        fence_token=lease.fence_token,
        expected_cursor=None,
        org_id="org_a",
        now=NOW,
        retry_base_seconds=5,
        retry_max_seconds=12,
    )
    assert first is not None
    assert first.failure_count == 1
    assert first.retry_not_before == NOW + timedelta(seconds=5)
    assert await store.complete_tenant(
        owner_id="worker_a",
        fence_token=lease.fence_token,
        expected_cursor="org_a",
        org_id="org_b",
        now=NOW,
    )
    assert (
        await store.load_deferred_tenant(
            owner_id="worker_a",
            fence_token=lease.fence_token,
            org_id="org_b",
            now=NOW,
        )
        is None
    )

    second = await store.defer_failed_tenant(
        owner_id="worker_a",
        fence_token=lease.fence_token,
        expected_cursor="org_b",
        org_id="org_a",
        now=NOW + timedelta(seconds=5),
        retry_base_seconds=5,
        retry_max_seconds=12,
    )
    assert second is not None
    assert second.failure_count == 2
    assert second.retry_not_before == NOW + timedelta(seconds=15)

    visible = await store.load_deferred_tenant(
        owner_id="worker_a",
        fence_token=lease.fence_token,
        org_id="org_a",
        now=NOW + timedelta(seconds=5),
    )
    assert visible == second
    assert not visible.is_eligible(now=NOW + timedelta(seconds=14))
    assert visible.is_eligible(now=NOW + timedelta(seconds=15))
    assert (
        await store.load_deferred_tenant(
            owner_id="worker_a",
            fence_token=lease.fence_token,
            org_id="org_a",
            now=NOW + timedelta(seconds=15),
        )
        is None
    )


async def test_file_schedule_restart_preserves_defer_but_not_cross_tenant_state(
    tmp_path,
) -> None:
    first = FileArtifactCleanupScheduleStore(root=tmp_path)
    lease = await first.acquire_lease(
        owner_id="worker_one", now=NOW, duration_seconds=30
    )
    assert lease is not None
    deferred = await first.defer_failed_tenant(
        owner_id="worker_one",
        fence_token=lease.fence_token,
        expected_cursor=None,
        org_id="org_first",
        now=NOW,
        retry_base_seconds=5,
        retry_max_seconds=20,
    )
    assert deferred is not None
    await first.release_lease(
        owner_id="worker_one", fence_token=lease.fence_token, now=NOW
    )

    restarted = FileArtifactCleanupScheduleStore(root=tmp_path)
    resumed = await restarted.acquire_lease(
        owner_id="worker_two", now=NOW + timedelta(seconds=1), duration_seconds=30
    )
    assert resumed is not None
    assert await restarted.load_cursor() == "org_first"
    assert (
        await restarted.load_deferred_tenant(
            owner_id="worker_two",
            fence_token=resumed.fence_token,
            org_id="org_first",
            now=NOW + timedelta(seconds=1),
        )
        == deferred
    )
    assert (
        await restarted.load_deferred_tenant(
            owner_id="worker_two",
            fence_token=resumed.fence_token,
            org_id="org_other",
            now=NOW + timedelta(seconds=1),
        )
        is None
    )


async def test_file_restart_observes_global_execution_admission_until_exact_release(
    tmp_path,
) -> None:
    """A second worker cannot bypass a quarantined fence after lease handoff."""

    first = FileArtifactCleanupScheduleStore(root=tmp_path)
    lease_one = await first.acquire_lease(
        owner_id="worker_one", now=NOW, duration_seconds=5
    )
    assert lease_one is not None
    execution = await first.acquire_tenant_execution(
        owner_id="worker_one",
        fence_token=lease_one.fence_token,
        org_id="org_a",
        now=NOW,
        maximum_active_executions=1,
    )
    assert execution is not None
    assert await first.mark_tenant_execution_quarantined(execution=execution, now=NOW)
    pending = await first.mark_tenant_execution_release_pending(
        execution=execution,
        now=NOW,
        retry_base_seconds=5,
        retry_max_seconds=20,
    )
    assert pending is not None
    assert pending.state == "release_pending"

    restarted = FileArtifactCleanupScheduleStore(root=tmp_path)
    lease_two = await restarted.acquire_lease(
        owner_id="worker_two", now=NOW + timedelta(seconds=6), duration_seconds=30
    )
    assert lease_two is not None
    tracked = await restarted.list_tracked_tenant_executions()
    assert len(tracked) == 1
    assert tracked[0].state == "release_pending"
    assert (
        await restarted.acquire_tenant_execution(
            owner_id="worker_two",
            fence_token=lease_two.fence_token,
            org_id="org_b",
            now=NOW + timedelta(seconds=6),
            maximum_active_executions=1,
        )
        is None
    )

    assert await first.release_tenant_execution(execution=execution)
    admitted = await restarted.acquire_tenant_execution(
        owner_id="worker_two",
        fence_token=lease_two.fence_token,
        org_id="org_b",
        now=NOW + timedelta(seconds=6),
        maximum_active_executions=1,
    )
    assert admitted is not None
    assert await restarted.release_tenant_execution(execution=admitted)


async def test_file_upgrade_discards_unfenced_legacy_lease_but_keeps_cursor(
    tmp_path,
) -> None:
    state_dir = tmp_path / "artifact_cleanup_schedule"
    state_dir.mkdir()
    (state_dir / "state.json").write_text(
        '{"cursor":"org_legacy","lease_expires_at":"2026-07-26T13:00:00+00:00",'
        '"lease_owner":"legacy_worker"}',
        encoding="utf-8",
    )
    store = FileArtifactCleanupScheduleStore(root=tmp_path)
    assert await store.load_cursor() == "org_legacy"
    lease = await store.acquire_lease(
        owner_id="fenced_worker", now=NOW, duration_seconds=30
    )
    assert lease is not None
    assert lease.fence_token == 1
