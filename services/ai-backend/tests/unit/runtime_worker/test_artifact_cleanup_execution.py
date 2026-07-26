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
    ArtifactCleanupExecutionLoop,
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
class _CancellationProbePersistence:
    """Lifecycle double that can cooperate with or deliberately ignore cancel."""

    org_ids: tuple[str, ...]
    blocking_org_ids: frozenset[str]
    ignore_cancellation_org_ids: frozenset[str]
    calls: list[str] = field(default_factory=list)
    audits: list[tuple[str, dict[str, object]]] = field(default_factory=list)
    started: dict[str, asyncio.Event] = field(init=False)
    finished: dict[str, asyncio.Event] = field(init=False)
    _release: dict[str, asyncio.Event] = field(init=False)

    def __post_init__(self) -> None:
        self.started = {org_id: asyncio.Event() for org_id in self.org_ids}
        self.finished = {org_id: asyncio.Event() for org_id in self.org_ids}
        self._release = {org_id: asyncio.Event() for org_id in self.org_ids}

    def release(self, org_id: str) -> None:
        self._release[org_id].set()

    async def list_retention_orgs(self) -> Sequence[str]:
        return self.org_ids

    async def execute_artifact_cleanup(
        self,
        *,
        org_id: str,
        now: datetime,
        limit: int,
        execution_fence: ArtifactCleanupExecutionFence | None = None,
    ) -> ArtifactPhysicalCleanupOutcome:
        del now
        assert limit == 7
        assert execution_fence is not None
        await execution_fence.assert_active()
        self.calls.append(org_id)
        self.started[org_id].set()
        try:
            if org_id in self.blocking_org_ids:
                while not self._release[org_id].is_set():
                    try:
                        await self._release[org_id].wait()
                    except asyncio.CancelledError:
                        if org_id not in self.ignore_cancellation_org_ids:
                            raise
            # A resumed hung pass must not perform another destructive phase
            # once its original scheduler generation has been released.
            await execution_fence.assert_active()
            return _outcome(org_id)
        finally:
            self.finished[org_id].set()

    async def write_audit_log(
        self,
        *,
        event_type: str,
        record: dict[str, object],
    ) -> None:
        self.audits.append((event_type, record))


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


class _ObservingSchedule(InMemoryArtifactCleanupScheduleStore):
    """In-memory schedule double exposing an exact-fence release for tests."""

    def __init__(self) -> None:
        super().__init__()
        self.execution_released: dict[str, asyncio.Event] = {}

    def release_event(self, org_id: str) -> asyncio.Event:
        return self.execution_released.setdefault(org_id, asyncio.Event())

    async def release_tenant_execution(self, *, execution) -> bool:  # noqa: ANN001
        released = await super().release_tenant_execution(execution=execution)
        self.release_event(execution.org_id).set()
        return released


class _ReleaseFailsOnceSchedule(_ObservingSchedule):
    """Keep the exact in-memory fence live while the first release fails."""

    def __init__(self) -> None:
        super().__init__()
        self.remaining_release_failures = 1
        self.release_failed = asyncio.Event()

    async def release_tenant_execution(self, *, execution) -> bool:  # noqa: ANN001
        if self.remaining_release_failures:
            self.remaining_release_failures -= 1
            self.release_failed.set()
            raise RuntimeError("release transport unavailable")
        return await super().release_tenant_execution(execution=execution)


@dataclass
class _StopTailPersistence:
    """Blocks the actual runner after the loop has begun a cycle."""

    entered: asyncio.Event
    allow_return: asyncio.Event
    calls: int = 0

    async def list_retention_orgs(self) -> Sequence[str]:
        self.calls += 1
        if self.calls == 1:
            self.entered.set()
            await self.allow_return.wait()
        return ()

    async def execute_artifact_cleanup(self, **kwargs):  # noqa: ANN003
        raise AssertionError("no tenant lifecycle should be entered")

    async def write_audit_log(self, **kwargs) -> None:  # noqa: ANN003
        return None


