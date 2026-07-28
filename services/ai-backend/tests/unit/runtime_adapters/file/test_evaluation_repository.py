from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json

import pytest

from agent_runtime.harness_quality.evaluation_contracts import (
    EvaluationCase,
    EvaluationCaseProgress,
    EvaluationCaseRef,
    EvaluationProjectionJob,
    EvaluationRecordKind,
    EvaluationRevisionSet,
    EvaluationScope,
    EvaluationStatus,
    EvaluationSuiteLimits,
    EvaluationSuiteRun,
    EvaluationSuiteRunCheckpoint,
    FixtureCatalog,
    FixtureResponse,
    HarnessManifest,
    HarnessManifestAssignment,
    HarnessManifestPointer,
    ProjectionJobStatus,
    TrajectoryManifest,
    evaluation_record_owner,
)
from agent_runtime.harness_quality.ports import (
    EvaluationObjectDeletionPolicy,
    EvaluationProtectedArtifactAccessError,
    EvaluationRepositoryCapacityError,
    EvaluationRepositoryConflict,
    EvaluationRepositoryCorruption,
    EvaluationRepositoryLimits,
    EvaluationRepositoryPort,
)
from agent_runtime.surfaces_v2.canonical_json import canonical_json_sha256
from runtime_adapters.file._paths import FileStoreLayout
from runtime_adapters.file._state_ledger import StateLedger
from runtime_adapters.file.evaluation_repository import FileEvaluationRepository

NOW = datetime(2026, 7, 27, 8, tzinfo=timezone.utc)
SHA_A = "a" * 64


def _scope(profile_id: str = "local-profile") -> EvaluationScope:
    return EvaluationScope(profile_id=profile_id, project_id="project-1")


def _case(*, input_ref: str, case_id: str = "case-1") -> EvaluationCase:
    return EvaluationCase(
        case_id=case_id,
        suite_id="suite-1",
        revision="case-r1",
        task_family="connector_selection",
        input_ref=input_ref,
        fixture_catalog_ref="catalog-1@fixture-r1",
        scorer_set_id="scorers-r1",
    )


def _catalog(*, response_ref: str) -> FixtureCatalog:
    fixture = FixtureResponse(
        capability_id="fixture.search",
        request_digest=canonical_json_sha256({"query": "fixture"}),
        response_ref=response_ref,
        response_digest=canonical_json_sha256({"result": "fixture"}),
    )
    values = {
        "catalog_id": "catalog-1",
        "revision": "fixture-r1",
        "fixtures": (fixture,),
        "created_at": NOW,
    }
    return FixtureCatalog(
        **values,
        catalog_digest=FixtureCatalog.digest_for(**values),
    )


def _limits() -> EvaluationSuiteLimits:
    return EvaluationSuiteLimits(
        revision="limits-r1",
        max_case_cost_microusd=1_000,
        max_suite_cost_microusd=10_000,
        max_case_model_turns=4,
        max_suite_model_turns=40,
        max_case_tool_calls=8,
        max_suite_tool_calls=80,
        max_case_tokens=4_000,
        max_suite_tokens=40_000,
        max_case_wall_time_ms=10_000,
        max_suite_wall_time_ms=100_000,
    )


def _suite_run() -> EvaluationSuiteRun:
    revisions = EvaluationRevisionSet(
        code_revision="code-r1",
        model_revision="model-r1",
        prompt_revision="prompt-r1",
        tool_revision="tool-r1",
        policy_revision="policy-r1",
        fixture_revision="fixture-r1",
        scorer_revision="scorer-r1",
    )
    values = {
        "suite_run_id": "suite-run-1",
        "suite_id": "suite-1",
        "suite_revision": "suite-r1",
        "variant_id": "candidate",
        "variant_revision": "variant-r1",
        "variant_digest": SHA_A,
        "fixture_catalog_id": "catalog-1",
        "fixture_catalog_revision": "fixture-r1",
        "case_refs": (EvaluationCaseRef(case_id="case-1", revision="case-r1"),),
        "revisions": revisions,
        "limits": _limits(),
        "created_at": NOW,
    }
    return EvaluationSuiteRun(
        **values,
        suite_run_digest=EvaluationSuiteRun.digest_for(**values),
    )


