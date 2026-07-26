"""Evidence-first E2 migration service for legacy drafts and staged writes.

The service has deliberately narrow authority:

* it reads a single tenant's complete bounded legacy inventory;
* it imports only verified draft-version histories into immutable artifacts;
* it writes a CAS checkpoint and a redacted audit marker; and
* it classifies old staged writes without changing, approving, queueing, or
  executing any of them.

This is a prerequisite for cohort enablement, not the cutover itself.  No
global flag, fallback, or direct write path is changed here.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
from typing import Protocol, runtime_checkable

from agent_runtime.artifacts import (
    ArtifactConflictError,
    ArtifactCreateRequest,
    ArtifactNotFoundError,
    ArtifactProvenance,
    ArtifactRevisionRequest,
    ArtifactService,
)
from agent_runtime.capabilities.backends.artifact_draft_backend import (
    ArtifactDraftPathBinding,
)
from agent_runtime.persistence.records import DraftRecord
from agent_runtime.surfaces_v2.canonical_json import canonical_json_sha256
from agent_runtime.surfaces_v2.legacy_migration import (
    LegacyDraftDisposition,
    LegacyDraftEvidence,
    LegacyDraftVersionEvidence,
    LegacyMigrationCheckpoint,
    LegacyMigrationCheckpointStore,
    LegacyMigrationInventory,
    LegacyMigrationReport,
    LegacyMigrationStateError,
    LegacyMigrationStatus,
    LegacyStageDisposition,
    LegacyStageEvidence,
    inventory_digest,
    report_digest,
)
from agent_runtime.surfaces_v2.ledger_models import ArtifactAuthor, ArtifactKind
from agent_runtime.surfaces_v2.staging import StagedWriteFold, StagedWriteStatus


class LegacyMigrationError(RuntimeError):
    """Safe control-plane failure; details are never returned to callers."""

    code = "legacy_migration_unavailable"
    safe_message = "Legacy migration evidence is unavailable."


class LegacyMigrationAuthorizationError(LegacyMigrationError):
    code = "legacy_migration_unauthorized"
    safe_message = "This migration operation is not authorized."


class LegacyMigrationSourceError(LegacyMigrationError):
    code = "legacy_migration_source_unavailable"
    safe_message = "The legacy migration source could not be read safely."


class LegacyMigrationAuditError(LegacyMigrationError):
    code = "legacy_migration_audit_unavailable"
    safe_message = "The migration result could not be audited."


@runtime_checkable
class LegacyDraftInventoryPort(Protocol):
    """Narrow full-history scan capability, intentionally not a UI port."""

    async def list_versions_for_migration(
        self,
        *,
        org_id: str,
        after: tuple[str, int] | None,
        limit: int,
    ) -> Sequence[DraftRecord]:
        """Return a stable keyset page in ``(draft_id, version)`` order."""


@runtime_checkable
class LegacyRunInventoryPort(Protocol):
    """Authorized tenant-wide run inventory used only by this migration."""

    async def list_runs_for_migration(
        self,
        *,
        org_id: str,
        after_run_id: str | None,
        limit: int,
    ) -> Sequence[object]:
        """Return a stable keyset page ordered by opaque ``run_id``."""


@runtime_checkable
class LegacyEventInventoryPort(Protocol):
    """The existing immutable event ledger read needed to classify stages."""

    async def list_events_after(
        self, *, org_id: str, run_id: str, after_sequence: int
    ) -> Sequence[object]:
        """Return the run's event prefix after a monotonic sequence number."""


@runtime_checkable
class LegacyMigrationAuditPort(Protocol):
    """The existing append-only audit sink, kept structurally narrow."""

    async def write_audit_log(self, *, event_type: str, record: object) -> None:
        """Append one safe audit record."""


@dataclass(frozen=True)
class _DraftGroup:
    evidence: LegacyDraftEvidence
    records: tuple[DraftRecord, ...]


@dataclass(frozen=True)
class _InventorySnapshot:
    inventory: LegacyMigrationInventory
    draft_groups: tuple[_DraftGroup, ...]


