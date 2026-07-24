"""Append-only file artifact metadata with one-record transaction boundaries."""

from __future__ import annotations

from typing import Any
from datetime import datetime

from agent_runtime.artifacts.contracts import (
    ArtifactAppendCommand,
    ArtifactCreateCommand,
    ArtifactGcCandidate,
    ArtifactIdempotencyBinding,
    ArtifactMutationResult,
    ArtifactSoftDeleteCommand,
    ArtifactStoredRecord,
    ArtifactStoredRevision,
)
from agent_runtime.persistence.records import OutboxStatus
from runtime_adapters._artifact_repository import (
    ArtifactRetentionPurgeResult,
    ArtifactRetentionScope,
    artifact_event_outbox_row,
    parse_datetime,
)
from runtime_adapters.artifact_lifecycle import (
    ArtifactDeletionInventory,
    ArtifactLifecycleEvidence,
    ArtifactLifecycleTombstoneResult,
    ORPHAN_PUBLICATION_RECOVERY_ORG_ID,
)
from runtime_adapters.artifact_references import (
    FileArtifactReferenceStore,
    artifact_revision_reference_edge,
)
from runtime_adapters.file._jsonl import JsonlIo
from runtime_adapters.file._paths import FileStoreLayout
from runtime_adapters.file.artifact_publication import (
    FileArtifactPublicationCoordinator,
)
from runtime_adapters.in_memory.artifact_metadata_store import (
    InMemoryArtifactMetadataStore,
)
from runtime_api.schemas.commands import RuntimeArtifactEventCommand


