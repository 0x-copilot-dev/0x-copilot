"""Conversation-scoped monotonic ordinal allocator for tool call citations.

Assigns a stable ``conversation_ordinal`` to each tool invocation and persists
the (ordinal ↔ tool_call_id) binding via ConversationToolOrdinalStorePort so the
CitationResolver can stamp source_tool_call_id on citation_made events, and
cross-turn observations continue to resolve to the same canonical mapping. Bound
per run via ContextVar; degrades to a silent no-op when unbound (replay/eval/tests).
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
    """Monotonic ordinal allocator for a single conversation's tool calls.

    Owns the in-memory counter and bidirectional ordinal ↔ tool_call_id map.
    State is loaded from the persistent store at construction and written back
    on every fresh allocation. Operates in-memory only when no store is provided
    (replay / eval / unit tests).
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
        """Initialise the allocator with optional pre-seeded binding state from a prior run."""
        if starting_ordinal < 0:
            raise ValueError("starting_ordinal must be non-negative")
        self._org_id = org_id
        self._conversation_id = conversation_id
        self._run_id = run_id
        self._store = store
        self._counter = starting_ordinal
        # Bidirectional mapping: ordinal → tool_call_id and its reverse.
        # The reverse enables fast idempotent lookup so retrying the same
        # tool_call_id always returns its original ordinal.
        self._ordinal_to_tool_call_id: dict[int, str] = dict(
            ordinal_to_tool_call_id or {}
        )
        self._tool_call_id_to_ordinal: dict[str, int] = {
            call_id: ordinal
            for ordinal, call_id in self._ordinal_to_tool_call_id.items()
        }

    @property
    def conversation_id(self) -> str:
        """Return the conversation id this allocator is scoped to."""
        return self._conversation_id

    @property
    def run_id(self) -> str:
        """Return the run id that created this allocator instance."""
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
            # Storeless path (replay / eval / unit tests): in-memory only.
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
        # Late import to avoid the capabilities → persistence.ports → capabilities
        # circular dependency; lazy import here breaks the cycle at runtime.
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
        """Reload the full binding map from the store after an ordinal conflict."""

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
        """Build an allocator seeded from the store's persisted binding map.

        Sets the counter to max(existing ordinals) so the first new allocation
        is strictly greater than any prior ordinal in the conversation, regardless
        of which run created it. Approval resumes pass the original run_id so
        new bindings remain attributed to the run that started.
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