class LegacyMigrationService:
    """Build and apply deterministic E2 migration evidence for one tenant."""

    _SCAN_PAGE_SIZE = 100
    _MAX_DRAFT_VERSIONS = 100_000
    _MAX_RUNS = 100_000
    _MAX_EVENTS_PER_RUN = 50_000
    _MAX_ARTIFACT_RETRIES = 3

    def __init__(
        self,
        *,
        draft_store: object | None,
        run_store: object | None,
        event_store: object | None,
        artifact_service: ArtifactService | None,
        checkpoints: LegacyMigrationCheckpointStore,
        audit: object | None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._draft_store = draft_store
        self._run_store = run_store
        self._event_store = event_store
        self._artifacts = artifact_service
        self._checkpoints = checkpoints
        self._audit = audit
        self._now = now or (lambda: datetime.now(timezone.utc))

    async def dry_run(self, *, org_id: str, migration_id: str) -> LegacyMigrationReport:
        """Inventory one tenant without creating artifacts or checkpoints."""

        snapshot = await self._inventory(org_id=org_id)
        report = self._report(
            snapshot=snapshot,
            migration_id=migration_id,
            dry_run=True,
            migration_status="dry_run",
            audit_recorded=True,
            checkpoint=None,
        )
        await self._append_audit(report=report, action="dry_run")
        return report

    async def apply(
        self,
        *,
        org_id: str,
        migration_id: str,
        batch_size: int,
    ) -> LegacyMigrationReport:
        """Resume at most ``batch_size`` full histories against a frozen digest."""

        if batch_size < 1 or batch_size > self._SCAN_PAGE_SIZE:
            raise LegacyMigrationSourceError()
        for _ in range(self._MAX_ARTIFACT_RETRIES):
            snapshot = await self._inventory(org_id=org_id)
            checkpoint = await self._load_or_create_checkpoint(
                snapshot=snapshot,
                migration_id=migration_id,
            )
            if checkpoint.source_digest != snapshot.inventory.source_digest:
                report = await self._refresh_report(
                    snapshot=snapshot,
                    migration_id=migration_id,
                    dry_run=False,
                    migration_status=LegacyMigrationStatus.BLOCKED.value,
                    audit_recorded=True,
                    extra_blockers=("source_drift",),
                )
                try:
                    await self._append_audit(report=report, action="source_drift")
                except LegacyMigrationAuditError:
                    pending = await self._mark_audit_pending(
                        snapshot=snapshot,
                        migration_id=migration_id,
                        checkpoint=checkpoint,
                        after_draft_id=checkpoint.after_draft_id,
                        extra_blockers=("source_drift",),
                    )
                    if pending is not None:
                        return pending
                    continue
                updated = await self._transition(
                    checkpoint=checkpoint,
                    after_draft_id=checkpoint.after_draft_id,
                    status=LegacyMigrationStatus.BLOCKED,
                    report_digest=report.report_digest,
                )
                if updated is not None:
                    return report
                continue

            if checkpoint.status is LegacyMigrationStatus.BLOCKED:
                report = await self._refresh_report(
                    snapshot=snapshot,
                    migration_id=migration_id,
                    dry_run=False,
                    migration_status=LegacyMigrationStatus.BLOCKED.value,
                    audit_recorded=True,
                )
                await self._append_audit(report=report, action="rechecked")
                return report

            if not snapshot.inventory.source_complete or self._artifacts is None:
                report = await self._refresh_report(
                    snapshot=snapshot,
                    migration_id=migration_id,
                    dry_run=False,
                    migration_status=LegacyMigrationStatus.BLOCKED.value,
                    audit_recorded=True,
                    extra_blockers=(
                        "artifact_repository_unavailable"
                        if self._artifacts is None
                        else "source_incomplete",
                    ),
                )
                try:
                    await self._append_audit(report=report, action="blocked_preflight")
                except LegacyMigrationAuditError:
                    pending = await self._mark_audit_pending(
                        snapshot=snapshot,
                        migration_id=migration_id,
                        checkpoint=checkpoint,
                        after_draft_id=checkpoint.after_draft_id,
                        extra_blockers=report.blockers,
                    )
                    if pending is not None:
                        return pending
                    continue
                updated = await self._transition(
                    checkpoint=checkpoint,
                    after_draft_id=checkpoint.after_draft_id,
                    status=LegacyMigrationStatus.BLOCKED,
                    report_digest=report.report_digest,
                )
                if updated is not None:
                    return report
                continue

            known_draft_ids = {
                group.evidence.draft_id for group in snapshot.draft_groups
            }
            if (
                checkpoint.after_draft_id is not None
                and checkpoint.after_draft_id not in known_draft_ids
            ):
                report = await self._refresh_report(
                    snapshot=snapshot,
                    migration_id=migration_id,
                    dry_run=False,
                    migration_status=LegacyMigrationStatus.BLOCKED.value,
                    audit_recorded=True,
                    extra_blockers=("checkpoint_cursor_outside_source",),
                )
                try:
                    await self._append_audit(
                        report=report, action="checkpoint_mismatch"
                    )
                except LegacyMigrationAuditError:
                    pending = await self._mark_audit_pending(
                        snapshot=snapshot,
                        migration_id=migration_id,
                        checkpoint=checkpoint,
                        after_draft_id=checkpoint.after_draft_id,
                        extra_blockers=("checkpoint_cursor_outside_source",),
                    )
                    if pending is not None:
                        return pending
                    continue
                updated = await self._transition(
                    checkpoint=checkpoint,
                    after_draft_id=checkpoint.after_draft_id,
                    status=LegacyMigrationStatus.BLOCKED,
                    report_digest=report.report_digest,
                )
                if updated is not None:
                    return report
                continue

            current = checkpoint
            groups = tuple(
                group
                for group in snapshot.draft_groups
                if current.after_draft_id is None
                or group.evidence.draft_id > current.after_draft_id
            )
            for group in groups[:batch_size]:
                if group.evidence.eligible:
                    await self._migrate_group(group=group, migration_id=migration_id)
                # A cursor may move only after a complete history is either
                # verified or deliberately quarantined.  In particular, a
                # transient artifact-store failure must not get converted into
                # an irreversible "blocked after cursor" state.
                disposition = await self._artifact_disposition(group=group)
                if disposition is LegacyDraftDisposition.PENDING:
                    report = await self._refresh_report(
                        snapshot=snapshot,
                        migration_id=migration_id,
                        dry_run=False,
                        migration_status=LegacyMigrationStatus.RUNNING.value,
                        audit_recorded=True,
                    )
                    try:
                        await self._append_audit(report=report, action="retry_pending")
                    except LegacyMigrationAuditError:
                        pending = await self._mark_audit_pending(
                            snapshot=snapshot,
                            migration_id=migration_id,
                            checkpoint=current,
                            after_draft_id=current.after_draft_id,
                        )
                        if pending is not None:
                            return pending
                        break
                    updated = await self._transition(
                        checkpoint=current,
                        after_draft_id=current.after_draft_id,
                        status=LegacyMigrationStatus.RUNNING,
                        report_digest=report.report_digest,
                    )
                    if updated is not None:
                        return report
                    break
                updated = await self._transition(
                    checkpoint=current,
                    after_draft_id=group.evidence.draft_id,
                    status=LegacyMigrationStatus.RUNNING,
                    report_digest=None,
                )
                if updated is None:
                    break
                current = updated
            else:
                candidate_status = self._target_status(
                    snapshot=snapshot, checkpoint=current
                )
                report = await self._refresh_report(
                    snapshot=snapshot,
                    migration_id=migration_id,
                    dry_run=False,
                    migration_status=candidate_status.value,
                    audit_recorded=True,
                )
                if (
                    candidate_status is LegacyMigrationStatus.COMPLETED
                    and not report.cohort_ready
                ):
                    candidate_status = LegacyMigrationStatus.BLOCKED
                    report = await self._refresh_report(
                        snapshot=snapshot,
                        migration_id=migration_id,
                        dry_run=False,
                        migration_status=candidate_status.value,
                        audit_recorded=True,
                    )
                try:
                    await self._append_audit(report=report, action="apply")
                except LegacyMigrationAuditError:
                    pending = await self._mark_audit_pending(
                        snapshot=snapshot,
                        migration_id=migration_id,
                        checkpoint=current,
                        after_draft_id=current.after_draft_id,
                    )
                    if pending is not None:
                        return pending
                    continue
                updated = await self._transition(
                    checkpoint=current,
                    after_draft_id=current.after_draft_id,
                    status=LegacyMigrationStatus(report.migration_status),
                    report_digest=report.report_digest,
                )
                if updated is not None:
                    return report
            # Lost a CAS race while advancing a group. Start from the durable
            # checkpoint and re-verify; deterministic artifact ids make it safe.
        raise LegacyMigrationStateError()

    async def _inventory(self, *, org_id: str) -> _InventorySnapshot:
        runs = await self._read_runs(org_id=org_id)
        run_ids = frozenset(
            run_id
            for run in runs
            if isinstance((run_id := getattr(run, "run_id", None)), str)
        )
        drafts = await self._read_drafts(org_id=org_id, run_ids=run_ids)
        stages, source_complete = await self._read_stages(org_id=org_id, runs=runs)
        evidences = tuple(group.evidence for group in drafts)
        digest = inventory_digest(
            org_id=org_id,
            source_complete=source_complete,
            drafts=evidences,
            stages=stages,
        )
        return _InventorySnapshot(
            inventory=LegacyMigrationInventory(
                org_id=org_id,
                source_digest=digest,
                source_complete=source_complete,
                drafts=evidences,
                stages=stages,
            ),
            draft_groups=drafts,
        )

    async def _read_drafts(
        self, *, org_id: str, run_ids: frozenset[str]
    ) -> tuple[_DraftGroup, ...]:
        store = self._require_draft_store()
        records: list[DraftRecord] = []
        after: tuple[str, int] | None = None
        while True:
            page = tuple(
                await store.list_versions_for_migration(
                    org_id=org_id,
                    after=after,
                    limit=self._SCAN_PAGE_SIZE,
                )
            )
            if len(page) > self._SCAN_PAGE_SIZE:
                raise LegacyMigrationSourceError()
            self._validate_draft_page(org_id=org_id, page=page, after=after)
            records.extend(page)
            if len(records) > self._MAX_DRAFT_VERSIONS:
                raise LegacyMigrationSourceError()
            if not page or len(page) < self._SCAN_PAGE_SIZE:
                break
            after = (page[-1].draft_id, page[-1].version)

        grouped: dict[str, list[DraftRecord]] = {}
        for record in records:
            grouped.setdefault(record.draft_id, []).append(record)
        return tuple(
            _DraftGroup(
                evidence=self._draft_evidence(records=tuple(history), run_ids=run_ids),
                records=tuple(history),
            )
            for _draft_id, history in sorted(grouped.items())
        )

    async def _read_stages(
        self, *, org_id: str, runs: Sequence[object]
    ) -> tuple[tuple[LegacyStageEvidence, ...], bool]:
        event_store = self._require_event_store()
        evidence: list[LegacyStageEvidence] = []
        source_complete = True
        for run in runs:
            run_id = getattr(run, "run_id", None)
            run_org_id = getattr(run, "org_id", None)
            if not isinstance(run_id, str) or run_org_id != org_id:
                return (), False
            try:
                events = tuple(
                    await event_store.list_events_after(
                        org_id=org_id,
                        run_id=run_id,
                        after_sequence=0,
                    )
                )
            except Exception:
                source_complete = False
                continue
            if len(events) > self._MAX_EVENTS_PER_RUN or not self._valid_event_run(
                events
            ):
                source_complete = False
                continue
            try:
                folded = StagedWriteFold.fold(events)
            except Exception:
                source_complete = False
                continue
            for stage_id, state in folded.items():
                disposition = self._stage_disposition(state.status)
                try:
                    # Hash the entire folded state, including any row-set or
                    # proposal details, without ever serialising those details
                    # into the report/audit surface.  This makes a frozen
                    # source digest detect a material legacy-stage change,
                    # rather than only a status transition.
                    folded_state_digest = canonical_json_sha256(
                        state.model_dump(mode="json")
                    )
                    source_digest = canonical_json_sha256(
                        {
                            "run_id": run_id,
                            "stage_id": stage_id,
                            "folded_state_digest": folded_state_digest,
                            "disposition": disposition.value,
                        }
                    )
                except Exception:
                    source_complete = False
                    continue
                evidence.append(
                    LegacyStageEvidence(
                        run_id=run_id,
                        stage_id=stage_id,
                        source_digest=source_digest,
                        disposition=disposition,
                        status=state.status.value,
                    )
                )
        return tuple(
            sorted(evidence, key=lambda item: (item.run_id, item.stage_id))
        ), source_complete

    async def _read_runs(self, *, org_id: str) -> tuple[object, ...]:
        store = self._require_run_store()
        records: list[object] = []
        after: str | None = None
        while True:
            page = tuple(
                await store.list_runs_for_migration(
                    org_id=org_id,
                    after_run_id=after,
                    limit=self._SCAN_PAGE_SIZE,
                )
            )
            if len(page) > self._SCAN_PAGE_SIZE:
                raise LegacyMigrationSourceError()
            identifiers = tuple(getattr(item, "run_id", None) for item in page)
            if (
                any(not isinstance(identifier, str) for identifier in identifiers)
                or tuple(sorted(identifiers)) != identifiers
                or len(set(identifiers)) != len(identifiers)
                or (
                    after is not None
                    and any(identifier <= after for identifier in identifiers)
                )
                or any(getattr(item, "org_id", None) != org_id for item in page)
            ):
                raise LegacyMigrationSourceError()
            records.extend(page)
            if len(records) > self._MAX_RUNS:
                raise LegacyMigrationSourceError()
            if not page or len(page) < self._SCAN_PAGE_SIZE:
                return tuple(records)
            after = identifiers[-1]

    def _draft_evidence(
        self, *, records: tuple[DraftRecord, ...], run_ids: frozenset[str] = frozenset()
    ) -> LegacyDraftEvidence:
        ordered = tuple(sorted(records, key=lambda record: record.version))
        first = ordered[0]
        scope = {
            "org_id": first.org_id,
            "conversation_id": first.conversation_id,
            "run_id": first.run_id,
            "user_id": first.user_id,
        }
        versions: list[LegacyDraftVersionEvidence] = []
        validation_code = "eligible"
        eligible = True
        expected_version = 1
        for record in ordered:
            content = record.content_text.encode("utf-8", errors="surrogatepass")
            title = record.title.encode("utf-8", errors="surrogatepass")
            versions.append(
                LegacyDraftVersionEvidence(
                    version=record.version,
                    content_digest=hashlib.sha256(content).hexdigest(),
                    title_digest=hashlib.sha256(title).hexdigest(),
                    byte_size=len(content),
                    created_at=record.created_at,
                )
            )
            if record.version != expected_version:
                eligible, validation_code = False, "history_noncontiguous"
            expected_version += 1
            if {
                "org_id": record.org_id,
                "conversation_id": record.conversation_id,
                "run_id": record.run_id,
                "user_id": record.user_id,
            } != scope:
                eligible, validation_code = False, "scope_mismatch"
        if first.run_id is None:
            eligible, validation_code = False, "run_scope_missing"
        elif first.run_id not in run_ids:
            eligible, validation_code = False, "run_not_found"
        scope_digest = canonical_json_sha256(scope)
        source_digest = canonical_json_sha256(
            {
                "draft_id": first.draft_id,
                "scope_digest": scope_digest,
                "versions": [item.model_dump(mode="json") for item in versions],
                "eligible": eligible,
                "validation_code": validation_code,
            }
        )
        return LegacyDraftEvidence(
            draft_id=first.draft_id,
            scope_digest=scope_digest,
            versions=tuple(versions),
            source_digest=source_digest,
            eligible=eligible,
            validation_code=validation_code,
        )

    async def _migrate_group(self, *, group: _DraftGroup, migration_id: str) -> None:
        if self._artifacts is None:
            return
        for _ in range(self._MAX_ARTIFACT_RETRIES):
            disposition = await self._artifact_disposition(group=group)
            if disposition is not LegacyDraftDisposition.PENDING:
                return
            first = group.records[0]
            binding = ArtifactDraftPathBinding(
                org_id=first.org_id,
                user_id=first.user_id,
                conversation_id=first.conversation_id,
                run_id=first.run_id or "",
                draft_id=first.draft_id,
            )
            try:
                record = await self._artifacts.get_metadata(
                    org_id=first.org_id,
                    user_id=first.user_id,
                    artifact_id=binding.artifact_id,
                )
            except ArtifactNotFoundError:
                try:
                    await self._artifacts.create_draft_from_bytes(
                        org_id=first.org_id,
                        user_id=first.user_id,
                        request=ArtifactCreateRequest(
                            run_id=first.run_id or "",
                            kind=ArtifactKind.DOCUMENT,
                            title=self._title(first),
                            media_type="text/markdown",
                            suggested_filename=f"{first.draft_id}.md",
                            expected_digest=group.evidence.versions[0].content_digest,
                            idempotency_key=self._idempotency_key(
                                migration_id=migration_id,
                                draft_id=first.draft_id,
                                version=1,
                            ),
                        ),
                        provenance=ArtifactProvenance(
                            author=ArtifactAuthor.IMPORT,
                            source_ref=binding.source_ref,
                        ),
                        content=first.content_text.encode(
                            "utf-8", errors="surrogatepass"
                        ),
                        artifact_id=binding.artifact_id,
                        created_at=first.created_at,
                    )
                except ArtifactConflictError:
                    continue
                except (ArtifactNotFoundError, ValueError):
                    return
                continue
            current_revision = record.current_revision.revision.revision
            if current_revision >= len(group.records):
                return
            next_record = group.records[current_revision]
            try:
                await self._artifacts.append_revision_from_stream(
                    org_id=next_record.org_id,
                    user_id=next_record.user_id,
                    request=ArtifactRevisionRequest(
                        artifact_id=binding.artifact_id,
                        parent_revision=current_revision,
                        expected_digest=group.evidence.versions[
                            current_revision
                        ].content_digest,
                        idempotency_key=self._idempotency_key(
                            migration_id=migration_id,
                            draft_id=next_record.draft_id,
                            version=next_record.version,
                        ),
                    ),
                    provenance=ArtifactProvenance(
                        author=ArtifactAuthor.IMPORT,
                        source_ref=binding.source_ref,
                    ),
                    chunks=self._single_chunk(
                        next_record.content_text.encode("utf-8", errors="surrogatepass")
                    ),
                    created_at=next_record.created_at,
                )
            except ArtifactConflictError:
                continue
            except (ArtifactNotFoundError, ValueError):
                return
        # A concurrent repository mutation never becomes a speculative import.
        # The final report keeps the group pending/quarantined and blocks cohort.

    async def _artifact_disposition(
        self, *, group: _DraftGroup
    ) -> LegacyDraftDisposition:
        if not group.evidence.eligible:
            return LegacyDraftDisposition.QUARANTINED
        if self._artifacts is None:
            return LegacyDraftDisposition.PENDING
        first = group.records[0]
        binding = ArtifactDraftPathBinding(
            org_id=first.org_id,
            user_id=first.user_id,
            conversation_id=first.conversation_id,
            run_id=first.run_id or "",
            draft_id=first.draft_id,
        )
        try:
            record = await self._artifacts.get_metadata(
                org_id=first.org_id,
                user_id=first.user_id,
                artifact_id=binding.artifact_id,
            )
        except ArtifactNotFoundError:
            return LegacyDraftDisposition.PENDING
        if (
            record.artifact.org_id != first.org_id
            or record.artifact.user_id != first.user_id
            or record.artifact.conversation_id != first.conversation_id
            or record.artifact.run_id != first.run_id
            or record.artifact.kind is not ArtifactKind.DOCUMENT
            or record.artifact.media_type != "text/markdown"
            or record.artifact.title != self._title(first)
            or record.current_revision.revision.source_ref != binding.source_ref
        ):
            return LegacyDraftDisposition.QUARANTINED
        current = record.current_revision.revision.revision
        if current > len(group.records):
            return LegacyDraftDisposition.QUARANTINED
        for expected, source in zip(group.evidence.versions[:current], group.records):
            try:
                stored = await self._artifacts.get_revision_metadata(
                    org_id=source.org_id,
                    user_id=source.user_id,
                    artifact_id=binding.artifact_id,
                    revision=expected.version,
                )
                _record, _revision, stream = await self._artifacts.stream_revision(
                    org_id=source.org_id,
                    user_id=source.user_id,
                    artifact_id=binding.artifact_id,
                    revision=expected.version,
                )
                payload = b"".join([chunk async for chunk in stream])
            except Exception:
                return LegacyDraftDisposition.QUARANTINED
            revision = stored.revision
            if (
                revision.revision != expected.version
                or revision.content_digest != expected.content_digest
                or revision.byte_size != expected.byte_size
                or hashlib.sha256(payload).hexdigest() != expected.content_digest
                or revision.author is not ArtifactAuthor.IMPORT
                or revision.source_ref != binding.source_ref
                or self._utc_iso(revision.created_at)
                != self._utc_iso(source.created_at)
            ):
                return LegacyDraftDisposition.QUARANTINED
        return (
            LegacyDraftDisposition.VERIFIED
            if current == len(group.records)
            else LegacyDraftDisposition.PENDING
        )

    def _report(
        self,
        *,
        snapshot: _InventorySnapshot,
        migration_id: str,
        dry_run: bool,
        migration_status: str,
        audit_recorded: bool,
        checkpoint: LegacyMigrationCheckpoint | None,
        extra_blockers: tuple[str, ...] = (),
    ) -> LegacyMigrationReport:
        dispositions: list[LegacyDraftDisposition] = []
        # This method is sync by design; it reports the current checkpoint
        # truth, then ``apply`` re-validates artifacts before its final report.
        for group in snapshot.draft_groups:
            if not group.evidence.eligible:
                dispositions.append(LegacyDraftDisposition.QUARANTINED)
            elif dry_run or self._artifacts is None:
                dispositions.append(LegacyDraftDisposition.PENDING)
            elif checkpoint is not None and (
                checkpoint.after_draft_id is None
                or group.evidence.draft_id > checkpoint.after_draft_id
            ):
                dispositions.append(LegacyDraftDisposition.PENDING)
            else:
                # A completed cursor alone is not integrity evidence; async
                # verification occurs in ``_refresh_report`` below.
                dispositions.append(LegacyDraftDisposition.PENDING)
        return self._report_from_dispositions(
            inventory=snapshot.inventory,
            migration_id=migration_id,
            dry_run=dry_run,
            migration_status=migration_status,
            audit_recorded=audit_recorded,
            dispositions=tuple(dispositions),
            extra_blockers=extra_blockers,
        )

    async def _refresh_report(
        self,
        *,
        snapshot: _InventorySnapshot,
        migration_id: str,
        dry_run: bool,
        migration_status: str,
        audit_recorded: bool,
        extra_blockers: tuple[str, ...] = (),
    ) -> LegacyMigrationReport:
        dispositions: list[LegacyDraftDisposition] = []
        for group in snapshot.draft_groups:
            dispositions.append(
                await self._artifact_disposition(group=group)
                if not dry_run
                else LegacyDraftDisposition.PENDING
            )
        return self._report_from_dispositions(
            inventory=snapshot.inventory,
            migration_id=migration_id,
            dry_run=dry_run,
            migration_status=migration_status,
            audit_recorded=audit_recorded,
            dispositions=tuple(dispositions),
            extra_blockers=extra_blockers,
        )

    def _report_from_dispositions(
        self,
        *,
        inventory: LegacyMigrationInventory,
        migration_id: str,
        dry_run: bool,
        migration_status: str,
        audit_recorded: bool,
        dispositions: tuple[LegacyDraftDisposition, ...],
        extra_blockers: tuple[str, ...],
    ) -> LegacyMigrationReport:
        verified = sum(item is LegacyDraftDisposition.VERIFIED for item in dispositions)
        pending = sum(item is LegacyDraftDisposition.PENDING for item in dispositions)
        quarantined = sum(
            item is LegacyDraftDisposition.QUARANTINED for item in dispositions
        )
        compatibility = sum(
            item.disposition is LegacyStageDisposition.COMPATIBILITY_ONLY
            for item in inventory.stages
        )
        requires_approval = sum(
            item.disposition is LegacyStageDisposition.REQUIRE_FRESH_APPROVAL
            for item in inventory.stages
        )
        stage_quarantined = sum(
            item.disposition is LegacyStageDisposition.QUARANTINED
            for item in inventory.stages
        )
        blockers: list[str] = list(extra_blockers)
        if not inventory.source_complete:
            blockers.append("source_incomplete")
        if pending:
            blockers.append("drafts_unverified")
        if quarantined:
            blockers.append("drafts_quarantined")
        if requires_approval:
            blockers.append("stages_require_fresh_approval")
        if stage_quarantined:
            blockers.append("stages_quarantined")
        if not audit_recorded:
            blockers.append("audit_pending")
        if dry_run:
            blockers.append("dry_run_only")
        normalized_blockers = tuple(sorted(set(blockers)))
        cohort_ready = not normalized_blockers and migration_status == "completed"
        digest = report_digest(
            migration_id=migration_id,
            org_id=inventory.org_id,
            dry_run=dry_run,
            source_digest=inventory.source_digest,
            migration_status=migration_status,
            drafts_total=len(dispositions),
            drafts_verified=verified,
            drafts_pending=pending,
            drafts_quarantined=quarantined,
            stages_compatibility_only=compatibility,
            stages_requiring_fresh_approval=requires_approval,
            stages_quarantined=stage_quarantined,
            source_complete=inventory.source_complete,
            audit_recorded=audit_recorded,
            cohort_ready=cohort_ready,
            blockers=normalized_blockers,
        )
        return LegacyMigrationReport(
            migration_id=migration_id,
            org_id=inventory.org_id,
            dry_run=dry_run,
            source_digest=inventory.source_digest,
            migration_status=migration_status,
            drafts_total=len(dispositions),
            drafts_verified=verified,
            drafts_pending=pending,
            drafts_quarantined=quarantined,
            stages_compatibility_only=compatibility,
            stages_requiring_fresh_approval=requires_approval,
            stages_quarantined=stage_quarantined,
            source_complete=inventory.source_complete,
            audit_recorded=audit_recorded,
            cohort_ready=cohort_ready,
            blockers=normalized_blockers,
            report_digest=digest,
        )

    def _target_status(
        self, *, snapshot: _InventorySnapshot, checkpoint: LegacyMigrationCheckpoint
    ) -> LegacyMigrationStatus:
        last_draft_id = (
            snapshot.draft_groups[-1].evidence.draft_id
            if snapshot.draft_groups
            else None
        )
        if last_draft_id is not None and checkpoint.after_draft_id != last_draft_id:
            return LegacyMigrationStatus.RUNNING
        # Actual verification determines completion later. This conservative
        # status keeps any terminal record in the audit/report path.
        return LegacyMigrationStatus.COMPLETED

    async def _load_or_create_checkpoint(
        self, *, snapshot: _InventorySnapshot, migration_id: str
    ) -> LegacyMigrationCheckpoint:
        now = self._utc_now()
        try:
            return await self._checkpoints.load_or_create(
                checkpoint=LegacyMigrationCheckpoint(
                    migration_id=migration_id,
                    org_id=snapshot.inventory.org_id,
                    source_digest=snapshot.inventory.source_digest,
                    after_draft_id=None,
                    status=LegacyMigrationStatus.RUNNING,
                    report_digest=None,
                    revision=0,
                    created_at=now,
                    updated_at=now,
                )
            )
        except LegacyMigrationStateError:
            existing = await self._checkpoints.load(
                org_id=snapshot.inventory.org_id,
                migration_id=migration_id,
            )
            if existing is None:
                raise
            return existing

    async def _transition(
        self,
        *,
        checkpoint: LegacyMigrationCheckpoint,
        after_draft_id: str | None,
        status: LegacyMigrationStatus,
        report_digest: str | None,
    ) -> LegacyMigrationCheckpoint | None:
        return await self._checkpoints.compare_and_set(
            expected=checkpoint,
            after_draft_id=after_draft_id,
            status=status,
            report_digest=report_digest,
            updated_at=self._utc_now(),
        )

    async def _append_audit(
        self, *, report: LegacyMigrationReport, action: str
    ) -> None:
        audit = self._audit
        if not isinstance(audit, LegacyMigrationAuditPort):
            raise LegacyMigrationAuditError()
        try:
            await audit.write_audit_log(
                event_type="e2_legacy_migration_reported",
                record={
                    "org_id": report.org_id,
                    "user_id": "system",
                    "actor_type": "service",
                    "resource_type": "e2_legacy_migration",
                    "resource_id": report.migration_id,
                    "outcome": report.migration_status,
                    "metadata": {
                        "action": action,
                        "dry_run": report.dry_run,
                        "source_digest": report.source_digest,
                        "report_digest": report.report_digest,
                        "cohort_ready": report.cohort_ready,
                        "drafts_total": report.drafts_total,
                        "drafts_verified": report.drafts_verified,
                        "drafts_pending": report.drafts_pending,
                        "drafts_quarantined": report.drafts_quarantined,
                        "stages_compatibility_only": report.stages_compatibility_only,
                        "stages_requiring_fresh_approval": report.stages_requiring_fresh_approval,
                        "stages_quarantined": report.stages_quarantined,
                        "blockers": list(report.blockers),
                    },
                },
            )
        except Exception as exc:
            raise LegacyMigrationAuditError() from exc

    async def _mark_audit_pending(
        self,
        *,
        snapshot: _InventorySnapshot,
        migration_id: str,
        checkpoint: LegacyMigrationCheckpoint,
        after_draft_id: str | None,
        extra_blockers: tuple[str, ...] = (),
    ) -> LegacyMigrationReport | None:
        """Durably fail closed when a control-plane audit append is unavailable."""

        report = await self._refresh_report(
            snapshot=snapshot,
            migration_id=migration_id,
            dry_run=False,
            migration_status=LegacyMigrationStatus.AUDIT_PENDING.value,
            audit_recorded=False,
            extra_blockers=extra_blockers,
        )
        updated = await self._transition(
            checkpoint=checkpoint,
            after_draft_id=after_draft_id,
            status=LegacyMigrationStatus.AUDIT_PENDING,
            report_digest=report.report_digest,
        )
        return report if updated is not None else None

    def _require_draft_store(self) -> LegacyDraftInventoryPort:
        if isinstance(self._draft_store, LegacyDraftInventoryPort):
            return self._draft_store
        raise LegacyMigrationSourceError()

    def _require_run_store(self) -> LegacyRunInventoryPort:
        if isinstance(self._run_store, LegacyRunInventoryPort):
            return self._run_store
        raise LegacyMigrationSourceError()

    def _require_event_store(self) -> LegacyEventInventoryPort:
        if isinstance(self._event_store, LegacyEventInventoryPort):
            return self._event_store
        raise LegacyMigrationSourceError()

    @staticmethod
    def _validate_draft_page(
        *,
        org_id: str,
        page: Sequence[DraftRecord],
        after: tuple[str, int] | None,
    ) -> None:
        keys = tuple((record.draft_id, record.version) for record in page)
        if (
            tuple(sorted(keys)) != keys
            or len(set(keys)) != len(keys)
            or (after is not None and any(key <= after for key in keys))
            or any(record.org_id != org_id for record in page)
        ):
            raise LegacyMigrationSourceError()

    @staticmethod
    def _valid_event_run(events: Sequence[object]) -> bool:
        sequence = tuple(getattr(event, "sequence_no", None) for event in events)
        return (
            all(isinstance(value, int) and value >= 1 for value in sequence)
            and tuple(sorted(sequence)) == sequence
            and len(set(sequence)) == len(sequence)
        )

    @staticmethod
    def _stage_disposition(status: StagedWriteStatus) -> LegacyStageDisposition:
        if status in {
            StagedWriteStatus.REJECTED,
            StagedWriteStatus.APPLIED,
            StagedWriteStatus.PARTIALLY_APPLIED,
        }:
            return LegacyStageDisposition.COMPATIBILITY_ONLY
        if status in {
            StagedWriteStatus.STAGED,
            StagedWriteStatus.APPROVED,
            StagedWriteStatus.APPLY_PENDING,
        }:
            return LegacyStageDisposition.REQUIRE_FRESH_APPROVAL
        return LegacyStageDisposition.QUARANTINED

    @staticmethod
    def _title(record: DraftRecord) -> str:
        title = record.title.strip()
        return title or f"Draft {record.draft_id}"

    @staticmethod
    def _idempotency_key(*, migration_id: str, draft_id: str, version: int) -> str:
        migration_hash = hashlib.sha256(migration_id.encode("utf-8")).hexdigest()[:16]
        return f"e2-import:{migration_hash}:{draft_id}:{version}"

    @staticmethod
    async def _single_chunk(content: bytes) -> AsyncIterator[bytes]:
        if content:
            yield content

    def _utc_now(self) -> datetime:
        value = self._now()
        if value.tzinfo is None or value.utcoffset() is None:
            raise LegacyMigrationStateError()
        return value.astimezone(timezone.utc)

    @staticmethod
    def _utc_iso(value: str | datetime) -> str:
        parsed = datetime.fromisoformat(value) if isinstance(value, str) else value
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            return ""
        return parsed.astimezone(timezone.utc).isoformat()


__all__ = (
    "LegacyMigrationAuditError",
    "LegacyMigrationAuthorizationError",
    "LegacyMigrationError",
    "LegacyMigrationService",
    "LegacyMigrationSourceError",
)
