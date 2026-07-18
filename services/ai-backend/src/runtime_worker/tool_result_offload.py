"""Offload oversized ``TOOL_RESULT`` output to the file store before it persists.

This is the worker's use of the shared offload contract. When the desktop file
store is active the run handler builds a :class:`ToolResultOffloader` from an
``OffloadWriter`` (backed by the file store's object store) and threads it into
the stream processor. Just before a ``TOOL_RESULT`` event is emitted, the
processor asks the offloader to rewrite the payload: an output that estimates
over the inline token budget is parked in the object store and the event keeps
only a bounded preview plus a ``/large_tool_results/<sha256>`` reference.

Nothing here is reached on the postgres / in-memory / web backends — the run
handler only constructs an offloader when the store exposes an object store, so
those paths keep emitting the full inline output exactly as before.
"""

from __future__ import annotations

import json

from agent_runtime.api.constants import Keys
from agent_runtime.context.memory.contracts import (
    ContextCompressionStrategy,
    TokenBudgetPolicy,
)
from agent_runtime.context.memory.summarization import (
    ContextPayloadManager,
    OffloadWriter,
)
from agent_runtime.execution.contracts import JsonObject


class ToolResultOffloader:
    """Rewrite an oversized ``TOOL_RESULT`` payload into a preview + object ref."""

    # Offload a tool result whose serialized output estimates above this many
    # tokens (~4 chars/token). Well under a model context window, but large
    # enough that ordinary results stay inline. Encoded as a ``TokenBudgetPolicy``
    # so the shared ``ContextPayloadManager`` makes the inline/offload decision.
    INLINE_TOKEN_BUDGET = 8_000
    _RECENT_RATIO = 0.25

    def __init__(
        self,
        offload_writer: OffloadWriter,
        *,
        policy: TokenBudgetPolicy | None = None,
    ) -> None:
        self._offload_writer = offload_writer
        self._policy = policy or TokenBudgetPolicy(
            # recent_context_tokens == max_input_tokens * recent_context_ratio,
            # and ``prepare_tool_output`` keeps content inline while it fits under
            # that, so this pins the offload threshold to ``INLINE_TOKEN_BUDGET``.
            max_input_tokens=int(self.INLINE_TOKEN_BUDGET / self._RECENT_RATIO),
            recent_context_ratio=self._RECENT_RATIO,
        )

    def apply(self, payload: JsonObject, *, trace_id: str) -> JsonObject:
        """Return ``payload`` unchanged, or with its output offloaded when large.

        The returned mapping keeps ``tool_name`` / ``call_id`` / ``status`` /
        ``visibility`` intact and, on offload, replaces ``output`` with a bounded
        preview while adding an ``output_ref`` pointer.
        """

        if Keys.Field.OUTPUT not in payload:
            return payload
        content = self._as_text(payload[Keys.Field.OUTPUT])
        if not content:
            return payload

        managed = ContextPayloadManager.prepare_tool_output(
            content=content,
            policy=self._policy,
            trace_id=trace_id,
            offload_writer=self._offload_writer,
        )
        if managed.strategy is not ContextCompressionStrategy.OFFLOAD:
            return payload

        rewritten = dict(payload)
        rewritten[Keys.Field.OUTPUT] = managed.preview or ""
        rewritten[Keys.Field.PREVIEW] = managed.preview or ""
        rewritten[Keys.Field.OUTPUT_REF] = managed.reference
        return rewritten

    @staticmethod
    def _as_text(output: object) -> str:
        """Serialize a tool-result output to the string the offload contract takes."""

        if isinstance(output, str):
            return output
        return json.dumps(output, ensure_ascii=False, default=str)


__all__ = ("ToolResultOffloader",)