def _checkpoint(
    checkpoint_no: int,
    *,
    status: EvaluationStatus,
    next_case_index: int,
) -> EvaluationSuiteRunCheckpoint:
    completed = ("result-1",) if next_case_index else ()
    active = (
        None
        if next_case_index
        else EvaluationCaseProgress(
            case_id="case-1",
            case_revision="case-r1",
            model_turns=1,
        )
    )
    values = {
        "suite_run_id": "suite-run-1",
        "checkpoint_no": checkpoint_no,
        "status": status,
        "next_case_index": next_case_index,
        "completed_result_ids": completed,
        "active_case": active,
        "reason_codes": (),
        "updated_at": NOW + timedelta(seconds=checkpoint_no),
    }
    return EvaluationSuiteRunCheckpoint(
        **values,
        checkpoint_digest=EvaluationSuiteRunCheckpoint.digest_for(**values),
    )


def _projection_job(
    *,
    status: ProjectionJobStatus = ProjectionJobStatus.PENDING,
    version: int = 0,
    next_sequence_no: int = 1,
    attempt_count: int = 0,
    lease: bool = False,
    variant_id: str | None = "candidate",
) -> EvaluationProjectionJob:
    values = {
        "job_id": "projection-1",
        "source_org_id": "source-org",
        "source_run_id": "source-run",
        "variant_id": variant_id,
        "policy_revision": "projection-r1",
        "terminal_sequence_no": 5,
        "status": status,
        "next_sequence_no": next_sequence_no,
        "attempt_count": attempt_count,
        "lease_owner_digest": SHA_A if lease else None,
        "lease_expires_at": NOW + timedelta(minutes=1) if lease else None,
        "trajectory_id": "trajectory-1"
        if status is ProjectionJobStatus.SUCCEEDED
        else None,
        "failure_reason_code": "projection_failed"
        if status is ProjectionJobStatus.FAILED
        else None,
        "version": version,
        "created_at": NOW,
        "updated_at": NOW + timedelta(seconds=version),
    }
    return EvaluationProjectionJob(
        **values,
        job_digest=EvaluationProjectionJob.digest_for(**values),
    )


def _trajectory(*, evidence_ref: str) -> TrajectoryManifest:
    values: dict[str, object] = {
        "trajectory_id": "trajectory-1",
        "run_id": "source-run",
        "case_id": None,
        "variant_id": "candidate",
        "ordered_steps": (),
        "evidence_refs": (evidence_ref,),
        "usage_summary": {},
        "redaction_policy_revision": "redaction-r1",
        "harness_revisions": {},
        "projected_at": NOW,
    }
    return TrajectoryManifest(
        **values,
        manifest_digest=TrajectoryManifest.digest_for(
            **{key: value for key, value in values.items() if key != "projected_at"}
        ),
    )


def _manifest(
    manifest_id: str,
    revision: str,
    *,
    previous_manifest_ref: str | None = None,
) -> HarnessManifest:
    values = {
        "schema_version": 1,
        "manifest_id": manifest_id,
        "revision": revision,
        "assignments": (
            HarnessManifestAssignment(
                variant_ref="variant://candidate",
                variant_digest=SHA_A,
                allocation_basis_points=10_000,
            ),
        ),
        "fallback_variant_ref": "variant://candidate",
        "assignment_revision": "assignment-r1",
        "source_report_ref": "report://paired-1",
        "previous_manifest_ref": previous_manifest_ref,
        "issued_at": NOW,
        "not_before": NOW,
        "expires_at": NOW + timedelta(days=30),
        "key_id": "release-key-1",
        "signature_algorithm": "ed25519",
    }
    provisional = HarnessManifest.model_construct(
        **values,
        payload_digest="0" * 64,
        signature_b64="A" * 88,
    )
    return HarnessManifest(
        **values,
        payload_digest=canonical_json_sha256(provisional.signed_payload()),
        signature_b64="A" * 88,
    )


def _pointer(
    manifest: HarnessManifest,
    *,
    version: int,
    previous_manifest_ref: str | None,
) -> HarnessManifestPointer:
    values = {
        "pointer_version": version,
        "manifest_id": manifest.manifest_id,
        "manifest_revision": manifest.revision,
        "manifest_payload_digest": manifest.payload_digest,
        "activation_decision_id": f"local-decision-{version}",
        "previous_manifest_ref": previous_manifest_ref,
        "updated_at": NOW + timedelta(seconds=version),
    }
    return HarnessManifestPointer(
        **values,
        pointer_digest=HarnessManifestPointer.digest_for(**values),
    )


