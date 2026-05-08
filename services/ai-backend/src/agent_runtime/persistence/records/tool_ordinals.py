"""Persisted (conversation_ordinal ↔ tool_call_id) binding records (PR 04).

A :class:`ToolOrdinalBindingRecord` is the durable mirror of one row in
``agent_conversation_tool_ordinals`` (migration 0026). The
:class:`agent_runtime.capabilities.conversation_ordinals.ConversationOrdinalAllocator`
owns insertion through the
:class:`agent_runtime.persistence.ports.ConversationToolOrdinalStorePort`.

The binding is keyed two ways:

* ``(conversation_id, conversation_ordinal)`` — primary key. The ordinal
  is conversation-scoped by definition and the same ordinal value in
  two different conversations is a different binding.
* ``(conversation_id, tool_call_id)`` — unique constraint. The same
  tool_call_id always maps to the same ordinal; retried allocations
  (e.g. LangGraph re-dispatch after an approval pause) collapse to the
  existing row.

Both keys exist on the table; the allocator's UPSERT path uses the
``tool_call_id`` constraint so retries are idempotent.
"""

from __future__ import annotations

from datetime import datetime, timezone

from pydantic import Field, PositiveInt

from agent_runtime.execution.contracts import RuntimeContract


class ToolOrdinalBindingRecord(RuntimeContract):
    """One ``(conversation_ordinal → tool_call_id)`` binding row."""

    org_id: str = Field(min_length=1)
    conversation_id: str = Field(min_length=1)
    conversation_ordinal: PositiveInt
    tool_call_id: str = Field(min_length=1, max_length=128)
    tool_name: str = Field(min_length=1, max_length=128)
    run_id: str = Field(min_length=1)
    allocated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
