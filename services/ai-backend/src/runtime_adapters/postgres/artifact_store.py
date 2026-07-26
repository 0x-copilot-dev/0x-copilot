"""Postgres artifact metadata adapter; artifact bytes never enter Postgres."""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from psycopg.errors import UniqueViolation
from psycopg.types.json import Jsonb

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
from agent_runtime.surfaces_v2.entities import Artifact, ArtifactRevision
from runtime_adapters._artifact_repository import (
    ARTIFACT_EVENT_COMMAND_TYPE,
    ArtifactRetentionPurgeResult,
    ArtifactRetentionScope,
    artifact_event_outbox_row,
    decode_cursor,
    encode_cursor,
    parse_datetime,
)
from runtime_adapters.artifact_lifecycle import (
    ArtifactDeletionInventory,
    ArtifactLifecycleEvidence,
    ArtifactLifecycleTombstoneResult,
)
from runtime_adapters.artifact_references import artifact_revision_reference_edge
from runtime_adapters.file.artifact_blob_store import FileArtifactBlobStore
from runtime_adapters.postgres.artifact_hold_fence import (
    acquire_artifact_hold_fences,
    active_hold_predicate,
    has_active_hold_for_scope,
)
from runtime_adapters.postgres.artifact_publication import (
    acquire_artifact_advisory_lock,
    acquire_artifact_scope_lock,
    restore_gc_quarantine,
    restore_quarantine_after_rollback,
)
from runtime_api.schemas.commands import RuntimeArtifactEventCommand

_IDEMPOTENCY_ABSENT = object()