async def _seed_case_and_catalog(
    repository: EvaluationRepositoryPort,
    scope: EvaluationScope,
    *,
    body: bytes = b'{"fixture":true}',
) -> tuple[EvaluationCase, FixtureCatalog]:
    artifact = await repository.put_protected_artifact(
        scope,
        data=body,
        media_type="application/json",
    )
    case = _case(input_ref=artifact.ref)
    catalog = _catalog(response_ref=artifact.ref)
    assert await repository.put_case(scope, case)
    assert await repository.put_fixture_catalog(scope, catalog)
    return case, catalog


@pytest.mark.asyncio
async def test_file_repository_is_restart_safe_and_exports_deterministically(
    tmp_path,
) -> None:
    layout = FileStoreLayout(tmp_path / "runtime")
    repository = FileEvaluationRepository(layout)
    assert isinstance(repository, EvaluationRepositoryPort)
    scope = _scope()
    case, catalog = await _seed_case_and_catalog(repository, scope)
    fixture_ledger = layout.state_path("harness_quality_fixture_catalog").read_text()
    assert '"record_inline":null' in fixture_ledger
    assert '"record_object":{' in fixture_ledger
    assert "fixture.search" not in fixture_ledger
    assert catalog.fixtures[0].response_ref not in fixture_ledger
    protected = repository.protected_object_digests()
    assert case.input_ref.removeprefix("eval-cas://sha256/") in protected
    assert canonical_json_sha256(catalog.model_dump(mode="json")) in protected
    assert not await repository.put_case(scope, case)
    assert not await repository.put_fixture_catalog(scope, catalog)
    suite = _suite_run()
    assert await repository.put_suite_run(scope, suite)
    first = _checkpoint(
        0,
        status=EvaluationStatus.RUNNING,
        next_case_index=0,
    )
    final = _checkpoint(
        1,
        status=EvaluationStatus.SUCCEEDED,
        next_case_index=1,
    )
    assert await repository.append_suite_run_checkpoint(scope, first)
    assert await repository.append_suite_run_checkpoint(scope, final)
    expected_export = await repository.export_scope(scope)

    reloaded = FileEvaluationRepository(layout)
    assert (
        await reloaded.get_case(scope, case_id=case.case_id, revision=case.revision)
        == case
    )
    assert (
        await reloaded.get_fixture_catalog(
            scope,
            catalog_id=catalog.catalog_id,
            revision=catalog.revision,
        )
        == catalog
    )
    assert await reloaded.get_suite_run(scope, suite_run_id=suite.suite_run_id) == suite
    assert (
        await reloaded.latest_suite_run_checkpoint(
            scope, suite_run_id=suite.suite_run_id
        )
        == final
    )
    assert await reloaded.export_scope(scope) == expected_export
    assert json.loads(expected_export)["scope"] == scope.model_dump(mode="json")
    deleted = await reloaded.delete_scope(scope)
    assert deleted.records_deleted == 5
    assert deleted.protected_objects_deleted == len(protected)
    assert all(not layout.object_path(digest).exists() for digest in protected)


@pytest.mark.asyncio
async def test_protected_cas_requires_exact_scope_and_record_ownership(
    tmp_path,
) -> None:
    layout = FileStoreLayout(tmp_path / "runtime")
    repository = FileEvaluationRepository(layout)
    first_scope = _scope("profile-a")
    second_scope = _scope("profile-b")
    artifact = await repository.put_protected_artifact(
        first_scope,
        data=b"private fixture bytes",
        media_type="text/plain",
    )
    case = _case(input_ref=artifact.ref)
    await repository.put_case(first_scope, case)

    assert (
        await repository.get_protected_artifact(
            first_scope,
            owner=evaluation_record_owner(case),
            ref=artifact,
        )
        == b"private fixture bytes"
    )
    with pytest.raises(EvaluationProtectedArtifactAccessError):
        await repository.get_protected_artifact(
            first_scope,
            owner=evaluation_record_owner(_catalog(response_ref=artifact.ref)),
            ref=artifact,
        )
    with pytest.raises(EvaluationProtectedArtifactAccessError):
        await repository.put_case(second_scope, case)

    second_artifact = await repository.put_protected_artifact(
        second_scope,
        data=b"private fixture bytes",
        media_type="text/plain",
    )
    assert second_artifact == artifact
    assert await repository.put_case(second_scope, case)

    first_delete = await repository.delete_scope(first_scope)
    assert first_delete.protected_objects_deleted == 0
    assert first_delete.protected_objects_retained == 1
    assert (
        await repository.get_protected_artifact(
            second_scope,
            owner=evaluation_record_owner(case),
            ref=artifact,
        )
        == b"private fixture bytes"
    )
    second_delete = await repository.delete_scope(second_scope)
    assert second_delete.protected_objects_deleted == 1


