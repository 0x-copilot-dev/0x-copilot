"""In-memory ``ConversationToolOrdinalStorePort`` for tests and dev (PR 04).

Mirrors the postgres adapter's contract:

* ``record(...)`` is idempotent on ``(conversation_id, tool_call_id)``.
  A retry for the same call_id returns the existing row unchanged.
* If the same ``tool_call_id`` is already bound to a *different*
  ordinal (concurrent allocator beat us), raise
  :class:`ConversationOrdinalConflict`.
* ``load(...)`` returns bindings sorted by ``conversation_ordinal``
  ascending.

The adapter is process-local. Concurrency comes from asyncio not
threading, so a plain dict is sufficient — no lock.
"""

from __future__ import annotations

from collections.abc import Sequence

from agent_runtime.persistence.ports import ConversationOrdinalConflict
from agent_runtime.persistence.records import ToolOrdinalBindingRecord


class InMemoryConversationToolOrdinalStore:
    """Deterministic in-memory implementation of the binding store port."""

    def __init__(self) -> None:
        # (conversation_id, conversation_ordinal) → record.
        # Mirrors the table's primary key.
        self._by_pk: dict[tuple[str, int], ToolOrdinalBindingRecord] = {}
        # (conversation_id, tool_call_id) → conversation_ordinal.
        # Mirrors the unique constraint that drives idempotency.
        self._by_tool_call_id: dict[tuple[str, str], int] = {}

    @property
    def rows(self) -> tuple[ToolOrdinalBindingRecord, ...]:
        """Snapshot of every persisted binding (test helper)."""

        return tuple(self._by_pk.values())

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
        if conversation_ordinal <= 0:
            raise ValueError("conversation_ordinal must be a positive integer")
        if not tool_call_id:
            raise ValueError("tool_call_id must be a non-empty string")
        existing_ordinal = self._by_tool_call_id.get((conversation_id, tool_call_id))
        if existing_ordinal is not None:
            existing = self._by_pk[(conversation_id, existing_ordinal)]
            # Same call_id + same ordinal → idempotent retry. Return the
            # canonical row without bumping anything.
            if existing_ordinal == conversation_ordinal:
                return existing
            # Same call_id but a *different* ordinal → a concurrent
            # allocator already won. Surface the conflict so the caller
            # can reload state and abandon the local ordinal.
            raise ConversationOrdinalConflict(
                conversation_id=conversation_id,
                tool_call_id=tool_call_id,
                attempted_ordinal=conversation_ordinal,
                existing_ordinal=existing_ordinal,
            )
        # Two different call_ids attempting the same ordinal also
        # violates the table's primary key. Surface as a conflict.
        if (conversation_id, conversation_ordinal) in self._by_pk:
            raise ConversationOrdinalConflict(
                conversation_id=conversation_id,
                tool_call_id=tool_call_id,
                attempted_ordinal=conversation_ordinal,
                existing_ordinal=conversation_ordinal,
            )
        record = ToolOrdinalBindingRecord(
            org_id=org_id,
            conversation_id=conversation_id,
            conversation_ordinal=conversation_ordinal,
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            run_id=run_id,
        )
        self._by_pk[(conversation_id, conversation_ordinal)] = record
        self._by_tool_call_id[(conversation_id, tool_call_id)] = conversation_ordinal
        return record

    async def load(
        self,
        *,
        org_id: str,
        conversation_id: str,
    ) -> Sequence[ToolOrdinalBindingRecord]:
        return tuple(
            sorted(
                (
                    record
                    for record in self._by_pk.values()
                    if record.org_id == org_id
                    and record.conversation_id == conversation_id
                ),
                key=lambda row: row.conversation_ordinal,
            )
        )


__all__ = ("InMemoryConversationToolOrdinalStore",)
