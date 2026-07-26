"""Durability and ownership checks for artifact-cleanup scheduler state."""

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
async def test_owner_checked_cursor_cannot_cross_worker_boundary(
    tmp_path, store_factory
) -> None:  # noqa: ANN001
    store = store_factory(tmp_path)
    assert await store.acquire_lease(
        owner_id="worker_a",
        now=NOW,
        expires_at=NOW + timedelta(seconds=30),
    )
    assert not await store.acquire_lease(
        owner_id="worker_b",
        now=NOW + timedelta(seconds=1),
        expires_at=NOW + timedelta(seconds=31),
    )
    assert not await store.advance_cursor(
        owner_id="worker_b", expected=None, next_cursor="org_other"
    )
    assert await store.advance_cursor(
        owner_id="worker_a", expected=None, next_cursor="org_a"
    )
    await store.release_lease(owner_id="worker_b")
    assert not await store.acquire_lease(
        owner_id="worker_b",
        now=NOW + timedelta(seconds=2),
        expires_at=NOW + timedelta(seconds=32),
    )
    await store.release_lease(owner_id="worker_a")
    assert await store.acquire_lease(
        owner_id="worker_b",
        now=NOW + timedelta(seconds=2),
        expires_at=NOW + timedelta(seconds=32),
    )
    assert await store.load_cursor() == "org_a"


async def test_file_schedule_cursor_survives_restart_without_tenant_state_leak(
    tmp_path,
) -> None:
    first = FileArtifactCleanupScheduleStore(root=tmp_path)
    assert await first.acquire_lease(
        owner_id="worker_one",
        now=NOW,
        expires_at=NOW + timedelta(seconds=30),
    )
    assert await first.advance_cursor(
        owner_id="worker_one", expected=None, next_cursor="org_first"
    )
    await first.release_lease(owner_id="worker_one")

    restarted = FileArtifactCleanupScheduleStore(root=tmp_path)
    # The only persisted scheduler fact is the opaque last-completed org id;
    # it carries no artifact, user, reference, or legal-hold data from that
    # tenant. A new worker starts after it and can only advance with its lease.
    assert await restarted.load_cursor() == "org_first"
    assert await restarted.acquire_lease(
        owner_id="worker_two",
        now=NOW + timedelta(seconds=1),
        expires_at=NOW + timedelta(seconds=31),
    )
    assert await restarted.advance_cursor(
        owner_id="worker_two", expected="org_first", next_cursor="org_second"
    )
    await restarted.release_lease(owner_id="worker_two")

    assert (tmp_path / "artifact_cleanup_schedule" / "state.json").exists()
    assert (
        await FileArtifactCleanupScheduleStore(root=tmp_path).load_cursor()
        == "org_second"
    )
