"""Atomic in-memory artifact metadata, idempotency, and outbox adapter."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from agent_runtime.artifacts.contracts import (
    ArtifactAppendCommand,
    ArtifactCreateCommand,
    ArtifactGcCandidate,
    ArtifactListPage,
    ArtifactListQuery,
    ArtifactMutationResult,
    ArtifactSoftDeleteCommand,
    ArtifactStoredRecord,
    ArtifactStoredRevision,
)
from agent_runtime.artifacts.errors import (
    ArtifactConflictError,
    ArtifactIdempotencyConflictError,
)
from agent_runtime.persistence.records import OutboxStatus
from runtime_adapters._artifact_repository import (
    ArtifactGcCandidateScope,
    ArtifactRetentionPurgeResult,
    ArtifactRetentionScope,
    artifact_event_outbox_row,
    decode_cursor,
    encode_cursor,
    is_after_cursor,
    parse_datetime,
    record_sort_key,
)
from runtime_adapters.artifact_lifecycle import (
    ArtifactDeletionInventory,
    ArtifactLifecycleEvidence,
    ArtifactLifecycleTombstoneResult,
)
from runtime_adapters.artifact_references import (
    ArtifactReferenceKind,
    InMemoryArtifactReferenceStore,
    artifact_revision_reference_edge,
)
from runtime_adapters.in_memory.artifact_publication import (
    InMemoryArtifactPublicationCoordinator,
)
from runtime_api.schemas.commands import RuntimeArtifactEventCommand

_IdempotencyKey = tuple[str, str, str, str]
_IDEMPOTENCY_ABSENT = object()


class InMemoryArtifactMetadataStore:
    """One-lock transaction boundary matching the durable adapters."""

    def __init__(
        self,
        coordinator: InMemoryArtifactPublicationCoordinator | None = None,
        reference_store: InMemoryArtifactReferenceStore | None = None,
    ) -> None:
        self.coordinator = coordinator or (
            reference_store.coordinator
            if reference_store is not None
            else InMemoryArtifactPublicationCoordinator()
        )
        self._lock = self.coordinator.lock
        self.reference_store = reference_store or InMemoryArtifactReferenceStore(
            self.coordinator
        )
        if self.reference_store.coordinator is not self.coordinator:
            raise ValueError("artifact adapters must share one publication coordinator")
        self._records: dict[tuple[str, str], ArtifactStoredRecord] = {}
        self._revisions: dict[tuple[str, str, int], ArtifactStoredRevision] = {}
        self._idempotency: dict[
            _IdempotencyKey, tuple[str, ArtifactMutationResult | None]
        ] = {}
        self._idempotency_artifact: dict[_IdempotencyKey, str] = {}
        self._outbox: dict[str, dict[str, object]] = {}
        self._lifecycle_evidence: dict[tuple[str, str], ArtifactLifecycleEvidence] = {}

    @property
    def pending_outbox_rows(self) -> tuple[dict[str, object], ...]:
        """Adapter integration seam for the artifact-event dispatcher lane."""

        with self._lock:
            return tuple(
                dict(row)
                for row in self._outbox.values()
                if row["status"] not in {"completed", "dead_letter"}
            )

    async def pending_artifact_events(
        self,
    ) -> tuple[RuntimeArtifactEventCommand, ...]:
        with self._lock:
            return tuple(
                RuntimeArtifactEventCommand.model_validate(row["payload_json"])
                for row in self._outbox.values()
                if row["status"] not in {"completed", "dead_letter"}
            )

    async def acknowledge_artifact_event(
        self,
        *,
        event_id: str,
        status: OutboxStatus,
    ) -> None:
        if status not in {OutboxStatus.COMPLETED, OutboxStatus.DEAD_LETTER}:
            raise ValueError("artifact canonical acknowledgement must be terminal")
        with self._lock:
            row = self._outbox.get(event_id)
            if row is not None:
                row["status"] = status.value

    async def create_artifact(
        self, command: ArtifactCreateCommand
    ) -> ArtifactMutationResult:
        with self._lock:
            replay = self._replay(command.idempotency)
            if replay is not _IDEMPOTENCY_ABSENT:
                if replay is None:
                    raise ArtifactConflictError()
                return replay
            artifact = command.record.artifact
            revision = command.record.current_revision
            key = (artifact.org_id, artifact.artifact_id)
            if key in self._records:
                raise ArtifactConflictError()
            result = ArtifactMutationResult(record=command.record)
            outbox_rows = [
                artifact_event_outbox_row(
                    event,
                    artifact_id=artifact.artifact_id,
                )
                for event in command.ledger_events
            ]
            edge = artifact_revision_reference_edge(
                org_id=artifact.org_id,
                user_id=artifact.user_id,
                artifact_id=artifact.artifact_id,
                revision=1,
                blob_key=revision.blob_key,
                created_at=parse_datetime(revision.revision.created_at),
            )
            self._validate_outbox(outbox_rows)
            self._validate_reference_edge(edge)
            self.coordinator.restore_locked(revision.blob_key)
            self._require_active_locked(revision.blob_key)
            self._insert_outbox(outbox_rows)
            self._records[key] = command.record
            self._revisions[(artifact.org_id, artifact.artifact_id, 1)] = revision
            self.reference_store.put_locked(edge)
            self.coordinator.cancel_candidate_locked(revision.blob_key)
            self._bind(
                command.idempotency,
                result,
                artifact_id=artifact.artifact_id,
            )
            return result

    async def append_revision(
        self, command: ArtifactAppendCommand
    ) -> ArtifactMutationResult:
        with self._lock:
            replay = self._replay(command.idempotency)
            if replay is not _IDEMPOTENCY_ABSENT:
                if replay is None:
                    raise ArtifactConflictError()
                return replay
            key = (command.scope.org_id, command.artifact_id)
            current = self._records.get(key)
            if (
                current is None
                or current.artifact.user_id != command.scope.user_id
                or current.artifact.deleted_at is not None
                or current.artifact.current_revision != command.expected_revision
            ):
                raise ArtifactConflictError()
            artifact = current.artifact.model_copy(
                update={
                    "current_revision": command.revision.revision.revision,
                    "updated_at": command.revision.revision.created_at,
                }
            )
            record = current.model_copy(
                update={
                    "artifact": artifact,
                    "current_revision": command.revision,
                }
            )
            result = ArtifactMutationResult(record=record)
            outbox_row = artifact_event_outbox_row(
                command.ledger_event,
                artifact_id=command.artifact_id,
            )
            edge = artifact_revision_reference_edge(
                org_id=command.scope.org_id,
                user_id=command.scope.user_id,
                artifact_id=command.artifact_id,
                revision=command.revision.revision.revision,
                blob_key=command.revision.blob_key,
                created_at=parse_datetime(command.revision.revision.created_at),
            )
            self._validate_outbox([outbox_row])
            self._validate_reference_edge(edge)
            self.coordinator.restore_locked(command.revision.blob_key)
            self._require_active_locked(command.revision.blob_key)
            self._insert_outbox([outbox_row])
            self._records[key] = record
            self._revisions[
                (
                    command.scope.org_id,
                    command.artifact_id,
                    command.revision.revision.revision,
                )
            ] = command.revision
            self.reference_store.put_locked(edge)
            self.coordinator.cancel_candidate_locked(command.revision.blob_key)
            self._bind(
                command.idempotency,
                result,
                artifact_id=command.artifact_id,
            )
            return result

    async def get_artifact(
        self,
        *,
        org_id: str,
        user_id: str,
        artifact_id: str,
        include_deleted: bool = False,
    ) -> ArtifactStoredRecord | None:
        with self._lock:
            record = self._records.get((org_id, artifact_id))
            if (
                record is None
                or record.artifact.user_id != user_id
                or (record.artifact.deleted_at is not None and not include_deleted)
            ):
                return None
            return record

    async def get_artifact_for_org(
        self,
        *,
        org_id: str,
        artifact_id: str,
        include_deleted: bool = False,
    ) -> ArtifactStoredRecord | None:
        """Return a same-org record for internal authorization classification."""

        with self._lock:
            record = self._records.get((org_id, artifact_id))
            if record is None or (
                record.artifact.deleted_at is not None and not include_deleted
            ):
                return None
            return record

    async def get_revision(
        self,
        *,
        org_id: str,
        user_id: str,
        artifact_id: str,
        revision: int,
        include_deleted: bool = False,
    ) -> ArtifactStoredRevision | None:
        record = await self.get_artifact(
            org_id=org_id,
            user_id=user_id,
            artifact_id=artifact_id,
            include_deleted=include_deleted,
        )
        if record is None:
            return None
        with self._lock:
            return self._revisions.get((org_id, artifact_id, revision))

    async def list_artifacts(self, query: ArtifactListQuery) -> ArtifactListPage:
        with self._lock:
            records = [
                record
                for (org_id, _), record in self._records.items()
                if org_id == query.org_id
                and record.artifact.user_id == query.user_id
                and record.artifact.run_id == query.run_id
                and (query.kind is None or record.artifact.kind == query.kind)
                and (query.include_deleted or record.artifact.deleted_at is None)
            ]
        records.sort(key=record_sort_key)
        if query.cursor is not None:
            cursor = decode_cursor(query.cursor)
            records = [record for record in records if is_after_cursor(record, cursor)]
        page = records[: query.limit]
        next_cursor = (
            encode_cursor(page[-1]) if len(records) > query.limit and page else None
        )
        return ArtifactListPage(artifacts=tuple(page), next_cursor=next_cursor)

    async def soft_delete(
        self, command: ArtifactSoftDeleteCommand
    ) -> ArtifactStoredRecord | None:
        with self._lock:
            replay = self._replay(command.idempotency)
            if replay is not _IDEMPOTENCY_ABSENT:
                return replay.record if replay is not None else None
            key = (command.org_id, command.artifact_id)
            current = self._records.get(key)
            if current is None or current.artifact.user_id != command.user_id:
                self._bind(
                    command.idempotency,
                    None,
                    artifact_id=command.artifact_id,
                )
                return None
            if current.artifact.deleted_at is not None:
                self._bind(
                    command.idempotency,
                    None,
                    artifact_id=command.artifact_id,
                )
                return None
            artifact = current.artifact.model_copy(
                update={
                    "deleted_at": command.deleted_at.isoformat(),
                    "updated_at": command.deleted_at.isoformat(),
                }
            )
            current = current.model_copy(update={"artifact": artifact})
            self._records[key] = current
            self._bind(
                command.idempotency,
                ArtifactMutationResult(record=current),
                artifact_id=command.artifact_id,
            )
            return current

    async def list_unreferenced_content(
        self,
        *,
        org_id: str,
        older_than: datetime,
        limit: int,
    ) -> Sequence[ArtifactGcCandidate]:
        with self._lock:
            candidates = {
                blob_key: state.candidate_since
                for blob_key, state in self.coordinator.candidates.items()
                if state.provenance_org_id == org_id
                and state.candidate_since < older_than
            }
        values = [
            ArtifactGcCandidate(blob_key=key, unreferenced_since=timestamp)
            for key, timestamp in candidates.items()
        ]
        values.sort(key=lambda item: (item.unreferenced_since, item.blob_key))
        return tuple(values[:limit])

    def has_revision_reference_locked(self, *, blob_key: str) -> bool:
        """Include every immutable revision, including tombstoned artifacts."""

        return any(
            revision.blob_key == blob_key for revision in self._revisions.values()
        )

    async def has_reference(self, *, org_id: str, blob_key: str) -> bool:
        with self._lock:
            return self.has_revision_reference_locked(blob_key=blob_key)

    async def purge_tombstones(
        self,
        *,
        scope: ArtifactRetentionScope,
        deleted_before: datetime,
        limit: int,
    ) -> ArtifactRetentionPurgeResult:
        """Atomically purge scoped tombstones and record digest eligibility."""

        with self._lock:
            victims = [
                (key, record)
                for key, record in self._records.items()
                if key[0] == scope.org_id
                and record.artifact.deleted_at is not None
                and parse_datetime(record.artifact.deleted_at) < deleted_before
                and (scope.user_id is None or record.artifact.user_id == scope.user_id)
                and (
                    scope.conversation_id is None
                    or record.artifact.conversation_id == scope.conversation_id
                )
                and not scope.protects_conversation(record.artifact.conversation_id)
            ]
            victims.sort(
                key=lambda item: (
                    parse_datetime(item[1].artifact.deleted_at or ""),
                    item[0][1],
                )
            )
            victims = victims[:limit]
            artifact_ids = {key[1] for key, _ in victims}
            digest_since: dict[str, datetime] = {}
            digest_scopes: dict[str, set[ArtifactGcCandidateScope]] = {}
            for (
                revision_org,
                revision_artifact,
                _,
            ), revision in self._revisions.items():
                if (
                    revision_org != scope.org_id
                    or revision_artifact not in artifact_ids
                ):
                    continue
                deleted_at = parse_datetime(
                    self._records[(revision_org, revision_artifact)].artifact.deleted_at
                    or ""
                )
                digest_since[revision.blob_key] = min(
                    digest_since.get(revision.blob_key, deleted_at),
                    deleted_at,
                )
                record = self._records[(revision_org, revision_artifact)]
                digest_scopes.setdefault(revision.blob_key, set()).add(
                    ArtifactGcCandidateScope(
                        org_id=record.artifact.org_id,
                        user_id=record.artifact.user_id,
                        conversation_id=record.artifact.conversation_id,
                    )
                )
            for key, _ in victims:
                self._records.pop(key, None)
            self._revisions = {
                key: revision
                for key, revision in self._revisions.items()
                if not (key[0] == scope.org_id and key[1] in artifact_ids)
            }
            self.reference_store.remove_artifact_edges_locked(
                org_id=scope.org_id,
                artifact_ids=artifact_ids,
            )
            idempotency_keys = {
                key
                for key, artifact_id in self._idempotency_artifact.items()
                if artifact_id in artifact_ids and key[0] == scope.org_id
            }
            for key in idempotency_keys:
                self._idempotency.pop(key, None)
                self._idempotency_artifact.pop(key, None)
            for blob_key, candidate_since in digest_since.items():
                self.coordinator.record_candidate_locked(
                    blob_key=blob_key,
                    provenance_org_id=scope.org_id,
                    candidate_since=candidate_since,
                    scopes=tuple(digest_scopes.get(blob_key, ())),
                )
            candidates = tuple(
                ArtifactGcCandidate(
                    blob_key=blob_key,
                    unreferenced_since=candidate_since,
                )
                for blob_key, candidate_since in sorted(
                    digest_since.items(), key=lambda item: (item[1], item[0])
                )
            )
            return ArtifactRetentionPurgeResult(
                purged_artifact_ids=tuple(sorted(artifact_ids)),
                eligible_candidates=candidates,
            )

    async def deletion_inventory(
        self,
        *,
        scope: ArtifactRetentionScope,
    ) -> ArtifactDeletionInventory:
        with self._lock:
            return self._deletion_inventory_locked(scope)

    async def tombstone_for_lifecycle(
        self,
        *,
        scope: ArtifactRetentionScope,
        deleted_at: datetime,
        evidence_id: str,
        reason: str,
    ) -> ArtifactLifecycleTombstoneResult:
        with self._lock:
            evidence_key = (scope.org_id, evidence_id)
            evidence = self._lifecycle_evidence.get(evidence_key)
            if evidence is not None:
                return ArtifactLifecycleTombstoneResult(
                    evidence=evidence,
                    inventory_after=self._deletion_inventory_locked(scope),
                )
            inventory_before = self._deletion_inventory_locked(scope)
            tombstoned: list[str] = []
            for key, record in tuple(self._records.items()):
                if not self._record_matches_scope(record, scope):
                    continue
                if record.artifact.deleted_at is not None:
                    continue
                artifact = record.artifact.model_copy(
                    update={
                        "deleted_at": deleted_at.isoformat(),
                        "updated_at": deleted_at.isoformat(),
                    }
                )
                self._records[key] = record.model_copy(update={"artifact": artifact})
                tombstoned.append(record.artifact.artifact_id)
            evidence = ArtifactLifecycleEvidence(
                evidence_id=evidence_id,
                scope=scope,
                reason=reason,
                created_at=deleted_at,
                tombstoned_artifact_ids=tuple(sorted(tombstoned)),
                inventory_before=inventory_before,
            )
            self._lifecycle_evidence[evidence_key] = evidence
            return ArtifactLifecycleTombstoneResult(
                evidence=evidence,
                inventory_after=self._deletion_inventory_locked(scope),
            )

    async def get_lifecycle_evidence(
        self,
        *,
        org_id: str,
        evidence_id: str,
    ) -> ArtifactLifecycleEvidence | None:
        with self._lock:
            return self._lifecycle_evidence.get((org_id, evidence_id))

    async def list_lifecycle_org_ids(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(
                sorted(
                    {org_id for org_id, _artifact_id in self._records}
                    | {org_id for org_id, _evidence_id in self._lifecycle_evidence}
                    | {
                        candidate.provenance_org_id
                        for candidate in self.coordinator.candidates.values()
                        if candidate.provenance_org_id is not None
                    }
                )
            )

    def _deletion_inventory_locked(
        self,
        scope: ArtifactRetentionScope,
    ) -> ArtifactDeletionInventory:
        records = [
            record
            for record in self._records.values()
            if self._record_matches_scope(record, scope)
        ]
        artifact_ids = {record.artifact.artifact_id for record in records}
        revisions = [
            revision
            for (org_id, artifact_id, _), revision in self._revisions.items()
            if org_id == scope.org_id and artifact_id in artifact_ids
        ]
        idempotency_keys = {
            key
            for key, artifact_id in self._idempotency_artifact.items()
            if key[0] == scope.org_id and artifact_id in artifact_ids
        }
        edges = [
            edge
            for edge in self.reference_store.inventory_edges_locked()
            if edge.org_id == scope.org_id
            and (
                (
                    edge.reference_kind is ArtifactReferenceKind.ARTIFACT
                    and edge.reference_id.split(":", 1)[0] in artifact_ids
                )
                or (
                    scope.conversation_id is None
                    and (scope.user_id is None or edge.user_id == scope.user_id)
                )
            )
        ]
        blob_keys = {revision.blob_key for revision in revisions}
        candidate_keys = {
            blob_key
            for blob_key, candidate in self.coordinator.candidates.items()
            if candidate.provenance_org_id == scope.org_id
            and (scope.user_id is None or blob_key in blob_keys)
        }
        quarantine_keys = set(self.coordinator.quarantine) & (
            candidate_keys | blob_keys
        )
        return ArtifactDeletionInventory(
            artifact_rows=len(records),
            revision_rows=len(revisions),
            idempotency_rows=len(idempotency_keys),
            reference_edge_rows=len(edges),
            gc_candidate_rows=len(candidate_keys),
            quarantined_digest_rows=len(quarantine_keys),
            reaping_digest_rows=0,
            artifact_ids=tuple(sorted(artifact_ids)),
            blob_keys=tuple(sorted(blob_keys | candidate_keys | quarantine_keys)),
        )

    @staticmethod
    def _record_matches_scope(
        record: ArtifactStoredRecord,
        scope: ArtifactRetentionScope,
    ) -> bool:
        artifact = record.artifact
        return (
            artifact.org_id == scope.org_id
            and (scope.user_id is None or artifact.user_id == scope.user_id)
            and (
                scope.conversation_id is None
                or artifact.conversation_id == scope.conversation_id
            )
            and not scope.protects_conversation(artifact.conversation_id)
        )

    def mark_outbox_terminal(self, command_id: str, status: str) -> None:
        with self._lock:
            row = self._outbox.get(command_id)
            if row is not None:
                row["status"] = status

    def _require_active_locked(self, blob_key: str) -> None:
        require = getattr(self.coordinator, "require_active_locked", None)
        if require is not None:
            require(blob_key)
            return
        if blob_key not in self.coordinator.blobs:
            raise FileNotFoundError("artifact blob is unavailable")

    def _replay(self, binding) -> ArtifactMutationResult | None | object:
        key = (binding.org_id, binding.user_id, binding.route, binding.key)
        existing = self._idempotency.get(key)
        if existing is None:
            return _IDEMPOTENCY_ABSENT
        request_digest, result = existing
        if request_digest != binding.request_digest:
            raise ArtifactIdempotencyConflictError()
        if result is None:
            return None
        return result.model_copy(update={"replayed": True})

    def _bind(
        self,
        binding,
        result: ArtifactMutationResult | None,
        *,
        artifact_id: str,
    ) -> None:
        key = (binding.org_id, binding.user_id, binding.route, binding.key)
        self._idempotency[key] = (binding.request_digest, result)
        self._idempotency_artifact[key] = artifact_id

    def _insert_outbox(self, rows: list[dict[str, object]]) -> None:
        self._validate_outbox(rows)
        for row in rows:
            self._outbox[str(row["id"])] = row

    def _validate_outbox(self, rows: list[dict[str, object]]) -> None:
        for row in rows:
            event_id = str(row["id"])
            existing = self._outbox.get(event_id)
            if existing is not None and existing != row:
                raise ArtifactConflictError()

    def _validate_reference_edge(self, edge) -> None:
        edges = getattr(self.reference_store, "_edges", None)
        if edges is None:
            return
        existing = edges.get((edge.org_id, edge.edge_id))
        if existing is not None and existing != edge:
            raise ArtifactConflictError()


__all__ = ("InMemoryArtifactMetadataStore",)
