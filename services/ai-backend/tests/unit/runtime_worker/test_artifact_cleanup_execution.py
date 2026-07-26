"""Adversarial coverage for the opt-in physical artifact cleanup executor."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
import logging

import pytest

from runtime_adapters.artifact_lifecycle import ArtifactPhysicalCleanupOutcome
from runtime_adapters.in_memory.artifact_cleanup_schedule_store import (
    InMemoryArtifactCleanupScheduleStore,
)
from runtime_adapters.file.artifact_cleanup_schedule_store import (
    FileArtifactCleanupScheduleStore,
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
    ) -> ArtifactPhysicalCleanupOutcome:
        assert now == NOW
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


async def test_runner_retries_after_failure_without_logging_body_or_advancing_state(
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
        metrics=metrics,  # type: ignore[arg-type]
    )

    with caplog.at_level(logging.WARNING):
        failed = await runner.run_once(now=NOW)
        retried = await runner.run_once(now=NOW)
        duplicate_delivery = await runner.run_once(now=NOW)

    assert failed.failures == 1
    assert failed.tenants_scanned == 1
    assert retried.reaped_blobs == 1
    assert duplicate_delivery.already_clean_tenants == 1
    assert persistence.calls == ["org_retry", "org_retry", "org_retry"]
    # Failure evidence is aggregate-only, while the second execution is the
    # sole physical outcome. A duplicate observes durable state as clean.
    assert len(persistence.audits) == 3
    assert persistence.audits[0][1]["outcome"] == "failed"
    assert metrics.outcomes == ["failed", "reaped", "already_clean"]
    assert _SECRET_BODY not in caplog.text


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


async def test_failed_tenant_is_retried_before_later_tenants_and_cursor_does_not_skip(
    caplog: pytest.LogCaptureFixture,
) -> None:
    persistence = _Persistence(
        org_ids=("org_a", "org_b", "org_c"),
        plans={
            "org_a": [_outcome("org_a"), _outcome("org_a")],
            "org_b": [RuntimeError(_SECRET_BODY), _outcome("org_b")],
            "org_c": [_outcome("org_c")],
        },
    )
    schedule = InMemoryArtifactCleanupScheduleStore()
    runner = ArtifactCleanupExecutionRunner(
        persistence=persistence,
        schedule=schedule,
        max_orgs=3,
        limit_per_org=7,
        metrics=_Metrics(),  # type: ignore[arg-type]
    )

    with caplog.at_level(logging.WARNING):
        failed = await runner.run_once(now=NOW)
        recovered = await runner.run_once(now=NOW)

    assert failed.failures == 1
    assert recovered.failures == 0
    assert persistence.calls == ["org_a", "org_b", "org_b", "org_c", "org_a"]
    assert await schedule.load_cursor() == "org_a"
    assert _SECRET_BODY not in caplog.text


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

    acquired = await schedule.acquire_lease(
        owner_id=first._owner_id,  # noqa: SLF001 - lease adversarial probe
        now=NOW,
        expires_at=NOW.replace(hour=13),
    )
    assert acquired
    skipped = await second.run_once(now=NOW)
    assert skipped.tenants_scanned == 0
    assert persistence.calls == []
    assert await schedule.load_cursor() is None
    await schedule.release_lease(owner_id=first._owner_id)  # noqa: SLF001


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