class PostgresArtifactMetadataStore:
    """Borrows ``PostgresRuntimeApiStore`` connections and transaction context."""

    def __init__(
        self,
        parent: object,
        blob_store: FileArtifactBlobStore,
    ) -> None:
        self._parent = parent
        self._blob_store = blob_store

    async def create_artifact(
        self, command: ArtifactCreateCommand
    ) -> ArtifactMutationResult:
        artifact = command.record.artifact
        revision = command.record.current_revision
        restored = False
        try:
            async with self._parent._tenant_connection(org_id=artifact.org_id) as conn:  # type: ignore[attr-defined]
                async with conn.transaction():
                    await acquire_artifact_scope_lock(conn, org_id=artifact.org_id)
                    await acquire_artifact_advisory_lock(
                        conn,
                        blob_key=revision.blob_key,
                    )
                    restored = await restore_gc_quarantine(
                        conn,
                        blob_key=revision.blob_key,
                        blob_store=self._blob_store,
                    )
                    replay = await self._idempotency_result(
                        conn,
                        command.idempotency,
                    )
                    if replay is not _IDEMPOTENCY_ABSENT:
                        return replay
                    await conn.execute(
                        """
                        INSERT INTO runtime_artifacts (
                            org_id, user_id, artifact_id, conversation_id, run_id,
                            kind, title, media_type, current_revision, created_by,
                            created_at, updated_at, deleted_at
                        ) VALUES (
                            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NULL
                        )
                        """,
                        (
                            artifact.org_id,
                            artifact.user_id,
                            artifact.artifact_id,
                            artifact.conversation_id,
                            artifact.run_id,
                            artifact.kind.value,
                            artifact.title,
                            artifact.media_type,
                            artifact.current_revision,
                            artifact.created_by.value,
                            artifact.created_at,
                            artifact.updated_at,
                        ),
                    )
                    await self._insert_revision(
                        conn,
                        org_id=artifact.org_id,
                        user_id=artifact.user_id,
                        revision=command.record.current_revision,
                        suggested_filename=command.record.suggested_filename,
                    )
                    await self._insert_artifact_reference(
                        conn,
                        artifact_revision_reference_edge(
                            org_id=artifact.org_id,
                            user_id=artifact.user_id,
                            artifact_id=artifact.artifact_id,
                            revision=1,
                            blob_key=revision.blob_key,
                            created_at=parse_datetime(revision.revision.created_at),
                        ),
                    )
                    result = ArtifactMutationResult(record=command.record)
                    for event in command.ledger_events:
                        await self._insert_outbox(
                            conn,
                            artifact_event_outbox_row(
                                event,
                                artifact_id=artifact.artifact_id,
                            ),
                        )
                    await self._insert_idempotency(
                        conn,
                        command.idempotency,
                        result,
                        artifact_id=artifact.artifact_id,
                        revision=1,
                    )
                    return result
        except UniqueViolation as exc:
            if restored:
                await restore_quarantine_after_rollback(
                    parent=self._parent,
                    blob_key=revision.blob_key,
                    blob_store=self._blob_store,
                )
            replay = await self._idempotency_result_fresh(command.idempotency)
            if replay is not _IDEMPOTENCY_ABSENT:
                if replay is None:
                    raise ArtifactConflictError() from exc
                return replay
            raise ArtifactConflictError() from exc
        except BaseException:
            if restored:
                await restore_quarantine_after_rollback(
                    parent=self._parent,
                    blob_key=revision.blob_key,
                    blob_store=self._blob_store,
                )
            raise

    async def append_revision(
        self, command: ArtifactAppendCommand
    ) -> ArtifactMutationResult:
        restored = False
        try:
            async with self._parent._tenant_connection(  # type: ignore[attr-defined]
                org_id=command.scope.org_id
            ) as conn:
                async with conn.transaction():
                    await acquire_artifact_scope_lock(
                        conn,
                        org_id=command.scope.org_id,
                    )
                    await acquire_artifact_advisory_lock(
                        conn,
                        blob_key=command.revision.blob_key,
                    )
                    restored = await restore_gc_quarantine(
                        conn,
                        blob_key=command.revision.blob_key,
                        blob_store=self._blob_store,
                    )
                    replay = await self._idempotency_result(
                        conn,
                        command.idempotency,
                    )
                    if replay is not _IDEMPOTENCY_ABSENT:
                        return replay
                    current = await self._select_record(
                        conn,
                        org_id=command.scope.org_id,
                        user_id=command.scope.user_id,
                        artifact_id=command.artifact_id,
                        include_deleted=False,
                        for_update=True,
                    )
                    if (
                        current is None
                        or current.artifact.current_revision
                        != command.expected_revision
                    ):
                        raise ArtifactConflictError()
                    revision_number = command.revision.revision.revision
                    artifact = current.artifact.model_copy(
                        update={
                            "current_revision": revision_number,
                            "updated_at": command.revision.revision.created_at,
                        }
                    )
                    record = current.model_copy(
                        update={
                            "artifact": artifact,
                            "current_revision": command.revision,
                        }
                    )
                    await self._insert_revision(
                        conn,
                        org_id=command.scope.org_id,
                        user_id=command.scope.user_id,
                        revision=command.revision,
                        suggested_filename=current.suggested_filename,
                    )
                    await self._insert_artifact_reference(
                        conn,
                        artifact_revision_reference_edge(
                            org_id=command.scope.org_id,
                            user_id=command.scope.user_id,
                            artifact_id=command.artifact_id,
                            revision=revision_number,
                            blob_key=command.revision.blob_key,
                            created_at=parse_datetime(
                                command.revision.revision.created_at
                            ),
                        ),
                    )
                    await conn.execute(
                        """
                        UPDATE runtime_artifacts
                           SET current_revision = %s, updated_at = %s
                         WHERE org_id = %s AND user_id = %s AND artifact_id = %s
                           AND current_revision = %s AND deleted_at IS NULL
                        """,
                        (
                            revision_number,
                            command.revision.revision.created_at,
                            command.scope.org_id,
                            command.scope.user_id,
                            command.artifact_id,
                            command.expected_revision,
                        ),
                    )
                    result = ArtifactMutationResult(record=record)
                    await self._insert_outbox(
                        conn,
                        artifact_event_outbox_row(
                            command.ledger_event,
                            artifact_id=command.artifact_id,
                        ),
                    )
                    await self._insert_idempotency(
                        conn,
                        command.idempotency,
                        result,
                        artifact_id=command.artifact_id,
                        revision=revision_number,
                    )
                    return result
        except UniqueViolation as exc:
            if restored:
                await restore_quarantine_after_rollback(
                    parent=self._parent,
                    blob_key=command.revision.blob_key,
                    blob_store=self._blob_store,
                )
            replay = await self._idempotency_result_fresh(command.idempotency)
            if replay is not _IDEMPOTENCY_ABSENT:
                if replay is None:
                    raise ArtifactConflictError() from exc
                return replay
            raise ArtifactConflictError() from exc
        except BaseException:
            if restored:
                await restore_quarantine_after_rollback(
                    parent=self._parent,
                    blob_key=command.revision.blob_key,
                    blob_store=self._blob_store,
                )
            raise

    async def get_artifact(
        self,
        *,
        org_id: str,
        user_id: str,
        artifact_id: str,
        include_deleted: bool = False,
    ) -> ArtifactStoredRecord | None:
        async with self._parent._tenant_connection(org_id=org_id) as conn:  # type: ignore[attr-defined]
            return await self._select_record(
                conn,
                org_id=org_id,
                user_id=user_id,
                artifact_id=artifact_id,
                include_deleted=include_deleted,
                for_update=False,
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
        async with self._parent._tenant_connection(org_id=org_id) as conn:  # type: ignore[attr-defined]
            cursor = await conn.execute(
                f"""
                SELECT r.*
                  FROM runtime_artifact_revisions r
                  JOIN runtime_artifacts a
                    ON a.org_id = r.org_id AND a.artifact_id = r.artifact_id
                 WHERE r.org_id = %s AND r.user_id = %s
                   AND r.artifact_id = %s AND r.revision = %s
                   {" " if include_deleted else "AND a.deleted_at IS NULL"}
                """,
                (org_id, user_id, artifact_id, revision),
            )
            row = await cursor.fetchone()
        return self._revision_from_row(row) if row is not None else None

    async def list_artifacts(self, query: ArtifactListQuery) -> ArtifactListPage:
        clauses = [
            "a.org_id = %s",
            "a.user_id = %s",
            "a.run_id = %s",
        ]
        params: list[object] = [query.org_id, query.user_id, query.run_id]
        if query.kind is not None:
            clauses.append("a.kind = %s")
            params.append(query.kind.value)
        if not query.include_deleted:
            clauses.append("a.deleted_at IS NULL")
        if query.cursor is not None:
            updated_at, artifact_id = decode_cursor(query.cursor)
            clauses.append(
                "(a.updated_at < %s OR (a.updated_at = %s AND a.artifact_id > %s))"
            )
            params.extend((updated_at, updated_at, artifact_id))
        params.append(query.limit + 1)
        async with self._parent._tenant_connection(org_id=query.org_id) as conn:  # type: ignore[attr-defined]
            cursor = await conn.execute(
                f"""
                {self._record_select()}
                 WHERE {" AND ".join(clauses)}
                 ORDER BY a.updated_at DESC, a.artifact_id ASC
                 LIMIT %s
                """,
                tuple(params),
            )
            rows = await cursor.fetchall()
        records = [self._record_from_row(row) for row in rows]
        page = records[: query.limit]
        return ArtifactListPage(
            artifacts=tuple(page),
            next_cursor=(
                encode_cursor(page[-1]) if len(records) > query.limit and page else None
            ),
        )

    async def soft_delete(
        self, command: ArtifactSoftDeleteCommand
    ) -> ArtifactStoredRecord | None:
        async with self._parent._tenant_connection(org_id=command.org_id) as conn:  # type: ignore[attr-defined]
            async with conn.transaction():
                await acquire_artifact_scope_lock(conn, org_id=command.org_id)
                await acquire_artifact_hold_fences(
                    conn,
                    org_id=command.org_id,
                    user_id=command.user_id,
                )
                replay = await self._idempotency_result(conn, command.idempotency)
                if replay is not _IDEMPOTENCY_ABSENT:
                    return replay.record if replay is not None else None
                current = await self._select_record(
                    conn,
                    org_id=command.org_id,
                    user_id=command.user_id,
                    artifact_id=command.artifact_id,
                    include_deleted=True,
                    for_update=True,
                )
                if current is None:
                    await self._insert_idempotency(
                        conn,
                        command.idempotency,
                        None,
                        artifact_id=command.artifact_id,
                        revision=None,
                    )
                    return None
                if await has_active_hold_for_scope(
                    conn,
                    org_id=command.org_id,
                    user_id=command.user_id,
                    conversation_id=current.artifact.conversation_id,
                ):
                    # A held artifact remains readable and is never hidden by
                    # an explicit soft-delete retry.  Returning the current
                    # record keeps the endpoint idempotent without creating a
                    # second policy outcome.
                    return current
                if current.artifact.deleted_at is not None:
                    await self._insert_idempotency(
                        conn,
                        command.idempotency,
                        None,
                        artifact_id=command.artifact_id,
                        revision=None,
                    )
                    return None
                artifact = current.artifact.model_copy(
                    update={
                        "deleted_at": command.deleted_at.isoformat(),
                        "updated_at": command.deleted_at.isoformat(),
                    }
                )
                current = current.model_copy(update={"artifact": artifact})
                await conn.execute(
                    """
                    UPDATE runtime_artifacts a
                       SET deleted_at = %s, updated_at = %s
                     WHERE org_id = %s AND user_id = %s AND artifact_id = %s
                    """,
                    (
                        command.deleted_at,
                        command.deleted_at,
                        command.org_id,
                        command.user_id,
                        command.artifact_id,
                    ),
                )
                result = ArtifactMutationResult(record=current)
                await self._insert_idempotency(
                    conn,
                    command.idempotency,
                    result,
                    artifact_id=command.artifact_id,
                    revision=current.artifact.current_revision,
                )
                return current

    async def list_unreferenced_content(
        self,
        *,
        org_id: str,
        older_than: datetime,
        limit: int,
    ) -> Sequence[ArtifactGcCandidate]:
        async with self._parent._tenant_connection(org_id=org_id) as conn:  # type: ignore[attr-defined]
            cursor = await conn.execute(
                """
                SELECT blob_key, candidate_since AS unreferenced_since
                  FROM runtime_artifact_gc_candidates
                 WHERE provenance_org_id = %s AND candidate_since < %s
                 ORDER BY candidate_since ASC, blob_key ASC
                 LIMIT %s
                """,
                (org_id, older_than, limit),
            )
            rows = await cursor.fetchall()
        return tuple(
            ArtifactGcCandidate(
                blob_key=row["blob_key"],
                unreferenced_since=row["unreferenced_since"],
            )
            for row in rows
        )

    async def pending_artifact_events(
        self,
    ) -> tuple[RuntimeArtifactEventCommand, ...]:
        """Expose the existing runtime outbox as the canonical Postgres ledger."""

        async with self._parent._role_connection("worker") as conn:  # type: ignore[attr-defined]
            cursor = await conn.execute(
                """
                SELECT payload_json
                  FROM runtime_outbox_events
                 WHERE event_type = %s
                   AND status NOT IN ('completed', 'dead_letter')
                 ORDER BY created_at, id
                """,
                (ARTIFACT_EVENT_COMMAND_TYPE,),
            )
            rows = await cursor.fetchall()
        return tuple(
            RuntimeArtifactEventCommand.model_validate(row["payload_json"])
            for row in rows
        )

    async def acknowledge_artifact_event(
        self,
        *,
        event_id: str,
        status: OutboxStatus,
    ) -> None:
        if status not in {OutboxStatus.COMPLETED, OutboxStatus.DEAD_LETTER}:
            raise ValueError("artifact canonical acknowledgement must be terminal")
        async with self._parent._role_connection("worker") as conn:  # type: ignore[attr-defined]
            await conn.execute(
                """
                UPDATE runtime_outbox_events
                   SET status = %s, locked_by = NULL, lock_expires_at = NULL,
                       updated_at = now()
                 WHERE id = %s AND event_type = %s
                """,
                (status.value, event_id, ARTIFACT_EVENT_COMMAND_TYPE),
            )

    async def deletion_inventory(
        self,
        *,
        scope: ArtifactRetentionScope,
    ) -> ArtifactDeletionInventory:
        async with self._parent._tenant_connection(org_id=scope.org_id) as conn:  # type: ignore[attr-defined]
            inventory = await self._deletion_inventory_conn(conn, scope)
        return await self._with_global_gc_inventory(inventory, scope.org_id)

    async def tombstone_for_lifecycle(
        self,
        *,
        scope: ArtifactRetentionScope,
        deleted_at: datetime,
        evidence_id: str,
        reason: str,
    ) -> ArtifactLifecycleTombstoneResult:
        async with self._parent._tenant_connection(org_id=scope.org_id) as conn:  # type: ignore[attr-defined]
            async with conn.transaction():
                await acquire_artifact_scope_lock(conn, org_id=scope.org_id)
                await acquire_artifact_hold_fences(
                    conn,
                    org_id=scope.org_id,
                    user_id=scope.user_id,
                    conversation_id=scope.conversation_id,
                )
                existing = await self._select_lifecycle_evidence(
                    conn,
                    evidence_id=evidence_id,
                )
                if existing is not None:
                    after = await self._deletion_inventory_conn(conn, scope)
                    return ArtifactLifecycleTombstoneResult(
                        evidence=existing,
                        inventory_after=await self._with_global_gc_inventory(
                            after,
                            scope.org_id,
                        ),
                    )
                inventory_before = await self._deletion_inventory_conn(conn, scope)
                clauses = ["a.org_id = %s", "a.deleted_at IS NULL"]
                params: list[object] = [scope.org_id]
                if scope.user_id is not None:
                    clauses.append("a.user_id = %s")
                    params.append(scope.user_id)
                if scope.conversation_id is not None:
                    clauses.append("a.conversation_id = %s")
                    params.append(scope.conversation_id)
                if scope.protected_conversation_ids:
                    clauses.append("NOT (a.conversation_id = ANY(%s))")
                    params.append(list(scope.protected_conversation_ids))
                cursor = await conn.execute(
                    f"""
                    UPDATE runtime_artifacts a
                       SET deleted_at = %s, updated_at = %s
                     WHERE {" AND ".join(clauses)}
                       AND {active_hold_predicate(artifact_alias="a")}
                    RETURNING artifact_id
                    """,
                    (deleted_at, deleted_at, *params),
                )
                tombstoned = tuple(
                    sorted(row["artifact_id"] for row in await cursor.fetchall())
                )
                evidence = ArtifactLifecycleEvidence(
                    evidence_id=evidence_id,
                    scope=scope,
                    reason=reason,
                    created_at=deleted_at,
                    tombstoned_artifact_ids=tombstoned,
                    inventory_before=await self._with_global_gc_inventory(
                        inventory_before,
                        scope.org_id,
                    ),
                )
                await conn.execute(
                    """
                    INSERT INTO runtime_deletion_evidence (
                        id, org_id, user_id, request_type, reason,
                        conversations_archived, messages_tombstoned,
                        runs_cancelled, events_retained, created_at
                    ) VALUES (
                        %s, %s, %s, 'artifact_lifecycle', %s,
                        0, %s, 0, %s, %s
                    )
                    """,
                    (
                        evidence_id,
                        scope.org_id,
                        scope.user_id or "__artifact_org_lifecycle__",
                        json.dumps(
                            self._evidence_to_json(evidence),
                            separators=(",", ":"),
                            sort_keys=True,
                        ),
                        len(tombstoned),
                        evidence.inventory_before.reference_edge_rows,
                        deleted_at,
                    ),
                )
                inventory_after = await self._deletion_inventory_conn(conn, scope)
        return ArtifactLifecycleTombstoneResult(
            evidence=evidence,
            inventory_after=await self._with_global_gc_inventory(
                inventory_after,
                scope.org_id,
            ),
        )

    async def get_lifecycle_evidence(
        self,
        *,
        org_id: str,
        evidence_id: str,
    ) -> ArtifactLifecycleEvidence | None:
        async with self._parent._tenant_connection(org_id=org_id) as conn:  # type: ignore[attr-defined]
            return await self._select_lifecycle_evidence(
                conn,
                evidence_id=evidence_id,
            )

    async def list_lifecycle_org_ids(self) -> tuple[str, ...]:
        """Enumerate only tenant identifiers needed by the worker sweep."""

        async with self._parent._role_connection("worker") as conn:  # type: ignore[attr-defined]
            cursor = await conn.execute(
                """
                SELECT org_id
                  FROM runtime_artifacts
                UNION
                SELECT provenance_org_id AS org_id
                  FROM runtime_artifact_gc_candidates
                UNION
                SELECT provenance_org_id AS org_id
                  FROM runtime_artifact_gc_quarantine
                 WHERE provenance_org_id IS NOT NULL
                ORDER BY org_id
                """
            )
            rows = await cursor.fetchall()
        return tuple(str(row["org_id"]) for row in rows)

    async def purge_tombstones(
        self,
        *,
        scope: ArtifactRetentionScope,
        deleted_before: datetime,
        limit: int,
    ) -> ArtifactRetentionPurgeResult:
        clauses = [
            "a.org_id = %s",
            "a.deleted_at IS NOT NULL",
            "a.deleted_at < %s",
        ]
        params: list[object] = [scope.org_id, deleted_before]
        if scope.user_id is not None:
            clauses.append("a.user_id = %s")
            params.append(scope.user_id)
        if scope.conversation_id is not None:
            clauses.append("a.conversation_id = %s")
            params.append(scope.conversation_id)
        if scope.protected_conversation_ids:
            clauses.append("NOT (a.conversation_id = ANY(%s))")
            params.append(list(scope.protected_conversation_ids))
        params.append(limit)
        async with self._parent._tenant_connection(org_id=scope.org_id) as conn:  # type: ignore[attr-defined]
            async with conn.transaction():
                await acquire_artifact_scope_lock(conn, org_id=scope.org_id)
                await acquire_artifact_hold_fences(
                    conn,
                    org_id=scope.org_id,
                    user_id=scope.user_id,
                    conversation_id=scope.conversation_id,
                )
                cursor = await conn.execute(
                    f"""
                    SELECT a.artifact_id, a.deleted_at
                      FROM runtime_artifacts a
                     WHERE {" AND ".join(clauses)}
                       AND {active_hold_predicate(artifact_alias="a")}
                     ORDER BY a.deleted_at ASC, a.artifact_id ASC
                     LIMIT %s
                     FOR UPDATE OF a
                    """,
                    tuple(params),
                )
                victims = await cursor.fetchall()
                artifact_ids = tuple(row["artifact_id"] for row in victims)
                if not artifact_ids:
                    return ArtifactRetentionPurgeResult()
                cursor = await conn.execute(
                    """
                    SELECT r.blob_key, MIN(a.deleted_at) AS candidate_since
                      FROM runtime_artifact_revisions r
                      JOIN runtime_artifacts a
                        ON a.org_id = r.org_id
                       AND a.artifact_id = r.artifact_id
                     WHERE r.org_id = %s AND r.artifact_id = ANY(%s)
                     GROUP BY r.blob_key
                     ORDER BY r.blob_key
                    """,
                    (scope.org_id, list(artifact_ids)),
                )
                digest_rows = await cursor.fetchall()
                for row in digest_rows:
                    await acquire_artifact_advisory_lock(conn, blob_key=row["blob_key"])
                for row in digest_rows:
                    await conn.execute(
                        """
                        INSERT INTO runtime_artifact_gc_candidates (
                            provenance_org_id, blob_key, candidate_since,
                            created_at
                        ) VALUES (%s, %s, %s, now())
                        ON CONFLICT (provenance_org_id, blob_key) DO UPDATE
                            SET candidate_since = LEAST(
                                runtime_artifact_gc_candidates.candidate_since,
                                EXCLUDED.candidate_since
                            )
                        """,
                        (
                            scope.org_id,
                            row["blob_key"],
                            row["candidate_since"],
                        ),
                    )
                # Keep only ownership facts needed to evaluate a *future*
                # legal hold after these artifacts/revisions are gone.  The
                # candidate row is the parent so scope rows automatically
                # disappear on a re-reference, restoration, or final reap.
                await conn.execute(
                    """
                    INSERT INTO runtime_artifact_gc_candidate_scopes (
                        provenance_org_id, blob_key, user_id, conversation_id
                    )
                    SELECT DISTINCT
                        a.org_id,
                        r.blob_key,
                        a.user_id,
                        a.conversation_id
                      FROM runtime_artifact_revisions r
                      JOIN runtime_artifacts a
                        ON a.org_id = r.org_id
                       AND a.artifact_id = r.artifact_id
                     WHERE r.org_id = %s AND r.artifact_id = ANY(%s)
                    ON CONFLICT DO NOTHING
                    """,
                    (scope.org_id, list(artifact_ids)),
                )
                await conn.execute(
                    """
                    DELETE FROM runtime_artifact_reference_edges
                     WHERE org_id = %s
                       AND reference_kind = 'artifact'
                       AND split_part(reference_id, ':', 1) = ANY(%s)
                    """,
                    (scope.org_id, list(artifact_ids)),
                )
                await conn.execute(
                    """
                    DELETE FROM runtime_artifact_idempotency
                     WHERE org_id = %s AND artifact_id = ANY(%s)
                    """,
                    (scope.org_id, list(artifact_ids)),
                )
                await conn.execute(
                    """
                    DELETE FROM runtime_artifacts
                     WHERE org_id = %s AND artifact_id = ANY(%s)
                    """,
                    (scope.org_id, list(artifact_ids)),
                )
        return ArtifactRetentionPurgeResult(
            purged_artifact_ids=tuple(sorted(artifact_ids)),
            eligible_candidates=tuple(
                ArtifactGcCandidate(
                    blob_key=row["blob_key"],
                    unreferenced_since=row["candidate_since"],
                )
                for row in digest_rows
            ),
        )

    async def _select_record(
        self,
        conn,
        *,
        org_id: str,
        user_id: str,
        artifact_id: str,
        include_deleted: bool,
        for_update: bool,
    ) -> ArtifactStoredRecord | None:
        cursor = await conn.execute(
            f"""
            {self._record_select()}
             WHERE a.org_id = %s AND a.user_id = %s AND a.artifact_id = %s
               {" " if include_deleted else "AND a.deleted_at IS NULL"}
             {"FOR UPDATE OF a" if for_update else ""}
            """,
            (org_id, user_id, artifact_id),
        )
        row = await cursor.fetchone()
        return self._record_from_row(row) if row is not None else None

    async def _deletion_inventory_conn(
        self,
        conn,
        scope: ArtifactRetentionScope,
    ) -> ArtifactDeletionInventory:
        clauses = ["org_id = %s"]
        params: list[object] = [scope.org_id]
        if scope.user_id is not None:
            clauses.append("user_id = %s")
            params.append(scope.user_id)
        if scope.conversation_id is not None:
            clauses.append("conversation_id = %s")
            params.append(scope.conversation_id)
        cursor = await conn.execute(
            f"""
            SELECT artifact_id
              FROM runtime_artifacts
             WHERE {" AND ".join(clauses)}
             ORDER BY artifact_id
            """,
            tuple(params),
        )
        artifact_ids = tuple(row["artifact_id"] for row in await cursor.fetchall())
        if artifact_ids:
            cursor = await conn.execute(
                """
                SELECT blob_key
                  FROM runtime_artifact_revisions
                 WHERE org_id = %s AND artifact_id = ANY(%s)
                 ORDER BY artifact_id, revision
                """,
                (scope.org_id, list(artifact_ids)),
            )
            revision_rows = await cursor.fetchall()
            blob_keys = tuple(sorted({row["blob_key"] for row in revision_rows}))
            cursor = await conn.execute(
                """
                SELECT count(*) AS count
                  FROM runtime_artifact_idempotency
                 WHERE org_id = %s AND artifact_id = ANY(%s)
                """,
                (scope.org_id, list(artifact_ids)),
            )
            idempotency = await cursor.fetchone()
        else:
            revision_rows = []
            blob_keys = ()
            idempotency = {"count": 0}
        edge_clauses = ["org_id = %s"]
        edge_params: list[object] = [scope.org_id]
        if scope.conversation_id is not None:
            edge_clauses.append(
                "reference_kind = 'artifact' "
                "AND split_part(reference_id, ':', 1) = ANY(%s)"
            )
            edge_params.append(list(artifact_ids))
        elif scope.user_id is not None:
            edge_clauses.append(
                "(user_id = %s OR (reference_kind = 'artifact' "
                "AND split_part(reference_id, ':', 1) = ANY(%s)))"
            )
            edge_params.extend((scope.user_id, list(artifact_ids)))
        cursor = await conn.execute(
            f"""
            SELECT count(*) AS count
              FROM runtime_artifact_reference_edges
             WHERE {" AND ".join(edge_clauses)}
            """,
            tuple(edge_params),
        )
        edges = await cursor.fetchone()
        cursor = await conn.execute(
            """
            SELECT blob_key
              FROM runtime_artifact_gc_candidates
             WHERE provenance_org_id = %s
             ORDER BY blob_key
            """,
            (scope.org_id,),
        )
        candidate_rows = await cursor.fetchall()
        candidate_keys = tuple(row["blob_key"] for row in candidate_rows)
        return ArtifactDeletionInventory(
            artifact_rows=len(artifact_ids),
            revision_rows=len(revision_rows),
            idempotency_rows=int(idempotency["count"]) if idempotency else 0,
            reference_edge_rows=int(edges["count"]) if edges else 0,
            gc_candidate_rows=len(candidate_keys),
            artifact_ids=artifact_ids,
            blob_keys=tuple(sorted(set(blob_keys) | set(candidate_keys))),
        )

    async def _with_global_gc_inventory(
        self,
        inventory: ArtifactDeletionInventory,
        org_id: str,
    ) -> ArtifactDeletionInventory:
        if not inventory.blob_keys:
            return inventory
        async with self._parent._role_connection("worker") as conn:  # type: ignore[attr-defined]
            cursor = await conn.execute(
                """
                SELECT count(*) AS quarantined,
                       count(*) FILTER (WHERE reaping_at IS NOT NULL) AS reaping
                  FROM runtime_artifact_gc_quarantine
                 WHERE blob_key = ANY(%s)
                   AND (
                       provenance_org_id = %s
                       OR blob_key = ANY(%s)
                   )
                """,
                (list(inventory.blob_keys), org_id, list(inventory.blob_keys)),
            )
            row = await cursor.fetchone()
        return ArtifactDeletionInventory(
            artifact_rows=inventory.artifact_rows,
            revision_rows=inventory.revision_rows,
            idempotency_rows=inventory.idempotency_rows,
            reference_edge_rows=inventory.reference_edge_rows,
            gc_candidate_rows=inventory.gc_candidate_rows,
            quarantined_digest_rows=int(row["quarantined"]) if row else 0,
            reaping_digest_rows=int(row["reaping"]) if row else 0,
            artifact_ids=inventory.artifact_ids,
            blob_keys=inventory.blob_keys,
        )

    async def _select_lifecycle_evidence(
        self,
        conn,
        *,
        evidence_id: str,
    ) -> ArtifactLifecycleEvidence | None:
        cursor = await conn.execute(
            """
            SELECT reason
              FROM runtime_deletion_evidence
             WHERE id = %s AND request_type = 'artifact_lifecycle'
            """,
            (evidence_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return self._evidence_from_json(json.loads(row["reason"]))

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
    def _evidence_from_json(value: dict[str, Any]) -> ArtifactLifecycleEvidence:
        scope = value["scope"]
        inventory = value["inventory_before"]
        return ArtifactLifecycleEvidence(
            evidence_id=str(value["evidence_id"]),
            scope=ArtifactRetentionScope(
                org_id=str(scope["org_id"]),
                user_id=(
                    str(scope["user_id"]) if scope.get("user_id") is not None else None
                ),
                conversation_id=(
                    str(scope["conversation_id"])
                    if scope.get("conversation_id") is not None
                    else None
                ),
                protected_conversation_ids=tuple(
                    str(item) for item in scope.get("protected_conversation_ids", ())
                ),
            ),
            reason=str(value["reason"]),
            created_at=parse_datetime(value["created_at"]),
            tombstoned_artifact_ids=tuple(value["tombstoned_artifact_ids"]),
            inventory_before=ArtifactDeletionInventory(
                artifact_rows=int(inventory["artifact_rows"]),
                revision_rows=int(inventory["revision_rows"]),
                idempotency_rows=int(inventory["idempotency_rows"]),
                reference_edge_rows=int(inventory["reference_edge_rows"]),
                gc_candidate_rows=int(inventory["gc_candidate_rows"]),
                quarantined_digest_rows=int(inventory["quarantined_digest_rows"]),
                reaping_digest_rows=int(inventory["reaping_digest_rows"]),
                artifact_ids=tuple(inventory["artifact_ids"]),
                blob_keys=tuple(inventory["blob_keys"]),
            ),
        )

    @staticmethod
    def _record_select() -> str:
        return """
            SELECT
                a.org_id, a.user_id, a.artifact_id, a.conversation_id, a.run_id,
                a.kind, a.title, a.media_type, a.current_revision, a.created_by,
                a.created_at, a.updated_at, a.deleted_at,
                r.parent_revision, r.content_ref, r.content_digest, r.byte_size,
                r.author, r.source_ref, r.created_at AS revision_created_at,
                r.blob_key, r.range_supported, r.suggested_filename
              FROM runtime_artifacts a
              JOIN runtime_artifact_revisions r
                ON r.org_id = a.org_id AND r.artifact_id = a.artifact_id
               AND r.revision = a.current_revision
        """

    @staticmethod
    def _record_from_row(row: dict[str, Any]) -> ArtifactStoredRecord:
        revision = ArtifactRevision(
            artifact_id=row["artifact_id"],
            revision=row["current_revision"],
            parent_revision=row["parent_revision"],
            content_ref=row["content_ref"],
            content_digest=row["content_digest"],
            byte_size=row["byte_size"],
            author=row["author"],
            source_ref=row["source_ref"],
            created_at=row["revision_created_at"].isoformat(),
        )
        return ArtifactStoredRecord(
            artifact=Artifact(
                artifact_id=row["artifact_id"],
                org_id=row["org_id"],
                user_id=row["user_id"],
                conversation_id=row["conversation_id"],
                run_id=row["run_id"],
                kind=row["kind"],
                title=row["title"],
                media_type=row["media_type"],
                current_revision=row["current_revision"],
                created_by=row["created_by"],
                created_at=row["created_at"].isoformat(),
                updated_at=row["updated_at"].isoformat(),
                deleted_at=(
                    row["deleted_at"].isoformat()
                    if row["deleted_at"] is not None
                    else None
                ),
            ),
            current_revision=ArtifactStoredRevision(
                revision=revision,
                blob_key=row["blob_key"],
                range_supported=row["range_supported"],
            ),
            suggested_filename=row["suggested_filename"],
        )

    @staticmethod
    def _revision_from_row(row: dict[str, Any]) -> ArtifactStoredRevision:
        return ArtifactStoredRevision(
            revision=ArtifactRevision(
                artifact_id=row["artifact_id"],
                revision=row["revision"],
                parent_revision=row["parent_revision"],
                content_ref=row["content_ref"],
                content_digest=row["content_digest"],
                byte_size=row["byte_size"],
                author=row["author"],
                source_ref=row["source_ref"],
                created_at=row["created_at"].isoformat(),
            ),
            blob_key=row["blob_key"],
            range_supported=row["range_supported"],
        )

    @staticmethod
    async def _insert_revision(
        conn,
        *,
        org_id: str,
        user_id: str,
        revision: ArtifactStoredRevision,
        suggested_filename: str | None,
    ) -> None:
        value = revision.revision
        await conn.execute(
            """
            INSERT INTO runtime_artifact_revisions (
                org_id, user_id, artifact_id, revision, parent_revision,
                content_ref, content_digest, byte_size, author, source_ref,
                created_at, blob_key, range_supported, suggested_filename
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
            )
            """,
            (
                org_id,
                user_id,
                value.artifact_id,
                value.revision,
                value.parent_revision,
                value.content_ref,
                value.content_digest,
                value.byte_size,
                value.author.value,
                value.source_ref,
                value.created_at,
                revision.blob_key,
                revision.range_supported,
                suggested_filename,
            ),
        )

    @staticmethod
    async def _insert_artifact_reference(conn, edge) -> None:
        await conn.execute(
            """
            INSERT INTO runtime_artifact_reference_edges (
                org_id, edge_id, user_id, blob_key, reference_kind,
                reference_id, created_at, released_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, NULL)
            ON CONFLICT (org_id, edge_id) DO NOTHING
            """,
            (
                edge.org_id,
                edge.edge_id,
                edge.user_id,
                edge.blob_key,
                edge.reference_kind.value,
                edge.reference_id,
                edge.created_at,
            ),
        )

    @staticmethod
    async def _insert_outbox(conn, row: dict[str, Any]) -> None:
        await conn.execute(
            """
            INSERT INTO runtime_outbox_events (
                id, aggregate_type, aggregate_id, org_id, event_type, payload_json,
                status, attempts, available_at, locked_by, lock_expires_at,
                created_at, updated_at
            ) VALUES (
                %s, %s, %s, %s, %s, %s, 'pending', 0, %s, NULL, NULL, %s, %s
            )
            """,
            (
                row["id"],
                row["aggregate_type"],
                row["aggregate_id"],
                row["org_id"],
                row["event_type"],
                Jsonb(row["payload_json"]),
                row["available_at"],
                row["created_at"],
                row["updated_at"],
            ),
        )

    @staticmethod
    async def _insert_idempotency(
        conn,
        binding,
        result: ArtifactMutationResult | None,
        *,
        artifact_id: str,
        revision: int | None,
    ) -> None:
        await conn.execute(
            """
            INSERT INTO runtime_artifact_idempotency (
                org_id, user_id, route, idempotency_key, request_digest,
                artifact_id, revision, response_json, created_at, updated_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, now(), now())
            """,
            (
                binding.org_id,
                binding.user_id,
                binding.route,
                binding.key,
                binding.request_digest,
                artifact_id,
                revision,
                Jsonb(result.model_dump(mode="json") if result is not None else None),
            ),
        )

    @staticmethod
    async def _idempotency_result(conn, binding):
        cursor = await conn.execute(
            """
            SELECT request_digest, response_json
              FROM runtime_artifact_idempotency
             WHERE org_id = %s AND user_id = %s
               AND route = %s AND idempotency_key = %s
             FOR UPDATE
            """,
            (binding.org_id, binding.user_id, binding.route, binding.key),
        )
        row = await cursor.fetchone()
        if row is None:
            return _IDEMPOTENCY_ABSENT
        if row["request_digest"] != binding.request_digest:
            raise ArtifactIdempotencyConflictError()
        if row["response_json"] is None:
            return None
        result = ArtifactMutationResult.model_validate(row["response_json"])
        return result.model_copy(update={"replayed": True})

    async def _idempotency_result_fresh(self, binding):
        """Reread a concurrent winner only after the failed tx is aborted."""

        async with self._parent._tenant_connection(org_id=binding.org_id) as conn:  # type: ignore[attr-defined]
            return await self._idempotency_result(conn, binding)


__all__ = ("PostgresArtifactMetadataStore",)
