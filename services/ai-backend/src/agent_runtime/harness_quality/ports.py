"""Persistence boundary for the local harness evaluation and release spine."""

from __future__ import annotations

from enum import StrEnum
from typing import Protocol, runtime_checkable

from pydantic import Field

from agent_runtime.execution.contracts import RuntimeContract
from agent_runtime.harness_quality.evaluation_contracts import (
    EvaluationCase,
    EvaluationProjectionJob,
    EvaluationRecordKind,
    EvaluationRecordOwner,
    EvaluationResult,
    EvaluationScope,
    EvaluationSuiteRun,
    EvaluationSuiteRunCheckpoint,
    FixtureCatalog,
    HarnessManifest,
    HarnessManifestPointer,
    PairedEvaluationReport,
    ProjectionJobStatus,
    PromotionDecision,
    ProtectedEvaluationArtifact,
    TrajectoryManifest,
)


class EvaluationRepositoryConflict(RuntimeError):
    """A stable record identity was reused for different immutable content."""

    def __init__(self, *, kind: EvaluationRecordKind, record_id: str) -> None:
        self.kind = kind
        self.record_id = record_id
        super().__init__(f"{kind.value} {record_id} conflicts with durable state")


class EvaluationRepositoryCapacityError(RuntimeError):
    """A bounded local evaluation repository rejected new material."""

    code = "evaluation_repository_capacity_exceeded"
    retryable = False


class EvaluationRepositoryCorruption(RuntimeError):
    """Durable evaluation state cannot be folded into valid typed records."""


class EvaluationProtectedArtifactAccessError(RuntimeError):
    """The requested record does not own the protected CAS reference."""


class EvaluationRepositoryLimits(RuntimeContract):
    """Desktop-safe capacity bounds; production composition must pass limits."""

    max_records_per_scope: int = Field(default=10_000, ge=1, le=1_000_000)
    max_projection_jobs_per_scope: int = Field(default=1_000, ge=1, le=100_000)
    max_record_bytes: int = Field(
        default=2 * 1024 * 1024,
        ge=1_024,
        le=64 * 1024 * 1024,
    )
    max_protected_artifacts_per_scope: int = Field(
        default=10_000,
        ge=1,
        le=1_000_000,
    )
    max_protected_artifact_bytes: int = Field(
        default=32 * 1024 * 1024,
        ge=1,
        le=1024 * 1024 * 1024,
    )
    max_protected_bytes_per_scope: int = Field(
        default=512 * 1024 * 1024,
        ge=1,
        le=16 * 1024 * 1024 * 1024,
    )
    max_export_bytes: int = Field(
        default=256 * 1024 * 1024,
        ge=1_024,
        le=16 * 1024 * 1024 * 1024,
    )
    max_list_limit: int = Field(default=1_000, ge=1, le=100_000)
    max_source_runs_per_delete: int = Field(default=1_000, ge=1, le=100_000)
    max_source_run_tombstones: int = Field(
        default=100_000,
        ge=1,
        le=10_000_000,
    )


class EvaluationObjectDeletionPolicy(StrEnum):
    """Whether this repository has exclusive ownership of its object store."""

    DEDICATED_STORE = "dedicated_store"
    SHARED_STORE_METADATA_ONLY = "shared_store_metadata_only"


class EvaluationDeletionReport(RuntimeContract):
    scope: EvaluationScope
    records_deleted: int
    protected_objects_deleted: int
    protected_objects_retained: int


class EvaluationSourceRunDeletionReport(RuntimeContract):
    """Content-free totals for a source-run deletion cascade."""

    source_org_id: str = Field(min_length=1, max_length=160)
    source_runs_requested: int = Field(ge=1)
    projection_jobs_deleted: int = Field(ge=0)
    trajectory_manifests_deleted: int = Field(ge=0)
    protected_objects_deleted: int = Field(ge=0)
    protected_objects_retained: int = Field(ge=0)


@runtime_checkable
class EvaluationProtectedReferenceProvider(Protocol):
    """Synchronous file-GC fence for evaluation-owned CAS objects."""

    def protected_object_digests(self) -> frozenset[str]: ...


