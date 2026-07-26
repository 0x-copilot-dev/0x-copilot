"""Durable PostgreSQL implementation of the C1 workspace overlay store.

The workspace port is intentionally scoped only by ``run_id``: callers in the
worker never receive a tenant-selected persistence handle.  This adapter
therefore derives the tenant from the durable ``agent_runs`` parent inside the
same transaction that creates or advances an overlay.  The database enforces
that derived ``(org_id, run_id)`` pair with a composite foreign key, so an
overlay can neither be written for an unknown run nor drift to another
tenant during an account re-key.

One JSONB manifest per run mirrors the file and in-memory adapters' immutable
model.  ``SELECT … FOR UPDATE`` plus the expected-version comparison is the
cross-process CAS boundary; no workspace bytes or host paths are persisted.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence

from psycopg.types.json import Jsonb

from agent_runtime.capabilities.workspace.contracts import (
    OverlayManifest,
    OverlayMutation,
    OverlayMutationKind,
)
from agent_runtime.capabilities.workspace.errors import WorkspaceOverlayConflictError

_TABLE = "runtime_workspace_overlay_manifests"
_WORKER_ROLE = "worker"
_COLUMNS = "org_id, run_id, version, manifest_json, updated_at"


class PostgresWorkspaceOverlayStore:
    """A worker-owned, run-scoped overlay manifest repository.

    ``store`` is the existing :class:`PostgresRuntimeApiStore`.  Borrowing its
    worker-role connection keeps all runtime persistence in one pool and
    avoids granting a second component independent database credentials.
    """

    def __init__(self, store: object) -> None:
        self._store = store

    async def get_manifest(self, *, run_id: str) -> OverlayManifest:
        """Return the latest durable manifest, or the canonical empty manifest.

        A missing run has the same read semantics as file/in-memory backends:
        it has no overlay yet.  ``append_revision`` is deliberately stricter
        and cannot materialise an unscoped row for such an id.
        """

        try:
            async with self._store._role_connection(_WORKER_ROLE) as conn:
                cursor = await conn.execute(
                    f"""
                    SELECT {_COLUMNS}
                      FROM {_TABLE}
                     WHERE run_id = %s
                    """,
                    (run_id,),
                )
                row = await cursor.fetchone()
            if row is None:
                return OverlayManifest(run_id=run_id)
            return _manifest_from_row(row, run_id=run_id)
        except WorkspaceOverlayConflictError:
            raise
        except Exception as exc:  # pragma: no cover - requires a broken driver
            raise WorkspaceOverlayConflictError() from exc

    async def get_manifest_version(
        self, *, run_id: str, version: int
    ) -> OverlayManifest | None:
        """Refuse immutable D3 reads until C1 history has a durable SQL adapter.

        The current JSONB row intentionally represents only C1's live manifest.
        Returning that row for a requested version would silently turn a D3
        snapshot into a mutable-latest fallback, so this adapter fails closed.
        """

        del run_id, version
        return None

    async def append_revision(
        self,
        *,
        run_id: str,
        expected_version: int,
        mutations: Sequence[OverlayMutation],
    ) -> OverlayManifest:
        """Atomically apply one overlay revision when the CAS version matches."""

        try:
            async with self._store._role_connection(_WORKER_ROLE) as conn:  # noqa: SIM117 - transaction scopes only the mutation path
                async with conn.transaction():
                    org_id = await _org_for_run(conn, run_id=run_id)
                    if org_id is None:
                        # Unlike a read, persisting an unknown run would create
                        # state without a tenant anchor. Fail closed.
                        raise WorkspaceOverlayConflictError()

                    empty = OverlayManifest(run_id=run_id)
                    await conn.execute(
                        f"""
                        INSERT INTO {_TABLE} ({_COLUMNS})
                        VALUES (%s, %s, %s, %s::jsonb, %s)
                        ON CONFLICT (org_id, run_id) DO NOTHING
                        """,
                        (
                            org_id,
                            run_id,
                            empty.version,
                            Jsonb(empty.model_dump(mode="json")),
                            empty.updated_at,
                        ),
                    )
                    cursor = await conn.execute(
                        f"""
                        SELECT {_COLUMNS}
                          FROM {_TABLE}
                         WHERE org_id = %s AND run_id = %s
                         FOR UPDATE
                        """,
                        (org_id, run_id),
                    )
                    row = await cursor.fetchone()
                    if row is None:
                        raise WorkspaceOverlayConflictError()
                    current = _manifest_from_row(row, run_id=run_id)
                    if current.version != expected_version:
                        raise WorkspaceOverlayConflictError()

                    updated = _apply_revision(current, mutations=mutations)
                    cursor = await conn.execute(
                        f"""
                        UPDATE {_TABLE}
                           SET version = %s,
                               manifest_json = %s::jsonb,
                               updated_at = %s
                         WHERE org_id = %s
                           AND run_id = %s
                           AND version = %s
                        RETURNING {_COLUMNS}
                        """,
                        (
                            updated.version,
                            Jsonb(updated.model_dump(mode="json")),
                            updated.updated_at,
                            org_id,
                            run_id,
                            current.version,
                        ),
                    )
                    stored = await cursor.fetchone()
                    if stored is None:
                        raise WorkspaceOverlayConflictError()
                    return _manifest_from_row(stored, run_id=run_id)
        except WorkspaceOverlayConflictError:
            raise
        except Exception as exc:  # pragma: no cover - requires a broken driver
            raise WorkspaceOverlayConflictError() from exc

    async def compact(self, *, run_id: str) -> OverlayManifest:
        """A single canonical JSONB manifest is already compact."""

        return await self.get_manifest(run_id=run_id)


async def _org_for_run(conn: object, *, run_id: str) -> str | None:
    """Resolve and key-share the parent run while a revision is being written."""

    cursor = await conn.execute(  # type: ignore[attr-defined]
        """
        SELECT org_id
          FROM agent_runs
         WHERE id = %s
         FOR KEY SHARE
        """,
        (run_id,),
    )
    row = await cursor.fetchone()
    if row is None:
        return None
    try:
        return str(_field(row, "org_id", 0))
    except (KeyError, IndexError, TypeError) as exc:
        raise WorkspaceOverlayConflictError() from exc


def _apply_revision(
    current: OverlayManifest,
    *,
    mutations: Sequence[OverlayMutation],
) -> OverlayManifest:
    """Apply the same immutable-manifest transition as file/in-memory stores."""

    next_version = current.version + 1
    entries = {entry.virtual_path: entry for entry in current.entries}
    for mutation in mutations:
        if mutation.kind is OverlayMutationKind.REMOVE:
            entries.pop(mutation.virtual_path, None)
        elif mutation.entry is not None:
            entries[mutation.virtual_path] = mutation.entry.model_copy(
                update={"overlay_revision": next_version}
            )
    return OverlayManifest(
        run_id=current.run_id,
        version=next_version,
        entries=tuple(entries[path] for path in sorted(entries)),
    )


def _manifest_from_row(row: object, *, run_id: str) -> OverlayManifest:
    """Decode a persisted row and reject malformed or cross-run state."""

    try:
        persisted_run_id = str(_field(row, "run_id", 1))
        persisted_version = int(_field(row, "version", 2))
        raw_manifest = _field(row, "manifest_json", 3)
        if isinstance(raw_manifest, (str, bytes, bytearray)):
            raw_manifest = json.loads(raw_manifest)
        manifest = OverlayManifest.model_validate(raw_manifest)
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise WorkspaceOverlayConflictError() from exc
    if (
        persisted_run_id != run_id
        or manifest.run_id != run_id
        or manifest.version != persisted_version
    ):
        raise WorkspaceOverlayConflictError()
    return manifest


def _field(row: object, key: str, index: int) -> object:
    """Read either the runtime store's dict row or a test-driver positional row."""

    if isinstance(row, Mapping):
        return row[key]
    return row[index]  # type: ignore[index]


__all__ = ("PostgresWorkspaceOverlayStore",)