def _outcome(org_id: str, **changes: int) -> ArtifactPhysicalCleanupOutcome:
    return ArtifactPhysicalCleanupOutcome(org_id=org_id, **changes)


async def _wait_for_call_count(
    persistence: _CancellationProbePersistence, expected: int
) -> None:
    while len(persistence.calls) < expected:
        await asyncio.sleep(0.001)


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


async def test_deadline_cancels_cooperative_lifecycle_and_releases_tenant_fence() -> (
    None
):
    """A cooperative pass is deferred, auditable, and conclusively unlocked."""

    persistence = _CancellationProbePersistence(
        org_ids=("org_a",),
        blocking_org_ids=frozenset({"org_a"}),
        ignore_cancellation_org_ids=frozenset(),
    )
    schedule = InMemoryArtifactCleanupScheduleStore()
    runner = ArtifactCleanupExecutionRunner(
        persistence=persistence,
        schedule=schedule,
        max_orgs=1,
        limit_per_org=7,
        tenant_timeout_seconds=0.01,
        cancel_grace_seconds=0.01,
        stop_grace_seconds=0.02,
        metrics=_Metrics(),  # type: ignore[arg-type]
    )

    result = await asyncio.wait_for(runner.run_once(now=NOW), timeout=1)

    assert result.failures == 1
    assert result.deferred_tenants == 1
    assert result.hung_tenants == 0
    assert persistence.calls == ["org_a"]
    assert persistence.finished["org_a"].is_set()
    metadata = persistence.audits[0][1]["metadata"]
    assert isinstance(metadata, dict)
    assert metadata["execution_state"] == "deadline_cancelled"

    # The cancelled task is conclusively stopped, so a later generation can
    # acquire the exact tenant fence (the durable retry itself is still due
    # later, but it never leaks a lock).
    successor = await schedule.acquire_lease(
        owner_id="successor",
        now=NOW,
        duration_seconds=60,
    )
    assert successor is not None
    execution = await schedule.acquire_tenant_execution(
        owner_id="successor",
        fence_token=successor.fence_token,
        org_id="org_a",
        now=NOW,
    )
    assert execution is not None
    await schedule.release_tenant_execution(execution=execution)
    await schedule.release_lease(
        owner_id="successor", fence_token=successor.fence_token, now=NOW
    )


async def test_hung_tenant_is_quarantined_while_later_tenants_progress() -> None:
    """A cancellation-ignoring tenant cannot starve B/C or overlap its retry."""

    persistence = _CancellationProbePersistence(
        org_ids=("org_a", "org_b", "org_c"),
        blocking_org_ids=frozenset({"org_a"}),
        ignore_cancellation_org_ids=frozenset({"org_a"}),
    )
    schedule = _ObservingSchedule()
    runner = ArtifactCleanupExecutionRunner(
        persistence=persistence,
        schedule=schedule,
        max_orgs=3,
        limit_per_org=7,
        tenant_timeout_seconds=0.01,
        cancel_grace_seconds=0.01,
        stop_grace_seconds=0.02,
        metrics=_Metrics(),  # type: ignore[arg-type]
    )

    result = await asyncio.wait_for(runner.run_once(now=NOW), timeout=1)

    assert result.tenants_scanned == 3
    assert result.failures == 1
    assert result.deferred_tenants == 1
    assert result.hung_tenants == 1
    assert persistence.calls == ["org_a", "org_b", "org_c"]
    assert await schedule.load_cursor() == "org_c"
    hung_metadata = persistence.audits[0][1]["metadata"]
    assert isinstance(hung_metadata, dict)
    assert hung_metadata["execution_state"] == "hung_quarantined"

    # A new global owner can work unrelated tenants, but cannot enter the
    # quarantined tenant until the original task exits and releases its exact
    # adapter-backed fence.
    successor = await schedule.acquire_lease(
        owner_id="successor",
        now=NOW,
        duration_seconds=60,
    )
    assert successor is not None
    blocked = await schedule.acquire_tenant_execution(
        owner_id="successor",
        fence_token=successor.fence_token,
        org_id="org_a",
        now=NOW,
    )
    assert blocked is None
    other = await schedule.acquire_tenant_execution(
        owner_id="successor",
        fence_token=successor.fence_token,
        org_id="org_b",
        now=NOW,
    )
    assert other is not None
    await schedule.release_tenant_execution(execution=other)
    await schedule.release_lease(
        owner_id="successor", fence_token=successor.fence_token, now=NOW
    )

    # Late completion does not advance the cursor or write a second audit row.
    # Its only allowed state mutation is releasing the exact quarantined fence.
    released_event = schedule.release_event("org_a")
    persistence.release("org_a")
    await asyncio.wait_for(persistence.finished["org_a"].wait(), timeout=1)
    await asyncio.wait_for(released_event.wait(), timeout=1)
    assert await schedule.load_cursor() == "org_c"
    assert len(persistence.audits) == 3

    later = await schedule.acquire_lease(owner_id="later", now=NOW, duration_seconds=60)
    assert later is not None
    released = await schedule.acquire_tenant_execution(
        owner_id="later",
        fence_token=later.fence_token,
        org_id="org_a",
        now=NOW,
    )
    assert released is not None
    await schedule.release_tenant_execution(execution=released)
    await schedule.release_lease(
        owner_id="later", fence_token=later.fence_token, now=NOW
    )