@pytest.mark.asyncio
async def test_projection_job_compare_and_set_is_bounded_and_restart_stable(
    tmp_path,
) -> None:
    layout = FileStoreLayout(tmp_path / "runtime")
    repository = FileEvaluationRepository(
        layout,
        limits=EvaluationRepositoryLimits(max_projection_jobs_per_scope=1),
    )
    scope = _scope()
    pending = _projection_job()
    assert await repository.put_projection_job(scope, pending)
    with pytest.raises(EvaluationRepositoryCapacityError):
        other = pending.model_copy(
            update={"job_id": "projection-2"},
        )
        await repository.put_projection_job(scope, other)

    running = _projection_job(
        status=ProjectionJobStatus.RUNNING,
        version=1,
        attempt_count=1,
        lease=True,
    )
    assert (
        await repository.compare_and_set_projection_job(
            scope,
            expected_version=0,
            replacement=running,
        )
        == running
    )
    with pytest.raises(EvaluationRepositoryConflict):
        await repository.compare_and_set_projection_job(
            scope,
            expected_version=0,
            replacement=running,
        )
    assert (
        await FileEvaluationRepository(
            layout,
            limits=EvaluationRepositoryLimits(max_projection_jobs_per_scope=1),
        ).get_projection_job(scope, job_id=pending.job_id)
        == running
    )


@pytest.mark.asyncio
async def test_projection_variant_resolves_once_on_success(tmp_path) -> None:
    repository = FileEvaluationRepository(FileStoreLayout(tmp_path / "runtime"))
    scope = _scope()
    pending = _projection_job(variant_id=None)
    await repository.put_projection_job(scope, pending)
    running = _projection_job(
        status=ProjectionJobStatus.RUNNING,
        version=1,
        attempt_count=1,
        lease=True,
        variant_id=None,
    )
    await repository.compare_and_set_projection_job(
        scope,
        expected_version=0,
        replacement=running,
    )
    completed = _projection_job(
        status=ProjectionJobStatus.SUCCEEDED,
        version=2,
        next_sequence_no=6,
        attempt_count=1,
        variant_id="candidate",
    )

    assert (
        await repository.compare_and_set_projection_job(
            scope,
            expected_version=1,
            replacement=completed,
        )
        == completed
    )
    changed = completed.model_copy(update={"variant_id": "other", "version": 3})
    with pytest.raises(ValueError, match="resolved variant changed"):
        await repository.compare_and_set_projection_job(
            scope,
            expected_version=2,
            replacement=changed,
        )


@pytest.mark.asyncio
async def test_repository_rejects_capacity_before_writing_new_bytes(tmp_path) -> None:
    layout = FileStoreLayout(tmp_path / "runtime")
    repository = FileEvaluationRepository(
        layout,
        limits=EvaluationRepositoryLimits(
            max_records_per_scope=1,
            max_protected_artifacts_per_scope=1,
        ),
    )
    scope = _scope()
    artifact = await repository.put_protected_artifact(scope, data=b"first")
    rejected = b"must-not-land"
    with pytest.raises(EvaluationRepositoryCapacityError):
        await repository.put_protected_artifact(scope, data=rejected)
    assert not layout.object_path(hashlib.sha256(rejected).hexdigest()).exists()

    first = _case(input_ref=artifact.ref)
    await repository.put_case(scope, first)
    with pytest.raises(EvaluationRepositoryCapacityError):
        await repository.put_case(
            scope,
            _case(input_ref=artifact.ref, case_id="case-2"),
        )


