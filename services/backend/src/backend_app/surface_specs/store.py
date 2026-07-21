"""Persistence adapters for the SurfaceSpec registry (PRD-08).

In-memory adapter mirrors the Postgres adapter semantics so the same
service-level tests cover both. Postgres adapter speaks SQL against
``services/backend/migrations/0041_surface_specs.sql``.

Storage identity is ``(org_id, server, tool, output_shape_hash,
spec_schema_version, skill_version, origin)`` — org-scoped (no cross-org
reads), unique per origin so a ``curated-override`` and the ``generated`` spec
for the same key can coexist, with the override winning on read. ``upsert``
replaces the row for that exact identity (PUT is idempotent on the full key +
origin).
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Protocol

from backend_app.surface_specs.contracts import SurfaceSpecOrigin, SurfaceSpecRecord


# The org-independent identity tuple plus origin. ``org_id`` partitions the
# whole store, so it is the outer dict key rather than part of this inner one.
_Identity = tuple[str, str, str, int, int, str]


def _identity(record: SurfaceSpecRecord) -> _Identity:
    return (
        record.server,
        record.tool,
        record.output_shape_hash,
        record.spec_schema_version,
        record.skill_version,
        record.origin.value,
    )


def _pick_best(records: list[SurfaceSpecRecord]) -> SurfaceSpecRecord | None:
    """Return the winning record: curated-override outranks generated, then newest."""

    if not records:
        return None
    return max(records, key=lambda r: (r.origin.precedence, r.created_at))


class SurfaceSpecStore(Protocol):
    """Adapter contract — every adapter implements every method."""

    @contextmanager
    def transaction(self) -> Iterator[Any]: ...  # pragma: no cover

    def upsert(
        self,
        record: SurfaceSpecRecord,
        *,
        conn: Any | None = None,
    ) -> SurfaceSpecRecord: ...

    def get_by_key(
        self,
        *,
        org_id: str,
        server: str,
        tool: str,
        output_shape_hash: str,
        spec_schema_version: int,
        skill_version: int,
    ) -> SurfaceSpecRecord | None: ...

    def get_latest_by_tool(
        self,
        *,
        org_id: str,
        server: str,
        tool: str,
    ) -> SurfaceSpecRecord | None: ...

    def get_by_id(self, *, org_id: str, spec_id: str) -> SurfaceSpecRecord | None: ...

    def delete(self, *, org_id: str, spec_id: str, conn: Any | None = None) -> bool: ...


@dataclass
class InMemorySurfaceSpecStore:
    """Dict-backed adapter for dev + tests. Mirrors postgres semantics."""

    # org_id -> identity -> record
    _rows: dict[str, dict[_Identity, SurfaceSpecRecord]] = field(default_factory=dict)

    @contextmanager
    def transaction(self) -> Iterator[None]:
        yield None

    def upsert(
        self,
        record: SurfaceSpecRecord,
        *,
        conn: Any | None = None,
    ) -> SurfaceSpecRecord:
        del conn
        partition = self._rows.setdefault(record.org_id, {})
        existing = partition.get(_identity(record))
        # Preserve the original spec_id + created_at on an update so the row's
        # identity is stable across upserts of the same key.
        if existing is not None:
            record = record.model_copy(
                update={
                    "spec_id": existing.spec_id,
                    "created_at": existing.created_at,
                }
            )
        partition[_identity(record)] = record
        return record

    def get_by_key(
        self,
        *,
        org_id: str,
        server: str,
        tool: str,
        output_shape_hash: str,
        spec_schema_version: int,
        skill_version: int,
    ) -> SurfaceSpecRecord | None:
        partition = self._rows.get(org_id, {})
        matches = [
            record
            for record in partition.values()
            if record.server == server
            and record.tool == tool
            and record.output_shape_hash == output_shape_hash
            and record.spec_schema_version == spec_schema_version
            and record.skill_version == skill_version
        ]
        return _pick_best(matches)

    def get_latest_by_tool(
        self,
        *,
        org_id: str,
        server: str,
        tool: str,
    ) -> SurfaceSpecRecord | None:
        partition = self._rows.get(org_id, {})
        matches = [
            record
            for record in partition.values()
            if record.server == server and record.tool == tool
        ]
        return _pick_best(matches)

    def get_by_id(self, *, org_id: str, spec_id: str) -> SurfaceSpecRecord | None:
        partition = self._rows.get(org_id, {})
        for record in partition.values():
            if record.spec_id == spec_id:
                return record
        return None

    def delete(self, *, org_id: str, spec_id: str, conn: Any | None = None) -> bool:
        del conn
        partition = self._rows.get(org_id)
        if not partition:
            return False
        target = next(
            (
                identity
                for identity, record in partition.items()
                if record.spec_id == spec_id
            ),
            None,
        )
        if target is None:
            return False
        del partition[target]
        return True


class PostgresSurfaceSpecStore:
    """Postgres-backed adapter for the ``surface_specs`` table.

    Mirrors the in-memory adapter's semantics row-for-row. All reads and the
    delete are org-scoped in SQL (``WHERE org_id = %s``) so tenant isolation is
    enforced at the query layer in addition to the table's RLS policy.
    """

    def __init__(self, pool: Any) -> None:
        self._pool = pool

    @contextmanager
    def transaction(self) -> Iterator[Any]:
        with self._pool.connection() as conn:
            with conn.transaction():
                yield conn

    @contextmanager
    def _cursor(self, conn: Any | None) -> Iterator[Any]:
        if conn is not None:
            with conn.cursor() as cur:
                yield cur
            return
        with self._pool.connection() as owned:
            with owned.cursor() as cur:
                yield cur

    def upsert(
        self,
        record: SurfaceSpecRecord,
        *,
        conn: Any | None = None,
    ) -> SurfaceSpecRecord:
        with self._cursor(conn) as cur:
            cur.execute(
                """
                INSERT INTO surface_specs (
                    spec_id, org_id, user_id, server, tool, output_shape_hash,
                    spec_schema_version, skill_version, origin, generator_model,
                    spec, created_at
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s
                )
                ON CONFLICT (
                    org_id, server, tool, output_shape_hash,
                    spec_schema_version, skill_version, origin
                ) DO UPDATE SET
                    spec = EXCLUDED.spec,
                    generator_model = EXCLUDED.generator_model,
                    user_id = EXCLUDED.user_id
                RETURNING spec_id, org_id, user_id, server, tool,
                          output_shape_hash, spec_schema_version, skill_version,
                          origin, generator_model, spec, created_at
                """,
                (
                    record.spec_id,
                    record.org_id,
                    record.user_id,
                    record.server,
                    record.tool,
                    record.output_shape_hash,
                    record.spec_schema_version,
                    record.skill_version,
                    record.origin.value,
                    record.generator_model,
                    json.dumps(record.spec),
                    record.created_at,
                ),
            )
            row = cur.fetchone()
        return _row_to_record(row)

    def get_by_key(
        self,
        *,
        org_id: str,
        server: str,
        tool: str,
        output_shape_hash: str,
        spec_schema_version: int,
        skill_version: int,
    ) -> SurfaceSpecRecord | None:
        with self._cursor(None) as cur:
            cur.execute(
                """
                SELECT spec_id, org_id, user_id, server, tool,
                       output_shape_hash, spec_schema_version, skill_version,
                       origin, generator_model, spec, created_at
                FROM surface_specs
                WHERE org_id = %s AND server = %s AND tool = %s
                  AND output_shape_hash = %s AND spec_schema_version = %s
                  AND skill_version = %s
                ORDER BY (origin = 'curated-override') DESC, created_at DESC
                LIMIT 1
                """,
                (
                    org_id,
                    server,
                    tool,
                    output_shape_hash,
                    spec_schema_version,
                    skill_version,
                ),
            )
            row = cur.fetchone()
        return _row_to_record(row) if row is not None else None

    def get_latest_by_tool(
        self,
        *,
        org_id: str,
        server: str,
        tool: str,
    ) -> SurfaceSpecRecord | None:
        with self._cursor(None) as cur:
            cur.execute(
                """
                SELECT spec_id, org_id, user_id, server, tool,
                       output_shape_hash, spec_schema_version, skill_version,
                       origin, generator_model, spec, created_at
                FROM surface_specs
                WHERE org_id = %s AND server = %s AND tool = %s
                ORDER BY (origin = 'curated-override') DESC, created_at DESC
                LIMIT 1
                """,
                (org_id, server, tool),
            )
            row = cur.fetchone()
        return _row_to_record(row) if row is not None else None

    def get_by_id(self, *, org_id: str, spec_id: str) -> SurfaceSpecRecord | None:
        with self._cursor(None) as cur:
            cur.execute(
                """
                SELECT spec_id, org_id, user_id, server, tool,
                       output_shape_hash, spec_schema_version, skill_version,
                       origin, generator_model, spec, created_at
                FROM surface_specs
                WHERE org_id = %s AND spec_id = %s
                """,
                (org_id, spec_id),
            )
            row = cur.fetchone()
        return _row_to_record(row) if row is not None else None

    def delete(self, *, org_id: str, spec_id: str, conn: Any | None = None) -> bool:
        with self._cursor(conn) as cur:
            cur.execute(
                "DELETE FROM surface_specs WHERE org_id = %s AND spec_id = %s",
                (org_id, spec_id),
            )
            return cur.rowcount > 0


def _row_to_record(row: Any) -> SurfaceSpecRecord:
    record = dict(row)
    raw_spec = record.get("spec")
    if isinstance(raw_spec, str):
        record["spec"] = json.loads(raw_spec) if raw_spec else {}
    elif isinstance(raw_spec, (bytes, bytearray)):
        record["spec"] = json.loads(bytes(raw_spec).decode("utf-8"))
    origin = record.get("origin")
    if isinstance(origin, str):
        record["origin"] = SurfaceSpecOrigin(origin)
    return SurfaceSpecRecord.model_validate(record)


__all__ = [
    "InMemorySurfaceSpecStore",
    "PostgresSurfaceSpecStore",
    "SurfaceSpecStore",
]
