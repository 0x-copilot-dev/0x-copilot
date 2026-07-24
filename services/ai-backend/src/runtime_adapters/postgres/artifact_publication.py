"""Postgres advisory-lock and shared-volume quarantine coordination."""

from __future__ import annotations

import hashlib
import os
from datetime import datetime, timezone

from runtime_adapters.file.artifact_blob_store import FileArtifactBlobStore


def artifact_advisory_lock_key(blob_key: str) -> int:
    """Return a stable signed bigint for ``pg_advisory_xact_lock``."""

    raw = hashlib.sha256(blob_key.encode()).digest()[:8]
    return int.from_bytes(raw, byteorder="big", signed=True)


def artifact_scope_advisory_lock_key(org_id: str) -> int:
    """Stable org-level key used to quiesce account merges."""

    raw = hashlib.sha256(f"artifact-scope:{org_id}".encode()).digest()[:8]
    return int.from_bytes(raw, byteorder="big", signed=True)


async def acquire_artifact_scope_lock(conn, *, org_id: str) -> None:
    """Share-lock normal artifact work against an exclusive account merge."""

    await conn.execute(
        "SELECT pg_advisory_xact_lock_shared(%s)",
        (artifact_scope_advisory_lock_key(org_id),),
    )


async def acquire_artifact_advisory_lock(
    conn,
    *,
    blob_key: str,
) -> None:
    await conn.execute(
        "SELECT pg_advisory_xact_lock(%s)",
        (artifact_advisory_lock_key(blob_key),),
    )


async def restore_gc_quarantine(
    conn,
    *,
    blob_key: str,
    blob_store: FileArtifactBlobStore,
    preserve_candidate: bool = False,
) -> bool:
    """Restore bytes and clear durable quarantine state in this transaction."""

    with blob_store.coordinator.locked():
        restored = blob_store.coordinator.restore_locked(blob_key)
        blob_store.coordinator.require_active_locked(blob_key)
    stat = await blob_store.stat(blob_key)
    if stat.blob_key != blob_key:
        raise FileNotFoundError("artifact blob is unavailable")
    await conn.execute(
        """
        DELETE FROM runtime_artifact_gc_quarantine
         WHERE blob_key = %s
        """,
        (blob_key,),
    )
    if not preserve_candidate:
        await conn.execute(
            """
            DELETE FROM runtime_artifact_gc_candidates
             WHERE blob_key = %s
            """,
            (blob_key,),
        )
    return restored


def restore_quarantine_after_rollback(
    *,
    blob_key: str,
    blob_store: FileArtifactBlobStore,
) -> None:
    """Put transactionally restored bytes back when the DB commit fails."""

    coordinator = blob_store.coordinator
    with coordinator.locked():
        active = coordinator.layout.object_path(blob_key)
        quarantine = coordinator.quarantine_path(blob_key)
        if not active.exists() or quarantine.exists():
            return
        type(coordinator.layout).ensure_dir(quarantine.parent)
        os.replace(active, quarantine)
        coordinator._fsync_directory(active.parent)
        coordinator._fsync_directory(quarantine.parent)
        coordinator.mark_quarantined_locked(
            blob_key=blob_key,
            quarantined_at=datetime.now(timezone.utc),
        )


__all__ = (
    "acquire_artifact_advisory_lock",
    "acquire_artifact_scope_lock",
    "artifact_advisory_lock_key",
    "artifact_scope_advisory_lock_key",
    "restore_gc_quarantine",
    "restore_quarantine_after_rollback",
)
