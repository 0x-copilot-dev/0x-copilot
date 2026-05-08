"""Conversation-scoped tool invocation ordinal allocator.

PR 04 — citations binding map.

Each tool invocation in a conversation is assigned a monotonic
``conversation_ordinal`` at dispatch time. The ordinal is a stable,
durable pointer used by:

- the per-tool result-hinting wrappers that prepend
  ``[Tool call #N — cite as [[N]] when referencing this result.]``
  to the result text the model reads, so the model can declare which
  tool grounded any factual claim with a stable pointer;
- the :class:`agent_runtime.capabilities.citation_resolver.CitationResolver`
  that watches streamed assistant text for ``[[N]]`` markers and stamps
  ``source_tool_call_id`` on each emitted ``citation_made`` event;
- the cross-turn observation builder that surfaces prior turns' tool
  calls into the model's context, so cross-turn citation continues to
  resolve to the same ``tool_call_id`` it referred to in the originating
  turn.

The allocator is a write-through cache over a persistent
``(conversation_ordinal ↔ tool_call_id)`` binding table
(:class:`agent_runtime.persistence.ports.ConversationToolOrdinalStorePort`,
backed by ``agent_conversation_tool_ordinals`` from migration 0026).
This replaces the prior positional-event-counting seeder, whose count
could disagree with the live counter when the MCP middleware allocated
inside a tool body or when approval interrupts caused repeated rebinds.
With persistence:

* Every allocation writes one row to the store. The PRIMARY KEY +
  UNIQUE constraint guarantee one ordinal per ``tool_call_id`` per
  conversation. Retries (LangGraph re-dispatch on resume) collapse
  to the existing row.
* Approval resumes reload the allocator from the store; in-memory
  state survives the pause without recomputation.
* Cross-turn citations look up the same canonical mapping the cross-turn
  observation builder reads from.

The allocator is bound per run via ``ContextVar`` so tools and middleware
reach it without threading the runtime context through every signature
(mirrors :class:`agent_runtime.capabilities.citations.CitationLedger`).
When no allocator is bound (replay, eval harnesses, unit tests of inner
tools), :meth:`ConversationOrdinalAllocator.active` returns ``None`` and
the wrappers degrade to a no-op append. Citations are best-effort
decoration, never required for tool correctness.
"""

from __future__ import annotations

import logging
from contextvars import ContextVar
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing-only imports
    from agent_runtime.persistence.ports import (
        ConversationToolOrdinalStorePort,
    )


_LOGGER = logging.getLogger(__name__)