@pytest.mark.asyncio
async def test_signed_manifests_use_one_atomic_active_pointer_and_rollback(
    tmp_path,
) -> None:
    layout = FileStoreLayout(tmp_path / "runtime")
    repository = FileEvaluationRepository(layout)
    scope = _scope()
    first = _manifest("manifest-1", "r1")
    second = _manifest(
        "manifest-2",
        "r2",
        previous_manifest_ref=first.manifest_ref,
    )
    assert await repository.put_harness_manifest(scope, first)
    assert await repository.put_harness_manifest(scope, second)
    first_pointer = _pointer(first, version=1, previous_manifest_ref=None)
    assert (
        await repository.compare_and_set_active_harness_manifest(
            scope,
            expected=None,
            replacement=first_pointer,
        )
        == first_pointer
    )
    second_pointer = _pointer(
        second,
        version=2,
        previous_manifest_ref=first.manifest_ref,
    )
    assert (
        await repository.compare_and_set_active_harness_manifest(
            scope,
            expected=first_pointer,
            replacement=second_pointer,
        )
        == second_pointer
    )
    rollback = _pointer(
        first,
        version=3,
        previous_manifest_ref=second.manifest_ref,
    )
    assert (
        await repository.compare_and_set_active_harness_manifest(
            scope,
            expected=second_pointer,
            replacement=rollback,
        )
        == rollback
    )
    with pytest.raises(EvaluationRepositoryConflict):
        await repository.compare_and_set_active_harness_manifest(
            scope,
            expected=first_pointer,
            replacement=rollback,
        )
    reloaded = FileEvaluationRepository(layout)
    assert await reloaded.get_active_harness_manifest(scope) == rollback
    assert (
        await reloaded.get_harness_manifest(
            scope,
            manifest_id=first.manifest_id,
            revision=first.revision,
        )
        == first
    )


@pytest.mark.asyncio
async def test_repository_detects_immutable_conflicts_and_durable_corruption(
    tmp_path,
) -> None:
    layout = FileStoreLayout(tmp_path / "runtime")
    repository = FileEvaluationRepository(layout)
    scope = _scope()
    artifact = await repository.put_protected_artifact(scope, data=b"one")
    case = _case(input_ref=artifact.ref)
    await repository.put_case(scope, case)
    with pytest.raises(EvaluationRepositoryConflict):
        await repository.put_case(
            scope,
            case.model_copy(update={"task_family": "different"}),
        )

    ledger = StateLedger(layout.state_path("harness_quality_case"))
    conflicting = case.model_copy(update={"task_family": "corrupt"})
    body = conflicting.model_dump(mode="json")
    owner = evaluation_record_owner(conflicting)
    ledger.append_put(
        {
            "scope": scope.model_dump(mode="json"),
            "scope_key": scope.storage_key,
            "kind": EvaluationRecordKind.CASE.value,
            "record_id": owner.record_id,
            "record_digest": canonical_json_sha256(body),
            "protected_refs": [artifact.sha256],
            "record_inline": body,
            "record_object": None,
        }
    )
    with pytest.raises(EvaluationRepositoryCorruption):
        FileEvaluationRepository(layout)


@pytest.mark.asyncio
async def test_restart_trims_a_torn_final_ledger_append(tmp_path) -> None:
    layout = FileStoreLayout(tmp_path / "runtime")
    repository = FileEvaluationRepository(layout)
    scope = _scope()
    artifact = await repository.put_protected_artifact(scope, data=b"input")
    case = _case(input_ref=artifact.ref)
    await repository.put_case(scope, case)
    case_path = layout.state_path("harness_quality_case")
    with case_path.open("ab") as handle:
        handle.write(b'{"op":"put","record":')

    recovered = FileEvaluationRepository(layout)
    assert (
        await recovered.get_case(
            scope,
            case_id=case.case_id,
            revision=case.revision,
        )
        == case
    )
    other = _case(input_ref=artifact.ref, case_id="case-2")
    assert await recovered.put_case(scope, other)
    assert await FileEvaluationRepository(layout).list_cases(
        scope,
        suite_id="suite-1",
    ) == (case, other)


@pytest.mark.asyncio
async def test_scope_deletion_recovers_from_fsynced_plan(tmp_path) -> None:
    layout = FileStoreLayout(tmp_path / "runtime")
    repository = FileEvaluationRepository(layout)
    scope = _scope()
    artifact = await repository.put_protected_artifact(scope, data=b"recover me")
    case = _case(input_ref=artifact.ref)
    await repository.put_case(scope, case)

    with repository._exclusive_lock():
        repository._reload()
        plan = repository._build_deletion_plan(scope.storage_key)
        repository._deletion_ledger.append_put(plan)

    recovered = FileEvaluationRepository(layout)
    assert (
        await recovered.get_case(
            scope,
            case_id=case.case_id,
            revision=case.revision,
        )
        is None
    )
    assert not layout.object_path(artifact.sha256).exists()
    with recovered._exclusive_lock():
        recovered._reload()
        assert recovered._deletion_plans == {}