class FileArtifactMetadataStore(InMemoryArtifactMetadataStore):
    """Durable single-ledger variant of the in-memory metadata state machine."""

    _SCHEMA_VERSION = 1
    _TABLE = "artifact_repository"

    def __init__(
        self,
        layout: FileStoreLayout,
        coordinator: FileArtifactPublicationCoordinator | None = None,
        reference_store: FileArtifactReferenceStore | None = None,
    ) -> None:
        self._layout = layout
        self.coordinator = coordinator or (
            reference_store.coordinator
            if reference_store is not None
            else FileArtifactPublicationCoordinator(layout)
        )
        self.reference_store = reference_store or FileArtifactReferenceStore(
            layout, self.coordinator
        )
        super().__init__(self.coordinator, self.reference_store)  # type: ignore[arg-type]
        self._path = layout.state_path(self._TABLE)
        with self._lock:
            self._refresh_locked()

    @property
    def pending_outbox_rows(self) -> tuple[dict[str, object], ...]:
        with self._lock:
            self._refresh_locked()
            return super().pending_outbox_rows

    async def pending_artifact_events(
        self,
    ) -> tuple[RuntimeArtifactEventCommand, ...]:
        with self._lock:
            self._refresh_locked()
            return await super().pending_artifact_events()

    async def acknowledge_artifact_event(
        self,
        *,
        event_id: str,
        status: OutboxStatus,
    ) -> None:
        if status not in {OutboxStatus.COMPLETED, OutboxStatus.DEAD_LETTER}:
            raise ValueError("artifact canonical acknowledgement must be terminal")
        with self._lock:
            self._refresh_locked()
            row = self._outbox.get(event_id)
            if row is None or row["status"] == status.value:
                return
            JsonlIo.append_line(
                self._path,
                {
                    "schema_version": self._SCHEMA_VERSION,
                    "op": "outbox_status",
                    "event_id": event_id,
                    "status": status.value,
                },
            )
            row["status"] = status.value

    async def create_artifact(
        self, command: ArtifactCreateCommand
    ) -> ArtifactMutationResult:
        with self._lock:
            self._refresh_locked()
            snapshot = self._snapshot()
            was_quarantined = self.coordinator.quarantine_path(
                command.record.current_revision.blob_key
            ).exists()
            candidate_before = self.coordinator.candidates.get(
                command.record.current_revision.blob_key
            )
            try:
                result = await super().create_artifact(command)
                if result.replayed:
                    return result
                row = self._transaction_row(
                    op="create",
                    binding=command.idempotency,
                    result=result,
                    revision=command.record.current_revision,
                    reference_edge=artifact_revision_reference_edge(
                        org_id=command.record.artifact.org_id,
                        user_id=command.record.artifact.user_id,
                        artifact_id=command.record.artifact.artifact_id,
                        revision=1,
                        blob_key=command.record.current_revision.blob_key,
                        created_at=parse_datetime(
                            command.record.current_revision.revision.created_at
                        ),
                    ),
                    outbox=[
                        artifact_event_outbox_row(
                            event,
                            artifact_id=command.record.artifact.artifact_id,
                        )
                        for event in command.ledger_events
                    ],
                )
                JsonlIo.append_line(self._path, row)
                return result
            except BaseException:
                self._restore(snapshot)
                if was_quarantined:
                    self.coordinator.rollback_restoration_locked(
                        command.record.current_revision.blob_key
                    )
                if candidate_before is not None:
                    self.coordinator.record_candidate_locked(
                        blob_key=command.record.current_revision.blob_key,
                        provenance_org_id=candidate_before.provenance_org_id,
                        candidate_since=candidate_before.candidate_since,
                    )
                raise

    async def append_revision(
        self, command: ArtifactAppendCommand
    ) -> ArtifactMutationResult:
        with self._lock:
            self._refresh_locked()
            snapshot = self._snapshot()
            was_quarantined = self.coordinator.quarantine_path(
                command.revision.blob_key
            ).exists()
            candidate_before = self.coordinator.candidates.get(
                command.revision.blob_key
            )
            try:
                result = await super().append_revision(command)
                if result.replayed:
                    return result
                row = self._transaction_row(
                    op="append",
                    binding=command.idempotency,
                    result=result,
                    revision=command.revision,
                    reference_edge=artifact_revision_reference_edge(
                        org_id=command.scope.org_id,
                        user_id=command.scope.user_id,
                        artifact_id=command.artifact_id,
                        revision=command.revision.revision.revision,
                        blob_key=command.revision.blob_key,
                        created_at=parse_datetime(command.revision.revision.created_at),
                    ),
                    outbox=[
                        artifact_event_outbox_row(
                            command.ledger_event,
                            artifact_id=command.artifact_id,
                        )
                    ],
                )
                JsonlIo.append_line(self._path, row)
                return result
            except BaseException:
                self._restore(snapshot)
                if was_quarantined:
                    self.coordinator.rollback_restoration_locked(
                        command.revision.blob_key
                    )
                if candidate_before is not None:
                    self.coordinator.record_candidate_locked(
                        blob_key=command.revision.blob_key,
                        provenance_org_id=candidate_before.provenance_org_id,
                        candidate_since=candidate_before.candidate_since,
                    )
                raise

    async def soft_delete(
        self, command: ArtifactSoftDeleteCommand
    ) -> ArtifactStoredRecord | None:
        with self._lock:
            self._refresh_locked()
            snapshot = self._snapshot()
            try:
                before = self._idempotency.get(
                    (
                        command.idempotency.org_id,
                        command.idempotency.user_id,
                        command.idempotency.route,
                        command.idempotency.key,
                    )
                )
                record = await super().soft_delete(command)
                after = self._idempotency.get(
                    (
                        command.idempotency.org_id,
                        command.idempotency.user_id,
                        command.idempotency.route,
                        command.idempotency.key,
                    )
                )
                if before == after:
                    return record
                result = (
                    ArtifactMutationResult(record=record)
                    if record is not None
                    else None
                )
                JsonlIo.append_line(
                    self._path,
                    self._transaction_row(
                        op="delete",
                        binding=command.idempotency,
                        result=result,
                        revision=None,
                        reference_edge=None,
                        outbox=[],
                        target_artifact_id=command.artifact_id,
                    ),
                )
                return record
            except BaseException:
                self._restore(snapshot)
                raise

    def _load(self) -> None:
        for row in JsonlIo.iter_lines(self._path):
            if row.get("schema_version") != self._SCHEMA_VERSION:
                raise ValueError("unsupported artifact repository ledger version")
            if row.get("op") == "purge":
                artifact_ids = set(row.get("artifact_ids", []))
                org_id = str(row["org_id"])
                self._records = {
                    key: record
                    for key, record in self._records.items()
                    if not (key[0] == org_id and key[1] in artifact_ids)
                }
                self._revisions = {
                    key: revision
                    for key, revision in self._revisions.items()
                    if not (key[0] == org_id and key[1] in artifact_ids)
                }
                remove_keys = {
                    key
                    for key, artifact_id in self._idempotency_artifact.items()
                    if key[0] == org_id and artifact_id in artifact_ids
                }
                for key in remove_keys:
                    self._idempotency.pop(key, None)
                    self._idempotency_artifact.pop(key, None)
                for candidate_json in row.get("candidates", []):
                    blob_key = str(candidate_json["blob_key"])
                    # Repair a crash between the metadata purge row and the
                    # GC-state append only while some physical state remains.
                    # A completed reap leaves no bytes, so replaying this
                    # historical purge must not resurrect its candidate.
                    if not (
                        self.coordinator.layout.object_path(blob_key).exists()
                        or self.coordinator.quarantine_path(blob_key).exists()
                        or self.coordinator.reaping_path(blob_key).exists()
                    ):
                        continue
                    candidate_since = datetime.fromisoformat(
                        str(candidate_json["unreferenced_since"]).replace("Z", "+00:00")
                    )
                    self.coordinator.record_candidate_locked(
                        blob_key=blob_key,
                        provenance_org_id=org_id,
                        candidate_since=candidate_since,
                    )
                continue
            if row.get("op") == "outbox_status":
                event_id = str(row["event_id"])
                if event_id in self._outbox:
                    self._outbox[event_id]["status"] = str(row["status"])
                continue
            if row.get("op") == "lifecycle_tombstone":
                deleted_at = str(row["deleted_at"])
                for artifact_id in row.get("artifact_ids", []):
                    key = (str(row["org_id"]), str(artifact_id))
                    record = self._records.get(key)
                    if record is None:
                        continue
                    artifact = record.artifact.model_copy(
                        update={
                            "deleted_at": deleted_at,
                            "updated_at": deleted_at,
                        }
                    )
                    self._records[key] = record.model_copy(
                        update={"artifact": artifact}
                    )
                evidence = self._evidence_from_json(row["evidence"])
                self._lifecycle_evidence[
                    (evidence.scope.org_id, evidence.evidence_id)
                ] = evidence
                continue
            binding = ArtifactIdempotencyBinding.model_validate(row["idempotency"])
            result_json = row.get("result")
            result = (
                ArtifactMutationResult.model_validate(result_json)
                if result_json is not None
                else None
            )
            key = (binding.org_id, binding.user_id, binding.route, binding.key)
            self._idempotency[key] = (binding.request_digest, result)
            artifact_id = row.get("artifact_id")
            if not isinstance(artifact_id, str):
                artifact_id = (
                    result.record.artifact.artifact_id
                    if result is not None
                    else row.get("target_artifact_id")
                )
            if isinstance(artifact_id, str):
                self._idempotency_artifact[key] = artifact_id
            if result is not None:
                record = result.record
                artifact_key = (
                    record.artifact.org_id,
                    record.artifact.artifact_id,
                )
                self._records[artifact_key] = record
            revision_json = row.get("revision")
            if revision_json is not None:
                revision = ArtifactStoredRevision.model_validate(revision_json)
                artifact = revision.revision
                record = result.record if result is not None else None
                if record is None:
                    raise ValueError("artifact revision ledger row lacks result")
                self._revisions[
                    (record.artifact.org_id, artifact.artifact_id, artifact.revision)
                ] = revision
            for outbox_json in row.get("outbox", []):
                event_id = str(outbox_json["id"])
                outbox_row = dict(outbox_json)
                existing = self._outbox.get(event_id)
                if existing is not None and existing != outbox_row:
                    raise ValueError("artifact outbox event id conflicts on reload")
                self._outbox[event_id] = outbox_row

    def _transaction_row(
        self,
        *,
        op: str,
        binding: ArtifactIdempotencyBinding,
        result: ArtifactMutationResult | None,
        revision: ArtifactStoredRevision | None,
        reference_edge,
        outbox: list[dict[str, Any]],
        target_artifact_id: str | None = None,
    ) -> dict[str, Any]:
        return {
            "schema_version": self._SCHEMA_VERSION,
            "op": op,
            "idempotency": binding.model_dump(mode="json"),
            "artifact_id": (
                result.record.artifact.artifact_id
                if result is not None
                else target_artifact_id
            ),
            "result": result.model_dump(mode="json") if result is not None else None,
            "revision": (
                revision.model_dump(mode="json") if revision is not None else None
            ),
            "reference_edge": (
                reference_edge.model_dump(mode="json")
                if reference_edge is not None
                else None
            ),
            "outbox": outbox,
        }

    def _snapshot(self):
        return (
            dict(self._records),
            dict(self._revisions),
            dict(self._idempotency),
            dict(self._idempotency_artifact),
            {key: dict(value) for key, value in self._outbox.items()},
            dict(self._lifecycle_evidence),
        )

    def _restore(self, snapshot) -> None:
        (
            self._records,
            self._revisions,
            self._idempotency,
            self._idempotency_artifact,
            self._outbox,
            self._lifecycle_evidence,
        ) = snapshot

    def _refresh_locked(self) -> None:
        """Fold durable JSONL again while holding the cross-process flock."""

        self._records.clear()
        self._revisions.clear()
        self._idempotency.clear()
        self._idempotency_artifact.clear()
        self._outbox.clear()
        self._lifecycle_evidence.clear()
        self._load()

    async def purge_tombstones(
        self,
        *,
        scope: ArtifactRetentionScope,
        deleted_before: datetime,
        limit: int,
    ) -> ArtifactRetentionPurgeResult:
        with self._lock:
            self._refresh_locked()
            snapshot = self._snapshot()
            try:
                result = await super().purge_tombstones(
                    scope=scope,
                    deleted_before=deleted_before,
                    limit=limit,
                )
                if not result.purged_artifact_ids:
                    return result
                JsonlIo.append_line(
                    self._path,
                    {
                        "schema_version": self._SCHEMA_VERSION,
                        "op": "purge",
                        "org_id": scope.org_id,
                        "user_id": scope.user_id,
                        "conversation_id": scope.conversation_id,
                        "artifact_ids": list(result.purged_artifact_ids),
                        "candidates": [
                            candidate.model_dump(mode="json")
                            for candidate in result.eligible_candidates
                        ],
                    },
                )
                for candidate in result.eligible_candidates:
                    self.coordinator.candidates.pop(candidate.blob_key, None)
                    self.coordinator.record_candidate_locked(
                        blob_key=candidate.blob_key,
                        provenance_org_id=scope.org_id,
                        candidate_since=candidate.unreferenced_since,
                    )
                return result
            except BaseException:
                self._restore(snapshot)
                raise

    async def get_artifact(
        self,
        *,
        org_id: str,
        user_id: str,
        artifact_id: str,
        include_deleted: bool = False,
    ) -> ArtifactStoredRecord | None:
        with self._lock:
            self._refresh_locked()
            return await super().get_artifact(
                org_id=org_id,
                user_id=user_id,
                artifact_id=artifact_id,
                include_deleted=include_deleted,
            )

    async def get_revision(
        self,
        *,
        org_id: str,
        user_id: str,
        artifact_id: str,
        revision: int,
        include_deleted: bool = False,
    ) -> ArtifactStoredRevision | None:
        with self._lock:
            self._refresh_locked()
            return await super().get_revision(
                org_id=org_id,
                user_id=user_id,
                artifact_id=artifact_id,
                revision=revision,
                include_deleted=include_deleted,
            )

    async def list_artifacts(self, query):
        with self._lock:
            self._refresh_locked()
            return await super().list_artifacts(query)

    async def list_unreferenced_content(
        self,
        *,
        org_id: str,
        older_than: datetime,
        limit: int,
    ):
        with self._lock:
            self._refresh_locked()
            candidates = [
                ArtifactGcCandidate(
                    blob_key=blob_key,
                    unreferenced_since=state.candidate_since,
                )
                for blob_key, state in self.coordinator.pending_candidates_locked()
                if state.candidate_since < older_than
                and (
                    state.provenance_org_id == org_id
                    or (
                        org_id == ORPHAN_PUBLICATION_RECOVERY_ORG_ID
                        and state.provenance_org_id is None
                    )
                )
            ]
            candidates.sort(
                key=lambda candidate: (
                    candidate.unreferenced_since,
                    candidate.blob_key,
                )
            )
            return tuple(candidates[:limit])

    async def deletion_inventory(
        self,
        *,
        scope: ArtifactRetentionScope,
    ) -> ArtifactDeletionInventory:
        with self._lock:
            self._refresh_locked()
            return await super().deletion_inventory(scope=scope)

    async def tombstone_for_lifecycle(
        self,
        *,
        scope: ArtifactRetentionScope,
        deleted_at: datetime,
        evidence_id: str,
        reason: str,
    ) -> ArtifactLifecycleTombstoneResult:
        with self._lock:
            self._refresh_locked()
            snapshot = self._snapshot()
            existed = (scope.org_id, evidence_id) in self._lifecycle_evidence
            try:
                result = await super().tombstone_for_lifecycle(
                    scope=scope,
                    deleted_at=deleted_at,
                    evidence_id=evidence_id,
                    reason=reason,
                )
                if existed:
                    return result
                JsonlIo.append_line(
                    self._path,
                    {
                        "schema_version": self._SCHEMA_VERSION,
                        "op": "lifecycle_tombstone",
                        "org_id": scope.org_id,
                        "deleted_at": deleted_at.isoformat(),
                        "artifact_ids": list(result.evidence.tombstoned_artifact_ids),
                        "evidence": self._evidence_to_json(result.evidence),
                    },
                )
                return result
            except BaseException:
                self._restore(snapshot)
                raise

    async def get_lifecycle_evidence(
        self,
        *,
        org_id: str,
        evidence_id: str,
    ) -> ArtifactLifecycleEvidence | None:
        with self._lock:
            self._refresh_locked()
            return await super().get_lifecycle_evidence(
                org_id=org_id,
                evidence_id=evidence_id,
            )

    async def list_lifecycle_org_ids(self) -> tuple[str, ...]:
        with self._lock:
            self._refresh_locked()
            return await super().list_lifecycle_org_ids()

    def mark_outbox_terminal(self, command_id: str, status: str) -> None:
        with self._lock:
            self._refresh_locked()
            row = self._outbox.get(command_id)
            if row is None or row["status"] == status:
                return
            JsonlIo.append_line(
                self._path,
                {
                    "schema_version": self._SCHEMA_VERSION,
                    "op": "outbox_status",
                    "event_id": command_id,
                    "status": status,
                },
            )
            row["status"] = status

    @staticmethod
    def _evidence_to_json(evidence: ArtifactLifecycleEvidence) -> dict[str, Any]:
        inventory = evidence.inventory_before
        return {
            "evidence_id": evidence.evidence_id,
            "scope": {
                "org_id": evidence.scope.org_id,
                "user_id": evidence.scope.user_id,
                "conversation_id": evidence.scope.conversation_id,
                "protected_conversation_ids": list(
                    evidence.scope.protected_conversation_ids
                ),
            },
            "reason": evidence.reason,
            "created_at": evidence.created_at.isoformat(),
            "tombstoned_artifact_ids": list(evidence.tombstoned_artifact_ids),
            "inventory_before": {
                "artifact_rows": inventory.artifact_rows,
                "revision_rows": inventory.revision_rows,
                "idempotency_rows": inventory.idempotency_rows,
                "reference_edge_rows": inventory.reference_edge_rows,
                "gc_candidate_rows": inventory.gc_candidate_rows,
                "quarantined_digest_rows": inventory.quarantined_digest_rows,
                "reaping_digest_rows": inventory.reaping_digest_rows,
                "artifact_ids": list(inventory.artifact_ids),
                "blob_keys": list(inventory.blob_keys),
            },
        }

    @staticmethod
    def _evidence_from_json(value: object) -> ArtifactLifecycleEvidence:
        if not isinstance(value, dict):
            raise ValueError("invalid artifact lifecycle evidence")
        scope_json = value["scope"]
        inventory_json = value["inventory_before"]
        if not isinstance(scope_json, dict) or not isinstance(inventory_json, dict):
            raise ValueError("invalid artifact lifecycle evidence")
        return ArtifactLifecycleEvidence(
            evidence_id=str(value["evidence_id"]),
            scope=ArtifactRetentionScope(
                org_id=str(scope_json["org_id"]),
                user_id=(
                    str(scope_json["user_id"])
                    if scope_json.get("user_id") is not None
                    else None
                ),
                conversation_id=(
                    str(scope_json["conversation_id"])
                    if scope_json.get("conversation_id") is not None
                    else None
                ),
                protected_conversation_ids=tuple(
                    str(item)
                    for item in scope_json.get("protected_conversation_ids", ())
                ),
            ),
            reason=str(value["reason"]),
            created_at=parse_datetime(str(value["created_at"])),
            tombstoned_artifact_ids=tuple(
                str(item) for item in value["tombstoned_artifact_ids"]
            ),
            inventory_before=ArtifactDeletionInventory(
                artifact_rows=int(inventory_json["artifact_rows"]),
                revision_rows=int(inventory_json["revision_rows"]),
                idempotency_rows=int(inventory_json["idempotency_rows"]),
                reference_edge_rows=int(inventory_json["reference_edge_rows"]),
                gc_candidate_rows=int(inventory_json["gc_candidate_rows"]),
                quarantined_digest_rows=int(inventory_json["quarantined_digest_rows"]),
                reaping_digest_rows=int(inventory_json["reaping_digest_rows"]),
                artifact_ids=tuple(
                    str(item) for item in inventory_json["artifact_ids"]
                ),
                blob_keys=tuple(str(item) for item in inventory_json["blob_keys"]),
            ),
        )


__all__ = ("FileArtifactMetadataStore",)
