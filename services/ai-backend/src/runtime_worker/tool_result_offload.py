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

from agent_runtime.api.constants import Keys
from agent_runtime.context.memory.contracts import TokenBudgetPolicy
from agent_runtime.context.memory.summarization import (
    OffloadWriter,
)
from agent_runtime.context.tool_result_admission import ToolResultAdmissionAdapter
from agent_runtime.execution.contracts import JsonObject


class ToolResultOffloader:
    """Project a pre-model admission decision into a tool-result event.

    The reusable :class:`ToolResultAdmissionAdapter` owns serialization,
    budgeting, and storage.  This worker adapter only maps that raw-free result
    onto the existing event payload shape.
    """

    # Offload a tool result whose serialized output estimates above this many
    # tokens (~4 chars/token). Well under a model context window, but large
    # enough that ordinary results stay inline. Encoded as a ``TokenBudgetPolicy``
    # so the shared ``ContextPayloadManager`` makes the inline/offload decision.
    INLINE_TOKEN_BUDGET = 8_000
    _RECENT_RATIO = 0.25

    def __init__(
        self,
        offload_writer: OffloadWriter | None = None,
        *,
        policy: TokenBudgetPolicy | None = None,
        admission_adapter: ToolResultAdmissionAdapter | None = None,
    ) -> None:
        if (offload_writer is None) == (admission_adapter is None):
            raise ValueError(
                "provide exactly one of offload_writer or admission_adapter"
            )
        effective_policy = policy or TokenBudgetPolicy(
            max_input_tokens=int(self.INLINE_TOKEN_BUDGET / self._RECENT_RATIO),
            recent_context_ratio=self._RECENT_RATIO,
        )
        if admission_adapter is not None:
            self._admission_adapter = admission_adapter
        else:
            assert offload_writer is not None  # narrowed by the xor guard above
            self._admission_adapter = ToolResultAdmissionAdapter(
                offload_writer,
                policy=effective_policy,
            )

    def apply(
        self,
        payload: JsonObject,
        *,
        trace_id: str,
        projection_key: str | None = None,
        projection_content: object | None = None,
    ) -> JsonObject:
        """Return ``payload`` unchanged, or with its output offloaded when large.

        The returned mapping keeps ``tool_name`` / ``call_id`` / ``status`` /
        ``visibility`` intact and, on offload, replaces ``output`` with a bounded
        preview while adding an ``output_ref`` pointer.
        """

        if Keys.Field.OUTPUT not in payload:
            return payload
        output = payload[Keys.Field.OUTPUT]
        if output == "":
            return payload

        admitted = (
            self._admission_adapter.consume_projection(
                output if projection_content is None else projection_content,
                projection_key=projection_key,
            )
            if projection_key is not None
            else None
        )
        # Legacy/direct stream paths do not cross the pre-model wrapper.  Keep
        # their existing post-hoc offload behavior, while the production bound
        # path projects the exact earlier admission and never decides twice.
        if admitted is None:
            admitted = self._admission_adapter.admit(
                output,
                trace_id=trace_id,
            )
        if admitted.output_ref is None:
            return payload

        rewritten = dict(payload)
        rewritten[Keys.Field.OUTPUT] = admitted.event_content
        rewritten[Keys.Field.PREVIEW] = admitted.preview or ""
        rewritten[Keys.Field.OUTPUT_REF] = admitted.output_ref
        return rewritten


__all__ = ("ToolResultOffloader",)
