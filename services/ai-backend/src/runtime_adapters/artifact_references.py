"""Reference-edge adapters used by fail-safe artifact retention."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from enum import StrEnum
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from runtime_adapters._artifact_repository import ArtifactGcCandidateScope

if TYPE_CHECKING:
    from runtime_adapters.file.artifact_publication import (
        FileArtifactPublicationCoordinator,
    )
    from runtime_adapters.in_memory.artifact_publication import (
        InMemoryArtifactPublicationCoordinator,
    )


class ArtifactReferenceKind(StrEnum):
    ARTIFACT = "artifact"
    EFFECT = "effect"
    RECEIPT = "receipt"
    AUDIT = "audit"
    LEGAL_HOLD = "legal_hold"


class ArtifactReferenceEdge(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    org_id: str = Field(min_length=1, max_length=255)
    edge_id: str = Field(min_length=1, max_length=255)
    user_id: str | None = Field(default=None, min_length=1, max_length=255)
    blob_key: str = Field(pattern=r"^[0-9a-f]{64}$")
    reference_kind: ArtifactReferenceKind
    reference_id: str = Field(min_length=1, max_length=2048)
    created_at: datetime
    released_at: datetime | None = None


@runtime_checkable
class ArtifactReferenceRepositoryPort(Protocol):
    """Typed acquire/release inventory for every external retention owner."""

    async def acquire(
        self,
        edge: ArtifactReferenceEdge,
    ) -> ArtifactReferenceEdge: ...

    async def release(
        self,
        *,
        org_id: str,
        edge_id: str,
        released_at: datetime | None = None,
    ) -> ArtifactReferenceEdge | None: ...

    async def has_reference(self, *, org_id: str, blob_key: str) -> bool: ...

    async def list_edges(
        self,
        *,
        org_id: str,
        user_id: str | None = None,
    ) -> tuple[ArtifactReferenceEdge, ...]: ...


def artifact_revision_reference_edge(
    *,
    org_id: str,
    user_id: str,
    artifact_id: str,
    revision: int,
    blob_key: str,
    created_at: datetime,
) -> ArtifactReferenceEdge:
    """Build the deterministic edge committed beside an artifact revision."""

    reference_id = f"{artifact_id}:{revision}"
    seed = f"{org_id}\0artifact\0{reference_id}\0{blob_key}".encode()
    return ArtifactReferenceEdge(
        org_id=org_id,
        edge_id=f"artifact-{hashlib.sha256(seed).hexdigest()}",
        user_id=user_id,
        blob_key=blob_key,
        reference_kind=ArtifactReferenceKind.ARTIFACT,
        reference_id=reference_id,
        created_at=created_at,
    )


class InMemoryArtifactReferenceStore:
    """Exact process-local reference inventory."""

    def __init__(
        self,
        coordinator: InMemoryArtifactPublicationCoordinator | None = None,
    ) -> None:
        if coordinator is None:
            from runtime_adapters.in_memory.artifact_publication import (
                InMemoryArtifactPublicationCoordinator,
            )

            coordinator = InMemoryArtifactPublicationCoordinator()
        self.coordinator = coordinator
        self._lock = self.coordinator.lock
        self._edges: dict[tuple[str, str], ArtifactReferenceEdge] = {}

    def put_locked(self, edge: ArtifactReferenceEdge) -> ArtifactReferenceEdge:
        existing = self._edges.get((edge.org_id, edge.edge_id))
        if existing is not None and existing != edge:
            raise ValueError("artifact reference edge already exists")
        self.coordinator.restore_locked(edge.blob_key)
        if edge.blob_key not in self.coordinator.blobs:
            raise FileNotFoundError("artifact blob is unavailable")
        self._edges[(edge.org_id, edge.edge_id)] = edge
        return edge

    async def acquire(self, edge: ArtifactReferenceEdge) -> ArtifactReferenceEdge:
        with self._lock:
            return self.put_locked(edge)

    async def put(self, edge: ArtifactReferenceEdge) -> ArtifactReferenceEdge:
        """Backward-compatible adapter alias; new producers use ``acquire``."""

        return await self.acquire(edge)

    async def release(
        self,
        *,
        org_id: str,
        edge_id: str,
        released_at: datetime | None = None,
    ) -> ArtifactReferenceEdge | None:
        with self._lock:
            existing = self._edges.get((org_id, edge_id))
            if existing is None:
                return None
            if existing.released_at is None:
                existing = existing.model_copy(
                    update={"released_at": released_at or datetime.now(timezone.utc)}
                )
                self._edges[(org_id, edge_id)] = existing
                if not self.has_reference_locked(blob_key=existing.blob_key):
                    self.coordinator.record_candidate_locked(
                        blob_key=existing.blob_key,
                        provenance_org_id=org_id,
                        candidate_since=existing.released_at,
                        scopes=(
                            ArtifactGcCandidateScope(
                                org_id=org_id,
                                user_id=existing.user_id,
                            ),
                        ),
                    )
            return existing

    async def has_reference(self, *, org_id: str, blob_key: str) -> bool:
        with self._lock:
            return self.has_reference_locked(blob_key=blob_key)

    async def list_edges(
        self,
        *,
        org_id: str,
        user_id: str | None = None,
    ) -> tuple[ArtifactReferenceEdge, ...]:
        with self._lock:
            return tuple(
                sorted(
                    (
                        edge
                        for edge in self._edges.values()
                        if edge.org_id == org_id
                        and (user_id is None or edge.user_id == user_id)
                    ),
                    key=lambda edge: edge.edge_id,
                )
            )

    def has_reference_locked(self, *, blob_key: str) -> bool:
        return any(
            edge.blob_key == blob_key and edge.released_at is None
            for edge in self._edges.values()
        )

    def inventory_edges_locked(self) -> tuple[ArtifactReferenceEdge, ...]:
        return tuple(self._edges.values())

    def remove_artifact_edges_locked(
        self, *, org_id: str, artifact_ids: set[str]
    ) -> None:
        self._edges = {
            key: edge
            for key, edge in self._edges.items()
            if not (
                edge.org_id == org_id
                and edge.reference_kind is ArtifactReferenceKind.ARTIFACT
                and edge.reference_id.split(":", 1)[0] in artifact_ids
            )
        }


class FileArtifactReferenceStore:
    """Fsynced external reference edges coordinated with file GC."""

    _TABLE = "artifact_reference_edges"
    _ARTIFACT_TABLE = "artifact_repository"

    def __init__(
        self,
        layout,
        coordinator: FileArtifactPublicationCoordinator | None = None,
    ) -> None:
        from runtime_adapters.file._state_ledger import StateLedger
        from runtime_adapters.file.artifact_publication import (
            FileArtifactPublicationCoordinator,
        )

        self._layout = layout
        self.coordinator = coordinator or FileArtifactPublicationCoordinator(layout)
        self._ledger = StateLedger(layout.state_path(self._TABLE))

    def put_locked(self, edge: ArtifactReferenceEdge) -> ArtifactReferenceEdge:
        """Prepare an artifact edge for its containing metadata transaction."""

        self.coordinator.restore_locked(edge.blob_key)
        self.coordinator.require_active_locked(edge.blob_key)
        self.coordinator.cancel_candidate_locked(edge.blob_key)
        return edge

    def _external_edges_locked(self) -> dict[tuple[str, str], ArtifactReferenceEdge]:
        edges: dict[tuple[str, str], ArtifactReferenceEdge] = {}
        for record_json in self._ledger.load_puts():
            edge = ArtifactReferenceEdge.model_validate(record_json)
            edges[(edge.org_id, edge.edge_id)] = edge
        return edges

    def _artifact_edges_locked(self) -> list[ArtifactReferenceEdge]:
        from runtime_adapters.file._jsonl import JsonlIo

        edges: dict[tuple[str, str], ArtifactReferenceEdge] = {}
        for row in JsonlIo.iter_lines(self._layout.state_path(self._ARTIFACT_TABLE)):
            if row.get("op") == "purge":
                org_id = str(row["org_id"])
                artifact_ids = set(row.get("artifact_ids", []))
                edges = {
                    key: edge
                    for key, edge in edges.items()
                    if not (
                        edge.org_id == org_id
                        and edge.reference_id.split(":", 1)[0] in artifact_ids
                    )
                }
                continue
            edge_json = row.get("reference_edge")
            if edge_json is not None:
                edge = ArtifactReferenceEdge.model_validate(edge_json)
                edges[(edge.org_id, edge.edge_id)] = edge
        return list(edges.values())

    async def acquire(self, edge: ArtifactReferenceEdge) -> ArtifactReferenceEdge:
        with self.coordinator.locked():
            existing = self._external_edges_locked().get((edge.org_id, edge.edge_id))
            if existing is not None:
                if existing != edge:
                    raise ValueError("artifact reference edge already exists")
                return existing
            was_quarantined = self.coordinator.quarantine_path(edge.blob_key).exists()
            try:
                self.coordinator.restore_locked(edge.blob_key)
                self.coordinator.require_active_locked(edge.blob_key)
                self._ledger.append_put(edge.model_dump(mode="json"))
                return edge
            except BaseException:
                if was_quarantined:
                    self.coordinator.rollback_restoration_locked(edge.blob_key)
                raise

    async def put(self, edge: ArtifactReferenceEdge) -> ArtifactReferenceEdge:
        """Backward-compatible adapter alias; new producers use ``acquire``."""

        return await self.acquire(edge)

    async def release(
        self,
        *,
        org_id: str,
        edge_id: str,
        released_at: datetime | None = None,
    ) -> ArtifactReferenceEdge | None:
        with self.coordinator.locked():
            existing = self._external_edges_locked().get((org_id, edge_id))
            if existing is None or existing.released_at is not None:
                return existing
            updated = existing.model_copy(
                update={"released_at": released_at or datetime.now(timezone.utc)}
            )
            self._ledger.append_put(updated.model_dump(mode="json"))
            if not self.has_reference_locked(blob_key=updated.blob_key):
                self.coordinator.record_candidate_locked(
                    blob_key=updated.blob_key,
                    provenance_org_id=org_id,
                    candidate_since=updated.released_at,
                    scopes=(
                        ArtifactGcCandidateScope(
                            org_id=org_id,
                            user_id=updated.user_id,
                        ),
                    ),
                )
            return updated

    async def has_reference(self, *, org_id: str, blob_key: str) -> bool:
        with self.coordinator.locked():
            return self.has_reference_locked(blob_key=blob_key)

    async def list_edges(
        self,
        *,
        org_id: str,
        user_id: str | None = None,
    ) -> tuple[ArtifactReferenceEdge, ...]:
        with self.coordinator.locked():
            edges = (
                *self._external_edges_locked().values(),
                *self._artifact_edges_locked(),
            )
            return tuple(
                sorted(
                    (
                        edge
                        for edge in edges
                        if edge.org_id == org_id
                        and (user_id is None or edge.user_id == user_id)
                    ),
                    key=lambda edge: edge.edge_id,
                )
            )

    def has_reference_locked(self, *, blob_key: str) -> bool:
        external = self._external_edges_locked().values()
        return any(
            edge.blob_key == blob_key and edge.released_at is None
            for edge in (*external, *self._artifact_edges_locked())
        )

    def inventory_edges_locked(self) -> tuple[ArtifactReferenceEdge, ...]:
        return (
            *self._external_edges_locked().values(),
            *self._artifact_edges_locked(),
        )

    def remove_artifact_edges_locked(
        self, *, org_id: str, artifact_ids: set[str]
    ) -> None:
        """The containing metadata purge row removes these during the fold."""


class PostgresArtifactReferenceStore:
    """RLS-scoped reference edges coordinated with Postgres GC."""

    def __init__(self, parent: object, blob_store) -> None:
        self._parent = parent
        self._blob_store = blob_store

    async def acquire(self, edge: ArtifactReferenceEdge) -> ArtifactReferenceEdge:
        from runtime_adapters.postgres.artifact_publication import (
            acquire_artifact_advisory_lock,
            acquire_artifact_scope_lock,
            restore_gc_quarantine,
            restore_quarantine_after_rollback,
        )

        restored = False
        try:
            async with self._parent._tenant_connection(org_id=edge.org_id) as conn:  # type: ignore[attr-defined]
                async with conn.transaction():
                    await acquire_artifact_scope_lock(conn, org_id=edge.org_id)
                    await acquire_artifact_advisory_lock(conn, blob_key=edge.blob_key)
                    restored = await restore_gc_quarantine(
                        conn,
                        blob_key=edge.blob_key,
                        blob_store=self._blob_store,
                        preserve_candidate=True,
                    )
                    await conn.execute(
                        """
                        INSERT INTO runtime_artifact_reference_edges (
                            org_id, edge_id, user_id, blob_key, reference_kind,
                            reference_id, created_at, released_at
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
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
                            edge.released_at,
                        ),
                    )
                    cursor = await conn.execute(
                        """
                        SELECT * FROM runtime_artifact_reference_edges
                         WHERE org_id = %s AND edge_id = %s
                        """,
                        (edge.org_id, edge.edge_id),
                    )
                    row = await cursor.fetchone()
                    if row is None:
                        raise ValueError("artifact reference edge already exists")
                    persisted = ArtifactReferenceEdge.model_validate(row)
                    if persisted != edge:
                        raise ValueError("artifact reference edge already exists")
                    return persisted
        except BaseException:
            if restored:
                await restore_quarantine_after_rollback(
                    parent=self._parent,
                    blob_key=edge.blob_key,
                    blob_store=self._blob_store,
                )
            raise

    async def put(self, edge: ArtifactReferenceEdge) -> ArtifactReferenceEdge:
        """Backward-compatible adapter alias; new producers use ``acquire``."""

        return await self.acquire(edge)

    async def release(
        self,
        *,
        org_id: str,
        edge_id: str,
        released_at: datetime | None = None,
    ) -> ArtifactReferenceEdge | None:
        from runtime_adapters.postgres.artifact_publication import (
            acquire_artifact_advisory_lock,
            acquire_artifact_scope_lock,
        )

        timestamp = released_at or datetime.now(timezone.utc)
        async with self._parent._tenant_connection(org_id=org_id) as conn:  # type: ignore[attr-defined]
            async with conn.transaction():
                await acquire_artifact_scope_lock(conn, org_id=org_id)
                cursor = await conn.execute(
                    """
                    UPDATE runtime_artifact_reference_edges
                       SET released_at = COALESCE(released_at, %s)
                     WHERE org_id = %s AND edge_id = %s
                     RETURNING *
                    """,
                    (timestamp, org_id, edge_id),
                )
                row = await cursor.fetchone()
                if row is not None:
                    await acquire_artifact_advisory_lock(
                        conn,
                        blob_key=str(row["blob_key"]),
                    )
                    cursor = await conn.execute(
                        """
                        SELECT
                            EXISTS (
                                SELECT 1 FROM runtime_artifact_revisions
                                 WHERE blob_key = %s
                            )
                            OR EXISTS (
                                SELECT 1 FROM runtime_artifact_reference_edges
                                 WHERE blob_key = %s AND released_at IS NULL
                            ) AS has_reference
                        """,
                        (row["blob_key"], row["blob_key"]),
                    )
                    reference_state = await cursor.fetchone()
                    if not bool(reference_state and reference_state["has_reference"]):
                        await conn.execute(
                            """
                            INSERT INTO runtime_artifact_gc_candidates (
                                provenance_org_id, blob_key, candidate_since, created_at
                            ) VALUES (%s, %s, %s, now())
                            ON CONFLICT (provenance_org_id, blob_key) DO UPDATE
                                SET candidate_since = LEAST(
                                    runtime_artifact_gc_candidates.candidate_since,
                                    EXCLUDED.candidate_since
                                )
                            """,
                            (org_id, row["blob_key"], timestamp),
                        )
                        await conn.execute(
                            """
                            INSERT INTO runtime_artifact_gc_candidate_scopes (
                                provenance_org_id, blob_key, user_id, conversation_id
                            ) VALUES (%s, %s, %s, '')
                            ON CONFLICT DO NOTHING
                            """,
                            (org_id, row["blob_key"], row["user_id"] or ""),
                        )
        return ArtifactReferenceEdge.model_validate(row) if row is not None else None

    async def has_reference(self, *, org_id: str, blob_key: str) -> bool:
        async with self._parent._role_connection("worker") as conn:  # type: ignore[attr-defined]
            cursor = await conn.execute(
                """
                SELECT EXISTS (
                    SELECT 1 FROM runtime_artifact_reference_edges
                     WHERE blob_key = %s AND released_at IS NULL
                ) AS has_reference
                """,
                (blob_key,),
            )
            row = await cursor.fetchone()
        return bool(row and row["has_reference"])

    async def list_edges(
        self,
        *,
        org_id: str,
        user_id: str | None = None,
    ) -> tuple[ArtifactReferenceEdge, ...]:
        params: list[object] = [org_id]
        user_clause = ""
        if user_id is not None:
            user_clause = "AND user_id = %s"
            params.append(user_id)
        async with self._parent._tenant_connection(org_id=org_id) as conn:  # type: ignore[attr-defined]
            cursor = await conn.execute(
                f"""
                SELECT *
                  FROM runtime_artifact_reference_edges
                 WHERE org_id = %s {user_clause}
                 ORDER BY edge_id
                """,
                tuple(params),
            )
            rows = await cursor.fetchall()
        return tuple(ArtifactReferenceEdge.model_validate(row) for row in rows)


__all__ = (
    "ArtifactReferenceEdge",
    "ArtifactReferenceKind",
    "ArtifactReferenceRepositoryPort",
    "FileArtifactReferenceStore",
    "InMemoryArtifactReferenceStore",
    "PostgresArtifactReferenceStore",
    "artifact_revision_reference_edge",
)
