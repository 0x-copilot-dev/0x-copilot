"""File-backed ``ConversationToolOrdinalStorePort`` — durable ordinal bindings.

Bindings are journaled append-only to ``state/tool_ordinals.jsonl`` and folded
into the primary-key + tool-call-id maps on construction. Constraint semantics
(idempotent retry / conflict) mirror the in-memory and Postgres adapters.
"""

from __future__ import annotations

from collections.abc import Sequence
from threading import RLock

from agent_runtime.persistence.ports import ConversationOrdinalConflict
from agent_runtime.persistence.records import ToolOrdinalBindingRecord
from runtime_adapters.file._paths import FileStoreLayout
from runtime_adapters.file._state_ledger import StateLedger


class FileConversationToolOrdinalStore:
    """Durable, single-writer ordinal-binding store backed by one ledger."""

    _TABLE = "tool_ordinals"

    def __init__(self, layout: FileStoreLayout) -> None:
        self._lock = RLock()
        self._ledger = StateLedger(layout.state_path(self._TABLE))
        self._by_pk: dict[tuple[str, int], ToolOrdinalBindingRecord] = {}
        self._by_tool_call_id: dict[tuple[str, str], int] = {}
        self._load()

    def _load(self) -> None:
        for record_json in self._ledger.load_puts():
            record = ToolOrdinalBindingRecord.model_validate(record_json)
            self._by_pk[(record.conversation_id, record.conversation_ordinal)] = record
            self._by_tool_call_id[(record.conversation_id, record.tool_call_id)] = (
                record.conversation_ordinal
            )

    @property
    def rows(self) -> tuple[ToolOrdinalBindingRecord, ...]:
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
        with self._lock:
            existing_ordinal = self._by_tool_call_id.get(
                (conversation_id, tool_call_id)
            )
            if existing_ordinal is not None:
                existing = self._by_pk[(conversation_id, existing_ordinal)]
                if existing_ordinal == conversation_ordinal:
                    return existing
                raise ConversationOrdinalConflict(
                    conversation_id=conversation_id,
                    tool_call_id=tool_call_id,
                    attempted_ordinal=conversation_ordinal,
                    existing_ordinal=existing_ordinal,
                )
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
            self._by_tool_call_id[(conversation_id, tool_call_id)] = (
                conversation_ordinal
            )
            self._ledger.append_put(record.model_dump(mode="json"))
            return record

    async def load(
        self, *, org_id: str, conversation_id: str
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


__all__ = ("FileConversationToolOrdinalStore",)
