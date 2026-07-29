"""Bounded, consent-gated projection of terminal runs into F1 trajectories."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timedelta, timezone
import hashlib
import logging

from pydantic import Field

from agent_runtime.api.ports import EventStorePort
from agent_runtime.api.run_termination import TerminationReason
from agent_runtime.execution.contracts import RuntimeContract
from agent_runtime.harness_quality.evaluation import TrajectoryProjector
from agent_runtime.harness_quality.evaluation_contracts import (
    EvaluationProjectionJob,
    EvaluationScope,
    ProjectionJobStatus,
    ProjectionPolicy,
)
from agent_runtime.harness_quality.ports import (
    EvaluationRepositoryConflict,
    EvaluationRepositoryPort,
)
from agent_runtime.surfaces_v2.canonical_json import canonical_json_sha256
from runtime_api.schemas import (
    AgentRunStatus,
    RunRecord,
    RuntimeApiEventType,
    RuntimeEventEnvelope,
)


_LOGGER = logging.getLogger(__name__)


class ProjectionJobLimits(RuntimeContract):
    """Hard local bounds for projection scheduling and execution."""

    max_events_per_run: int = Field(default=10_000, ge=1, le=100_000)
    max_attempts: int = Field(default=3, ge=1, le=10)
    lease_seconds: int = Field(default=60, ge=1, le=3_600)
    claim_batch_size: int = Field(default=20, ge=1, le=100)


class TerminalEvaluationProjectionScheduler:
    """Create one immutable, idempotent job after a terminal event is durable."""

    def __init__(
        self,
        *,
        repository: EvaluationRepositoryPort,
        scope: EvaluationScope,
        policy: ProjectionPolicy,
        is_development_run: bool,
        limits: ProjectionJobLimits = ProjectionJobLimits(),
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self._repository = repository
        self._scope = scope
        self._policy = policy
        self._is_development_run = is_development_run
        self._limits = limits
        self._clock = clock

    async def observe_terminal_run(
        self,
        *,
        run: RunRecord,
        terminal_status: AgentRunStatus,
        reason: TerminationReason,
        terminal_event: RuntimeEventEnvelope,
    ) -> None:
        del terminal_status, reason
        if not self._policy.permits(is_development_run=self._is_development_run):
            return
        if terminal_event.run_id != run.run_id:
            raise ValueError("terminal projection event is outside the run scope")
        if terminal_event.sequence_no > self._limits.max_events_per_run:
            await self._record_skipped(
                run=run,
                terminal_event=terminal_event,
                reason_code="event_limit_exceeded",
            )
            return

        values = self._job_values(
            run=run,
            terminal_event=terminal_event,
            variant_id=None,
            status=ProjectionJobStatus.PENDING,
            failure_reason_code=None,
        )
        await self._repository.put_projection_job(
            self._scope,
            EvaluationProjectionJob(
                **values,
                job_digest=EvaluationProjectionJob.digest_for(**values),
            ),
        )

    async def _record_skipped(
        self,
        *,
        run: RunRecord,
        terminal_event: RuntimeEventEnvelope,
        reason_code: str,
    ) -> None:
        values = self._job_values(
            run=run,
            terminal_event=terminal_event,
            variant_id="variant_unavailable",
            status=ProjectionJobStatus.SKIPPED,
            failure_reason_code=reason_code,
        )
        await self._repository.put_projection_job(
            self._scope,
            EvaluationProjectionJob(
                **values,
                job_digest=EvaluationProjectionJob.digest_for(**values),
            ),
        )
        _LOGGER.info(
            "evaluation_projection_skipped",
            extra={
                "metadata": {
                    "run_id": run.run_id,
                    "reason_code": reason_code,
                }
            },
        )

    def _job_values(
        self,
        *,
        run: RunRecord,
        terminal_event: RuntimeEventEnvelope,
        variant_id: str | None,
        status: ProjectionJobStatus,
        failure_reason_code: str | None,
    ) -> dict[str, object]:
        now = self._clock()
        return {
            "job_id": _projection_job_id(
                scope=self._scope,
                run_id=run.run_id,
                policy_revision=self._policy.revision,
                terminal_sequence_no=terminal_event.sequence_no,
            ),
            "source_org_id": run.org_id,
            "source_run_id": run.run_id,
            "variant_id": variant_id,
            "policy_revision": self._policy.revision,
            "terminal_sequence_no": terminal_event.sequence_no,
            "status": status,
            "next_sequence_no": 1,
            "attempt_count": 0,
            "lease_owner_digest": None,
            "lease_expires_at": None,
            "trajectory_id": None,
            "failure_reason_code": failure_reason_code,
            "version": 0,
            "created_at": now,
            "updated_at": now,
        }


class EvaluationProjectionJobRunner:
    """Lease, resume, and finish one fixture-free read-only projection job."""

    def __init__(
        self,
        *,
        repository: EvaluationRepositoryPort,
        event_store: EventStorePort,
        scope: EvaluationScope,
        worker_id: str,
        redaction_policy_revision: str,
        limits: ProjectionJobLimits = ProjectionJobLimits(),
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        if not worker_id.strip():
            raise ValueError("projection worker_id must be non-empty")
        self._repository = repository
        self._event_store = event_store
        self._scope = scope
        self._lease_owner_digest = hashlib.sha256(worker_id.encode("utf-8")).hexdigest()
        self._projector = TrajectoryProjector(
            redaction_policy_revision=redaction_policy_revision
        )
        self._limits = limits
        self._clock = clock

    async def run_once(self) -> bool:
        now = self._clock()
        candidates = await self._repository.list_projection_jobs(
            self._scope,
            statuses=frozenset(
                {ProjectionJobStatus.PENDING, ProjectionJobStatus.RUNNING}
            ),
            limit=self._limits.claim_batch_size,
        )
        for candidate in candidates:
            if not self._claimable(candidate, now=now):
                continue
            claimed = await self._try_claim(candidate, now=now)
            if claimed is None:
                continue
            await self._project(claimed)
            return True
        return False

    def _claimable(
        self,
        job: EvaluationProjectionJob,
        *,
        now: datetime,
    ) -> bool:
        if job.attempt_count >= self._limits.max_attempts:
            return False
        if job.status is ProjectionJobStatus.PENDING:
            return True
        return bool(
            job.status is ProjectionJobStatus.RUNNING
            and job.lease_expires_at is not None
            and job.lease_expires_at <= now
        )

    async def _try_claim(
        self,
        job: EvaluationProjectionJob,
        *,
        now: datetime,
    ) -> EvaluationProjectionJob | None:
        replacement = _replace_job(
            job,
            status=ProjectionJobStatus.RUNNING,
            attempt_count=job.attempt_count + 1,
            lease_owner_digest=self._lease_owner_digest,
            lease_expires_at=now + timedelta(seconds=self._limits.lease_seconds),
            failure_reason_code=None,
            version=job.version + 1,
            updated_at=now,
        )
        try:
            return await self._repository.compare_and_set_projection_job(
                self._scope,
                expected_version=job.version,
                replacement=replacement,
            )
        except EvaluationRepositoryConflict:
            return None

    async def _project(self, job: EvaluationProjectionJob) -> None:
        try:
            events = await self._event_store.list_events_after(
                org_id=job.source_org_id,
                run_id=job.source_run_id,
                after_sequence=0,
            )
            projected_events = _bounded_terminal_events(
                events,
                terminal_sequence_no=job.terminal_sequence_no,
                maximum=self._limits.max_events_per_run,
            )
            variant_ref = _variant_ref_from_events(projected_events)
            if variant_ref is None:
                raise _ProjectionFailure("control_snapshot_missing")
            resolved_variant_id = _opaque_variant_id(variant_ref)
            if job.variant_id is not None and resolved_variant_id != job.variant_id:
                raise _ProjectionFailure("variant_mismatch")
            trajectory_id = f"trajectory_{job.job_id}"
            trajectory = self._projector.project(
                run_id=job.source_run_id,
                variant_id=resolved_variant_id,
                events=projected_events,
                harness_revisions=_harness_revisions(projected_events),
                trajectory_id=trajectory_id,
            )
            await self._repository.put_trajectory_manifest(
                self._scope,
                trajectory,
            )
            completed = _replace_job(
                job,
                status=ProjectionJobStatus.SUCCEEDED,
                variant_id=resolved_variant_id,
                next_sequence_no=job.terminal_sequence_no + 1,
                lease_owner_digest=None,
                lease_expires_at=None,
                trajectory_id=trajectory_id,
                failure_reason_code=None,
                version=job.version + 1,
                updated_at=self._clock(),
            )
            await self._repository.compare_and_set_projection_job(
                self._scope,
                expected_version=job.version,
                replacement=completed,
            )
        except Exception as exc:  # noqa: BLE001 - persist a bounded reason only
            await self._fail(job, reason_code=_projection_failure_code(exc))

    async def _fail(
        self,
        job: EvaluationProjectionJob,
        *,
        reason_code: str,
    ) -> None:
        failed = _replace_job(
            job,
            status=ProjectionJobStatus.FAILED,
            lease_owner_digest=None,
            lease_expires_at=None,
            failure_reason_code=reason_code,
            version=job.version + 1,
            updated_at=self._clock(),
        )
        try:
            await self._repository.compare_and_set_projection_job(
                self._scope,
                expected_version=job.version,
                replacement=failed,
            )
        except EvaluationRepositoryConflict:
            return


class _ProjectionFailure(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


def _replace_job(
    job: EvaluationProjectionJob,
    **updates: object,
) -> EvaluationProjectionJob:
    values = job.model_dump(mode="python", exclude={"job_digest"})
    values.update(updates)
    return EvaluationProjectionJob(
        **values,
        job_digest=EvaluationProjectionJob.digest_for(**values),
    )


def _projection_job_id(
    *,
    scope: EvaluationScope,
    run_id: str,
    policy_revision: str,
    terminal_sequence_no: int,
) -> str:
    digest = canonical_json_sha256(
        {
            "scope": scope.model_dump(mode="json"),
            "run_id": run_id,
            "policy_revision": policy_revision,
            "terminal_sequence_no": terminal_sequence_no,
        }
    )
    return f"projection_{digest[:40]}"


def _opaque_variant_id(variant_ref: str) -> str:
    if len(variant_ref) <= 160:
        return variant_ref
    return f"variant_sha256_{hashlib.sha256(variant_ref.encode()).hexdigest()}"


def _variant_ref_from_events(
    events: Sequence[RuntimeEventEnvelope],
) -> str | None:
    matches = tuple(
        str(event.payload.get("harness_variant_ref"))
        for event in events
        if event.event_type is RuntimeApiEventType.QUALITY_CONTROL_BOUND
        and isinstance(event.payload.get("harness_variant_ref"), str)
        and str(event.payload["harness_variant_ref"]).strip()
    )
    if len(matches) != 1:
        return None
    return matches[0]


def _bounded_terminal_events(
    events: Sequence[RuntimeEventEnvelope],
    *,
    terminal_sequence_no: int,
    maximum: int,
) -> tuple[RuntimeEventEnvelope, ...]:
    if terminal_sequence_no > maximum:
        raise _ProjectionFailure("event_limit_exceeded")
    selected = tuple(
        sorted(
            (event for event in events if event.sequence_no <= terminal_sequence_no),
            key=lambda event: event.sequence_no,
        )
    )
    if (
        len(selected) != terminal_sequence_no
        or not selected
        or selected[-1].sequence_no != terminal_sequence_no
    ):
        raise _ProjectionFailure("terminal_event_gap")
    if selected[-1].event_type not in {
        RuntimeApiEventType.RUN_COMPLETED,
        RuntimeApiEventType.RUN_FAILED,
        RuntimeApiEventType.RUN_CANCELLED,
    }:
        raise _ProjectionFailure("terminal_event_missing")
    return selected


def _harness_revisions(
    events: Sequence[RuntimeEventEnvelope],
) -> Mapping[str, str]:
    control = next(
        (
            event
            for event in events
            if event.event_type is RuntimeApiEventType.QUALITY_CONTROL_BOUND
        ),
        None,
    )
    if control is None:
        return {}
    revisions = {
        key: value
        for key, value in control.payload.items()
        if (
            isinstance(value, str)
            and (key.endswith("_revision") or key == "harness_variant_ref")
        )
    }
    return dict(sorted(revisions.items()))


def _projection_failure_code(exc: Exception) -> str:
    if isinstance(exc, _ProjectionFailure):
        return exc.reason_code
    if isinstance(exc, EvaluationRepositoryConflict):
        return "repository_conflict"
    if isinstance(exc, ValueError):
        return "invalid_projection_input"
    return "projection_failed"


__all__ = (
    "EvaluationProjectionJobRunner",
    "ProjectionJobLimits",
    "TerminalEvaluationProjectionScheduler",
)
