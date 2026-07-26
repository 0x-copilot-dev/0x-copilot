"""Adversarial coverage for the opt-in physical artifact cleanup executor."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
import logging

import pytest

from runtime_adapters.artifact_lifecycle import ArtifactPhysicalCleanupOutcome
from runtime_adapters.artifact_lifecycle import ArtifactCleanupExecutionFence
from runtime_adapters.file.artifact_cleanup_schedule_store import (
    FileArtifactCleanupScheduleStore,
)
from runtime_adapters.in_memory.artifact_cleanup_schedule_store import (
    InMemoryArtifactCleanupScheduleStore,
)
from runtime_worker.jobs.artifact_cleanup_execution import (
    ArtifactCleanupExecutionEnv,
    ArtifactCleanupExecutionRunner,
)


pytestmark = pytest.mark.anyio

NOW = datetime(2026, 7, 26, 12, 0, tzinfo=UTC)
_SECRET_BODY = "body: do-not-log-this-artifact-content"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@dataclass
class _Metrics:
    outcomes: list[str] = field(default_factory=list)

    def record_artifact_cleanup_execution(self, *, outcome: str) -> None:
        self.outcomes.append(outcome)


@dataclass
class _Persistence:
    org_ids: tuple[str, ...]
    plans: dict[str, list[ArtifactPhysicalCleanupOutcome | Exception]]
    calls: list[str] = field(default_factory=list)
    audits: list[tuple[str, dict[str, object]]] = field(default_factory=list)

    async def list_retention_orgs(self) -> Sequence[str]:
        return self.org_ids

    async def execute_artifact_cleanup(
        self,
        *,
        org_id: str,
        now: datetime,
        limit: int,
        execution_fence: object | None = None,
    ) -> ArtifactPhysicalCleanupOutcome:
        del execution_fence
        assert now.tzinfo is not None
        assert limit == 7
        self.calls.append(org_id)
        result = self.plans[org_id].pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    async def write_audit_log(
        self,
        *,
        event_type: str,
        record: dict[str, object],
    ) -> None:
        self.audits.append((event_type, record))


@dataclass
class _SlowPersistence:
    started: asyncio.Event
    release: asyncio.Event
    calls: list[str] = field(default_factory=list)

    async def list_retention_orgs(self) -> Sequence[str]:
        return ("org_a",)

    async def execute_artifact_cleanup(
        self,
        *,
        org_id: str,
        now: datetime,
        limit: int,
        execution_fence: object | None = None,
    ) -> ArtifactPhysicalCleanupOutcome:
        del now, limit, execution_fence
        self.calls.append(org_id)
        self.started.set()
        await self.release.wait()
        return _outcome(org_id)

    async def write_audit_log(
        self,
        *,
        event_type: str,
        record: dict[str, object],
    ) -> None:
        del event_type, record


@dataclass
class _FenceAwareStallingPersistence:
    started: asyncio.Event
    release: asyncio.Event
    destructive_calls: list[str] = field(default_factory=list)

    async def list_retention_orgs(self) -> Sequence[str]:
        return ("org_a",)

    async def execute_artifact_cleanup(
        self,
        *,
        org_id: str,
        now: datetime,
        limit: int,
        execution_fence: ArtifactCleanupExecutionFence | None = None,
    ) -> ArtifactPhysicalCleanupOutcome:
        del now, limit
        assert execution_fence is not None
        await execution_fence.assert_active()
        self.destructive_calls.append(org_id)
        if len(self.destructive_calls) == 1:
            self.started.set()
            await self.release.wait()
            # The generation expired while this lifecycle pass was stalled.
            # It must not transition through another destructive phase after
            # a successor has claimed the global scheduler lease.
            await execution_fence.assert_active()
        return _outcome(org_id)

    async def write_audit_log(
        self,
        *,
        event_type: str,
        record: dict[str, object],
    ) -> None:
        del event_type, record


@dataclass
class _MutableClock:
    value: datetime

    def __call__(self) -> datetime:
        return self.value


class _FirstHeartbeatStopsSchedule(InMemoryArtifactCleanupScheduleStore):
    """Model a paused worker whose lease heartbeat no longer reaches storage."""

    def __init__(self) -> None:
        super().__init__()
        self.stalled_owner: str | None = None

    async def renew_lease(self, **kwargs):  # noqa: ANN003
        if kwargs["owner_id"] == self.stalled_owner:
            return None
        return await super().renew_lease(**kwargs)


def _outcome(org_id: str, **changes: int) -> ArtifactPhysicalCleanupOutcome:
    return ArtifactPhysicalCleanupOutcome(org_id=org_id, **changes)


async def test_runner_is_tenant_bounded_idempotent_and_audits_counts_only() -> None:
    persistence = _Persistence(
        org_ids=("org_b", "org_a", "org_a", "org_c"),
        plans={
            "org_a": [_outcome("org_a", purged_artifacts=2, reaped_blobs=1)],
            "org_b": [_outcome("org_b", withheld_blobs=1)],
            "org_c": [_outcome("org_c", reaped_blobs=99)],
        },
    )
    metrics = _Metrics()
    runner = ArtifactCleanupExecutionRunner(
        persistence=persistence,
        schedule=InMemoryArtifactCleanupScheduleStore(),
        max_orgs=2,
        limit_per_org=7,
        metrics=metrics,  # type: ignore[arg-type]
    )

    result = await runner.run_once(now=NOW)

    assert persistence.calls == ["org_a", "org_b"]
    assert result.tenants_scanned == 2
    assert result.purged_artifacts == 2
    assert result.reaped_blobs == 1
    assert result.withheld_blobs == 1
    assert metrics.outcomes == ["purged", "purged", "reaped", "withheld"]
    assert [event_type for event_type, _record in persistence.audits] == [
        "artifact_cleanup.executed",
        "artifact_cleanup.executed",
    ]
    for _event_type, record in persistence.audits:
        assert set(record) == {
            "org_id",
            "actor_type",
            "action",
            "resource_type",
            "resource_id",
            "outcome",
            "metadata",
        }
        assert set(record["metadata"]) == {
            "purged_artifacts",
            "quarantined_blobs",
            "reaped_blobs",
            "restored_blobs",
            "withheld_blobs",
        }
        assert _SECRET_BODY not in repr(record)


async def test_runner_defers_failure_without_logging_body_then_retries_when_due(
    caplog: pytest.LogCaptureFixture,
) -> None:
    persistence = _Persistence(
        org_ids=("org_retry",),
        plans={
            "org_retry": [
                RuntimeError(_SECRET_BODY),
                _outcome("org_retry", reaped_blobs=1),
                _outcome("org_retry"),
            ]
        },
    )
    metrics = _Metrics()
    runner = ArtifactCleanupExecutionRunner(
        persistence=persistence,
        schedule=InMemoryArtifactCleanupScheduleStore(),
        max_orgs=2,
        limit_per_org=7,
        retry_base_seconds=30,
        retry_max_seconds=120,
        metrics=metrics,  # type: ignore[arg-type]
    )

    with caplog.at_level(logging.WARNING):
        failed = await runner.run_once(now=NOW)
        skipped_until_due = await runner.run_once(now=NOW + timedelta(seconds=29))
        retried = await runner.run_once(now=NOW + timedelta(seconds=30))
        duplicate_delivery = await runner.run_once(now=NOW + timedelta(seconds=30))

    assert failed.failures == 1
    assert failed.deferred_tenants == 1
    assert skipped_until_due.tenants_scanned == 0
    assert retried.reaped_blobs == 1
    assert duplicate_delivery.already_clean_tenants == 1
    assert persistence.calls == ["org_retry", "org_retry", "org_retry"]
    assert [record["outcome"] for _event, record in persistence.audits] == [
        "deferred",
        "completed",
        "already_clean",
    ]
    deferred_metadata = persistence.audits[0][1]["metadata"]
    assert isinstance(deferred_metadata, dict)
    assert deferred_metadata["retry_count"] == 1
    assert _SECRET_BODY not in caplog.text
    assert metrics.outcomes == ["failed", "reaped", "already_clean"]


async def test_persistent_failure_defers_and_later_tenants_progress_then_retries() -> (
    None
):
    """A failing early tenant never freezes the durable global rotation."""

    persistence = _Persistence(
        org_ids=("org_a", "org_b", "org_c"),
        plans={
            "org_a": [RuntimeError(_SECRET_BODY), _outcome("org_a")],
            "org_b": [_outcome("org_b"), _outcome("org_b"), _outcome("org_b")],
            "org_c": [_outcome("org_c"), _outcome("org_c"), _outcome("org_c")],
        },
    )
    schedule = InMemoryArtifactCleanupScheduleStore()
    runner = ArtifactCleanupExecutionRunner(
        persistence=persistence,
        schedule=schedule,
        max_orgs=3,
        limit_per_org=7,
        retry_base_seconds=10,
        retry_max_seconds=40,
        metrics=_Metrics(),  # type: ignore[arg-type]
    )

    first = await runner.run_once(now=NOW)
    before_backoff = await runner.run_once(now=NOW + timedelta(seconds=5))
    after_backoff = await runner.run_once(now=NOW + timedelta(seconds=10))

    assert first.failures == 1
    assert first.deferred_tenants == 1
    # A fails, but B/C still run in the same bounded cycle.
    assert persistence.calls[:3] == ["org_a", "org_b", "org_c"]
    # A is visible but deferred, so B/C keep rotating while it backs off.
    assert before_backoff.failures == 0
    assert before_backoff.tenants_scanned == 2
    assert persistence.calls[3:5] == ["org_b", "org_c"]
    # Once due, A re-enters the same fair page before B/C.
    assert after_backoff.failures == 0
    assert persistence.calls[5:] == ["org_a", "org_b", "org_c"]
    assert await schedule.load_cursor() == "org_c"
    assert all(
        _SECRET_BODY not in repr(record) for _event, record in persistence.audits
    )


def test_execution_flag_is_explicitly_opt_in(monkeypatch) -> None:
    monkeypatch.delenv(ArtifactCleanupExecutionEnv.ENABLED, raising=False)
    assert ArtifactCleanupExecutionEnv.enabled() is False
    monkeypatch.setenv(ArtifactCleanupExecutionEnv.ENABLED, "true")
    assert ArtifactCleanupExecutionEnv.enabled() is True
    monkeypatch.setenv(ArtifactCleanupExecutionEnv.ENABLED, "false")
    assert ArtifactCleanupExecutionEnv.enabled() is False


async def test_runner_rotates_past_continuously_busy_early_tenants() -> None:
    persistence = _Persistence(
        org_ids=("org_a", "org_b", "org_c", "org_d"),
        plans={
            org_id: [_outcome(org_id), _outcome(org_id)]
            for org_id in ("org_a", "org_b", "org_c", "org_d")
        },
    )
    schedule = InMemoryArtifactCleanupScheduleStore()
    runner = ArtifactCleanupExecutionRunner(
        persistence=persistence,
        schedule=schedule,
        max_orgs=2,
        limit_per_org=7,
        metrics=_Metrics(),  # type: ignore[arg-type]
    )

    await runner.run_once(now=NOW)
    await runner.run_once(now=NOW)

    assert persistence.calls == ["org_a", "org_b", "org_c", "org_d"]
    assert await schedule.load_cursor() == "org_d"


async def test_concurrent_runner_cannot_take_or_advance_another_lease() -> None:
    persistence = _Persistence(
        org_ids=("org_a",),
        plans={"org_a": [_outcome("org_a")]},
    )
    schedule = InMemoryArtifactCleanupScheduleStore()
    first = ArtifactCleanupExecutionRunner(
        persistence=persistence,
        schedule=schedule,
        max_orgs=1,
        limit_per_org=7,
        metrics=_Metrics(),  # type: ignore[arg-type]
    )
    second = ArtifactCleanupExecutionRunner(
        persistence=persistence,
        schedule=schedule,
        max_orgs=1,
        limit_per_org=7,
        metrics=_Metrics(),  # type: ignore[arg-type]
    )

    lease = await schedule.acquire_lease(
        owner_id=first._owner_id,  # noqa: SLF001 - lease adversarial probe
        now=NOW,
        duration_seconds=60,
    )
    assert lease is not None
    skipped = await second.run_once(now=NOW)
    assert skipped.tenants_scanned == 0
    assert persistence.calls == []
    assert await schedule.load_cursor() is None
    await schedule.release_lease(
        owner_id=first._owner_id,
        fence_token=lease.fence_token,  # noqa: SLF001
        now=NOW,
    )


async def test_renewing_lease_prevents_long_running_cleanup_overlap() -> None:
    """Heartbeat renewal keeps one generation exclusive through slow IO."""

    persistence = _SlowPersistence(started=asyncio.Event(), release=asyncio.Event())
    schedule = InMemoryArtifactCleanupScheduleStore()
    first = ArtifactCleanupExecutionRunner(
        persistence=persistence,
        schedule=schedule,
        max_orgs=1,
        limit_per_org=7,
        lease_seconds=0.12,
        metrics=_Metrics(),  # type: ignore[arg-type]
    )
    second = ArtifactCleanupExecutionRunner(
        persistence=persistence,
        schedule=schedule,
        max_orgs=1,
        limit_per_org=7,
        lease_seconds=0.12,
        metrics=_Metrics(),  # type: ignore[arg-type]
    )

    first_task = asyncio.create_task(first.run_once())
    await asyncio.wait_for(persistence.started.wait(), timeout=1)
    # This exceeds the original lease. The first worker heartbeats every
    # 50ms, so a contender still cannot acquire a second generation.
    await asyncio.sleep(0.2)
    contender = await second.run_once()
    assert contender.tenants_scanned == 0
    assert persistence.calls == ["org_a"]

    persistence.release.set()
    first_result = await asyncio.wait_for(first_task, timeout=1)
    assert first_result.tenants_scanned == 1


async def test_stalled_tenant_pass_blocks_successor_destructive_cleanup_after_expiry() -> (
    None
):
    """A global lease takeover never overlaps a paused tenant lifecycle pass."""

    clock = _MutableClock(NOW)
    schedule = _FirstHeartbeatStopsSchedule()
    persistence = _FenceAwareStallingPersistence(
        started=asyncio.Event(), release=asyncio.Event()
    )
    first = ArtifactCleanupExecutionRunner(
        persistence=persistence,
        schedule=schedule,
        max_orgs=1,
        limit_per_org=7,
        lease_seconds=0.05,
        metrics=_Metrics(),  # type: ignore[arg-type]
        clock=clock,
    )
    second = ArtifactCleanupExecutionRunner(
        persistence=persistence,
        schedule=schedule,
        max_orgs=1,
        limit_per_org=7,
        lease_seconds=0.05,
        metrics=_Metrics(),  # type: ignore[arg-type]
        clock=clock,
    )

    first_task = asyncio.create_task(first.run_once())
    await asyncio.wait_for(persistence.started.wait(), timeout=1)
    schedule.stalled_owner = first._owner_id  # noqa: SLF001 - adversarial probe
    clock.value = NOW + timedelta(seconds=1)
    await asyncio.sleep(0.1)

    # B owns the new scheduler generation but finds A's durable tenant lock
    # busy, so it performs no concurrent destructive lifecycle call.
    blocked = await second.run_once()
    assert blocked.tenants_scanned == 0
    assert persistence.destructive_calls == ["org_a"]

    persistence.release.set()
    first_result = await asyncio.wait_for(first_task, timeout=1)
    assert first_result.failures == 1
    # Once A exits, the successor can safely perform the later retry.
    resumed = await second.run_once()
    assert resumed.tenants_scanned == 1
    assert persistence.destructive_calls == ["org_a", "org_a"]


async def test_restart_uses_durable_cursor_contract_after_prior_success() -> None:
    persistence = _Persistence(
        org_ids=("org_a", "org_b", "org_c"),
        plans={
            "org_a": [_outcome("org_a")],
            "org_b": [_outcome("org_b")],
            "org_c": [_outcome("org_c")],
        },
    )
    schedule = InMemoryArtifactCleanupScheduleStore()
    first = ArtifactCleanupExecutionRunner(
        persistence=persistence,
        schedule=schedule,
        max_orgs=1,
        limit_per_org=7,
        metrics=_Metrics(),  # type: ignore[arg-type]
    )
    restarted = ArtifactCleanupExecutionRunner(
        persistence=persistence,
        schedule=schedule,
        max_orgs=1,
        limit_per_org=7,
        metrics=_Metrics(),  # type: ignore[arg-type]
    )

    await first.run_once(now=NOW)
    await restarted.run_once(now=NOW)

    assert persistence.calls == ["org_a", "org_b"]
    assert await schedule.load_cursor() == "org_b"


async def test_file_schedule_resume_rotates_after_process_restart(tmp_path) -> None:
    persistence = _Persistence(
        org_ids=("org_a", "org_b", "org_c"),
        plans={
            "org_a": [_outcome("org_a")],
            "org_b": [_outcome("org_b")],
            "org_c": [_outcome("org_c")],
        },
    )
    first = ArtifactCleanupExecutionRunner(
        persistence=persistence,
        schedule=FileArtifactCleanupScheduleStore(root=tmp_path),
        max_orgs=1,
        limit_per_org=7,
        metrics=_Metrics(),  # type: ignore[arg-type]
    )
    await first.run_once(now=NOW)

    # A fresh state adapter and runner model a worker restart. It must resume
    # after org_a rather than restarting at the first lexicographic tenant.
    restarted = ArtifactCleanupExecutionRunner(
        persistence=persistence,
        schedule=FileArtifactCleanupScheduleStore(root=tmp_path),
        max_orgs=1,
        limit_per_org=7,
        metrics=_Metrics(),  # type: ignore[arg-type]
    )
    await restarted.run_once(now=NOW)

    assert persistence.calls == ["org_a", "org_b"]


def test_fair_page_does_not_include_a_stale_foreign_cursor() -> None:
    from runtime_worker.jobs.artifact_cleanup_execution import _fair_org_page

    # The cursor is worker-only metadata. A stale org from a different source
    # inventory is never returned as a cleanup target or used to skip a local
    # tenant; the current trusted inventory begins at its first tenant.
    assert _fair_org_page(
        org_ids=("org_a", "org_b"),
        cursor_after_org_id="org_retired_elsewhere",
        limit=2,
    ) == ("org_a", "org_b")
