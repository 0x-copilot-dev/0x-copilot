"""Postgres-backed ``ConversationToolOrdinalStorePort``."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from psycopg import errors as psycopg_errors
from psycopg.rows import dict_row

from agent_runtime.persistence.ports import ConversationOrdinalConflict
from agent_runtime.persistence.records import ToolOrdinalBindingRecord


_TABLE = "agent_conversation_tool_ordinals"


class _BindingRowDecoder:
    """Translate one Postgres dict_row into a :class:`ToolOrdinalBindingRecord`."""

    @classmethod
    def decode(cls, row: dict[str, object]) -> ToolOrdinalBindingRecord:
        """Decode a raw Postgres row dict into a typed binding record."""
        return ToolOrdinalBindingRecord(
            org_id=str(row["org_id"]),
            conversation_id=str(row["conversation_id"]),
            conversation_ordinal=int(row["conversation_ordinal"]),  # type: ignore[arg-type]
            tool_call_id=str(row["tool_call_id"]),
            tool_name=str(row["tool_name"]),
            run_id=str(row["run_id"]),
            allocated_at=cls._coerce_datetime(row["allocated_at"]),
        )

    @staticmethod
    def _coerce_datetime(value: object) -> datetime:
        """Return the value as a datetime, parsing from ISO 8601 string if needed."""
        if isinstance(value, datetime):
            return value
        return datetime.fromisoformat(str(value))


class PostgresConversationToolOrdinalStore:
    """Postgres-backed ``ConversationToolOrdinalStorePort``."""

    def __init__(self, parent: object) -> None:
        self._parent = parent

    async def record(
        self,
        *,
        org_id: str,
        conversation_id: str,
        conversation_ordinal: int,
        tool_call_id: str,
        tool_name: str,
        run_id: str,
    ) -> ToolOrdinalBindingRecord:
        """Persist a tool-call ordinal binding; idempotent on same (call_id, ordinal), raises on conflict."""
        if conversation_ordinal <= 0:
            raise ValueError("conversation_ordinal must be a positive integer")
        if not tool_call_id:
            raise ValueError("tool_call_id must be a non-empty string")

        # ``ON CONFLICT (conversation_id, tool_call_id) DO NOTHING`` makes
        # the same-call_id retry path idempotent: we INSERT, and either
        # we get the new row back via RETURNING or the conflict swallows
        # the write and we fall through to the SELECT below to read the
        # canonical row. The same-(conversation_id, ordinal) collision —
        # different call_ids racing for the same ordinal — surfaces as a
        # ``UniqueViolation`` on the primary key, which we translate into
        # :class:`ConversationOrdinalConflict`.
        async with self._parent._tenant_connection(org_id=org_id) as conn:  # type: ignore[attr-defined]
            try:
                async with conn.cursor(row_factory=dict_row) as cursor:
                    await cursor.execute(
                        f"""
                        INSERT INTO {_TABLE} (
                            org_id, conversation_id, conversation_ordinal,
                            tool_call_id, tool_name, run_id
                        )
                        VALUES (%s, %s, %s, %s, %s, %s)
                        ON CONFLICT (conversation_id, tool_call_id)
                        DO NOTHING
                        RETURNING *
                        """,
                        (
                            org_id,
                            conversation_id,
                            conversation_ordinal,
                            tool_call_id,
                            tool_name,
                            run_id,
                        ),
                    )
                    inserted = await cursor.fetchone()
                    if inserted is not None:
                        return _BindingRowDecoder.decode(dict(inserted))
                    # Conflict on (conversation_id, tool_call_id). Read the
                    # existing row to decide whether this is an idempotent
                    # retry (same ordinal) or a true conflict (different).
                    await cursor.execute(
                        f"""
                        SELECT *
                        FROM {_TABLE}
                        WHERE conversation_id = %s
                          AND tool_call_id = %s
                        """,
                        (conversation_id, tool_call_id),
                    )
                    existing = await cursor.fetchone()
            except psycopg_errors.UniqueViolation as exc:
                # Different call_ids racing for the same
                # (conversation_id, conversation_ordinal) — the primary
                # key catches it. Surface as a conflict so the allocator
                # reloads + retries with a fresh counter.
                raise ConversationOrdinalConflict(
                    conversation_id=conversation_id,
                    tool_call_id=tool_call_id,
                    attempted_ordinal=conversation_ordinal,
                    existing_ordinal=conversation_ordinal,
                ) from exc

        if existing is None:
            # Should not happen — DO NOTHING returned no row, but the
            # SELECT also found none. Treat as a conflict for safety.
            raise ConversationOrdinalConflict(
                conversation_id=conversation_id,
                tool_call_id=tool_call_id,
                attempted_ordinal=conversation_ordinal,
            )
        existing_record = _BindingRowDecoder.decode(dict(existing))
        if existing_record.conversation_ordinal == conversation_ordinal:
            return existing_record
        raise ConversationOrdinalConflict(
            conversation_id=conversation_id,
            tool_call_id=tool_call_id,
            attempted_ordinal=conversation_ordinal,
            existing_ordinal=existing_record.conversation_ordinal,
        )

    async def load(
        self,
        *,
        org_id: str,
        conversation_id: str,
    ) -> Sequence[ToolOrdinalBindingRecord]:
        """Return all bindings for a conversation ordered by ordinal ascending."""
        async with self._parent._tenant_connection(org_id=org_id) as conn:  # type: ignore[attr-defined]
            async with conn.cursor(row_factory=dict_row) as cursor:
                await cursor.execute(
                    f"""
                    SELECT *
                    FROM {_TABLE}
                    WHERE org_id = %s
                      AND conversation_id = %s
                    ORDER BY conversation_ordinal ASC
                    """,
                    (org_id, conversation_id),
                )
                rows = await cursor.fetchall()
        return tuple(_BindingRowDecoder.decode(dict(row)) for row in rows)


__all__ = ("PostgresConversationToolOrdinalStore",)
