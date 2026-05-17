"""Postgres-backed ``TodoExtractionStorePort``.

Shares the parent ``PostgresRuntimeApiStore``'s pool and tenant-connection
helper so RLS gets a chance to enforce ``org_id`` isolation at the database
boundary; the in-tenant queries below also predicate ``org_id`` explicitly so
RLS-disabled environments stay safe.

Proposal text is not field-encrypted in this initial drop. The migration
declares the column as ``TEXT`` so adding ``FieldCodec`` encryption later is
purely additive (same path the draft store took before encryption landed).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from agent_runtime.persistence.records import (
    TodoExtractionRecord,
    TodoExtractionState,
)


_TABLE = "todo_extractions"


class PostgresTodoExtractionStore:
    """Postgres adapter for the ``todo_extractions`` table."""

    def __init__(self, parent: object) -> None:
        # ``parent`` is :class:`PostgresRuntimeApiStore` — we only call already
        # public helpers (``_tenant_connection``). Same indirection pattern
        # PostgresDraftStore / PostgresShareStore use.
        self._parent = parent

    async def insert_many(
        self, records: Sequence[TodoExtractionRecord]
    ) -> Sequence[TodoExtractionRecord]:
        """Insert a batch in one transaction; empty input is a no-op."""
        if not records:
            return ()
        # All rows in a batch must share an org — the worker job always
        # extracts from one run, which belongs to one tenant. Pin that
        # invariant explicitly so a future caller doesn't try to cross
        # the boundary by accident.
        org_id = records[0].org_id
        if any(r.org_id != org_id for r in records):
            raise ValueError("insert_many requires uniform org_id across records")

        rows = [
            (
                r.id,
                r.org_id,
                r.owner_user_id,
                r.run_id,
                r.conversation_id,
                r.proposed_text,
                r.suggested_due,
                r.suggested_project_id,
                r.source_message_id,
                r.confidence_score,
                r.state.value,
                r.created_at,
                r.resolved_at,
            )
            for r in records
        ]
        async with self._parent._tenant_connection(  # type: ignore[attr-defined]
            org_id=org_id
        ) as conn:
            async with conn.transaction():
                async with conn.cursor() as cur:
                    await cur.executemany(
                        f"""
                        INSERT INTO {_TABLE}
                            (id, org_id, owner_user_id, run_id, conversation_id,
                             proposed_text, suggested_due, suggested_project_id,
                             source_message_id, confidence_score, state,
                             created_at, resolved_at)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        rows,
                    )
        return tuple(records)

    async def get_by_id(
        self, *, org_id: str, extraction_id: str
    ) -> TodoExtractionRecord | None:
        """Tenant-scoped point read."""
        async with self._parent._tenant_connection(  # type: ignore[attr-defined]
            org_id=org_id
        ) as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    f"""
                    SELECT id, org_id, owner_user_id, run_id, conversation_id,
                           proposed_text, suggested_due, suggested_project_id,
                           source_message_id, confidence_score, state,
                           created_at, resolved_at
                      FROM {_TABLE}
                     WHERE org_id = %s AND id = %s
                    """,
                    (org_id, extraction_id),
                )
                row = await cur.fetchone()
        return self._from_row(row) if row else None

    async def list_pending(
        self,
        *,
        org_id: str,
        owner_user_id: str,
        limit: int,
    ) -> Sequence[TodoExtractionRecord]:
        """Tenant-and-owner-scoped pending list."""
        if limit <= 0:
            return ()
        async with self._parent._tenant_connection(  # type: ignore[attr-defined]
            org_id=org_id
        ) as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    f"""
                    SELECT id, org_id, owner_user_id, run_id, conversation_id,
                           proposed_text, suggested_due, suggested_project_id,
                           source_message_id, confidence_score, state,
                           created_at, resolved_at
                      FROM {_TABLE}
                     WHERE org_id = %s
                       AND owner_user_id = %s
                       AND state = %s
                     ORDER BY created_at DESC
                     LIMIT %s
                    """,
                    (org_id, owner_user_id, TodoExtractionState.PENDING.value, limit),
                )
                rows = await cur.fetchall()
        return tuple(self._from_row(row) for row in rows)

    async def update_state(
        self,
        *,
        org_id: str,
        extraction_id: str,
        state: TodoExtractionState,
        resolved_at: datetime,
    ) -> TodoExtractionRecord | None:
        """Update the lifecycle column. Returns the updated row or ``None``."""
        async with self._parent._tenant_connection(  # type: ignore[attr-defined]
            org_id=org_id
        ) as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    f"""
                    UPDATE {_TABLE}
                       SET state = %s, resolved_at = %s
                     WHERE org_id = %s AND id = %s
                 RETURNING id, org_id, owner_user_id, run_id, conversation_id,
                           proposed_text, suggested_due, suggested_project_id,
                           source_message_id, confidence_score, state,
                           created_at, resolved_at
                    """,
                    (state.value, resolved_at, org_id, extraction_id),
                )
                row = await cur.fetchone()
        return self._from_row(row) if row else None

    @staticmethod
    def _from_row(row: tuple[object, ...]) -> TodoExtractionRecord:
        """Translate a tuple from the cursor into a typed record."""
        return TodoExtractionRecord(
            id=row[0],
            org_id=row[1],
            owner_user_id=row[2],
            run_id=row[3],
            conversation_id=row[4],
            proposed_text=row[5],
            suggested_due=row[6],
            suggested_project_id=row[7],
            source_message_id=row[8],
            confidence_score=float(row[9]) if row[9] is not None else 0.0,
            state=TodoExtractionState(row[10]),
            created_at=row[11],
            resolved_at=row[12],
        )
