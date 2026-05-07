"""Conversation-scoped tool invocation ordinal allocator.

PR 1.1-rev2 — citations as model-declared, conversation-scoped pointers.

Each tool invocation in a conversation is assigned a monotonic
``conversation_ordinal`` at dispatch time. The ordinal is used by:

- the per-tool result-hinting wrappers that prepend
  ``[Tool call #N — cite as [[N]] when referencing this result.]``
  to the result text the model reads, so the model can declare which
  tool grounded any factual claim with a stable pointer;
- the ``CitationResolver`` that watches streamed assistant text for
  ``[[N]]`` markers and resolves them against the tool invocation log;
- the ``ToolObservationIndexBuilder`` that surfaces prior turns' tool
  calls into the model's context, so cross-turn citation continues to
  resolve.

The allocator is bound per run via ``ContextVar`` (mirroring
:class:`agent_runtime.capabilities.citations.CitationLedger`). At run
start, the ``ConversationOrdinalSeeder`` computes the starting ordinal
by counting ``TOOL_CALL_STARTED`` events persisted by prior runs in the
conversation's active branch — so a brand-new tool call in turn T gets
an ordinal strictly greater than any previously emitted in this thread.

When no allocator is bound (replay, eval harnesses, unit tests of inner
tools), :meth:`ConversationOrdinalAllocator.active` returns ``None`` and
the wrappers degrade to a no-op append. Citations are best-effort
decoration, never required for tool correctness.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from contextvars import ContextVar
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing-only imports
    from agent_runtime.api.ports import EventStorePort
    from runtime_api.schemas import RuntimeEventEnvelope


_LOGGER = logging.getLogger(__name__)


class ConversationOrdinalAllocator:
    """Per-conversation monotonic ordinal allocator for tool invocations.

    The allocator owns the in-memory counter for the active run. Its
    starting value is supplied by :class:`ConversationOrdinalSeeder` at
    bind time so the ordinal sequence continues from the prior runs of
    the same conversation/branch — making the chip a stable pointer
    across turns.
    """

    def __init__(
        self,
        *,
        conversation_id: str,
        starting_ordinal: int,
        ordinal_to_tool_call_id: dict[int, str] | None = None,
    ) -> None:
        if starting_ordinal < 0:
            raise ValueError("starting_ordinal must be non-negative")
        self._conversation_id = conversation_id
        self._counter = starting_ordinal
        # Bidirectional index: lets the resolver populate
        # ``CitationLink.source_tool_call_id`` from a parsed ``[[N]]``
        # token without an extra persistence lookup. Seeded with prior
        # turns' (ordinal, tool_call_id) pairs by
        # :class:`ConversationOrdinalSeeder`, then extended live as the
        # current run dispatches new tool calls via
        # :meth:`allocate_for_tool_call`.
        self._ordinal_to_tool_call_id: dict[int, str] = dict(
            ordinal_to_tool_call_id or {}
        )

    @property
    def conversation_id(self) -> str:
        return self._conversation_id

    @property
    def last_allocated(self) -> int:
        """Return the most recently allocated ordinal (0 before any allocate)."""

        return self._counter

    def allocate(self) -> int:
        """Allocate the next conversation_ordinal without binding a tool call.

        Used by callers that don't have a stable ``tool_call_id`` to
        register (e.g. provider-native passthrough adapters that fire
        before the tool message is materialized). Most call sites
        should prefer :meth:`allocate_for_tool_call`.
        """

        self._counter += 1
        return self._counter

    def allocate_for_tool_call(self, *, tool_call_id: str) -> int:
        """Allocate the next ordinal and bind it to ``tool_call_id``.

        Recording the binding lets the :class:`CitationResolver`
        populate ``CitationLink.source_tool_call_id`` on emit without a
        round-trip through persistence.
        """

        if not tool_call_id:
            raise ValueError("tool_call_id must be a non-empty string")
        ordinal = self.allocate()
        self._ordinal_to_tool_call_id[ordinal] = tool_call_id
        return ordinal

    def tool_call_id_for(self, ordinal: int) -> str | None:
        """Return the ``tool_call_id`` bound to ``ordinal`` (or ``None``)."""

        return self._ordinal_to_tool_call_id.get(ordinal)

    def has_ordinal(self, ordinal: int) -> bool:
        """Return ``True`` when ``ordinal`` has ever been allocated."""

        return 0 < ordinal <= self._counter

    @classmethod
    def bind_for_run(cls, allocator: "ConversationOrdinalAllocator") -> object:
        """Set the active allocator; return the previous token for restoration."""

        return _CONVERSATION_ORDINAL_CTX.set(allocator)

    @classmethod
    def unbind(cls, token: object) -> None:
        """Restore the previous allocator token. Safe to call with the bind result."""

        _CONVERSATION_ORDINAL_CTX.reset(token)  # type: ignore[arg-type]

    @classmethod
    def active(cls) -> "ConversationOrdinalAllocator | None":
        """Return the currently bound allocator, or ``None`` when unbound.

        Tools and middleware reach the active allocator via this class
        method so the runtime context never has to be threaded through
        tool signatures (mirrors :meth:`CitationLedger.active`).
        """

        return _CONVERSATION_ORDINAL_CTX.get(None)


class ConversationOrdinalSeeder:
    """Compute the starting ordinal + ordinal/tool-call mapping for a new run.

    The seed equals the count of ``TOOL_CALL_STARTED`` events already
    persisted across the conversation's prior runs (filtered by branch
    chain via the supplied ``prior_run_ids``). The next ``allocate``
    therefore returns ``seed + 1`` — strictly greater than any ordinal
    already embedded in a persisted tool result message.

    The seeder also returns a ``{ordinal: tool_call_id}`` mapping so the
    :class:`CitationResolver` can resolve cross-turn ``[[N]]`` markers
    (citations from the current run that point at a prior turn's tool)
    without an extra persistence round-trip.

    Implementation re-uses :meth:`EventStorePort.list_events_after` so we
    do not introduce a new port method or query primitive.
    """

    class Keys:
        CALL_ID = "call_id"

    @dataclass(frozen=True)
    class Seed:
        """Outcome of seeding: the starting ordinal + the prior mapping."""

        starting_ordinal: int
        ordinal_to_tool_call_id: dict[int, str]

    @classmethod
    async def seed_from_event_log(
        cls,
        *,
        org_id: str,
        conversation_id: str,
        prior_run_ids: Sequence[str],
        event_store: "EventStorePort",
    ) -> "ConversationOrdinalSeeder.Seed":
        # Late import to avoid the citations.py / runtime_api.schemas
        # circular import path used elsewhere in the runtime.
        from runtime_api.schemas import RuntimeApiEventType  # noqa: PLC0415

        ordinal = 0
        mapping: dict[int, str] = {}
        for run_id in prior_run_ids:
            events: Sequence[
                RuntimeEventEnvelope
            ] = await event_store.list_events_after(
                org_id=org_id,
                run_id=run_id,
                after_sequence=0,
            )
            for event in events:
                if event.conversation_id != conversation_id:
                    continue
                if event.event_type is RuntimeApiEventType.TOOL_CALL_STARTED:
                    ordinal += 1
                    call_id = event.payload.get(cls.Keys.CALL_ID)
                    if isinstance(call_id, str) and call_id:
                        mapping[ordinal] = call_id
        return cls.Seed(
            starting_ordinal=ordinal,
            ordinal_to_tool_call_id=mapping,
        )


_CONVERSATION_ORDINAL_CTX: ContextVar[ConversationOrdinalAllocator | None] = ContextVar(
    "conversation_ordinal_allocator", default=None
)


__all__ = ("ConversationOrdinalAllocator", "ConversationOrdinalSeeder")