@runtime_checkable
class EvaluationRepositoryPort(EvaluationProtectedReferenceProvider, Protocol):
    """One repository for F1 metadata, CAS ownership, and durable cursors."""

    async def put_case(self, scope: EvaluationScope, case: EvaluationCase) -> bool: ...

    async def get_case(
        self,
        scope: EvaluationScope,
        *,
        case_id: str,
        revision: str,
    ) -> EvaluationCase | None: ...

    async def list_cases(
        self,
        scope: EvaluationScope,
        *,
        suite_id: str | None = None,
        limit: int = 100,
    ) -> tuple[EvaluationCase, ...]: ...

    async def put_fixture_catalog(
        self,
        scope: EvaluationScope,
        catalog: FixtureCatalog,
    ) -> bool: ...

    async def get_fixture_catalog(
        self,
        scope: EvaluationScope,
        *,
        catalog_id: str,
        revision: str,
    ) -> FixtureCatalog | None: ...

    async def put_trajectory_manifest(
        self,
        scope: EvaluationScope,
        manifest: TrajectoryManifest,
    ) -> bool: ...

    async def get_trajectory_manifest(
        self,
        scope: EvaluationScope,
        *,
        trajectory_id: str,
    ) -> TrajectoryManifest | None: ...

    async def put_suite_run(
        self,
        scope: EvaluationScope,
        suite_run: EvaluationSuiteRun,
    ) -> bool: ...

    async def get_suite_run(
        self,
        scope: EvaluationScope,
        *,
        suite_run_id: str,
    ) -> EvaluationSuiteRun | None: ...

    async def append_suite_run_checkpoint(
        self,
        scope: EvaluationScope,
        checkpoint: EvaluationSuiteRunCheckpoint,
    ) -> bool: ...

    async def latest_suite_run_checkpoint(
        self,
        scope: EvaluationScope,
        *,
        suite_run_id: str,
    ) -> EvaluationSuiteRunCheckpoint | None: ...

    async def put_evaluation_result(
        self,
        scope: EvaluationScope,
        result: EvaluationResult,
    ) -> bool: ...

    async def get_evaluation_result(
        self,
        scope: EvaluationScope,
        *,
        evaluation_run_id: str,
    ) -> EvaluationResult | None: ...

    async def list_evaluation_results(
        self,
        scope: EvaluationScope,
        *,
        variant_id: str | None = None,
        case_id: str | None = None,
        limit: int = 100,
    ) -> tuple[EvaluationResult, ...]: ...

    async def put_paired_report(
        self,
        scope: EvaluationScope,
        report: PairedEvaluationReport,
    ) -> bool: ...

    async def get_paired_report(
        self,
        scope: EvaluationScope,
        *,
        report_id: str,
    ) -> PairedEvaluationReport | None: ...

    async def put_promotion_decision(
        self,
        scope: EvaluationScope,
        decision: PromotionDecision,
    ) -> bool: ...

    async def list_promotion_decisions(
        self,
        scope: EvaluationScope,
        *,
        limit: int = 100,
    ) -> tuple[PromotionDecision, ...]: ...

    async def put_harness_manifest(
        self,
        scope: EvaluationScope,
        manifest: HarnessManifest,
    ) -> bool: ...

    async def get_harness_manifest(
        self,
        scope: EvaluationScope,
        *,
        manifest_id: str,
        revision: str,
    ) -> HarnessManifest | None: ...

    async def list_harness_manifests(
        self,
        scope: EvaluationScope,
        *,
        limit: int = 100,
    ) -> tuple[HarnessManifest, ...]: ...

    async def get_active_harness_manifest(
        self,
        scope: EvaluationScope,
    ) -> HarnessManifestPointer | None: ...

    async def compare_and_set_active_harness_manifest(
        self,
        scope: EvaluationScope,
        *,
        expected: HarnessManifestPointer | None,
        replacement: HarnessManifestPointer,
    ) -> HarnessManifestPointer: ...

    async def put_projection_job(
        self,
        scope: EvaluationScope,
        job: EvaluationProjectionJob,
    ) -> bool: ...

    async def get_projection_job(
        self,
        scope: EvaluationScope,
        *,
        job_id: str,
    ) -> EvaluationProjectionJob | None: ...

    async def list_projection_jobs(
        self,
        scope: EvaluationScope,
        *,
        statuses: frozenset[ProjectionJobStatus] | None = None,
        limit: int = 100,
    ) -> tuple[EvaluationProjectionJob, ...]: ...

    async def compare_and_set_projection_job(
        self,
        scope: EvaluationScope,
        *,
        expected_version: int,
        replacement: EvaluationProjectionJob,
    ) -> EvaluationProjectionJob: ...

    async def put_protected_artifact(
        self,
        scope: EvaluationScope,
        *,
        data: bytes,
        media_type: str = "application/octet-stream",
    ) -> ProtectedEvaluationArtifact: ...

    async def get_protected_artifact(
        self,
        scope: EvaluationScope,
        *,
        owner: EvaluationRecordOwner,
        ref: ProtectedEvaluationArtifact,
    ) -> bytes: ...

    async def export_scope(self, scope: EvaluationScope) -> bytes: ...

    async def delete_scope(
        self,
        scope: EvaluationScope,
    ) -> EvaluationDeletionReport: ...

    async def delete_source_runs(
        self,
        *,
        source_org_id: str,
        source_run_ids: frozenset[str],
    ) -> EvaluationSourceRunDeletionReport: ...


__all__ = (
    "EvaluationDeletionReport",
    "EvaluationObjectDeletionPolicy",
    "EvaluationProtectedArtifactAccessError",
    "EvaluationProtectedReferenceProvider",
    "EvaluationRepositoryCapacityError",
    "EvaluationRepositoryConflict",
    "EvaluationRepositoryCorruption",
    "EvaluationRepositoryLimits",
    "EvaluationRepositoryPort",
    "EvaluationSourceRunDeletionReport",
)
