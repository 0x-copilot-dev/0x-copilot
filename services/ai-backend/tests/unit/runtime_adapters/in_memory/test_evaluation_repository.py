from __future__ import annotations

from datetime import datetime, timezone
import json

import pytest

from agent_runtime.harness_quality.evaluation_contracts import (
    EvaluationCase,
    EvaluationProjectionJob,
    EvaluationScope,
    ProjectionJobStatus,
    evaluation_record_owner,
)
from agent_runtime.harness_quality.ports import (
    EvaluationProtectedArtifactAccessError,
    EvaluationRepositoryConflict,
    EvaluationRepositoryPort,
)
from runtime_adapters.in_memory.evaluation_repository import (
    InMemoryEvaluationRepository,
)


@pytest.mark.asyncio
async def test_in_memory_repository_matches_immutable_and_protected_contracts() -> None:
    repository = InMemoryEvaluationRepository()
    assert isinstance(repository, EvaluationRepositoryPort)
    scope = EvaluationScope(profile_id="local-profile")
    artifact = await repository.put_protected_artifact(
        scope,
        data=b"fixture input",
        media_type="text/plain",
    )
    case = EvaluationCase(
        case_id="case-1",
        suite_id="suite-1",
        revision="r1",
        task_family="evidence",
        input_ref=artifact.ref,
        fixture_catalog_ref="fixture-r1",
        scorer_set_id="scorer-r1",
    )
    assert await repository.put_case(scope, case)
    assert not await repository.put_case(scope, case)
    with pytest.raises(EvaluationRepositoryConflict):
        await repository.put_case(
            scope,
            case.model_copy(update={"task_family": "different"}),
        )
    assert (
        await repository.get_protected_artifact(
            scope,
            owner=evaluation_record_owner(case),
            ref=artifact,
        )
        == b"fixture input"
    )
    exported = json.loads(await repository.export_scope(scope))
    assert exported["records"][0]["protected_refs"] == [artifact.sha256]
    report = await repository.delete_scope(scope)
    assert report.records_deleted == 1
    assert report.protected_objects_deleted == 1
    with pytest.raises(EvaluationProtectedArtifactAccessError):
        await repository.get_protected_artifact(
            scope,
            owner=evaluation_record_owner(case),
            ref=artifact,
        )


@pytest.mark.asyncio
async def test_in_memory_source_run_deletion_matches_tombstone_contract() -> None:
    repository = InMemoryEvaluationRepository()
    scope = EvaluationScope(profile_id="local-profile")
    now = datetime.now(timezone.utc)
    values: dict[str, object] = {
        "job_id": "projection-1",
        "source_org_id": "org-1",
        "source_run_id": "run-1",
        "variant_id": "variant-1",
        "policy_revision": "policy-1",
        "terminal_sequence_no": 1,
        "status": ProjectionJobStatus.PENDING,
        "next_sequence_no": 1,
        "attempt_count": 0,
        "lease_owner_digest": None,
        "lease_expires_at": None,
        "trajectory_id": None,
        "failure_reason_code": None,
        "version": 0,
        "created_at": now,
        "updated_at": now,
    }
    job = EvaluationProjectionJob(
        **values,
        job_digest=EvaluationProjectionJob.digest_for(**values),
    )
    assert await repository.put_projection_job(scope, job)

    report = await repository.delete_source_runs(
        source_org_id="org-1",
        source_run_ids=frozenset({"run-1"}),
    )

    assert report.projection_jobs_deleted == 1
    assert await repository.get_projection_job(scope, job_id=job.job_id) is None
    assert not await repository.put_projection_job(scope, job)
