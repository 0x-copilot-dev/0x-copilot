"""Per-run filter that resolves ``[[N]]`` ordinal markers in streamed assistant text.

Watches each MODEL_DELTA for ``[[N]]`` tokens, accumulates per-message buffers to
handle partial tokens split across deltas, and emits a ``citation_made`` event for
each new (prose_offset, ordinal) pair. Hallucinated ordinals still emit with an
empty source_tool_call_id (frontend renders a muted placeholder). Bound per run
via ContextVar, mirroring CitationLedger and ConversationOrdinalAllocator.
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
    """citation_made event payload field names, stable for replay compatibility."""

    LINK = "link"
    CONVERSATION_ORDINAL = "conversation_ordinal"
    MESSAGE_ID = "message_id"
    PROSE_OFFSET = "prose_offset"
    PROSE_LENGTH = "prose_length"
    SOURCE_TOOL_CALL_ID = "source_tool_call_id"


class CitationResolver:
    """Watch streamed assistant deltas and emit ``citation_made`` events for each ``[[N]]``."""

    # Matches ``[[N]]`` where N is one or more decimal digits only. Partial tokens
    # split across deltas naturally fail to match until the closing ``]]`` arrives.
    _CITATION_PATTERN = re.compile(r"\[\[(\d+)\]\]")

    class _MessageBuffer:
        """Accumulated text and already-emitted (offset, ordinal) pairs for one message."""

        __slots__ = ("accumulated", "emitted")

        def __init__(self) -> None:
            """Initialise a fresh per-delta accumulation buffer."""
            self.accumulated: str = ""
            # Keyed by (prose_offset, ordinal) so the same ordinal at two
            # different prose positions emits twice (two chips), while a
            # re-delivered delta on stream resume does not duplicate.
            self.emitted: set[tuple[int, int]] = set()

    def __init__(
        self,
        *,
        run: "RunRecord",
        allocator: "ConversationOrdinalAllocator",
        producer: "RuntimeEventProducer",
        source: "StreamEventSource",
    ) -> None:
        """Initialise the resolver bound to a run, ordinal allocator, event producer, and stream source."""
        self._run = run
        self._allocator = allocator
        self._producer = producer
        self._source = source
        self._buffers: dict[str, CitationResolver._MessageBuffer] = {}
        self._seen_ordinals: list[int] = []
        self._seen_ordinals_set: set[int] = set()

    @property
    def run_id(self) -> str:
        """Return the run id this resolver is scoped to."""
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
        """Accumulate delta text and emit a ``citation_made`` event for each new ``[[N]]`` match."""
        # Late import to avoid the capabilities ↔ runtime_api.schemas circular
        # import path that CitationLedger also side-steps the same way.
        from runtime_api.schemas import RuntimeApiEventType  # noqa: PLC0415

        buf = self._buffers.get(message_id)
        if buf is None:
            buf = self._MessageBuffer()
            self._buffers[message_id] = buf
        buf.accumulated += delta_text
        emitted_this_call = 0
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
                # Preserve first-occurrence ordering for sealed_ordinals().
                self._seen_ordinals_set.add(ordinal)
                self._seen_ordinals.append(ordinal)
            tool_call_id = self._allocator.tool_call_id_for(ordinal) or ""
            if tool_call_id:
                _LOGGER.info(
                    "[citations] resolver.match run=%s msg=%s ordinal=%d "
                    "offset=%d tool_call_id='%s' (allocator_last=%d)",
                    self._run.run_id,
                    message_id,
                    ordinal,
                    prose_offset,
                    tool_call_id,
                    self._allocator.last_allocated,
                )
            else:
                # Empty tool_call_id means either (a) the model hallucinated
                # [[N]] for an ordinal never allocated, or (b) a tool dispatch
                # path bypassed the binding. Both render as "?" on the frontend;
                # this warning is the signal to investigate (b).
                _LOGGER.warning(
                    "[citations] resolver.unbound_ordinal run=%s msg=%s "
                    "ordinal=%d offset=%d (allocator_last=%d) — chip will "
                    "render as `?`",
                    self._run.run_id,
                    message_id,
                    ordinal,
                    prose_offset,
                    self._allocator.last_allocated,
                )
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
            emitted_this_call += 1
        if emitted_this_call == 0 and "[[" in delta_text:
            # Surface partial-token state on the noisy path so a missing
            # closing ``]]`` is visible during debug.
            _LOGGER.debug(
                "[citations] resolver.partial run=%s msg=%s delta_tail=%r "
                "buffer_len=%d",
                self._run.run_id,
                message_id,
                delta_text[-40:],
                len(buf.accumulated),
            )

    def sealed_ordinals(self) -> list[int]:
        """Snapshot the resolved ordinals for ``final_response.cited_ordinals``.

        Returned in first-occurrence order — matches the spec's
        "sealed list of cited ordinals in order of first reference."
        """

        ordinals = list(self._seen_ordinals)
        _LOGGER.info(
            "[citations] resolver.sealed_ordinals run=%s ordinals=%s",
            self._run.run_id,
            ordinals,
        )
        return ordinals

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