async def test_quarantine_capacity_fails_closed_without_releasing_hung_fences() -> None:
    """Only bounded hung tasks are admitted; unrelated tenants run below cap."""

    persistence = _CancellationProbePersistence(
        org_ids=("org_a", "org_b", "org_c", "org_d"),
        blocking_org_ids=frozenset({"org_a", "org_c", "org_d"}),
        ignore_cancellation_org_ids=frozenset({"org_a", "org_c", "org_d"}),
    )
    schedule = _ObservingSchedule()
    metrics = _Metrics()
    runner = ArtifactCleanupExecutionRunner(
        persistence=persistence,
        schedule=schedule,
        max_orgs=4,
        limit_per_org=7,
        tenant_timeout_seconds=0.01,
        cancel_grace_seconds=0.01,
        stop_grace_seconds=0.02,
        max_quarantined_executions=2,
        metrics=metrics,  # type: ignore[arg-type]
    )

    saturated = await asyncio.wait_for(runner.run_once(now=NOW), timeout=1)

    # A is quarantined, B still progresses below the cap, C reaches the cap,
    # and D is never admitted. This caps retained Postgres advisory-lock
    # connections as well as file/in-memory tenant locks.
    assert persistence.calls == ["org_a", "org_b", "org_c"]
    assert saturated.tenants_scanned == 3
    assert saturated.hung_tenants == 2
    assert saturated.quarantine_capacity_reached is True
    assert await schedule.load_cursor() == "org_c"
    assert metrics.outcomes.count("quarantine_capacity_reached") == 1
    assert persistence.audits[-1][0] == "artifact_cleanup.quarantine_capacity_reached"
    capacity_metadata = persistence.audits[-1][1]["metadata"]
    assert isinstance(capacity_metadata, dict)
    assert capacity_metadata == {
        "health": "quarantine_capacity_reached",
        "quarantined_execution_count": 2,
        "max_quarantined_executions": 2,
    }

    # A saturated worker does not acquire a new global lease or schedule any
    # additional lifecycle work. It keeps the exact fences for A/C in place.
    blocked = await runner.run_once(now=NOW)
    assert blocked.quarantine_capacity_reached is True
    assert persistence.calls == ["org_a", "org_b", "org_c"]
    assert metrics.outcomes.count("quarantine_capacity_reached") == 1
    assert len(persistence.audits) == 4

    successor = await schedule.acquire_lease(
        owner_id="successor", now=NOW, duration_seconds=60
    )
    assert successor is not None
    for org_id in ("org_a", "org_c"):
        assert (
            await schedule.acquire_tenant_execution(
                owner_id="successor",
                fence_token=successor.fence_token,
                org_id=org_id,
                now=NOW,
            )
            is None
        )
    await schedule.release_lease(
        owner_id="successor", fence_token=successor.fence_token, now=NOW
    )

    # Let the probes exit. Their late callbacks can only release their exact
    # fences; they cannot add audit rows or move the durable cursor.
    release_events = {
        org_id: schedule.release_event(org_id) for org_id in ("org_a", "org_c")
    }
    for org_id in ("org_a", "org_c"):
        persistence.release(org_id)
        await asyncio.wait_for(persistence.finished[org_id].wait(), timeout=1)
        await asyncio.wait_for(release_events[org_id].wait(), timeout=1)
    assert await schedule.load_cursor() == "org_c"
    assert len(persistence.audits) == 4


