"""Per-run resolver for model-declared citation markers.

PR 1.1-rev2 — citations as model-declared, conversation-scoped pointers.

The resolver watches streamed assistant text for ``[[N]]`` tokens, where
``N`` is a ``conversation_ordinal`` allocated by
:class:`agent_runtime.capabilities.conversation_ordinals.ConversationOrdinalAllocator`.
For each newly-observed marker it emits a ``citation_made`` event
carrying a :class:`CitationLink` payload tying the prose location to the
underlying tool invocation.

Design notes:

- **Per-message accumulation.** We keep the running text of each
  assistant message keyed by ``message_id``. Because a marker is
  matched only when it appears in full (``[[`` … ``]]``), partial
  tokens split across deltas naturally don't fire — the regex simply
  doesn't match until the closing ``]]`` arrives in a later delta.
- **Idempotency.** Each emission is keyed by ``(prose_offset, ordinal)``
  so re-deliveries of the same delta on stream resume don't duplicate
  the event. Replay rebuild on the FE is also idempotent because
  ``citation_made`` events ride the run's normal sequence_no log.
- **Hallucinated ordinals.** When the model writes ``[[99]]`` for an
  ordinal that was never allocated, we still emit the event (the FE
  renders a muted placeholder per spec); the ``source_tool_call_id``
  field is left empty for the FE to detect the unresolved case.
- **No mutation of model output.** The resolver does not rewrite text
  — the original ``[[N]]`` marker stays in the persisted assistant
  message and the FE remark plugin replaces it with a chip at render
  time.

Bound per run via ``ContextVar`` (mirroring ``CitationLedger`` /
``ConversationOrdinalAllocator`` / ``ToolBudgetGuard``).
"""

from __future__ import annotations

import logging
import re
from contextvars import ContextVar
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing-only imports
    from agent_runtime.api.events import RuntimeEventProducer
    from agent_runtime.capabilities.conversation_ordinals import (
        ConversationOrdinalAllocator,
    )
    from agent_runtime.execution.contracts import StreamEventSource
    from runtime_api.schemas import RunRecord


_LOGGER = logging.getLogger(__name__)


class _Fields:
    """Wire payload field names — kept stable for replay compatibility."""

    LINK = "link"
    CONVERSATION_ORDINAL = "conversation_ordinal"
    MESSAGE_ID = "message_id"
    PROSE_OFFSET = "prose_offset"
    PROSE_LENGTH = "prose_length"
    SOURCE_TOOL_CALL_ID = "source_tool_call_id"


class CitationResolver:
    """Per-run filter that resolves ``[[N]]`` markers into ``citation_made`` events."""

    # Decimal-int conversation ordinal between double brackets. Anchored
    # to digits only (no whitespace, no other characters) to keep partial
    # matches that can never become valid tokens (e.g. ``[[abc]]``) out
    # of the registry without an extra validation pass.
    _CITATION_PATTERN = re.compile(r"\[\[(\d+)\]\]")

    class _MessageBuffer:
        """Running text + already-emitted set per assistant message."""

        __slots__ = ("accumulated", "emitted")

        def __init__(self) -> None:
            self.accumulated: str = ""
            # (prose_offset, ordinal) — keyed jointly so the same ordinal
            # cited at two distinct prose positions still emits twice
            # (two chips → two events), but a re-delivered delta does not.
            self.emitted: set[tuple[int, int]] = set()

    def __init__(
        self,
        *,
        run: "RunRecord",
        allocator: "ConversationOrdinalAllocator",
        producer: "RuntimeEventProducer",
        source: "StreamEventSource",
    ) -> None:
        self._run = run
        self._allocator = allocator
        self._producer = producer
        self._source = source
        self._buffers: dict[str, CitationResolver._MessageBuffer] = {}
        self._seen_ordinals: list[int] = []
        self._seen_ordinals_set: set[int] = set()

    @property
    def run_id(self) -> str:
        return self._run.run_id

    async def observe_delta(self, *, message_id: str, delta_text: str) -> None:
        """Process one streamed model_delta for an assistant message.

        Called by the runtime's streaming pipeline after each
        ``MODEL_DELTA`` event is emitted, with the same delta text and
        the message_id the delta belongs to. Best-effort: this method
        never raises into the streaming path — a resolver failure must
        not break model output streaming.
        """

        if not delta_text or not message_id:
            return
        try:
            await self._observe(message_id=message_id, delta_text=delta_text)
        except Exception:  # noqa: BLE001 - best-effort, must not break streaming
            _LOGGER.warning(
                "citation resolver raised on run %s message %s; skipping",
                self._run.run_id,
                message_id,
                exc_info=True,
            )

    async def _observe(self, *, message_id: str, delta_text: str) -> None:
        # Late import inside the method to avoid the recurring
        # ``capabilities`` <-> ``runtime_api.schemas`` circular import
        # path used by ``CitationLedger`` and adopted here for parity.
        from runtime_api.schemas import RuntimeApiEventType  # noqa: PLC0415

        buf = self._buffers.get(message_id)
        if buf is None:
            buf = self._MessageBuffer()
            self._buffers[message_id] = buf
        buf.accumulated += delta_text
        for match in self._CITATION_PATTERN.finditer(buf.accumulated):
            try:
                ordinal = int(match.group(1))
            except ValueError:  # pragma: no cover - regex guarantees digits
                continue
            if ordinal <= 0:
                continue
            prose_offset = match.start()
            prose_length = match.end() - match.start()
            key = (prose_offset, ordinal)
            if key in buf.emitted:
                continue
            buf.emitted.add(key)
            if ordinal not in self._seen_ordinals_set:
                self._seen_ordinals_set.add(ordinal)
                self._seen_ordinals.append(ordinal)
            tool_call_id = self._allocator.tool_call_id_for(ordinal) or ""
            await self._producer.append_api_event(
                run=self._run,
                source=self._source,
                event_type=RuntimeApiEventType.CITATION_MADE,
                payload={
                    _Fields.LINK: {
                        _Fields.CONVERSATION_ORDINAL: ordinal,
                        _Fields.MESSAGE_ID: message_id,
                        _Fields.PROSE_OFFSET: prose_offset,
                        _Fields.PROSE_LENGTH: prose_length,
                        _Fields.SOURCE_TOOL_CALL_ID: tool_call_id,
                    }
                },
            )

    def sealed_ordinals(self) -> list[int]:
        """Snapshot the resolved ordinals for ``final_response.cited_ordinals``.

        Returned in first-occurrence order — matches the spec's
        "sealed list of cited ordinals in order of first reference."
        """

        return list(self._seen_ordinals)

    @classmethod
    def bind_for_run(cls, resolver: "CitationResolver") -> object:
        """Set the active resolver; return the previous token for restoration."""

        return _CITATION_RESOLVER_CTX.set(resolver)

    @classmethod
    def unbind(cls, token: object) -> None:
        """Restore the previous resolver token. Safe to call with the bind result."""

        _CITATION_RESOLVER_CTX.reset(token)  # type: ignore[arg-type]

    @classmethod
    def active(cls) -> "CitationResolver | None":
        """Return the currently bound resolver, or ``None`` when unbound."""

        return _CITATION_RESOLVER_CTX.get(None)


_CITATION_RESOLVER_CTX: ContextVar[CitationResolver | None] = ContextVar(
    "citation_resolver",
    default=None,
)


__all__ = ("CitationResolver",)
