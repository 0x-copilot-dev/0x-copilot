"""The typed carrier for one observed context compaction.

We have had real context compaction for a long time -- ``summarization.py``'s
:class:`~agent_runtime.context.memory.summarization.ContextPayloadManager`,
``token_budget.py``, and
:class:`~agent_runtime.context.tool_result_admission.ToolResultAdmissionAdapter`,
which bounds every tool result before it reaches the model. What we did not have
was any way for a user to SEE it: an oversized result was parked in the object
store, the model was handed a bounded preview instead of the bytes, and the
transcript said nothing. The user could only observe the consequence -- the
agent not knowing something it had "already read".

``RuntimeApiEventType.COMPRESSION_NOTE`` and
:meth:`RuntimeEventProducer.append_compression_note` already existed for exactly
this and had no caller. This module supplies the missing middle: the admission
path already builds a
:class:`~agent_runtime.context.memory.contracts.ContextCompressionEvent` for
every decision it makes and then drops it on the floor. :class:`CompactionNotice`
is that fact in the shape the transcript needs, and
:meth:`CompactionNotice.from_admission` is the one adapter between them.

Why this is a plain value and not a run-scoped recorder
------------------------------------------------------
An earlier draft of this module carried a ContextVar-bound, idempotent
``ContextCompactionRecorder`` mirroring ``CitationLedger``. It was deleted: the
seam it was written for does not exist. There is no summarization middleware in
this runtime, so there is no folded request replayed on every subsequent model
call and therefore no duplicate to suppress. Compaction is decided once per tool
result, and the worker's stream processor emits it once, in the same async pass
that emits the ``TOOL_RESULT`` event it describes -- ordered inside the run's
causal prefix, before the terminal event seals it. A ContextVar and a dedup set
would have been machinery for a problem this runtime does not have.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import Field

from agent_runtime.context.memory.constants import Values
from agent_runtime.context.memory.contracts import ContextCompressionStrategy
from agent_runtime.execution.contracts import RuntimeContract

if TYPE_CHECKING:  # pragma: no cover - typing-only imports
    from agent_runtime.context.tool_result_admission import ToolResultAdmission


class CompactionNotice(RuntimeContract):
    """One compaction that actually happened, in the numbers a reader needs.

    ``before_tokens`` / ``after_tokens`` are measured on the bytes that actually
    existed at the admission seam: the serialized tool output the runtime
    received (before) and the bounded content the model was handed in its place
    (after). They are not estimates of what a policy intended to do -- they are
    what was done.
    """

    before_tokens: int = Field(ge=0)
    after_tokens: int = Field(ge=0)
    #: Mirrors ``ContextCompressionStrategy`` -- ``offload`` when the source was
    #: parked in the object store, ``summarize`` / ``fallback_summary`` when it
    #: was compressed in place.
    strategy: str = Field(min_length=1)
    trigger: str = Field(
        default=Values.CompactionTrigger.TOKEN_THRESHOLD,
        min_length=1,
    )
    #: The tool whose result was compacted, when the call was identified. The
    #: divider reads far better naming the tool than as an anonymous event.
    tool_name: str | None = Field(default=None, min_length=1, max_length=128)

    @property
    def tokens_saved(self) -> int:
        """How many estimated tokens the compaction kept out of model context."""

        return max(self.before_tokens - self.after_tokens, 0)

    def is_material(self) -> bool:
        """False when nothing was actually compacted away.

        An inline admission is the overwhelmingly common case and is not a
        boundary the user should see: the transcript would carry a divider for a
        moment at which the model lost nothing.
        """

        return self.tokens_saved > 0

    @classmethod
    def from_admission(
        cls,
        admission: "ToolResultAdmission",
        *,
        tool_name: str | None = None,
    ) -> "CompactionNotice | None":
        """Adapt one :class:`ToolResultAdmission` into a notice, or ``None``.

        Returns ``None`` for an admission that compacted nothing, so the caller
        never has to restate the materiality rule. ``INLINE`` is rejected by
        strategy rather than by token arithmetic alone: an inline admission is
        the model seeing the whole source, which is the opposite of the fact
        this event exists to report.
        """

        if admission.strategy is ContextCompressionStrategy.INLINE:
            return None
        event = admission.event
        notice = cls(
            before_tokens=event.before_tokens,
            after_tokens=event.after_tokens,
            strategy=str(event.strategy.value),
            trigger=Values.CompactionTrigger.TOKEN_THRESHOLD,
            tool_name=(
                tool_name
                if tool_name
                else (admission.fact.tool_name if admission.fact is not None else None)
            ),
        )
        return notice if notice.is_material() else None


__all__ = ("CompactionNotice",)