async def test_durable_quarantine_cap_blocks_a_second_runner_after_lease_handoff() -> (
    None
):
    """A process-local task map cannot bypass the shared admission cap."""

    persistence = _CancellationProbePersistence(
        org_ids=("org_a", "org_b"),
        blocking_org_ids=frozenset({"org_a"}),
        ignore_cancellation_org_ids=frozenset({"org_a"}),
    )
    schedule = _ObservingSchedule()
    first = ArtifactCleanupExecutionRunner(
        persistence=persistence,
        schedule=schedule,
        max_orgs=1,
        limit_per_org=7,
        tenant_timeout_seconds=0.01,
        cancel_grace_seconds=0.01,
        stop_grace_seconds=0.02,
        max_quarantined_executions=1,
        metrics=_Metrics(),  # type: ignore[arg-type]
    )
    second = ArtifactCleanupExecutionRunner(
        persistence=persistence,
        schedule=schedule,
        max_orgs=1,
        limit_per_org=7,
        max_quarantined_executions=1,
        metrics=_Metrics(),  # type: ignore[arg-type]
    )

    first_result = await first.run_once(now=NOW)
    assert first_result.quarantine_capacity_reached is True
    assert persistence.calls == ["org_a"]
    assert len(await schedule.list_tracked_tenant_executions()) == 1

    # A new runner owns no local lifecycle tasks, but it observes the same
    # durable admission record and cannot start unrelated B while the global
    # cap is full.
    blocked = await second.run_once(now=NOW)
    assert blocked.quarantine_capacity_reached is True
    assert persistence.calls == ["org_a"]

    released = schedule.release_event("org_a")
    persistence.release("org_a")
    await asyncio.wait_for(released.wait(), timeout=1)
    assert await schedule.list_tracked_tenant_executions() == ()


async def test_release_failure_retains_capacity_until_bounded_retry_succeeds() -> None:
    """A failed release keeps the exact handle/record and later frees it once."""

    persistence = _CancellationProbePersistence(
        org_ids=("org_a", "org_b"),
        blocking_org_ids=frozenset({"org_a"}),
        ignore_cancellation_org_ids=frozenset({"org_a"}),
    )
    schedule = _ReleaseFailsOnceSchedule()
    first = ArtifactCleanupExecutionRunner(
        persistence=persistence,
        schedule=schedule,
        max_orgs=1,
        limit_per_org=7,
        tenant_timeout_seconds=0.01,
        cancel_grace_seconds=0.01,
        stop_grace_seconds=0.02,
        retry_base_seconds=5,
        retry_max_seconds=20,
        max_quarantined_executions=1,
        metrics=_Metrics(),  # type: ignore[arg-type]
    )
    second = ArtifactCleanupExecutionRunner(
        persistence=persistence,
        schedule=schedule,
        max_orgs=1,
        limit_per_org=7,
        retry_base_seconds=5,
        retry_max_seconds=20,
        max_quarantined_executions=1,
        metrics=_Metrics(),  # type: ignore[arg-type]
    )

    await first.run_once(now=NOW)
    persistence.release("org_a")
    await asyncio.wait_for(schedule.release_failed.wait(), timeout=1)
    # The completed task has a failed release, so it remains globally visible
    # as release-pending and B is still denied to a second worker.
    tracked = await schedule.list_tracked_tenant_executions()
    assert len(tracked) == 1
    assert tracked[0].state == "release_pending"
    assert tracked[0].release_failure_count == 1
    assert (await second.run_once(now=NOW)).quarantine_capacity_reached is True
    assert persistence.calls == ["org_a"]

    # The retry is skipped before its durable backoff, then succeeds after it.
    assert (
        await first.run_once(now=NOW + timedelta(seconds=4))
    ).quarantine_capacity_reached
    resumed = await first.run_once(now=NOW + timedelta(seconds=5))
    assert resumed.tenants_scanned == 1
    assert persistence.calls == ["org_a", "org_b"]
    assert await schedule.list_tracked_tenant_executions() == ()
    assert [event for event, _record in persistence.audits].count(
        "artifact_cleanup.execution_release_pending"
    ) == 1
    assert [event for event, _record in persistence.audits].count(
        "artifact_cleanup.execution_release_resolved"
    ) == 1


