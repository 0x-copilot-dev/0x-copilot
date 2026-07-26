"""Adversarial coverage for the opt-in physical artifact cleanup executor."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
import logging

import pytest

from runtime_adapters.artifact_lifecycle import ArtifactPhysicalCleanupOutcome
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