@pytest.mark.asyncio
async def test_shared_object_store_deletion_is_metadata_only(tmp_path) -> None:
    layout = FileStoreLayout(tmp_path / "runtime")
    repository = FileEvaluationRepository(
        layout,
        object_deletion_policy=(
            EvaluationObjectDeletionPolicy.SHARED_STORE_METADATA_ONLY
        ),
    )
    scope = _scope()
    artifact = await repository.put_protected_artifact(scope, data=b"shared bytes")
    await repository.put_case(scope, _case(input_ref=artifact.ref))

    deleted = await repository.delete_scope(scope)

    assert deleted.protected_objects_deleted == 0
    assert deleted.protected_objects_retained == 1
    assert layout.object_path(artifact.sha256).exists()
    assert artifact.sha256 not in repository.protected_object_digests()


@pytest.mark.asyncio
async def test_source_run_deletion_cascades_and_blocks_resurrection(tmp_path) -> None:
    layout = FileStoreLayout(tmp_path / "runtime")
    repository = FileEvaluationRepository(layout)
    scope = _scope()
    artifact = await repository.put_protected_artifact(scope, data=b"evidence")
    pending = _projection_job()
    trajectory = _trajectory(evidence_ref=artifact.ref)
    assert await repository.put_projection_job(scope, pending)
    assert await repository.put_trajectory_manifest(scope, trajectory)
    succeeded = _projection_job(
        status=ProjectionJobStatus.SUCCEEDED,
        version=1,
        next_sequence_no=6,
    )
    await repository.compare_and_set_projection_job(
        scope,
        expected_version=0,
        replacement=succeeded,
    )

    deleted = await repository.delete_source_runs(
        source_org_id="source-org",
        source_run_ids=frozenset({"source-run"}),
    )

    assert deleted.projection_jobs_deleted == 1
    assert deleted.trajectory_manifests_deleted == 1
    assert deleted.protected_objects_deleted == 1
    assert await repository.get_projection_job(scope, job_id=pending.job_id) is None
    assert (
        await repository.get_trajectory_manifest(
            scope,
            trajectory_id=trajectory.trajectory_id,
        )
        is None
    )
    assert not await repository.put_projection_job(scope, pending)
    assert not await repository.put_trajectory_manifest(scope, trajectory)
    source_ledger = layout.state_path(
        "harness_quality_source_run_deletions"
    ).read_text()
    assert "source-org" not in source_ledger
    assert "source-run" not in source_ledger
    assert trajectory.trajectory_id not in source_ledger

    reloaded = FileEvaluationRepository(layout)
    assert not await reloaded.put_projection_job(scope, pending)
    assert not await reloaded.put_trajectory_manifest(scope, trajectory)
    repeated = await reloaded.delete_source_runs(
        source_org_id="source-org",
        source_run_ids=frozenset({"source-run"}),
    )
    assert repeated.projection_jobs_deleted == 0
    assert repeated.trajectory_manifests_deleted == 0


@pytest.mark.asyncio
async def test_source_run_cascade_recovers_from_fsynced_tombstone(tmp_path) -> None:
    layout = FileStoreLayout(tmp_path / "runtime")
    repository = FileEvaluationRepository(layout)
    scope = _scope()
    artifact = await repository.put_protected_artifact(scope, data=b"evidence")
    pending = _projection_job()
    trajectory = _trajectory(evidence_ref=artifact.ref)
    assert await repository.put_projection_job(scope, pending)
    assert await repository.put_trajectory_manifest(scope, trajectory)
    succeeded = _projection_job(
        status=ProjectionJobStatus.SUCCEEDED,
        version=1,
        next_sequence_no=6,
    )
    await repository.compare_and_set_projection_job(
        scope,
        expected_version=0,
        replacement=succeeded,
    )
    source_key = hashlib.sha256(
        json.dumps(
            ["source-org", "source-run"],
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    with repository._exclusive_lock():
        repository._reload()
        plan = repository._build_source_deletion_plan(source_key)
        repository._source_deletion_ledger.append_put(plan)

    recovered = FileEvaluationRepository(layout)

    assert await recovered.get_projection_job(scope, job_id=pending.job_id) is None
    assert (
        await recovered.get_trajectory_manifest(
            scope,
            trajectory_id=trajectory.trajectory_id,
        )
        is None
    )
    assert not layout.object_path(artifact.sha256).exists()
    assert not await recovered.put_projection_job(scope, pending)
    assert not await recovered.put_trajectory_manifest(scope, trajectory)