async def test_loop_stop_is_bounded_with_a_quarantined_lifecycle_task() -> None:
    """Graceful stop cannot await a cancellation-ignoring pass forever."""

    persistence = _CancellationProbePersistence(
        org_ids=("org_a",),
        blocking_org_ids=frozenset({"org_a"}),
        ignore_cancellation_org_ids=frozenset({"org_a"}),
    )
    schedule = _ObservingSchedule()
    runner = ArtifactCleanupExecutionRunner(
        persistence=persistence,
        schedule=schedule,
        max_orgs=1,
        limit_per_org=7,
        tenant_timeout_seconds=30,
        cancel_grace_seconds=0.01,
        stop_grace_seconds=0.05,
        metrics=_Metrics(),  # type: ignore[arg-type]
    )
    loop = ArtifactCleanupExecutionLoop(runner=runner, interval_seconds=0.001)
    await loop.start()
    await asyncio.wait_for(persistence.started["org_a"].wait(), timeout=1)

    await asyncio.wait_for(loop.stop(), timeout=0.2)
    assert persistence.calls == ["org_a"]
    assert not persistence.finished["org_a"].is_set()

    # The fence survives the bounded shutdown return, so no successor can
    # overlap the still-running pass. Release the test double and let the
    # quarantine callback perform its sole legal mutation: exact-fence release.
    successor = await schedule.acquire_lease(
        owner_id="successor", now=datetime.now(UTC), duration_seconds=60
    )
    assert successor is not None
    assert (
        await schedule.acquire_tenant_execution(
            owner_id="successor",
            fence_token=successor.fence_token,
            org_id="org_a",
            now=datetime.now(UTC),
        )
        is None
    )
    await schedule.release_lease(
        owner_id="successor",
        fence_token=successor.fence_token,
        now=datetime.now(UTC),
    )
    released_event = schedule.release_event("org_a")
    persistence.release("org_a")
    await asyncio.wait_for(persistence.finished["org_a"].wait(), timeout=1)
    await asyncio.wait_for(released_event.wait(), timeout=1)


async def test_loop_timeout_tail_clears_generation_before_restart() -> None:
    """A timed-out loop is restartable only after its original tail exits."""

    persistence = _StopTailPersistence(
        entered=asyncio.Event(), allow_return=asyncio.Event()
    )
    runner = ArtifactCleanupExecutionRunner(
        persistence=persistence,
        schedule=InMemoryArtifactCleanupScheduleStore(),
        max_orgs=1,
        limit_per_org=7,
        cancel_grace_seconds=0.005,
        stop_grace_seconds=0.01,
        metrics=_Metrics(),  # type: ignore[arg-type]
    )
    loop = ArtifactCleanupExecutionLoop(runner=runner, interval_seconds=0.001)
    await loop.start()
    await asyncio.wait_for(persistence.entered.wait(), timeout=1)

    await asyncio.wait_for(loop.stop(), timeout=0.1)
    old_task = loop._task  # noqa: SLF001 - supervisor tail regression probe
    assert old_task is not None
    assert not old_task.done()
    # A second start while the original pass unwinds is inert.
    await loop.start()
    assert loop._task is old_task  # noqa: SLF001

    persistence.allow_return.set()
    await asyncio.wait_for(old_task, timeout=1)
    for _ in range(20):
        if loop._task is None:  # noqa: SLF001
            break
        await asyncio.sleep(0)
    assert loop._task is None  # noqa: SLF001

    await loop.start()
    for _ in range(20):
        if persistence.calls >= 2:
            break
        await asyncio.sleep(0.001)
    assert persistence.calls >= 2
    await asyncio.wait_for(loop.stop(), timeout=0.2)


