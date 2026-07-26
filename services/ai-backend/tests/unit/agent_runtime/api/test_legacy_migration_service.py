"""Evidence and safety tests for the E2 legacy migration prerequisite."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import pytest

from agent_runtime.api.legacy_migration_service import LegacyMigrationService
from agent_runtime.artifacts import (
    ArtifactConflictError,
    ArtifactCreateRequest,
    ArtifactNotFoundError,
    ArtifactProvenance,
    ArtifactScope,
    ArtifactService,
)
from agent_runtime.capabilities.backends.artifact_draft_backend import (
    ArtifactDraftPathBinding,
)
from agent_runtime.persistence.records import DraftRecord, DraftStatus
from agent_runtime.surfaces_v2.legacy_migration import (
    LegacyMigrationStatus,
)
from agent_runtime.surfaces_v2.ledger_models import ArtifactAuthor, ArtifactKind
from runtime_adapters.in_memory.artifact_blob_store import InMemoryArtifactBlobStore
from runtime_adapters.in_memory.artifact_metadata_store import (
    InMemoryArtifactMetadataStore,
)
from runtime_adapters.in_memory.artifact_publication import (
    InMemoryArtifactPublicationCoordinator,
)
from runtime_adapters.in_memory.draft_store import InMemoryDraftStore
from runtime_adapters.in_memory.legacy_migration_store import (
    InMemoryLegacyMigrationCheckpointStore,
)


pytestmark = pytest.mark.anyio

ORG = "org_e2_migration"
USER = "user_e2_migration"
CONVERSATION = "conv_e2_migration"
RUN_1 = "run_e2_migration_1"
RUN_2 = "run_e2_migration_2"
NOW = datetime(2026, 7, 26, 12, tzinfo=UTC)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@dataclass(frozen=True)
class _Run:
    run_id: str
    org_id: str = ORG
    user_id: str = USER
    conversation_id: str = CONVERSATION
    trace_id: str = "trace_e2_migration"


@dataclass(frozen=True)
class _Event:
    event_type: str
    sequence_no: int
    payload: dict[str, object]


@dataclass
class _Runs:
    rows: list[_Run]

    async def list_runs_for_migration(
        self, *, org_id: str, after_run_id: str | None, limit: int
    ) -> Sequence[_Run]:
        rows = sorted(
            (row for row in self.rows if row.org_id == org_id),
            key=lambda row: row.run_id,
        )
        if after_run_id is not None:
            rows = [row for row in rows if row.run_id > after_run_id]
        return tuple(rows[:limit])


@dataclass
class _Events:
    rows: dict[str, list[_Event]] = field(default_factory=dict)
    reads: list[tuple[str, str, int]] = field(default_factory=list)
    dangerous_calls: int = 0

    async def list_events_after(
        self, *, org_id: str, run_id: str, after_sequence: int
    ) -> Sequence[_Event]:
        self.reads.append((org_id, run_id, after_sequence))
        return tuple(
            event
            for event in self.rows.get(run_id, ())
            if event.sequence_no > after_sequence
        )

    async def append_event(self, *args: object, **kwargs: object) -> None:
        del args, kwargs
        self.dangerous_calls += 1
        raise AssertionError("legacy migration must never append a run event")


@dataclass
class _Audit:
    records: list[tuple[str, object]] = field(default_factory=list)
    fail: bool = False

    async def write_audit_log(self, *, event_type: str, record: object) -> None:
        if self.fail:
            raise RuntimeError("audit sink down")
        self.records.append((event_type, record))


class _Scopes:
    def __init__(self, runs: Sequence[_Run]) -> None:
        self._scopes = {
            (run.org_id, run.user_id, run.run_id): ArtifactScope(
                org_id=run.org_id,
                user_id=run.user_id,
                conversation_id=run.conversation_id,
                run_id=run.run_id,
                trace_id=run.trace_id,
            )
            for run in runs
        }

    async def resolve_run(
        self, *, org_id: str, user_id: str, run_id: str
    ) -> ArtifactScope | None:
        return self._scopes.get((org_id, user_id, run_id))


class _TransientArtifacts:
    """Repository double that never confirms a create, simulating a retry."""

    def __init__(self) -> None:
        self.create_attempts = 0

    async def get_metadata(self, **kwargs: object) -> None:
        del kwargs
        raise ArtifactNotFoundError()

    async def create_draft_from_bytes(self, **kwargs: object) -> None:
        del kwargs
        self.create_attempts += 1
        raise ArtifactConflictError()


def _artifact_service(runs: Sequence[_Run]) -> ArtifactService:
    coordinator = InMemoryArtifactPublicationCoordinator()
    return ArtifactService(
        metadata=InMemoryArtifactMetadataStore(coordinator),
        blobs=InMemoryArtifactBlobStore(coordinator),
        run_scopes=_Scopes(runs),
    )


def _draft(
    *,
    draft_number: int,
    version: int,
    body: str,
    run_id: str = RUN_1,
    created_at: datetime = NOW,
) -> DraftRecord:
    return DraftRecord(
        draft_id=f"{draft_number:032x}",
        version=version,
        org_id=ORG,
        conversation_id=CONVERSATION,
        run_id=run_id,
        user_id=USER,
        title=f"Legacy {draft_number}",
        content_text=body,
        status=DraftStatus.DRAFT,
        created_at=created_at,
    )


async def _read_revision(
    service: ArtifactService, *, artifact_id: str, revision: int
) -> bytes:
    _record, _stored, stream = await service.stream_revision(
        org_id=ORG,
        user_id=USER,
        artifact_id=artifact_id,
        revision=revision,
    )
    return b"".join([chunk async for chunk in stream])


def _migration_service(
    *,
    drafts: InMemoryDraftStore,
    runs: Sequence[_Run],
    events: _Events | None = None,
    artifacts: ArtifactService | None = None,
    audit: _Audit | None = None,
    checkpoints: InMemoryLegacyMigrationCheckpointStore | None = None,
) -> tuple[
    LegacyMigrationService,
    _Events,
    _Audit,
    InMemoryLegacyMigrationCheckpointStore,
]:
    event_port = events or _Events()
    audit_port = audit or _Audit()
    checkpoint_store = checkpoints or InMemoryLegacyMigrationCheckpointStore()
    return (
        LegacyMigrationService(
            draft_store=drafts,
            run_store=_Runs(rows=list(runs)),
            event_store=event_port,
            artifact_service=artifacts,
            checkpoints=checkpoint_store,
            audit=audit_port,
            now=lambda: NOW,
        ),
        event_port,
        audit_port,
        checkpoint_store,
    )


class TestLegacyMigrationService:
    async def test_dry_run_is_deterministic_redacted_and_non_mutating(self) -> None:
        drafts = InMemoryDraftStore()
        await drafts.insert_version(
            _draft(draft_number=1, version=1, body="secret one")
        )
        await drafts.insert_version(
            _draft(
                draft_number=1,
                version=2,
                body="secret two",
                created_at=NOW + timedelta(seconds=1),
            )
        )
        artifacts = _artifact_service((_Run(RUN_1),))
        service, events, audit, checkpoints = _migration_service(
            drafts=drafts,
            runs=(_Run(RUN_1),),
            artifacts=artifacts,
        )

        first = await service.dry_run(org_id=ORG, migration_id="e2_cohort_a")
        second = await service.dry_run(org_id=ORG, migration_id="e2_cohort_a")

        assert first == second
        assert first.dry_run is True
        assert first.cohort_ready is False
        assert first.drafts_total == 1
        assert first.drafts_pending == 1
        assert first.blockers == ("drafts_unverified", "dry_run_only")
        assert await checkpoints.load(org_id=ORG, migration_id="e2_cohort_a") is None
        assert events.dangerous_calls == 0
        assert len(audit.records) == 2
        serialized = first.model_dump_json()
        assert "secret one" not in serialized
        assert "secret two" not in serialized
        event_type, audit_record = audit.records[0]
        assert event_type == "e2_legacy_migration_reported"
        assert "content_text" not in repr(audit_record)

    async def test_apply_resumes_by_history_and_preserves_immutable_revisions(
        self,
    ) -> None:
        drafts = InMemoryDraftStore()
        await drafts.insert_version(_draft(draft_number=1, version=1, body="one-v1"))
        await drafts.insert_version(
            _draft(
                draft_number=1,
                version=2,
                body="one-v2",
                created_at=NOW + timedelta(seconds=1),
            )
        )
        await drafts.insert_version(
            _draft(
                draft_number=2,
                version=1,
                body="two-v1",
                run_id=RUN_2,
                created_at=NOW + timedelta(seconds=2),
            )
        )
        runs = (_Run(RUN_1), _Run(RUN_2))
        artifacts = _artifact_service(runs)
        service, events, _audit, checkpoints = _migration_service(
            drafts=drafts,
            runs=runs,
            artifacts=artifacts,
        )

        first = await service.apply(
            org_id=ORG, migration_id="e2_cohort_b", batch_size=1
        )
        checkpoint_after_first = await checkpoints.load(
            org_id=ORG, migration_id="e2_cohort_b"
        )
        assert first.migration_status == LegacyMigrationStatus.RUNNING.value
        assert first.drafts_verified == 1
        assert first.drafts_pending == 1
        assert checkpoint_after_first is not None
        assert checkpoint_after_first.after_draft_id == f"{1:032x}"

        completed = await service.apply(
            org_id=ORG, migration_id="e2_cohort_b", batch_size=1
        )
        checkpoint = await checkpoints.load(org_id=ORG, migration_id="e2_cohort_b")
        assert completed.migration_status == LegacyMigrationStatus.COMPLETED.value
        assert completed.cohort_ready is True
        assert completed.drafts_verified == 2
        assert checkpoint is not None
        assert checkpoint.status is LegacyMigrationStatus.COMPLETED
        assert checkpoint.after_draft_id == f"{2:032x}"
        assert events.dangerous_calls == 0

        binding = ArtifactDraftPathBinding(
            org_id=ORG,
            user_id=USER,
            conversation_id=CONVERSATION,
            run_id=RUN_1,
            draft_id=f"{1:032x}",
        )
        record = await artifacts.get_metadata(
            org_id=ORG, user_id=USER, artifact_id=binding.artifact_id
        )
        assert record.artifact.current_revision == 2
        assert (
            await _read_revision(artifacts, artifact_id=binding.artifact_id, revision=1)
            == b"one-v1"
        )
        assert (
            await _read_revision(artifacts, artifact_id=binding.artifact_id, revision=2)
            == b"one-v2"
        )
        first_revision = await artifacts.get_revision_metadata(
            org_id=ORG,
            user_id=USER,
            artifact_id=binding.artifact_id,
            revision=1,
        )
        second_revision = await artifacts.get_revision_metadata(
            org_id=ORG,
            user_id=USER,
            artifact_id=binding.artifact_id,
            revision=2,
        )
        assert first_revision.revision.author is ArtifactAuthor.IMPORT
        assert first_revision.revision.created_at == NOW.isoformat()
        assert (
            second_revision.revision.created_at
            == (NOW + timedelta(seconds=1)).isoformat()
        )

    async def test_transient_artifact_conflict_does_not_advance_the_cursor(
        self,
    ) -> None:
        drafts = InMemoryDraftStore()
        await drafts.insert_version(_draft(draft_number=1, version=1, body="retry"))
        transient = _TransientArtifacts()
        service, _events, _audit, checkpoints = _migration_service(
            drafts=drafts,
            runs=(_Run(RUN_1),),
            artifacts=transient,  # type: ignore[arg-type]
        )

        report = await service.apply(
            org_id=ORG, migration_id="e2_cohort_retry", batch_size=1
        )
        checkpoint = await checkpoints.load(org_id=ORG, migration_id="e2_cohort_retry")

        assert report.migration_status == LegacyMigrationStatus.RUNNING.value
        assert report.drafts_pending == 1
        assert checkpoint is not None
        assert checkpoint.after_draft_id is None
        assert transient.create_attempts == 3

    async def test_source_drift_blocks_without_importing_unfenced_history(self) -> None:
        drafts = InMemoryDraftStore()
        await drafts.insert_version(_draft(draft_number=1, version=1, body="one-v1"))
        await drafts.insert_version(
            _draft(draft_number=2, version=1, body="two-v1", run_id=RUN_2)
        )
        runs = (_Run(RUN_1), _Run(RUN_2))
        artifacts = _artifact_service(runs)
        service, _events, _audit, checkpoints = _migration_service(
            drafts=drafts,
            runs=runs,
            artifacts=artifacts,
        )

        running = await service.apply(
            org_id=ORG, migration_id="e2_cohort_c", batch_size=1
        )
        assert running.migration_status == LegacyMigrationStatus.RUNNING.value
        await drafts.insert_version(
            _draft(
                draft_number=2,
                version=2,
                body="two-v2-after-fence",
                run_id=RUN_2,
                created_at=NOW + timedelta(seconds=1),
            )
        )

        blocked = await service.apply(
            org_id=ORG, migration_id="e2_cohort_c", batch_size=1
        )
        checkpoint = await checkpoints.load(org_id=ORG, migration_id="e2_cohort_c")
        assert blocked.migration_status == LegacyMigrationStatus.BLOCKED.value
        assert "source_drift" in blocked.blockers
        assert checkpoint is not None
        assert checkpoint.status is LegacyMigrationStatus.BLOCKED

        binding = ArtifactDraftPathBinding(
            org_id=ORG,
            user_id=USER,
            conversation_id=CONVERSATION,
            run_id=RUN_2,
            draft_id=f"{2:032x}",
        )
        with pytest.raises(ArtifactNotFoundError):
            await artifacts.get_metadata(
                org_id=ORG, user_id=USER, artifact_id=binding.artifact_id
            )

    async def test_existing_conflicting_artifact_is_quarantined_not_overwritten(
        self,
    ) -> None:
        drafts = InMemoryDraftStore()
        source = _draft(draft_number=1, version=1, body="canonical source")
        await drafts.insert_version(source)
        artifacts = _artifact_service((_Run(RUN_1),))
        binding = ArtifactDraftPathBinding(
            org_id=ORG,
            user_id=USER,
            conversation_id=CONVERSATION,
            run_id=RUN_1,
            draft_id=source.draft_id,
        )
        await artifacts.create_draft_from_bytes(
            org_id=ORG,
            user_id=USER,
            request=ArtifactCreateRequest(
                run_id=RUN_1,
                kind=ArtifactKind.DOCUMENT,
                title=source.title,
                media_type="text/markdown",
                suggested_filename=f"{source.draft_id}.md",
                idempotency_key="existing-conflict",
            ),
            provenance=ArtifactProvenance(
                author=ArtifactAuthor.IMPORT,
                source_ref=binding.source_ref,
            ),
            content=b"different-existing-content",
            artifact_id=binding.artifact_id,
            created_at=source.created_at,
        )
        service, _events, _audit, _checkpoints = _migration_service(
            drafts=drafts,
            runs=(_Run(RUN_1),),
            artifacts=artifacts,
        )

        report = await service.apply(
            org_id=ORG, migration_id="e2_cohort_d", batch_size=1
        )
        record = await artifacts.get_metadata(
            org_id=ORG, user_id=USER, artifact_id=binding.artifact_id
        )
        assert report.migration_status == LegacyMigrationStatus.BLOCKED.value
        assert report.drafts_quarantined == 1
        assert record.artifact.current_revision == 1
        assert (
            await _read_revision(artifacts, artifact_id=binding.artifact_id, revision=1)
            == b"different-existing-content"
        )

    async def test_open_legacy_stage_requires_fresh_approval_and_never_dispatches(
        self,
    ) -> None:
        drafts = InMemoryDraftStore()
        events = _Events(
            rows={
                RUN_1: [
                    _Event(
                        event_type="write.staged",
                        sequence_no=1,
                        payload={
                            "stage_id": "stage_e2_migration",
                            "surface_id": "surface_e2_migration",
                            "proposal_ref": f"draft://{1:032x}/v1",
                            "target": {"connector": "linear", "op": "create"},
                        },
                    )
                ]
            }
        )
        service, event_port, _audit, _checkpoints = _migration_service(
            drafts=drafts,
            runs=(_Run(RUN_1),),
            events=events,
            artifacts=_artifact_service((_Run(RUN_1),)),
        )

        report = await service.apply(
            org_id=ORG, migration_id="e2_cohort_e", batch_size=1
        )

        assert report.migration_status == LegacyMigrationStatus.BLOCKED.value
        assert report.stages_requiring_fresh_approval == 1
        assert "stages_require_fresh_approval" in report.blockers
        assert event_port.dangerous_calls == 0

    async def test_audit_failure_leaves_evidence_non_enableable(self) -> None:
        drafts = InMemoryDraftStore()
        await drafts.insert_version(_draft(draft_number=1, version=1, body="audit"))
        audit = _Audit(fail=True)
        checkpoints = InMemoryLegacyMigrationCheckpointStore()
        service, _events, _audit, _checkpoints = _migration_service(
            drafts=drafts,
            runs=(_Run(RUN_1),),
            artifacts=_artifact_service((_Run(RUN_1),)),
            audit=audit,
            checkpoints=checkpoints,
        )

        report = await service.apply(
            org_id=ORG, migration_id="e2_cohort_f", batch_size=1
        )
        checkpoint = await checkpoints.load(org_id=ORG, migration_id="e2_cohort_f")

        assert report.migration_status == LegacyMigrationStatus.AUDIT_PENDING.value
        assert report.audit_recorded is False
        assert report.cohort_ready is False
        assert "audit_pending" in report.blockers
        assert checkpoint is not None
        assert checkpoint.status is LegacyMigrationStatus.AUDIT_PENDING

    async def test_source_drift_with_audit_failure_downgrades_completed_evidence(
        self,
    ) -> None:
        drafts = InMemoryDraftStore()
        await drafts.insert_version(_draft(draft_number=1, version=1, body="v1"))
        audit = _Audit()
        checkpoints = InMemoryLegacyMigrationCheckpointStore()
        service, _events, _audit, _checkpoints = _migration_service(
            drafts=drafts,
            runs=(_Run(RUN_1),),
            artifacts=_artifact_service((_Run(RUN_1),)),
            audit=audit,
            checkpoints=checkpoints,
        )
        completed = await service.apply(
            org_id=ORG, migration_id="e2_cohort_g", batch_size=1
        )
        assert completed.cohort_ready is True
        await drafts.insert_version(
            _draft(
                draft_number=1,
                version=2,
                body="v2-after-fence",
                created_at=NOW + timedelta(seconds=1),
            )
        )
        audit.fail = True

        report = await service.apply(
            org_id=ORG, migration_id="e2_cohort_g", batch_size=1
        )
        checkpoint = await checkpoints.load(org_id=ORG, migration_id="e2_cohort_g")

        assert report.migration_status == LegacyMigrationStatus.AUDIT_PENDING.value
        assert report.cohort_ready is False
        assert {"audit_pending", "source_drift"}.issubset(report.blockers)
        assert checkpoint is not None
        assert checkpoint.status is LegacyMigrationStatus.AUDIT_PENDING
