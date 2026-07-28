"""Crash-safe desktop repository for local harness evaluation and promotion."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from typing import ClassVar

from pydantic import ValidationError

from agent_runtime.harness_quality.evaluation_contracts import (
    EvaluationCase,
    EvaluationProjectionJob,
    EvaluationRecordKind,
    EvaluationRecordOwner,
    EvaluationRepositoryRecord,
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
    evaluation_record_owner,
)
from agent_runtime.harness_quality.ports import (
    EvaluationDeletionReport,
    EvaluationObjectDeletionPolicy,
    EvaluationProtectedArtifactAccessError,
    EvaluationRepositoryCapacityError,
    EvaluationRepositoryConflict,
    EvaluationRepositoryCorruption,
    EvaluationRepositoryLimits,
    EvaluationSourceRunDeletionReport,
)
from agent_runtime.surfaces_v2.canonical_json import (
    canonical_json_bytes,
    canonical_json_sha256,
)
from runtime_adapters.file._advisory_lock import (
    acquire_exclusive,
    release_exclusive,
)
from runtime_adapters.file._paths import FileStoreLayout
from runtime_adapters.file._state_ledger import StateLedger
from runtime_adapters.file.object_store import (
    FileObjectStore,
    ObjectRef,
    ObjectStoreError,
)

_CAS_PREFIX = "eval-cas://sha256/"
_STAGING_TABLE = "harness_quality_protected_staging"
_DELETION_TABLE = "harness_quality_scope_deletions"
_SOURCE_DELETION_TABLE = "harness_quality_source_run_deletions"
_ACTIVE_ID = "active"
_RECORD_MEDIA_TYPE = "application/vnd.0xcopilot.evaluation-record+json"
_CAS_BACKED_KINDS = frozenset(
    {
        EvaluationRecordKind.FIXTURE_CATALOG,
        EvaluationRecordKind.PAIRED_REPORT,
        EvaluationRecordKind.PROMOTION_DECISION,
    }
)

_RECORD_TYPES: dict[EvaluationRecordKind, type[EvaluationRepositoryRecord]] = {
    EvaluationRecordKind.CASE: EvaluationCase,
    EvaluationRecordKind.FIXTURE_CATALOG: FixtureCatalog,
    EvaluationRecordKind.TRAJECTORY_MANIFEST: TrajectoryManifest,
    EvaluationRecordKind.SUITE_RUN: EvaluationSuiteRun,
    EvaluationRecordKind.SUITE_RUN_CHECKPOINT: EvaluationSuiteRunCheckpoint,
    EvaluationRecordKind.EVALUATION_RESULT: EvaluationResult,
    EvaluationRecordKind.PAIRED_REPORT: PairedEvaluationReport,
    EvaluationRecordKind.PROMOTION_DECISION: PromotionDecision,
    EvaluationRecordKind.HARNESS_MANIFEST: HarnessManifest,
    EvaluationRecordKind.HARNESS_MANIFEST_POINTER: HarnessManifestPointer,
    EvaluationRecordKind.PROJECTION_JOB: EvaluationProjectionJob,
}


class FileEvaluationRepository:
    """Bounded StateLedger metadata plus protected bodies in the existing CAS."""

    _LOCK_FILENAME: ClassVar[str] = ".harness-quality.lock"
    _FILE_MODE: ClassVar[int] = 0o600

    def __init__(
        self,
        layout: FileStoreLayout,
        *,
        object_store: FileObjectStore | None = None,
        limits: EvaluationRepositoryLimits | None = None,
        object_deletion_policy: EvaluationObjectDeletionPolicy = (
            EvaluationObjectDeletionPolicy.DEDICATED_STORE
        ),
    ) -> None:
        self._layout = layout
        self._layout.ensure_scaffold()
        self._object_store = object_store or FileObjectStore(layout)
        self._limits = limits or EvaluationRepositoryLimits()
        self._object_deletion_policy = object_deletion_policy
        self._ledgers = {
            kind: StateLedger(layout.state_path(f"harness_quality_{kind.value}"))
            for kind in EvaluationRecordKind
        }
        self._staging_ledger = StateLedger(layout.state_path(_STAGING_TABLE))
        self._deletion_ledger = StateLedger(layout.state_path(_DELETION_TABLE))
        self._source_deletion_ledger = StateLedger(
            layout.state_path(_SOURCE_DELETION_TABLE)
        )
        self._lock_path = layout.state_dir / self._LOCK_FILENAME
        self._lock = asyncio.Lock()
        self._records: dict[
            str,
            dict[EvaluationRecordKind, dict[str, EvaluationRepositoryRecord]],
        ] = {}
        self._scopes: dict[str, EvaluationScope] = {}
        self._staged: dict[str, dict[str, ProtectedEvaluationArtifact]] = {}
        self._deletion_plans: dict[str, dict[str, object]] = {}
        self._source_deletions: dict[str, dict[str, object]] = {}
        with self._exclusive_lock():
            self._reload()
            self._recover_pending_deletions()
            self._recover_pending_source_deletions()
            self._recover_loaded_ledgers()

    async def put_case(self, scope: EvaluationScope, case: EvaluationCase) -> bool:
        return await self._put(scope, case)

    async def get_case(
        self,
        scope: EvaluationScope,
        *,
        case_id: str,
        revision: str,
    ) -> EvaluationCase | None:
        return await self._get_by_fields(
            scope,
            EvaluationRecordKind.CASE,
            EvaluationCase,
            case_id=case_id,
            revision=revision,
        )

    async def list_cases(
        self,
        scope: EvaluationScope,
        *,
        suite_id: str | None = None,
        limit: int = 100,
    ) -> tuple[EvaluationCase, ...]:
        self._validate_limit(limit)
        async with self._locked():
            rows = (
                row
                for row in self._ordered(scope, EvaluationRecordKind.CASE)
                if isinstance(row, EvaluationCase)
                and (suite_id is None or row.suite_id == suite_id)
            )
            return tuple(rows)[:limit]

    async def put_fixture_catalog(
        self,
        scope: EvaluationScope,
        catalog: FixtureCatalog,
    ) -> bool:
        return await self._put(scope, catalog)

    async def get_fixture_catalog(
        self,
        scope: EvaluationScope,
        *,
        catalog_id: str,
        revision: str,
    ) -> FixtureCatalog | None:
        return await self._get_by_fields(
            scope,
            EvaluationRecordKind.FIXTURE_CATALOG,
            FixtureCatalog,
            catalog_id=catalog_id,
            revision=revision,
        )

    async def put_trajectory_manifest(
        self,
        scope: EvaluationScope,
        manifest: TrajectoryManifest,
    ) -> bool:
        async with self._locked():
            if _trajectory_key(manifest.trajectory_id) in (
                self._deleted_trajectory_keys()
            ):
                return False
            return self._put_locked(scope, manifest)

    async def get_trajectory_manifest(
        self,
        scope: EvaluationScope,
        *,
        trajectory_id: str,
    ) -> TrajectoryManifest | None:
        return await self._get_by_fields(
            scope,
            EvaluationRecordKind.TRAJECTORY_MANIFEST,
            TrajectoryManifest,
            trajectory_id=trajectory_id,
        )

    async def put_suite_run(
        self,
        scope: EvaluationScope,
        suite_run: EvaluationSuiteRun,
    ) -> bool:
        async with self._locked():
            self._validate_suite_dependencies(scope, suite_run)
            return self._put_locked(scope, suite_run)

    async def get_suite_run(
        self,
        scope: EvaluationScope,
        *,
        suite_run_id: str,
    ) -> EvaluationSuiteRun | None:
        return await self._get_by_fields(
            scope,
            EvaluationRecordKind.SUITE_RUN,
            EvaluationSuiteRun,
            suite_run_id=suite_run_id,
        )

    async def append_suite_run_checkpoint(
        self,
        scope: EvaluationScope,
        checkpoint: EvaluationSuiteRunCheckpoint,
    ) -> bool:
        async with self._locked():
            self._validate_checkpoint(scope, checkpoint)
            return self._put_locked(scope, checkpoint)

    async def latest_suite_run_checkpoint(
        self,
        scope: EvaluationScope,
        *,
        suite_run_id: str,
    ) -> EvaluationSuiteRunCheckpoint | None:
        async with self._locked():
            matches = [
                row
                for row in self._records_for(
                    scope, EvaluationRecordKind.SUITE_RUN_CHECKPOINT
                ).values()
                if isinstance(row, EvaluationSuiteRunCheckpoint)
                and row.suite_run_id == suite_run_id
            ]
            return max(matches, key=lambda row: row.checkpoint_no, default=None)

    async def put_evaluation_result(
        self,
        scope: EvaluationScope,
        result: EvaluationResult,
    ) -> bool:
        return await self._put(scope, result)

    async def get_evaluation_result(
        self,
        scope: EvaluationScope,
        *,
        evaluation_run_id: str,
    ) -> EvaluationResult | None:
        return await self._get_by_fields(
            scope,
            EvaluationRecordKind.EVALUATION_RESULT,
            EvaluationResult,
            evaluation_run_id=evaluation_run_id,
        )

    async def list_evaluation_results(
        self,
        scope: EvaluationScope,
        *,
        variant_id: str | None = None,
        case_id: str | None = None,
        limit: int = 100,
    ) -> tuple[EvaluationResult, ...]:
        self._validate_limit(limit)
        async with self._locked():
            rows = (
                row
                for row in self._ordered(scope, EvaluationRecordKind.EVALUATION_RESULT)
                if isinstance(row, EvaluationResult)
                and (variant_id is None or row.variant_id == variant_id)
                and (case_id is None or row.case_id == case_id)
            )
            return tuple(rows)[:limit]

    async def put_paired_report(
        self,
        scope: EvaluationScope,
        report: PairedEvaluationReport,
    ) -> bool:
        return await self._put(scope, report)

    async def get_paired_report(
        self,
        scope: EvaluationScope,
        *,
        report_id: str,
    ) -> PairedEvaluationReport | None:
        return await self._get_by_fields(
            scope,
            EvaluationRecordKind.PAIRED_REPORT,
            PairedEvaluationReport,
            report_id=report_id,
        )

    async def put_promotion_decision(
        self,
        scope: EvaluationScope,
        decision: PromotionDecision,
    ) -> bool:
        return await self._put(scope, decision)

    async def list_promotion_decisions(
        self,
        scope: EvaluationScope,
        *,
        limit: int = 100,
    ) -> tuple[PromotionDecision, ...]:
        rows = await self._list(scope, EvaluationRecordKind.PROMOTION_DECISION, limit)
        return tuple(row for row in rows if isinstance(row, PromotionDecision))

    async def put_harness_manifest(
        self,
        scope: EvaluationScope,
        manifest: HarnessManifest,
    ) -> bool:
        return await self._put(scope, manifest)

    async def get_harness_manifest(
        self,
        scope: EvaluationScope,
        *,
        manifest_id: str,
        revision: str,
    ) -> HarnessManifest | None:
        return await self._get_by_fields(
            scope,
            EvaluationRecordKind.HARNESS_MANIFEST,
            HarnessManifest,
            manifest_id=manifest_id,
            revision=revision,
        )

    async def list_harness_manifests(
        self,
        scope: EvaluationScope,
        *,
        limit: int = 100,
    ) -> tuple[HarnessManifest, ...]:
        rows = await self._list(scope, EvaluationRecordKind.HARNESS_MANIFEST, limit)
        return tuple(row for row in rows if isinstance(row, HarnessManifest))

    async def get_active_harness_manifest(
        self,
        scope: EvaluationScope,
    ) -> HarnessManifestPointer | None:
        async with self._locked():
            return self._active_pointer_locked(scope)

    async def compare_and_set_active_harness_manifest(
        self,
        scope: EvaluationScope,
        *,
        expected: HarnessManifestPointer | None,
        replacement: HarnessManifestPointer,
    ) -> HarnessManifestPointer:
        async with self._locked():
            current = self._active_pointer_locked(scope)
            if current != expected or replacement.pointer_version != (
                1 if current is None else current.pointer_version + 1
            ):
                raise EvaluationRepositoryConflict(
                    kind=EvaluationRecordKind.HARNESS_MANIFEST_POINTER,
                    record_id=_ACTIVE_ID,
                )
            self._validate_manifest_pointer(scope, replacement, current)
            self._replace_mutable_locked(scope, replacement)
            return replacement

    async def put_projection_job(
        self,
        scope: EvaluationScope,
        job: EvaluationProjectionJob,
    ) -> bool:
        if job.version != 0 or job.status not in {
            ProjectionJobStatus.PENDING,
            ProjectionJobStatus.SKIPPED,
        }:
            raise ValueError(
                "new projection job must be pending or skipped at version zero"
            )
        async with self._locked():
            if _source_key(job.source_org_id, job.source_run_id) in (
                self._source_deletions
            ):
                return False
            jobs = self._records_for(scope, EvaluationRecordKind.PROJECTION_JOB)
            owner = evaluation_record_owner(job)
            if (
                owner.record_id not in jobs
                and len(jobs) >= self._limits.max_projection_jobs_per_scope
            ):
                raise EvaluationRepositoryCapacityError(
                    "projection job capacity exceeded"
                )
            return self._put_locked(scope, job)

    async def get_projection_job(
        self,
        scope: EvaluationScope,
        *,
        job_id: str,
    ) -> EvaluationProjectionJob | None:
        return await self._get_by_fields(
            scope,
            EvaluationRecordKind.PROJECTION_JOB,
            EvaluationProjectionJob,
            job_id=job_id,
        )

    async def list_projection_jobs(
        self,
        scope: EvaluationScope,
        *,
        statuses: frozenset[ProjectionJobStatus] | None = None,
        limit: int = 100,
    ) -> tuple[EvaluationProjectionJob, ...]:
        self._validate_limit(limit)
        async with self._locked():
            rows = (
                row
                for row in self._ordered(scope, EvaluationRecordKind.PROJECTION_JOB)
                if isinstance(row, EvaluationProjectionJob)
                and (statuses is None or row.status in statuses)
            )
            return tuple(rows)[:limit]

    async def compare_and_set_projection_job(
        self,
        scope: EvaluationScope,
        *,
        expected_version: int,
        replacement: EvaluationProjectionJob,
    ) -> EvaluationProjectionJob:
        async with self._locked():
            current = self._projection_job_locked(scope, replacement.job_id)
            if (
                current is None
                or current.version != expected_version
                or replacement.version != expected_version + 1
            ):
                raise EvaluationRepositoryConflict(
                    kind=EvaluationRecordKind.PROJECTION_JOB,
                    record_id=replacement.job_id,
                )
            _validate_projection_transition(current, replacement)
            self._replace_mutable_locked(scope, replacement)
            return replacement

    async def put_protected_artifact(
        self,
        scope: EvaluationScope,
        *,
        data: bytes,
        media_type: str = "application/octet-stream",
    ) -> ProtectedEvaluationArtifact:
        if len(data) > self._limits.max_protected_artifact_bytes:
            raise EvaluationRepositoryCapacityError("protected artifact is too large")
        async with self._locked():
            digest = hashlib.sha256(data).hexdigest()
            staged = self._staged.setdefault(scope.storage_key, {})
            existing = staged.get(digest)
            candidate = ProtectedEvaluationArtifact(
                sha256=digest,
                size=len(data),
                media_type=media_type,
            )
            if existing is not None:
                if existing != candidate:
                    raise EvaluationRepositoryConflict(
                        kind=EvaluationRecordKind.FIXTURE_CATALOG,
                        record_id=digest,
                    )
                return existing
            if len(staged) >= self._limits.max_protected_artifacts_per_scope:
                raise EvaluationRepositoryCapacityError(
                    "protected artifact count capacity exceeded"
                )
            if sum(item.size for item in staged.values()) + len(data) > (
                self._limits.max_protected_bytes_per_scope
            ):
                raise EvaluationRepositoryCapacityError(
                    "protected artifact byte capacity exceeded"
                )
            object_ref = self._object_store.put(data, media_type=media_type)
            artifact = ProtectedEvaluationArtifact(
                sha256=object_ref.sha256,
                size=object_ref.size,
                media_type=media_type,
            )
            scope_key = scope.storage_key
            envelope = _stage_envelope(scope, artifact)
            self._staging_ledger.append_put(envelope)
            self._staged[scope_key][artifact.sha256] = artifact
            self._scopes[scope_key] = scope
            self._compact_if_needed(self._staging_ledger, self._all_stage_envelopes())
            return artifact

    async def get_protected_artifact(
        self,
        scope: EvaluationScope,
        *,
        owner: EvaluationRecordOwner,
        ref: ProtectedEvaluationArtifact,
    ) -> bytes:
        async with self._locked():
            record = self._records_for(scope, owner.kind).get(owner.record_id)
            staged = self._staged.get(scope.storage_key, {}).get(ref.sha256)
            if (
                record is None
                or ref.sha256 not in _protected_digests(record)
                or staged != ref
            ):
                raise EvaluationProtectedArtifactAccessError(
                    "record does not own protected artifact"
                )
            try:
                data = self._object_store.get(ref.sha256)
            except ObjectStoreError as exc:
                raise EvaluationProtectedArtifactAccessError(
                    "protected artifact is unavailable"
                ) from exc
            if len(data) != ref.size:
                raise EvaluationProtectedArtifactAccessError(
                    "protected artifact size does not match"
                )
            return data

    async def export_scope(self, scope: EvaluationScope) -> bytes:
        async with self._locked():
            records = self._export_records(scope)
            digests = sorted(
                {
                    digest
                    for record in self._all_records(scope)
                    for digest in _protected_digests(record)
                }
            )
            staged = self._staged.get(scope.storage_key, {})
            objects: list[dict[str, object]] = []
            for digest in digests:
                artifact = staged.get(digest)
                if artifact is None:
                    raise EvaluationRepositoryCorruption(
                        "record owns an unstaged protected object"
                    )
                try:
                    data = self._object_store.get(digest)
                except ObjectStoreError as exc:
                    raise EvaluationRepositoryCorruption(
                        "record owns a missing protected object"
                    ) from exc
                objects.append(
                    {
                        "sha256": digest,
                        "size": artifact.size,
                        "media_type": artifact.media_type,
                        "body_b64": base64.b64encode(data).decode("ascii"),
                    }
                )
            payload = {
                "schema_version": 1,
                "scope": scope.model_dump(mode="json"),
                "records": records,
                "objects": objects,
            }
            encoded = json.dumps(
                payload,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode()
            if len(encoded) > self._limits.max_export_bytes:
                raise EvaluationRepositoryCapacityError(
                    "evaluation export is too large"
                )
            return encoded

    async def delete_scope(
        self,
        scope: EvaluationScope,
    ) -> EvaluationDeletionReport:
        async with self._locked():
            scope_key = scope.storage_key
            plan = self._deletion_plans.get(scope_key)
            if plan is None:
                plan = self._build_deletion_plan(scope_key)
                self._deletion_ledger.append_put(plan)
                self._deletion_plans[scope_key] = plan
            deleted, removed, retained = self._execute_deletion_plan(plan)
            return EvaluationDeletionReport(
                scope=scope,
                records_deleted=deleted,
                protected_objects_deleted=removed,
                protected_objects_retained=retained,
            )

    async def delete_source_runs(
        self,
        *,
        source_org_id: str,
        source_run_ids: frozenset[str],
    ) -> EvaluationSourceRunDeletionReport:
        _validate_source_run_delete(
            source_org_id=source_org_id,
            source_run_ids=source_run_ids,
            maximum=self._limits.max_source_runs_per_delete,
        )
        async with self._locked():
            jobs_deleted = 0
            trajectories_deleted = 0
            objects_deleted = 0
            objects_retained = 0
            for run_id in sorted(source_run_ids):
                source_key = _source_key(source_org_id, run_id)
                plan = self._source_deletions.get(source_key)
                if plan is None:
                    if (
                        len(self._source_deletions)
                        >= self._limits.max_source_run_tombstones
                    ):
                        raise EvaluationRepositoryCapacityError(
                            "source-run deletion tombstone capacity exceeded"
                        )
                    plan = self._build_source_deletion_plan(source_key)
                    self._source_deletion_ledger.append_put(plan)
                    self._source_deletions[source_key] = plan
                deleted = self._execute_source_deletion_plan(plan)
                jobs_deleted += deleted[0]
                trajectories_deleted += deleted[1]
                objects_deleted += deleted[2]
                objects_retained += deleted[3]
            return EvaluationSourceRunDeletionReport(
                source_org_id=source_org_id,
                source_runs_requested=len(source_run_ids),
                projection_jobs_deleted=jobs_deleted,
                trajectory_manifests_deleted=trajectories_deleted,
                protected_objects_deleted=objects_deleted,
                protected_objects_retained=objects_retained,
            )

    def protected_object_digests(self) -> frozenset[str]:
        """Snapshot every CAS digest that global file GC must retain."""

        with self._exclusive_lock():
            self._reload()
            self._recover_pending_deletions()
            self._recover_pending_source_deletions()
            return frozenset(self._globally_referenced_digests())

    async def _put(
        self,
        scope: EvaluationScope,
        record: EvaluationRepositoryRecord,
    ) -> bool:
        async with self._locked():
            return self._put_locked(scope, record)

    def _put_locked(
        self,
        scope: EvaluationScope,
        record: EvaluationRepositoryRecord,
    ) -> bool:
        owner = evaluation_record_owner(record)
        rows = self._records_for(scope, owner.kind)
        existing = rows.get(owner.record_id)
        if existing is not None:
            if existing != record:
                raise EvaluationRepositoryConflict(
                    kind=owner.kind, record_id=owner.record_id
                )
            return False
        if sum(len(items) for items in self._scope_records(scope).values()) >= (
            self._limits.max_records_per_scope
        ):
            raise EvaluationRepositoryCapacityError(
                "evaluation record capacity exceeded"
            )
        required = _protected_digests(record)
        staged = set(self._staged.get(scope.storage_key, {}))
        if not required.issubset(staged):
            raise EvaluationProtectedArtifactAccessError(
                "record references a protected artifact not staged in this scope"
            )
        envelope = self._record_envelope(scope, owner, record)
        if len(_compact_json(envelope)) > self._limits.max_record_bytes:
            raise EvaluationRepositoryCapacityError("evaluation record is too large")
        self._ledgers[owner.kind].append_put(envelope)
        rows[owner.record_id] = record
        self._scopes[scope.storage_key] = scope
        self._compact_if_needed(
            self._ledgers[owner.kind],
            self._all_record_envelopes(owner.kind),
        )
        return True

    def _replace_mutable_locked(
        self,
        scope: EvaluationScope,
        replacement: HarnessManifestPointer | EvaluationProjectionJob,
    ) -> None:
        owner = evaluation_record_owner(replacement)
        rows = self._records_for(scope, owner.kind)
        if (
            owner.record_id not in rows
            and sum(len(items) for items in self._scope_records(scope).values())
            >= self._limits.max_records_per_scope
        ):
            raise EvaluationRepositoryCapacityError(
                "evaluation record capacity exceeded"
            )
        envelope = self._record_envelope(scope, owner, replacement)
        self._ledgers[owner.kind].append_put(envelope)
        self._records_for(scope, owner.kind)[owner.record_id] = replacement
        self._compact_if_needed(
            self._ledgers[owner.kind],
            self._all_record_envelopes(owner.kind),
        )

    async def _get_by_fields(
        self,
        scope: EvaluationScope,
        kind: EvaluationRecordKind,
        expected_type: type[EvaluationRepositoryRecord],
        **fields: object,
    ) -> EvaluationRepositoryRecord | None:
        async with self._locked():
            for record in self._records_for(scope, kind).values():
                if isinstance(record, expected_type) and all(
                    getattr(record, name) == value for name, value in fields.items()
                ):
                    return record
            return None

    async def _list(
        self,
        scope: EvaluationScope,
        kind: EvaluationRecordKind,
        limit: int,
    ) -> tuple[EvaluationRepositoryRecord, ...]:
        self._validate_limit(limit)
        async with self._locked():
            return tuple(self._ordered(scope, kind))[:limit]

    def _ordered(
        self,
        scope: EvaluationScope,
        kind: EvaluationRecordKind,
    ) -> Iterator[EvaluationRepositoryRecord]:
        rows = self._records_for(scope, kind)
        return iter(sorted(rows.values(), key=_record_sort_key))

    def _validate_limit(self, limit: int) -> None:
        if not 1 <= limit <= self._limits.max_list_limit:
            raise ValueError("evaluation list limit is out of bounds")

    def _scope_records(
        self,
        scope: EvaluationScope,
    ) -> dict[EvaluationRecordKind, dict[str, EvaluationRepositoryRecord]]:
        return self._records.setdefault(scope.storage_key, {})

    def _records_for(
        self,
        scope: EvaluationScope,
        kind: EvaluationRecordKind,
    ) -> dict[str, EvaluationRepositoryRecord]:
        return self._scope_records(scope).setdefault(kind, {})

    def _all_records(
        self, scope: EvaluationScope
    ) -> Iterable[EvaluationRepositoryRecord]:
        for rows in self._scope_records(scope).values():
            yield from rows.values()

    def _validate_suite_dependencies(
        self,
        scope: EvaluationScope,
        suite_run: EvaluationSuiteRun,
    ) -> None:
        present_cases = {
            (row.case_id, row.revision)
            for row in self._records_for(scope, EvaluationRecordKind.CASE).values()
            if isinstance(row, EvaluationCase)
        }
        if any(
            (ref.case_id, ref.revision) not in present_cases
            for ref in suite_run.case_refs
        ):
            raise ValueError("suite run references an unknown case revision")
        if not any(
            isinstance(row, FixtureCatalog)
            and row.catalog_id == suite_run.fixture_catalog_id
            and row.revision == suite_run.fixture_catalog_revision
            for row in self._records_for(
                scope, EvaluationRecordKind.FIXTURE_CATALOG
            ).values()
        ):
            raise ValueError("suite run references an unknown fixture catalog")

    def _validate_checkpoint(
        self,
        scope: EvaluationScope,
        checkpoint: EvaluationSuiteRunCheckpoint,
    ) -> None:
        suite = next(
            (
                row
                for row in self._records_for(
                    scope, EvaluationRecordKind.SUITE_RUN
                ).values()
                if isinstance(row, EvaluationSuiteRun)
                and row.suite_run_id == checkpoint.suite_run_id
            ),
            None,
        )
        if suite is None:
            raise ValueError("checkpoint references an unknown suite run")
        if checkpoint.next_case_index > len(suite.case_refs):
            raise ValueError("checkpoint case cursor is beyond the suite")
        prior = [
            row
            for row in self._records_for(
                scope, EvaluationRecordKind.SUITE_RUN_CHECKPOINT
            ).values()
            if isinstance(row, EvaluationSuiteRunCheckpoint)
            and row.suite_run_id == checkpoint.suite_run_id
        ]
        by_no = {row.checkpoint_no: row for row in prior}
        existing = by_no.get(checkpoint.checkpoint_no)
        if existing is not None:
            if existing != checkpoint:
                raise EvaluationRepositoryConflict(
                    kind=EvaluationRecordKind.SUITE_RUN_CHECKPOINT,
                    record_id=evaluation_record_owner(checkpoint).record_id,
                )
            return
        latest = max(prior, key=lambda row: row.checkpoint_no, default=None)
        expected_no = 0 if latest is None else latest.checkpoint_no + 1
        if checkpoint.checkpoint_no != expected_no:
            raise ValueError("suite checkpoint sequence is not contiguous")
        if latest is not None:
            if latest.status in {
                latest.status.SUCCEEDED,
                latest.status.FAILED,
                latest.status.INCONCLUSIVE,
            }:
                raise ValueError("terminal suite checkpoint cannot be advanced")
            if checkpoint.next_case_index < latest.next_case_index:
                raise ValueError("suite checkpoint case cursor regressed")
            if not set(latest.completed_result_ids).issubset(
                checkpoint.completed_result_ids
            ):
                raise ValueError("suite checkpoint completed results regressed")

    def _active_pointer_locked(
        self,
        scope: EvaluationScope,
    ) -> HarnessManifestPointer | None:
        return next(
            (
                row
                for row in self._records_for(
                    scope, EvaluationRecordKind.HARNESS_MANIFEST_POINTER
                ).values()
                if isinstance(row, HarnessManifestPointer)
            ),
            None,
        )

    def _validate_manifest_pointer(
        self,
        scope: EvaluationScope,
        replacement: HarnessManifestPointer,
        current: HarnessManifestPointer | None,
    ) -> None:
        manifests = self._records_for(
            scope, EvaluationRecordKind.HARNESS_MANIFEST
        ).values()
        if not any(
            isinstance(row, HarnessManifest)
            and row.manifest_id == replacement.manifest_id
            and row.revision == replacement.manifest_revision
            and row.payload_digest == replacement.manifest_payload_digest
            for row in manifests
        ):
            raise ValueError("active pointer references an unknown signed manifest")
        if current is not None:
            current_manifest = next(
                (
                    row
                    for row in manifests
                    if isinstance(row, HarnessManifest)
                    and row.manifest_id == current.manifest_id
                    and row.revision == current.manifest_revision
                ),
                None,
            )
            if (
                current_manifest is None
                or replacement.previous_manifest_ref != current_manifest.manifest_ref
            ):
                raise ValueError("manifest pointer does not bind its previous release")

    def _projection_job_locked(
        self,
        scope: EvaluationScope,
        job_id: str,
    ) -> EvaluationProjectionJob | None:
        return next(
            (
                row
                for row in self._records_for(
                    scope, EvaluationRecordKind.PROJECTION_JOB
                ).values()
                if isinstance(row, EvaluationProjectionJob) and row.job_id == job_id
            ),
            None,
        )

    def _reload(self) -> None:
        self._records = {}
        self._scopes = {}
        self._staged = {}
        self._deletion_plans = {}
        self._source_deletions = {}
        try:
            for kind, ledger in self._ledgers.items():
                live: dict[str, dict[str, object]] = {}
                for op, payload in ledger.load_ops():
                    if op == "delete":
                        live.pop(str(payload), None)
                        continue
                    if not isinstance(payload, dict):
                        raise EvaluationRepositoryCorruption(
                            "evaluation ledger contains a non-object row"
                        )
                    key = _envelope_ledger_key(payload)
                    prior = live.get(key)
                    if prior is not None and prior != payload:
                        if kind not in {
                            EvaluationRecordKind.HARNESS_MANIFEST_POINTER,
                            EvaluationRecordKind.PROJECTION_JOB,
                        }:
                            raise EvaluationRepositoryCorruption(
                                "immutable evaluation record was rewritten"
                            )
                        _validate_durable_mutable_transition(kind, prior, payload)
                    live[key] = payload
                for payload in live.values():
                    scope, owner, record = _decode_record_envelope(
                        kind,
                        payload,
                        object_store=self._object_store,
                    )
                    scope_key = scope.storage_key
                    self._scopes[scope_key] = scope
                    rows = self._records.setdefault(scope_key, {}).setdefault(kind, {})
                    if owner.record_id in rows:
                        raise EvaluationRepositoryCorruption(
                            "evaluation ledger contains duplicate identity"
                        )
                    rows[owner.record_id] = record
            staged_live: dict[str, dict[str, object]] = {}
            for op, payload in self._staging_ledger.load_ops():
                if op == "delete":
                    staged_live.pop(str(payload), None)
                    continue
                if not isinstance(payload, dict):
                    raise EvaluationRepositoryCorruption(
                        "protected staging ledger contains a non-object row"
                    )
                key = _envelope_ledger_key(payload)
                prior = staged_live.get(key)
                if prior is not None and prior != payload:
                    raise EvaluationRepositoryCorruption(
                        "protected staging metadata conflicts"
                    )
                staged_live[key] = payload
            for payload in staged_live.values():
                scope, artifact = _decode_stage_envelope(payload)
                if not self._object_store.exists(artifact.sha256):
                    raise EvaluationRepositoryCorruption(
                        "protected staging row references a missing object"
                    )
                scope_key = scope.storage_key
                self._scopes[scope_key] = scope
                self._staged.setdefault(scope_key, {})[artifact.sha256] = artifact
            deletion_live: dict[str, dict[str, object]] = {}
            for op, payload in self._deletion_ledger.load_ops():
                if op == "delete":
                    deletion_live.pop(str(payload), None)
                    continue
                if not isinstance(payload, dict):
                    raise EvaluationRepositoryCorruption(
                        "scope deletion ledger contains a non-object row"
                    )
                scope_key = _validate_deletion_plan(payload)
                prior = deletion_live.get(scope_key)
                if prior is not None and prior != payload:
                    raise EvaluationRepositoryCorruption(
                        "scope deletion plan conflicts"
                    )
                deletion_live[scope_key] = payload
            self._deletion_plans = deletion_live
            source_deletion_live: dict[str, dict[str, object]] = {}
            for op, payload in self._source_deletion_ledger.load_ops():
                if op == "delete":
                    raise EvaluationRepositoryCorruption(
                        "source-run deletion tombstones cannot be deleted"
                    )
                if not isinstance(payload, dict):
                    raise EvaluationRepositoryCorruption(
                        "source-run deletion ledger contains a non-object row"
                    )
                source_key = _validate_source_deletion_plan(payload)
                prior = source_deletion_live.get(source_key)
                if prior is not None and prior != payload:
                    _validate_source_deletion_transition(prior, payload)
                source_deletion_live[source_key] = payload
            self._source_deletions = source_deletion_live
            for scope_key, scoped in self._records.items():
                staged = set(self._staged.get(scope_key, {}))
                for rows in scoped.values():
                    for record in rows.values():
                        if not _protected_digests(record).issubset(staged):
                            raise EvaluationRepositoryCorruption(
                                "record references protected bytes outside its scope"
                            )
        except EvaluationRepositoryCorruption:
            raise
        except (ValidationError, ValueError, TypeError) as exc:
            raise EvaluationRepositoryCorruption(
                "evaluation ledgers contain invalid typed state"
            ) from exc

    def _globally_referenced_digests(self) -> set[str]:
        refs = {digest for artifacts in self._staged.values() for digest in artifacts}
        for scoped in self._records.values():
            for rows in scoped.values():
                for record in rows.values():
                    refs.update(_protected_digests(record))
                    body_digest = _record_body_digest(record)
                    if body_digest is not None:
                        refs.add(body_digest)
        return refs

    def _recover_loaded_ledgers(self) -> None:
        """Atomically trim a tolerated torn final append during restart."""

        for kind, ledger in self._ledgers.items():
            if ledger.path.exists():
                ledger.rewrite(self._all_record_envelopes(kind))
        if self._staging_ledger.path.exists():
            self._staging_ledger.rewrite(self._all_stage_envelopes())
        if self._deletion_ledger.path.exists():
            self._deletion_ledger.rewrite(
                self._deletion_plans[key] for key in sorted(self._deletion_plans)
            )
        if self._source_deletion_ledger.path.exists():
            self._source_deletion_ledger.rewrite(
                self._source_deletions[key] for key in sorted(self._source_deletions)
            )

    def _recover_pending_deletions(self) -> None:
        """Finish every fsynced deletion plan before admitting new work."""

        for scope_key in tuple(sorted(self._deletion_plans)):
            self._execute_deletion_plan(self._deletion_plans[scope_key])

    def _recover_pending_source_deletions(self) -> None:
        """Finish source cascades whose tombstone was fsynced before a crash."""

        for source_key in tuple(sorted(self._source_deletions)):
            plan = self._source_deletions[source_key]
            if not plan["completed"]:
                self._execute_source_deletion_plan(plan)

    def _deleted_trajectory_keys(self) -> set[str]:
        return {
            str(key)
            for plan in self._source_deletions.values()
            for key in plan["trajectory_keys"]
        }

    def _build_source_deletion_plan(self, source_key: str) -> dict[str, object]:
        affected_jobs: list[tuple[str, str, EvaluationProjectionJob]] = []
        trajectory_keys: set[str] = set()
        for scope_key, scoped in self._records.items():
            for record_id, record in scoped.get(
                EvaluationRecordKind.PROJECTION_JOB, {}
            ).items():
                if (
                    isinstance(record, EvaluationProjectionJob)
                    and _source_key(record.source_org_id, record.source_run_id)
                    == source_key
                ):
                    affected_jobs.append((scope_key, record_id, record))
                    trajectory_keys.add(_trajectory_key(f"trajectory_{record.job_id}"))
                    if record.trajectory_id is not None:
                        trajectory_keys.add(_trajectory_key(record.trajectory_id))

        record_ids: dict[str, dict[str, list[str]]] = {}
        affected_records: list[EvaluationRepositoryRecord] = []
        for scope_key, record_id, record in affected_jobs:
            record_ids.setdefault(scope_key, {}).setdefault(
                EvaluationRecordKind.PROJECTION_JOB.value, []
            ).append(record_id)
            affected_records.append(record)
        for scope_key, scoped in self._records.items():
            for record_id, record in scoped.get(
                EvaluationRecordKind.TRAJECTORY_MANIFEST, {}
            ).items():
                if (
                    isinstance(record, TrajectoryManifest)
                    and _trajectory_key(record.trajectory_id) in trajectory_keys
                ):
                    record_ids.setdefault(scope_key, {}).setdefault(
                        EvaluationRecordKind.TRAJECTORY_MANIFEST.value, []
                    ).append(record_id)
                    affected_records.append(record)
        canonical_records = {
            scope_key: {kind: sorted(ids) for kind, ids in sorted(kinds.items())}
            for scope_key, kinds in sorted(record_ids.items())
        }
        staged_refs: dict[str, list[str]] = {}
        for scope_key, kinds in canonical_records.items():
            removed_ids = {
                record_id for record_ids in kinds.values() for record_id in record_ids
            }
            affected_refs = {
                digest
                for kind_value, record_ids in kinds.items()
                for record_id in record_ids
                if (
                    record := self._records.get(scope_key, {})
                    .get(EvaluationRecordKind(kind_value), {})
                    .get(record_id)
                )
                is not None
                for digest in _protected_digests(record)
            }
            remaining_refs = {
                digest
                for rows in self._records.get(scope_key, {}).values()
                for record_id, record in rows.items()
                if record_id not in removed_ids
                for digest in _protected_digests(record)
            }
            removable = sorted(
                (affected_refs - remaining_refs) & set(self._staged.get(scope_key, {}))
            )
            if removable:
                staged_refs[scope_key] = removable
        candidates = {
            digest
            for record in affected_records
            for digest in _protected_digests(record)
        } | {
            digest
            for record in affected_records
            if (digest := _record_body_digest(record)) is not None
        }
        values: dict[str, object] = {
            "source_key": source_key,
            "records": canonical_records,
            "staged_refs": staged_refs,
            "trajectory_keys": sorted(trajectory_keys),
            "object_candidates": sorted(candidates),
            "projection_job_count": len(affected_jobs),
            "trajectory_manifest_count": (len(affected_records) - len(affected_jobs)),
            "completed": False,
        }
        return {**values, "plan_digest": canonical_json_sha256(values)}

    def _execute_source_deletion_plan(
        self,
        plan: dict[str, object],
    ) -> tuple[int, int, int, int]:
        source_key = _validate_source_deletion_plan(plan)
        if plan["completed"]:
            return (0, 0, 0, 0)
        records = plan["records"]
        if not isinstance(records, dict):  # validated above; type narrowing
            raise EvaluationRepositoryCorruption("source deletion records are invalid")
        jobs_deleted = 0
        trajectories_deleted = 0
        for scope_key, kinds in records.items():
            if not isinstance(kinds, dict):
                raise EvaluationRepositoryCorruption(
                    "source deletion record kinds are invalid"
                )
            for kind_value, record_ids in kinds.items():
                kind = EvaluationRecordKind(kind_value)
                if not isinstance(record_ids, list):
                    raise EvaluationRepositoryCorruption(
                        "source deletion record ids are invalid"
                    )
                rows = self._records.get(scope_key, {}).get(kind, {})
                for record_id in record_ids:
                    if str(record_id) not in rows:
                        continue
                    self._ledgers[kind].append_delete(
                        _ledger_key(scope_key, str(record_id))
                    )
                    rows.pop(str(record_id))
                    if kind is EvaluationRecordKind.PROJECTION_JOB:
                        jobs_deleted += 1
                    else:
                        trajectories_deleted += 1

        staged_refs = plan["staged_refs"]
        if not isinstance(staged_refs, dict):
            raise EvaluationRepositoryCorruption(
                "source deletion staged refs are invalid"
            )
        for scope_key, digests in staged_refs.items():
            if not isinstance(digests, list):
                raise EvaluationRepositoryCorruption(
                    "source deletion staged digests are invalid"
                )
            for digest in digests:
                if str(digest) not in self._staged.get(scope_key, {}):
                    continue
                self._staging_ledger.append_delete(_ledger_key(scope_key, str(digest)))
                self._staged[scope_key].pop(str(digest))

        deleted, retained = self._delete_object_candidates(plan["object_candidates"])
        completed_values = {
            key: value
            for key, value in plan.items()
            if key not in {"completed", "plan_digest"}
        }
        completed_values["completed"] = True
        completed = {
            **completed_values,
            "plan_digest": canonical_json_sha256(completed_values),
        }
        self._source_deletion_ledger.append_put(completed)
        self._source_deletions[source_key] = completed
        for kind, ledger in self._ledgers.items():
            self._compact_if_needed(ledger, self._all_record_envelopes(kind))
        self._compact_if_needed(self._staging_ledger, self._all_stage_envelopes())
        self._compact_if_needed(
            self._source_deletion_ledger,
            (self._source_deletions[key] for key in sorted(self._source_deletions)),
        )
        return jobs_deleted, trajectories_deleted, deleted, retained

    def _build_deletion_plan(self, scope_key: str) -> dict[str, object]:
        scoped = self._records.get(scope_key, {})
        records = {
            kind.value: sorted(rows)
            for kind, rows in sorted(scoped.items(), key=lambda item: item[0].value)
            if rows
        }
        candidates = (
            {
                digest
                for rows in scoped.values()
                for record in rows.values()
                for digest in _protected_digests(record)
            }
            | {
                digest
                for rows in scoped.values()
                for record in rows.values()
                if (digest := _record_body_digest(record)) is not None
            }
            | set(self._staged.get(scope_key, {}))
        )
        values: dict[str, object] = {
            "scope_key": scope_key,
            "records": records,
            "staged_digests": sorted(self._staged.get(scope_key, {})),
            "object_candidates": sorted(candidates),
            "record_count": sum(len(rows) for rows in scoped.values()),
        }
        return {**values, "plan_digest": canonical_json_sha256(values)}

    def _execute_deletion_plan(
        self,
        plan: dict[str, object],
    ) -> tuple[int, int, int]:
        scope_key = _validate_deletion_plan(plan)
        records = plan["records"]
        if not isinstance(records, dict):  # validated above; type narrowing
            raise EvaluationRepositoryCorruption("scope deletion records are invalid")
        for kind_value, record_ids in records.items():
            kind = EvaluationRecordKind(kind_value)
            if not isinstance(record_ids, list):
                raise EvaluationRepositoryCorruption(
                    "scope deletion record ids are invalid"
                )
            rows = self._records.get(scope_key, {}).get(kind, {})
            for record_id in record_ids:
                self._ledgers[kind].append_delete(
                    _ledger_key(scope_key, str(record_id))
                )
                rows.pop(str(record_id), None)
        staged_digests = plan["staged_digests"]
        if not isinstance(staged_digests, list):
            raise EvaluationRepositoryCorruption(
                "scope deletion staged refs are invalid"
            )
        for digest in staged_digests:
            self._staging_ledger.append_delete(_ledger_key(scope_key, str(digest)))
            self._staged.get(scope_key, {}).pop(str(digest), None)
        self._records.pop(scope_key, None)
        self._staged.pop(scope_key, None)
        self._scopes.pop(scope_key, None)

        object_candidates = plan["object_candidates"]
        if not isinstance(object_candidates, list):
            raise EvaluationRepositoryCorruption(
                "scope deletion object refs are invalid"
            )
        removed, retained = self._delete_object_candidates(object_candidates)
        self._deletion_ledger.append_delete(scope_key)
        self._deletion_plans.pop(scope_key, None)
        for kind, ledger in self._ledgers.items():
            self._compact_if_needed(ledger, self._all_record_envelopes(kind))
        self._compact_if_needed(self._staging_ledger, self._all_stage_envelopes())
        self._compact_if_needed(
            self._deletion_ledger,
            (self._deletion_plans[key] for key in sorted(self._deletion_plans)),
        )
        return int(plan["record_count"]), removed, retained

    def _delete_object_candidates(
        self,
        candidates: object,
    ) -> tuple[int, int]:
        if not isinstance(candidates, list):
            raise EvaluationRepositoryCorruption(
                "evaluation deletion object refs are invalid"
            )
        globally_referenced = self._globally_referenced_digests()
        removed = 0
        retained = 0
        for digest_value in candidates:
            digest = str(digest_value)
            if digest in globally_referenced:
                retained += 1
            elif (
                self._object_deletion_policy
                is EvaluationObjectDeletionPolicy.SHARED_STORE_METADATA_ONLY
            ):
                if self._object_store.exists(digest):
                    retained += 1
            elif self._object_store.delete(digest):
                removed += 1
        return removed, retained

    def _all_record_envelopes(
        self,
        kind: EvaluationRecordKind,
    ) -> tuple[dict[str, object], ...]:
        envelopes: list[dict[str, object]] = []
        for scope_key in sorted(self._records):
            scope = self._scopes[scope_key]
            for record_id in sorted(self._records[scope_key].get(kind, {})):
                record = self._records[scope_key][kind][record_id]
                envelopes.append(
                    self._record_envelope(
                        scope,
                        evaluation_record_owner(record),
                        record,
                    )
                )
        return tuple(envelopes)

    def _record_envelope(
        self,
        scope: EvaluationScope,
        owner: EvaluationRecordOwner,
        record: EvaluationRepositoryRecord,
    ) -> dict[str, object]:
        body = record.model_dump(mode="json")
        encoded = canonical_json_bytes(body)
        if len(encoded) > self._limits.max_record_bytes:
            raise EvaluationRepositoryCapacityError("evaluation record is too large")
        if owner.kind in _CAS_BACKED_KINDS:
            object_ref = self._object_store.put(
                encoded,
                media_type=_RECORD_MEDIA_TYPE,
            )
            inline: dict[str, object] | None = None
            body_object: dict[str, object] | None = object_ref.model_dump(mode="json")
        else:
            inline = body
            body_object = None
        return {
            "scope": scope.model_dump(mode="json"),
            "scope_key": scope.storage_key,
            "kind": owner.kind.value,
            "record_id": owner.record_id,
            "record_digest": canonical_json_sha256(body),
            "protected_refs": sorted(_protected_digests(record)),
            "record_inline": inline,
            "record_object": body_object,
        }

    def _all_stage_envelopes(self) -> tuple[dict[str, object], ...]:
        return tuple(
            _stage_envelope(self._scopes[scope_key], artifacts[digest])
            for scope_key, artifacts in sorted(self._staged.items())
            for digest in sorted(artifacts)
        )

    def _export_records(self, scope: EvaluationScope) -> list[dict[str, object]]:
        exported: list[dict[str, object]] = []
        for kind, rows in self._scope_records(scope).items():
            for record_id, record in rows.items():
                exported.append(
                    {
                        "kind": kind.value,
                        "record_id": record_id,
                        "record": record.model_dump(mode="json"),
                        "protected_refs": sorted(_protected_digests(record)),
                    }
                )
        return sorted(
            exported,
            key=lambda row: (str(row["kind"]), str(row["record_id"])),
        )

    def _compact_if_needed(
        self,
        ledger: StateLedger,
        live_envelopes: Iterable[dict[str, object]],
    ) -> None:
        live = tuple(live_envelopes)
        if ledger.line_count > max(64, len(live) * 2 + 16):
            ledger.rewrite(live)

    @contextmanager
    def _exclusive_lock(self) -> Iterator[None]:
        FileStoreLayout.ensure_dir(self._lock_path.parent)
        fd = os.open(
            self._lock_path,
            os.O_CREAT | os.O_RDWR,
            self._FILE_MODE,
        )
        try:
            acquire_exclusive(fd)
            yield
        finally:
            release_exclusive(fd)
            os.close(fd)

    @contextmanager
    def _process_state(self) -> Iterator[None]:
        with self._exclusive_lock():
            self._reload()
            self._recover_pending_deletions()
            self._recover_pending_source_deletions()
            yield

    def _locked(self) -> "_AsyncRepositoryLock":
        return _AsyncRepositoryLock(self)


class _AsyncRepositoryLock:
    def __init__(self, repository: FileEvaluationRepository) -> None:
        self._repository = repository
        self._process_lock: Iterator[None] | None = None

    async def __aenter__(self) -> None:
        await self._repository._lock.acquire()
        try:
            process_lock = self._repository._process_state()
            process_lock.__enter__()
            self._process_lock = process_lock
        except BaseException:
            self._repository._lock.release()
            raise

    async def __aexit__(
        self,
        exc_type: object,
        exc: object,
        traceback: object,
    ) -> None:
        try:
            if self._process_lock is not None:
                self._process_lock.__exit__(exc_type, exc, traceback)
        finally:
            self._repository._lock.release()


def _stage_envelope(
    scope: EvaluationScope,
    artifact: ProtectedEvaluationArtifact,
) -> dict[str, object]:
    return {
        "scope": scope.model_dump(mode="json"),
        "scope_key": scope.storage_key,
        "digest": artifact.sha256,
        "artifact": artifact.model_dump(mode="json"),
    }


def _decode_record_envelope(
    expected_kind: EvaluationRecordKind,
    payload: dict[str, object],
    *,
    object_store: FileObjectStore | None = None,
) -> tuple[EvaluationScope, EvaluationRecordOwner, EvaluationRepositoryRecord]:
    expected_keys = {
        "scope",
        "scope_key",
        "kind",
        "record_id",
        "record_digest",
        "protected_refs",
        "record_inline",
        "record_object",
    }
    if set(payload) != expected_keys or payload["kind"] != expected_kind.value:
        raise EvaluationRepositoryCorruption("evaluation envelope shape is invalid")
    scope = EvaluationScope.model_validate(payload["scope"])
    if payload["scope_key"] != scope.storage_key:
        raise EvaluationRepositoryCorruption("evaluation scope digest is invalid")
    record_inline = payload["record_inline"]
    record_object_json = payload["record_object"]
    if expected_kind in _CAS_BACKED_KINDS:
        if record_inline is not None or not isinstance(record_object_json, dict):
            raise EvaluationRepositoryCorruption(
                "CAS-backed evaluation record envelope is invalid"
            )
        if object_store is None:
            raise EvaluationRepositoryCorruption(
                "CAS-backed evaluation record cannot be decoded without its store"
            )
        object_ref = ObjectRef.model_validate(record_object_json)
        if (
            object_ref.media_type != _RECORD_MEDIA_TYPE
            or object_ref.sha256 != payload["record_digest"]
        ):
            raise EvaluationRepositoryCorruption(
                "CAS-backed evaluation record metadata is invalid"
            )
        try:
            encoded = object_store.get(object_ref)
            decoded = json.loads(encoded)
        except (ObjectStoreError, json.JSONDecodeError) as exc:
            raise EvaluationRepositoryCorruption(
                "CAS-backed evaluation record body is unavailable"
            ) from exc
        if not isinstance(decoded, dict):
            raise EvaluationRepositoryCorruption(
                "CAS-backed evaluation record body is invalid"
            )
        record_json = decoded
    else:
        if not isinstance(record_inline, dict) or record_object_json is not None:
            raise EvaluationRepositoryCorruption(
                "inline evaluation record envelope is invalid"
            )
        record_json = record_inline
    if payload["record_digest"] != canonical_json_sha256(record_json):
        raise EvaluationRepositoryCorruption("evaluation record digest is invalid")
    record = _RECORD_TYPES[expected_kind].model_validate(record_json)
    owner = evaluation_record_owner(record)
    if payload["record_id"] != owner.record_id:
        raise EvaluationRepositoryCorruption("evaluation record identity is invalid")
    protected = payload["protected_refs"]
    if not isinstance(protected, list) or protected != sorted(
        _protected_digests(record)
    ):
        raise EvaluationRepositoryCorruption("protected ownership is invalid")
    return scope, owner, record


def _decode_stage_envelope(
    payload: dict[str, object],
) -> tuple[EvaluationScope, ProtectedEvaluationArtifact]:
    if set(payload) != {"scope", "scope_key", "digest", "artifact"}:
        raise EvaluationRepositoryCorruption("protected stage shape is invalid")
    scope = EvaluationScope.model_validate(payload["scope"])
    artifact = ProtectedEvaluationArtifact.model_validate(payload["artifact"])
    if (
        payload["scope_key"] != scope.storage_key
        or payload["digest"] != artifact.sha256
    ):
        raise EvaluationRepositoryCorruption("protected stage identity is invalid")
    return scope, artifact


def _envelope_ledger_key(payload: dict[str, object]) -> str:
    scope_key = payload.get("scope_key")
    logical_id = payload.get("record_id", payload.get("digest"))
    if not isinstance(scope_key, str) or not isinstance(logical_id, str):
        raise EvaluationRepositoryCorruption("evaluation ledger key is invalid")
    return _ledger_key(scope_key, logical_id)


def _ledger_key(scope_key: str, logical_id: str) -> str:
    return canonical_json_sha256({"scope_key": scope_key, "logical_id": logical_id})


def _validate_deletion_plan(plan: dict[str, object]) -> str:
    expected_keys = {
        "scope_key",
        "records",
        "staged_digests",
        "object_candidates",
        "record_count",
        "plan_digest",
    }
    if set(plan) != expected_keys:
        raise EvaluationRepositoryCorruption("scope deletion plan shape is invalid")

    scope_key = plan["scope_key"]
    if not _is_sha256(scope_key):
        raise EvaluationRepositoryCorruption("scope deletion plan scope key is invalid")

    records = plan["records"]
    if not isinstance(records, dict):
        raise EvaluationRepositoryCorruption("scope deletion plan records are invalid")
    allowed_kinds = {kind.value for kind in EvaluationRecordKind}
    if any(
        not isinstance(kind, str) or kind not in allowed_kinds for kind in records
    ) or list(records) != sorted(records):
        raise EvaluationRepositoryCorruption(
            "scope deletion plan record kinds are invalid"
        )
    record_count = 0
    for record_ids in records.values():
        if (
            not isinstance(record_ids, list)
            or any(not _is_sha256(record_id) for record_id in record_ids)
            or record_ids != sorted(set(record_ids))
        ):
            raise EvaluationRepositoryCorruption(
                "scope deletion plan record ids are invalid"
            )
        record_count += len(record_ids)

    staged_digests = plan["staged_digests"]
    object_candidates = plan["object_candidates"]
    for values, label in (
        (staged_digests, "staged refs"),
        (object_candidates, "object refs"),
    ):
        if (
            not isinstance(values, list)
            or any(not _is_sha256(value) for value in values)
            or values != sorted(set(values))
        ):
            raise EvaluationRepositoryCorruption(
                f"scope deletion plan {label} are invalid"
            )
    if not set(staged_digests).issubset(object_candidates):
        raise EvaluationRepositoryCorruption(
            "scope deletion staged refs are not deletion candidates"
        )
    if plan["record_count"] != record_count:
        raise EvaluationRepositoryCorruption("scope deletion record count is invalid")

    unsigned = {key: value for key, value in plan.items() if key != "plan_digest"}
    if plan["plan_digest"] != canonical_json_sha256(unsigned):
        raise EvaluationRepositoryCorruption("scope deletion plan digest is invalid")
    return scope_key


def _validate_source_deletion_plan(plan: dict[str, object]) -> str:
    expected_keys = {
        "source_key",
        "records",
        "staged_refs",
        "trajectory_keys",
        "object_candidates",
        "projection_job_count",
        "trajectory_manifest_count",
        "completed",
        "plan_digest",
    }
    if set(plan) != expected_keys:
        raise EvaluationRepositoryCorruption(
            "source-run deletion plan shape is invalid"
        )
    source_key = plan["source_key"]
    if not _is_sha256(source_key):
        raise EvaluationRepositoryCorruption("source-run deletion key is invalid")
    records = plan["records"]
    if not isinstance(records, dict) or list(records) != sorted(records):
        raise EvaluationRepositoryCorruption(
            "source-run deletion record scopes are invalid"
        )
    allowed = {
        EvaluationRecordKind.PROJECTION_JOB.value,
        EvaluationRecordKind.TRAJECTORY_MANIFEST.value,
    }
    job_count = 0
    trajectory_count = 0
    for scope_key, kinds in records.items():
        if (
            not _is_sha256(scope_key)
            or not isinstance(kinds, dict)
            or list(kinds) != sorted(kinds)
            or any(kind not in allowed for kind in kinds)
        ):
            raise EvaluationRepositoryCorruption(
                "source-run deletion record ownership is invalid"
            )
        for kind, record_ids in kinds.items():
            if (
                not isinstance(record_ids, list)
                or any(not _is_sha256(record_id) for record_id in record_ids)
                or record_ids != sorted(set(record_ids))
            ):
                raise EvaluationRepositoryCorruption(
                    "source-run deletion record ids are invalid"
                )
            if kind == EvaluationRecordKind.PROJECTION_JOB.value:
                job_count += len(record_ids)
            else:
                trajectory_count += len(record_ids)
    staged_refs = plan["staged_refs"]
    if not isinstance(staged_refs, dict) or list(staged_refs) != sorted(staged_refs):
        raise EvaluationRepositoryCorruption(
            "source-run deletion staged scopes are invalid"
        )
    for scope_key, digests in staged_refs.items():
        if (
            not _is_sha256(scope_key)
            or scope_key not in records
            or not isinstance(digests, list)
            or any(not _is_sha256(value) for value in digests)
            or digests != sorted(set(digests))
        ):
            raise EvaluationRepositoryCorruption(
                "source-run deletion staged refs are invalid"
            )
    for name in ("trajectory_keys", "object_candidates"):
        values = plan[name]
        if (
            not isinstance(values, list)
            or any(not _is_sha256(value) for value in values)
            or values != sorted(set(values))
        ):
            raise EvaluationRepositoryCorruption(
                f"source-run deletion {name} are invalid"
            )
    if (
        type(plan["completed"]) is not bool
        or plan["projection_job_count"] != job_count
        or plan["trajectory_manifest_count"] != trajectory_count
    ):
        raise EvaluationRepositoryCorruption(
            "source-run deletion counts or state are invalid"
        )
    unsigned = {key: value for key, value in plan.items() if key != "plan_digest"}
    if plan["plan_digest"] != canonical_json_sha256(unsigned):
        raise EvaluationRepositoryCorruption(
            "source-run deletion plan digest is invalid"
        )
    return source_key


def _validate_source_deletion_transition(
    prior: dict[str, object],
    replacement: dict[str, object],
) -> None:
    _validate_source_deletion_plan(prior)
    _validate_source_deletion_plan(replacement)
    immutable_keys = set(prior) - {"completed", "plan_digest"}
    if any(prior[key] != replacement[key] for key in immutable_keys):
        raise EvaluationRepositoryCorruption(
            "source-run deletion plan changed after persistence"
        )
    if prior["completed"] is not False or replacement["completed"] is not True:
        raise EvaluationRepositoryCorruption(
            "source-run deletion state transition is invalid"
        )


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(char in "0123456789abcdef" for char in value)
    )


def _source_key(source_org_id: str, source_run_id: str) -> str:
    return hashlib.sha256(
        json.dumps(
            [source_org_id, source_run_id],
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode()
    ).hexdigest()


def _trajectory_key(trajectory_id: str) -> str:
    return canonical_json_sha256(["trajectory", trajectory_id])


def _validate_source_run_delete(
    *,
    source_org_id: str,
    source_run_ids: frozenset[str],
    maximum: int,
) -> None:
    if not source_org_id or len(source_org_id) > 160:
        raise ValueError("source_org_id is out of bounds")
    if (
        not isinstance(source_run_ids, frozenset)
        or not source_run_ids
        or len(source_run_ids) > maximum
        or any(not run_id or len(run_id) > 160 for run_id in source_run_ids)
    ):
        raise ValueError("source_run_ids are out of bounds")


def _protected_digests(record: EvaluationRepositoryRecord) -> set[str]:
    found: set[str] = set()

    def visit(value: object) -> None:
        if isinstance(value, str) and value.startswith(_CAS_PREFIX):
            digest = value.removeprefix(_CAS_PREFIX)
            if len(digest) == 64 and all(char in "0123456789abcdef" for char in digest):
                found.add(digest)
        elif isinstance(value, dict):
            for item in value.values():
                visit(item)
        elif isinstance(value, (list, tuple)):
            for item in value:
                visit(item)

    visit(record.model_dump(mode="json"))
    return found


def _record_body_digest(record: EvaluationRepositoryRecord) -> str | None:
    owner = evaluation_record_owner(record)
    if owner.kind not in _CAS_BACKED_KINDS:
        return None
    return canonical_json_sha256(record.model_dump(mode="json"))


def _compact_json(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode()


def _record_sort_key(record: EvaluationRepositoryRecord) -> str:
    return json.dumps(
        record.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def _validate_projection_transition(
    current: EvaluationProjectionJob,
    replacement: EvaluationProjectionJob,
) -> None:
    immutable = (
        "job_id",
        "source_org_id",
        "source_run_id",
        "policy_revision",
        "terminal_sequence_no",
        "created_at",
    )
    if any(
        getattr(current, field) != getattr(replacement, field) for field in immutable
    ):
        raise ValueError("projection job immutable assignment changed")
    if current.variant_id is not None and replacement.variant_id != current.variant_id:
        raise ValueError("projection job resolved variant changed")
    if (
        current.variant_id is None
        and replacement.variant_id is not None
        and replacement.status is not ProjectionJobStatus.SUCCEEDED
    ):
        raise ValueError("projection variant may resolve only on successful completion")
    if replacement.updated_at < current.updated_at:
        raise ValueError("projection job update time regressed")
    if replacement.next_sequence_no < current.next_sequence_no:
        raise ValueError("projection job cursor regressed")
    if replacement.attempt_count < current.attempt_count:
        raise ValueError("projection job attempt count regressed")
    if current.status in {
        ProjectionJobStatus.SUCCEEDED,
        ProjectionJobStatus.SKIPPED,
        ProjectionJobStatus.CANCELLED,
    }:
        raise ValueError("terminal projection job cannot transition")


def _validate_durable_mutable_transition(
    kind: EvaluationRecordKind,
    prior_payload: dict[str, object],
    next_payload: dict[str, object],
) -> None:
    _, _, prior = _decode_record_envelope(kind, prior_payload)
    _, _, following = _decode_record_envelope(kind, next_payload)
    if isinstance(prior, EvaluationProjectionJob) and isinstance(
        following, EvaluationProjectionJob
    ):
        if following.version != prior.version + 1:
            raise EvaluationRepositoryCorruption(
                "projection job version is not contiguous"
            )
        _validate_projection_transition(prior, following)
        return
    if isinstance(prior, HarnessManifestPointer) and isinstance(
        following, HarnessManifestPointer
    ):
        if following.pointer_version != prior.pointer_version + 1:
            raise EvaluationRepositoryCorruption(
                "manifest pointer version is not contiguous"
            )
        return
    raise EvaluationRepositoryCorruption("unsupported mutable evaluation record")


__all__ = ("FileEvaluationRepository",)