async def test_loop_restart_resets_stop_signals_without_dropping_fence_state() -> None:
    """A cleanly stopped loop can restart via the runner's supervisor seam."""

    persistence = _CancellationProbePersistence(
        org_ids=("org_a",),
        blocking_org_ids=frozenset({"org_a"}),
        ignore_cancellation_org_ids=frozenset(),
    )
    runner = ArtifactCleanupExecutionRunner(
        persistence=persistence,
        schedule=InMemoryArtifactCleanupScheduleStore(),
        max_orgs=1,
        limit_per_org=7,
        retry_base_seconds=0.001,
        retry_max_seconds=0.001,
        tenant_timeout_seconds=30,
        cancel_grace_seconds=0.01,
        stop_grace_seconds=0.05,
        metrics=_Metrics(),  # type: ignore[arg-type]
    )
    loop = ArtifactCleanupExecutionLoop(runner=runner, interval_seconds=0.001)

    await loop.start()
    await asyncio.wait_for(persistence.started["org_a"].wait(), timeout=1)
    await asyncio.wait_for(loop.stop(), timeout=0.2)
    assert persistence.calls == ["org_a"]

    # The first pass obeyed cancellation and has no retained fence. Reusing
    # the loop must create fresh stop events and clear only its scheduler-stop
    # request; it must not rely on constructing a new runner.
    persistence.release("org_a")
    await loop.start()
    await asyncio.wait_for(_wait_for_call_count(persistence, expected=2), timeout=1)
    await asyncio.wait_for(loop.stop(), timeout=0.2)
    assert len(persistence.calls) >= 2


async def test_loop_restart_preserves_a_quarantined_fence() -> None:
    """Restart cannot treat a prior stop as permission to overlap a hung pass."""

    persistence = _CancellationProbePersistence(
        org_ids=("org_a", "org_b"),
        blocking_org_ids=frozenset({"org_a"}),
        ignore_cancellation_org_ids=frozenset({"org_a"}),
    )
    schedule = _ObservingSchedule()
    runner = ArtifactCleanupExecutionRunner(
        persistence=persistence,
        schedule=schedule,
        max_orgs=2,
        limit_per_org=7,
        tenant_timeout_seconds=30,
        cancel_grace_seconds=0.01,
        stop_grace_seconds=0.05,
        max_quarantined_executions=2,
        metrics=_Metrics(),  # type: ignore[arg-type]
    )
    loop = ArtifactCleanupExecutionLoop(runner=runner, interval_seconds=0.001)

    await loop.start()
    await asyncio.wait_for(persistence.started["org_a"].wait(), timeout=1)
    await asyncio.wait_for(loop.stop(), timeout=0.2)
    assert persistence.calls == ["org_a"]

    # The restarted loop may progress B because it is still below the cap,
    # but its fresh stop signal must not clear A's retained tenant fence.
    await loop.start()
    await asyncio.wait_for(_wait_for_call_count(persistence, expected=2), timeout=1)
    await asyncio.wait_for(loop.stop(), timeout=0.2)
    assert persistence.calls[0:2] == ["org_a", "org_b"]
    assert persistence.calls.count("org_a") == 1

    released_event = schedule.release_event("org_a")
    persistence.release("org_a")
    await asyncio.wait_for(persistence.finished["org_a"].wait(), timeout=1)
    await asyncio.wait_for(released_event.wait(), timeout=1)


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
