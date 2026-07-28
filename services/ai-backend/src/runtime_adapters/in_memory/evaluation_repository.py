"""Hermetic parity adapter for the local harness evaluation repository."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
from collections.abc import Iterable

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
    EvaluationProtectedArtifactAccessError,
    EvaluationRepositoryCapacityError,
    EvaluationRepositoryConflict,
    EvaluationRepositoryLimits,
    EvaluationSourceRunDeletionReport,
)

_CAS_PREFIX = "eval-cas://sha256/"


class InMemoryEvaluationRepository:
    """Process-local protocol parity for unit tests; never production state."""

    def __init__(
        self,
        *,
        limits: EvaluationRepositoryLimits | None = None,
    ) -> None:
        self._limits = limits or EvaluationRepositoryLimits()
        self._records: dict[
            str,
            dict[EvaluationRecordKind, dict[str, EvaluationRepositoryRecord]],
        ] = {}
        self._objects: dict[str, bytes] = {}
        self._staged: dict[
            str,
            dict[str, ProtectedEvaluationArtifact],
        ] = {}
        self._deleted_source_keys: set[str] = set()
        self._deleted_trajectory_ids: set[str] = set()
        self._lock = asyncio.Lock()

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
        async with self._lock:
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
        async with self._lock:
            if manifest.trajectory_id in self._deleted_trajectory_ids:
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
        async with self._lock:
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
        async with self._lock:
            self._validate_checkpoint(scope, checkpoint)
            return self._put_locked(scope, checkpoint)

    async def latest_suite_run_checkpoint(
        self,
        scope: EvaluationScope,
        *,
        suite_run_id: str,
    ) -> EvaluationSuiteRunCheckpoint | None:
        async with self._lock:
            rows = self._records_for(
                scope, EvaluationRecordKind.SUITE_RUN_CHECKPOINT
            ).values()
            matches = [
                row
                for row in rows
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
        async with self._lock:
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
        async with self._lock:
            rows = self._records_for(
                scope, EvaluationRecordKind.HARNESS_MANIFEST_POINTER
            )
            return next(
                (
                    row
                    for row in rows.values()
                    if isinstance(row, HarnessManifestPointer)
                ),
                None,
            )

    async def compare_and_set_active_harness_manifest(
        self,
        scope: EvaluationScope,
        *,
        expected: HarnessManifestPointer | None,
        replacement: HarnessManifestPointer,
    ) -> HarnessManifestPointer:
        async with self._lock:
            current = self._active_pointer_locked(scope)
            if current != expected or replacement.pointer_version != (
                1 if current is None else current.pointer_version + 1
            ):
                raise EvaluationRepositoryConflict(
                    kind=EvaluationRecordKind.HARNESS_MANIFEST_POINTER,
                    record_id="active",
                )
            self._validate_manifest_pointer(scope, replacement, current)
            rows = self._records_for(
                scope, EvaluationRecordKind.HARNESS_MANIFEST_POINTER
            )
            owner = evaluation_record_owner(replacement)
            if (
                owner.record_id not in rows
                and sum(len(items) for items in self._scope_records(scope).values())
                >= self._limits.max_records_per_scope
            ):
                raise EvaluationRepositoryCapacityError(
                    "evaluation record capacity exceeded"
                )
            rows[owner.record_id] = replacement
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
        async with self._lock:
            if _source_key(job.source_org_id, job.source_run_id) in (
                self._deleted_source_keys
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
        async with self._lock:
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
        async with self._lock:
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
            rows = self._records_for(scope, EvaluationRecordKind.PROJECTION_JOB)
            rows[evaluation_record_owner(replacement).record_id] = replacement
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
        digest = hashlib.sha256(data).hexdigest()
        async with self._lock:
            staged = self._staged.setdefault(scope.storage_key, {})
            existing_artifact = staged.get(digest)
            existing_data = self._objects.get(digest)
            candidate = ProtectedEvaluationArtifact(
                sha256=digest,
                size=len(data),
                media_type=media_type,
            )
            if digest in staged:
                if existing_artifact != candidate or existing_data != data:
                    raise EvaluationRepositoryConflict(
                        kind=EvaluationRecordKind.FIXTURE_CATALOG,
                        record_id=digest,
                    )
                return candidate
            if len(staged) >= self._limits.max_protected_artifacts_per_scope:
                raise EvaluationRepositoryCapacityError(
                    "protected artifact count capacity exceeded"
                )
            staged_bytes = sum(item.size for item in staged.values())
            if staged_bytes + len(data) > self._limits.max_protected_bytes_per_scope:
                raise EvaluationRepositoryCapacityError(
                    "protected artifact byte capacity exceeded"
                )
            if existing_data is not None and existing_data != data:  # pragma: no cover
                raise EvaluationRepositoryConflict(
                    kind=EvaluationRecordKind.FIXTURE_CATALOG,
                    record_id=digest,
                )
            self._objects[digest] = bytes(data)
            staged[digest] = candidate
        return candidate

    async def get_protected_artifact(
        self,
        scope: EvaluationScope,
        *,
        owner: EvaluationRecordOwner,
        ref: ProtectedEvaluationArtifact,
    ) -> bytes:
        async with self._lock:
            record = self._records_for(scope, owner.kind).get(owner.record_id)
            staged = self._staged.get(scope.storage_key, {}).get(ref.sha256)
            if record is None or ref.sha256 not in _protected_digests(record):
                raise EvaluationProtectedArtifactAccessError(
                    "record does not own protected artifact"
                )
            stored = self._objects.get(ref.sha256)
            if stored is None or staged != ref:
                raise EvaluationProtectedArtifactAccessError(
                    "protected artifact is unavailable"
                )
            if len(stored) != ref.size:
                raise EvaluationProtectedArtifactAccessError(
                    "protected artifact metadata does not match"
                )
            return bytes(stored)

    async def export_scope(self, scope: EvaluationScope) -> bytes:
        async with self._lock:
            records = self._export_records(scope)
            digests = sorted(
                {
                    digest
                    for row in self._all_records(scope)
                    for digest in _protected_digests(row)
                }
            )
            objects = [
                {
                    "sha256": digest,
                    "media_type": self._staged[scope.storage_key][digest].media_type,
                    "body_b64": base64.b64encode(self._objects[digest]).decode("ascii"),
                }
                for digest in digests
                if digest in self._objects
            ]
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
        async with self._lock:
            scope_key = scope.storage_key
            scoped = self._records.pop(scope_key, {})
            deleted = sum(len(rows) for rows in scoped.values())
            candidates = {
                digest
                for rows in scoped.values()
                for record in rows.values()
                for digest in _protected_digests(record)
            } | set(self._staged.pop(scope_key, {}))
            retained_refs = self._globally_referenced_digests()
            removed = 0
            retained = 0
            for digest in candidates:
                if digest in retained_refs:
                    retained += 1
                elif self._objects.pop(digest, None) is not None:
                    removed += 1
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
        async with self._lock:
            source_keys = {
                _source_key(source_org_id, run_id) for run_id in source_run_ids
            }
            self._deleted_source_keys.update(source_keys)
            affected_jobs: list[tuple[str, str, EvaluationProjectionJob]] = []
            derived_trajectory_ids: set[str] = set()
            for scope_key, scoped in self._records.items():
                for record_id, record in scoped.get(
                    EvaluationRecordKind.PROJECTION_JOB, {}
                ).items():
                    if (
                        isinstance(record, EvaluationProjectionJob)
                        and _source_key(record.source_org_id, record.source_run_id)
                        in source_keys
                    ):
                        affected_jobs.append((scope_key, record_id, record))
                        derived_trajectory_ids.add(f"trajectory_{record.job_id}")
                        if record.trajectory_id is not None:
                            derived_trajectory_ids.add(record.trajectory_id)
            self._deleted_trajectory_ids.update(derived_trajectory_ids)

            candidates: set[str] = set()
            affected_scopes: set[str] = set()
            jobs_deleted = 0
            trajectories_deleted = 0
            for scope_key, record_id, record in affected_jobs:
                rows = self._records[scope_key][EvaluationRecordKind.PROJECTION_JOB]
                if rows.pop(record_id, None) is not None:
                    affected_scopes.add(scope_key)
                    jobs_deleted += 1
                    candidates.update(_protected_digests(record))
            for scope_key, scoped in self._records.items():
                rows = scoped.get(EvaluationRecordKind.TRAJECTORY_MANIFEST, {})
                for record_id, record in tuple(rows.items()):
                    if (
                        isinstance(record, TrajectoryManifest)
                        and record.trajectory_id in derived_trajectory_ids
                    ):
                        rows.pop(record_id)
                        affected_scopes.add(scope_key)
                        trajectories_deleted += 1
                        candidates.update(_protected_digests(record))

            for scope_key in affected_scopes:
                remaining_refs = {
                    digest
                    for rows in self._records.get(scope_key, {}).values()
                    for record in rows.values()
                    for digest in _protected_digests(record)
                }
                staged = self._staged.get(scope_key, {})
                for digest in candidates - remaining_refs:
                    staged.pop(digest, None)
            retained_refs = self._globally_referenced_digests()
            removed = 0
            retained = 0
            for digest in candidates:
                if digest in retained_refs:
                    retained += 1
                elif self._objects.pop(digest, None) is not None:
                    removed += 1
            return EvaluationSourceRunDeletionReport(
                source_org_id=source_org_id,
                source_runs_requested=len(source_run_ids),
                projection_jobs_deleted=jobs_deleted,
                trajectory_manifests_deleted=trajectories_deleted,
                protected_objects_deleted=removed,
                protected_objects_retained=retained,
            )

    def protected_object_digests(self) -> frozenset[str]:
        """Return the hermetic parity view used by file-GC integration tests."""

        return frozenset(self._globally_referenced_digests())

    async def _put(
        self,
        scope: EvaluationScope,
        record: EvaluationRepositoryRecord,
    ) -> bool:
        async with self._lock:
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
        if not required.issubset(self._staged.get(scope.storage_key, {})):
            raise EvaluationProtectedArtifactAccessError(
                "record references a protected artifact not staged in this scope"
            )
        if _record_encoded_size(record) > self._limits.max_record_bytes:
            raise EvaluationRepositoryCapacityError("evaluation record is too large")
        rows[owner.record_id] = record
        return True

    async def _get_by_fields(
        self,
        scope: EvaluationScope,
        kind: EvaluationRecordKind,
        expected_type: type[EvaluationRepositoryRecord],
        **fields: object,
    ) -> EvaluationRepositoryRecord | None:
        async with self._lock:
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
        async with self._lock:
            rows = self._records_for(scope, kind)
            return tuple(rows[key] for key in sorted(rows))[:limit]

    def _ordered(
        self,
        scope: EvaluationScope,
        kind: EvaluationRecordKind,
    ) -> Iterable[EvaluationRepositoryRecord]:
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
        cases = self._records_for(scope, EvaluationRecordKind.CASE).values()
        present_cases = {
            (row.case_id, row.revision)
            for row in cases
            if isinstance(row, EvaluationCase)
        }
        if any(
            (ref.case_id, ref.revision) not in present_cases
            for ref in suite_run.case_refs
        ):
            raise ValueError("suite run references an unknown case revision")
        catalogs = self._records_for(
            scope, EvaluationRecordKind.FIXTURE_CATALOG
        ).values()
        if not any(
            isinstance(row, FixtureCatalog)
            and row.catalog_id == suite_run.fixture_catalog_id
            and row.revision == suite_run.fixture_catalog_revision
            for row in catalogs
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
            manifests_by_identity = {
                (row.manifest_id, row.revision): row
                for row in manifests
                if isinstance(row, HarnessManifest)
            }
            current_manifest = manifests_by_identity.get(
                (current.manifest_id, current.manifest_revision)
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

    def _globally_referenced_digests(self) -> set[str]:
        refs = {digest for digests in self._staged.values() for digest in digests}
        for scoped in self._records.values():
            for rows in scoped.values():
                for record in rows.values():
                    refs.update(_protected_digests(record))
        return refs

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


def _record_sort_key(record: EvaluationRepositoryRecord) -> str:
    return json.dumps(
        record.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def _record_encoded_size(record: EvaluationRepositoryRecord) -> int:
    return len(_record_sort_key(record).encode())


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


def _source_key(source_org_id: str, source_run_id: str) -> str:
    return hashlib.sha256(
        json.dumps(
            [source_org_id, source_run_id],
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode()
    ).hexdigest()


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


__all__ = ("InMemoryEvaluationRepository",)