class ConversationOrdinalAllocator:
    """Per-conversation monotonic ordinal allocator for tool invocations.

    Owns the in-memory counter for the active run, plus the
    ``(ordinal → tool_call_id)`` mapping the resolver consults when
    stamping ``citation_made`` events. State is loaded from
    :class:`ConversationToolOrdinalStorePort` at construction
    (:meth:`for_conversation`) and written back on every successful
    :meth:`allocate_for_tool_call`. Without a store the allocator
    operates purely in memory — used by replay paths and unit tests of
    inner tools.
    """

    def __init__(
        self,
        *,
        org_id: str,
        conversation_id: str,
        run_id: str,
        store: "ConversationToolOrdinalStorePort | None" = None,
        starting_ordinal: int = 0,
        ordinal_to_tool_call_id: dict[int, str] | None = None,
    ) -> None:
        if starting_ordinal < 0:
            raise ValueError("starting_ordinal must be non-negative")
        self._org_id = org_id
        self._conversation_id = conversation_id
        self._run_id = run_id
        self._store = store
        self._counter = starting_ordinal
        # Bidirectional index: ordinal → tool_call_id, plus the reverse
        # for fast idempotent lookup on retries (same ``tool_call_id``
        # bound twice must collapse to the same ordinal).
        self._ordinal_to_tool_call_id: dict[int, str] = dict(
            ordinal_to_tool_call_id or {}
        )
        self._tool_call_id_to_ordinal: dict[str, int] = {
            call_id: ordinal
            for ordinal, call_id in self._ordinal_to_tool_call_id.items()
        }

    @property
    def conversation_id(self) -> str:
        return self._conversation_id

    @property
    def run_id(self) -> str:
        return self._run_id

    @property
    def last_allocated(self) -> int:
        """Return the most recently allocated ordinal (0 before any allocate)."""

        return self._counter

    async def allocate_for_tool_call(
        self,
        *,
        tool_call_id: str,
        tool_name: str,
    ) -> int:
        """Allocate the next ordinal and bind it to ``tool_call_id``.

        Idempotent on ``tool_call_id`` — a retry for the same call_id
        returns the existing ordinal without bumping the counter or
        writing a second row. When the allocator is bound to a store,
        every fresh binding is persisted before returning. On a
        :class:`ConversationOrdinalConflict` (a concurrent allocator
        beat us with a different counter value), reload state from the
        store and retry once with the canonical ordinal.
        """

        if not tool_call_id:
            raise ValueError("tool_call_id must be a non-empty string")
        if not tool_name:
            raise ValueError("tool_name must be a non-empty string")
        existing = self._tool_call_id_to_ordinal.get(tool_call_id)
        if existing is not None:
            return existing
        if self._store is None:
            # No persistence layer (replay / eval / unit tests of
            # inner tools). Allocate in memory and return.
            self._counter += 1
            self._ordinal_to_tool_call_id[self._counter] = tool_call_id
            self._tool_call_id_to_ordinal[tool_call_id] = self._counter
            _LOGGER.info(
                "[citations] allocator.allocate_for_tool_call conv=%s "
                "ordinal=%d call_id=%s tool=%s (no_store)",
                self._conversation_id,
                self._counter,
                tool_call_id,
                tool_name,
            )
            return self._counter
        # Late import to avoid a cross-package import cycle:
        # capabilities.conversation_ordinals → persistence.ports →
        # capabilities (via record dataclasses) — keeping the conflict
        # exception import lazy preserves the prior import topology.
        from agent_runtime.persistence.ports import (  # noqa: PLC0415
            ConversationOrdinalConflict,
        )

        attempted = self._counter + 1
        try:
            binding = await self._store.record(
                org_id=self._org_id,
                conversation_id=self._conversation_id,
                conversation_ordinal=attempted,
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                run_id=self._run_id,
            )
        except ConversationOrdinalConflict as exc:
            _LOGGER.info(
                "[citations] allocator.conflict conv=%s attempted=%d "
                "call_id=%s reason=%s — reloading and retrying",
                self._conversation_id,
                attempted,
                tool_call_id,
                exc.existing_ordinal,
            )
            await self._reload_from_store()
            return await self.allocate_for_tool_call(
                tool_call_id=tool_call_id, tool_name=tool_name
            )
        self._counter = max(self._counter, binding.conversation_ordinal)
        self._ordinal_to_tool_call_id[binding.conversation_ordinal] = (
            binding.tool_call_id
        )
        self._tool_call_id_to_ordinal[binding.tool_call_id] = (
            binding.conversation_ordinal
        )
        _LOGGER.info(
            "[citations] allocator.allocate_for_tool_call conv=%s "
            "ordinal=%d call_id=%s tool=%s",
            self._conversation_id,
            binding.conversation_ordinal,
            binding.tool_call_id,
            binding.tool_name,
        )
        return binding.conversation_ordinal

    def tool_call_id_for(self, ordinal: int) -> str | None:
        """Return the ``tool_call_id`` bound to ``ordinal`` (or ``None``)."""

        return self._ordinal_to_tool_call_id.get(ordinal)

    def has_ordinal(self, ordinal: int) -> bool:
        """Return ``True`` when ``ordinal`` has ever been allocated."""

        return 0 < ordinal <= self._counter

    async def _reload_from_store(self) -> None:
        """Refresh in-memory state after a conflict from the store."""

        if self._store is None:
            return
        bindings = await self._store.load(
            org_id=self._org_id,
            conversation_id=self._conversation_id,
        )
        self._ordinal_to_tool_call_id = {
            b.conversation_ordinal: b.tool_call_id for b in bindings
        }
        self._tool_call_id_to_ordinal = {
            b.tool_call_id: b.conversation_ordinal for b in bindings
        }
        self._counter = max(
            (b.conversation_ordinal for b in bindings),
            default=0,
        )

    @classmethod
    async def for_conversation(
        cls,
        *,
        org_id: str,
        conversation_id: str,
        run_id: str,
        store: "ConversationToolOrdinalStorePort",
    ) -> "ConversationOrdinalAllocator":
        """Build an allocator restored from the persistent binding map.

        Reads every binding for ``conversation_id`` from the store, sets
        the counter to ``max(conversation_ordinal)``, and seeds the
        in-memory map. The next allocate returns ``counter + 1`` —
        strictly greater than any ordinal already persisted for this
        conversation, regardless of which run created it (so a brand-new
        tool call in turn T+k does not collide with an ordinal allocated
        in turn T).

        Approval resumes call this with the same ``run_id`` the run
        started under, so new bindings made post-resume stay attributed
        to the original run.
        """

        bindings = await store.load(org_id=org_id, conversation_id=conversation_id)
        starting = max(
            (b.conversation_ordinal for b in bindings),
            default=0,
        )
        mapping = {b.conversation_ordinal: b.tool_call_id for b in bindings}
        _LOGGER.info(
            "[citations] allocator.for_conversation conv=%s run=%s "
            "starting_ordinal=%d mapped_ordinals=%d",
            conversation_id,
            run_id,
            starting,
            len(mapping),
        )
        return cls(
            org_id=org_id,
            conversation_id=conversation_id,
            run_id=run_id,
            store=store,
            starting_ordinal=starting,
            ordinal_to_tool_call_id=mapping,
        )

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


_CONVERSATION_ORDINAL_CTX: ContextVar[ConversationOrdinalAllocator | None] = ContextVar(
    "conversation_ordinal_allocator", default=None
)


__all__ = ("ConversationOrdinalAllocator",)
