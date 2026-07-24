"""Postgres-authoritative global artifact quarantine and reaping."""

from __future__ import annotations

import os
from datetime import datetime, timezone

from agent_runtime.artifacts.contracts import ArtifactGcCandidate
from runtime_adapters._artifact_repository import ArtifactQuarantineReapResult
from runtime_adapters.file._paths import FileStoreLayout
from runtime_adapters.file.artifact_blob_store import FileArtifactBlobStore
from runtime_adapters.postgres.artifact_publication import (
    acquire_artifact_advisory_lock,
)


class PostgresArtifactGarbageCollector:
    """Use worker-global reference checks and a digest-global lock."""

    # A publication can crash before the application knows which tenant will
    # own it.  The worker role is allowed to sweep this synthetic provenance;
    # it is never surfaced as a customer tenant.
    ORPHAN_RECOVERY_ORG_ID = "__artifact_orphan_recovery__"

    def __init__(self, parent: object, blob_store: FileArtifactBlobStore) -> None:
        self._parent = parent
        self._blob_store = blob_store

    def has_pending_publications(self) -> bool:
        """Whether the durable shared-volume manifest needs a worker sweep."""

        with self._blob_store.coordinator.locked():
            return bool(
                self._blob_store.coordinator.discover_active_candidates_locked()
            )

    async def discover_orphaned_publications(
        self,
        *,
        provenance_org_id: str,
        older_than: datetime,
        limit: int,
    ) -> tuple[ArtifactGcCandidate, ...]:
        """Project durable physical publication manifests into GC candidates.

        The filesystem manifest is written before ``os.replace`` by the blob
        adapter.  Here, the worker takes the *same* digest advisory lock used
        by metadata commits and GC, then either proves a reference exists and
        retires the manifest, or records an ordinary durable candidate.  A
        metadata commit racing this scan can therefore only commit first, or
        restore a deterministic quarantine after the scan wins; it can never
        lose active bytes to a blind orphan reaper.
        """

        coordinator = self._blob_store.coordinator
        with coordinator.locked():
            discovered = coordinator.discover_active_candidates_locked()
        candidates: list[ArtifactGcCandidate] = []
        for blob_key, state in discovered[: max(1, limit)]:
            if state.candidate_since > older_than:
                continue
            referenced = False
            async with self._parent._role_connection("worker") as conn:  # type: ignore[attr-defined]
                async with conn.transaction():
                    await acquire_artifact_advisory_lock(conn, blob_key=blob_key)
                    referenced = await self._has_global_reference(conn, blob_key)
                    if referenced:
                        await conn.execute(
                            "DELETE FROM runtime_artifact_gc_candidates WHERE blob_key = %s",
                            (blob_key,),
                        )
                    else:
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
                                provenance_org_id,
                                blob_key,
                                state.candidate_since,
                            ),
                        )
            if referenced:
                # The ref was observed while holding the digest transaction
                # lock.  It is now safe to retire only the physical manifest;
                # no content is moved or removed on this path.
                with coordinator.locked():
                    coordinator.cancel_candidate_locked(blob_key)
                continue
            candidates.append(
                ArtifactGcCandidate(
                    blob_key=blob_key,
                    unreferenced_since=state.candidate_since,
                )
            )
        return tuple(candidates)

    async def collect_if_unreferenced(
        self,
        *,
        org_id: str,
        candidate: ArtifactGcCandidate,
        grace_before: datetime,
    ) -> bool:
        if candidate.unreferenced_since > grace_before:
            return False
        coordinator = self._blob_store.coordinator
        active = coordinator.layout.object_path(candidate.blob_key)
        quarantine = coordinator.quarantine_path(candidate.blob_key)
        moved = False
        try:
            async with self._parent._role_connection("worker") as conn:  # type: ignore[attr-defined]
                async with conn.transaction():
                    await acquire_artifact_advisory_lock(
                        conn, blob_key=candidate.blob_key
                    )
                    cursor = await conn.execute(
                        """
                        SELECT candidate_since
                          FROM runtime_artifact_gc_candidates
                         WHERE provenance_org_id = %s AND blob_key = %s
                        """,
                        (org_id, candidate.blob_key),
                    )
                    candidate_row = await cursor.fetchone()
                    if (
                        candidate_row is None
                        or candidate_row["candidate_since"]
                        != candidate.unreferenced_since
                        or candidate_row["candidate_since"] > grace_before
                    ):
                        return False
                    if await self._has_global_reference(conn, candidate.blob_key):
                        await conn.execute(
                            "DELETE FROM runtime_artifact_gc_candidates WHERE blob_key = %s",
                            (candidate.blob_key,),
                        )
                        with coordinator.locked():
                            coordinator.cancel_candidate_locked(candidate.blob_key)
                        return False
                    with coordinator.locked():
                        if quarantine.exists():
                            coordinator.mark_quarantined_locked(
                                blob_key=candidate.blob_key,
                                quarantined_at=datetime.fromtimestamp(
                                    quarantine.stat().st_mtime,
                                    tz=timezone.utc,
                                ),
                            )
                        else:
                            try:
                                modified = datetime.fromtimestamp(
                                    active.stat().st_mtime, tz=timezone.utc
                                )
                            except FileNotFoundError:
                                return False
                            if modified > grace_before:
                                return False
                            FileStoreLayout.ensure_dir(quarantine.parent)
                            os.replace(active, quarantine)
                            coordinator._fsync_directory(active.parent)
                            coordinator._fsync_directory(quarantine.parent)
                            moved = True
                            coordinator.mark_quarantined_locked(
                                blob_key=candidate.blob_key,
                                quarantined_at=datetime.now(timezone.utc),
                            )
                    await conn.execute(
                        """
                        INSERT INTO runtime_artifact_gc_quarantine (
                            blob_key, provenance_org_id, candidate_since,
                            quarantined_at, reaping_at
                        ) VALUES (%s, %s, %s, now(), NULL)
                        ON CONFLICT (blob_key) DO UPDATE
                            SET provenance_org_id =
                                    EXCLUDED.provenance_org_id,
                                candidate_since = LEAST(
                                    runtime_artifact_gc_quarantine.candidate_since,
                                    EXCLUDED.candidate_since
                                ),
                                quarantined_at = EXCLUDED.quarantined_at,
                                reaping_at = NULL
                        """,
                        (
                            candidate.blob_key,
                            org_id,
                            candidate.unreferenced_since,
                        ),
                    )
            return True
        except BaseException:
            if moved:
                with coordinator.locked():
                    coordinator.restore_locked(candidate.blob_key)
            raise

    async def reap_quarantine(
        self,
        *,
        older_than: datetime,
        limit: int,
    ) -> ArtifactQuarantineReapResult:
        async with self._parent._role_connection("worker") as conn:  # type: ignore[attr-defined]
            cursor = await conn.execute(
                """
                SELECT blob_key
                  FROM runtime_artifact_gc_quarantine
                 WHERE quarantined_at < %s
                 ORDER BY quarantined_at ASC, blob_key ASC
                 LIMIT %s
                """,
                (older_than, limit),
            )
            rows = await cursor.fetchall()
        reaped: list[str] = []
        restored: list[str] = []
        for row in rows:
            blob_key = row["blob_key"]
            outcome = await self._reap_one(blob_key=blob_key, older_than=older_than)
            if outcome == "reaped":
                reaped.append(blob_key)
            elif outcome == "restored":
                restored.append(blob_key)
        return ArtifactQuarantineReapResult(
            reaped_blob_keys=tuple(reaped),
            restored_blob_keys=tuple(restored),
        )

    async def _reap_one(self, *, blob_key: str, older_than: datetime) -> str | None:
        coordinator = self._blob_store.coordinator
        quarantine = coordinator.quarantine_path(blob_key)
        reaping = coordinator.reaping_path(blob_key)
        active = coordinator.layout.object_path(blob_key)
        moved_to_reaping = False
        referenced = False
        try:
            async with self._parent._role_connection("worker") as conn:  # type: ignore[attr-defined]
                async with conn.transaction():
                    await acquire_artifact_advisory_lock(conn, blob_key=blob_key)
                    cursor = await conn.execute(
                        """
                        SELECT quarantined_at
                          FROM runtime_artifact_gc_quarantine
                         WHERE blob_key = %s
                         FOR UPDATE
                        """,
                        (blob_key,),
                    )
                    state = await cursor.fetchone()
                    if state is None or state["quarantined_at"] >= older_than:
                        return None
                    referenced = await self._has_global_reference(conn, blob_key)
                    if referenced:
                        with coordinator.locked():
                            coordinator.restore_locked(blob_key)
                            coordinator.cancel_candidate_locked(blob_key)
                        await self._clear_state(conn, blob_key)
                    else:
                        with coordinator.locked():
                            if not quarantine.exists() and reaping.exists():
                                pass
                            elif quarantine.exists():
                                FileStoreLayout.ensure_dir(reaping.parent)
                                os.replace(quarantine, reaping)
                                coordinator._fsync_directory(quarantine.parent)
                                coordinator._fsync_directory(reaping.parent)
                                moved_to_reaping = True
                            elif active.exists():
                                await self._clear_state(conn, blob_key)
                                return "restored"
                            else:
                                await self._clear_state(conn, blob_key)
                                return "reaped"
                        await conn.execute(
                            """
                            UPDATE runtime_artifact_gc_quarantine
                               SET reaping_at = now()
                             WHERE blob_key = %s
                            """,
                            (blob_key,),
                        )
            if referenced:
                return "restored"
            with coordinator.locked():
                if reaping.exists():
                    reaping.unlink()
                    coordinator._fsync_directory(reaping.parent)
                elif active.exists():
                    return "restored"
                integrity = self._blob_store.integrity_path(blob_key)
                try:
                    integrity.unlink()
                    coordinator._fsync_directory(integrity.parent)
                except FileNotFoundError:
                    pass
                coordinator.clear_reaped_locked(blob_key)
            async with self._parent._role_connection("worker") as conn:  # type: ignore[attr-defined]
                async with conn.transaction():
                    await acquire_artifact_advisory_lock(conn, blob_key=blob_key)
                    if await self._has_global_reference(conn, blob_key):
                        # Publication can only have committed after restoring
                        # the deterministic reaping path, so active bytes exist.
                        return "restored"
                    await self._clear_state(conn, blob_key)
            return "reaped"
        except BaseException:
            if moved_to_reaping:
                with coordinator.locked():
                    if reaping.exists() and not quarantine.exists():
                        FileStoreLayout.ensure_dir(quarantine.parent)
                        os.replace(reaping, quarantine)
                        coordinator._fsync_directory(reaping.parent)
                        coordinator._fsync_directory(quarantine.parent)
            raise

    @staticmethod
    async def _has_global_reference(conn, blob_key: str) -> bool:
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
            (blob_key, blob_key),
        )
        row = await cursor.fetchone()
        return bool(row and row["has_reference"])

    @staticmethod
    async def _clear_state(conn, blob_key: str) -> None:
        await conn.execute(
            "DELETE FROM runtime_artifact_gc_quarantine WHERE blob_key = %s",
            (blob_key,),
        )
        await conn.execute(
            "DELETE FROM runtime_artifact_gc_candidates WHERE blob_key = %s",
            (blob_key,),
        )


__all__ = ("PostgresArtifactGarbageCollector",)
