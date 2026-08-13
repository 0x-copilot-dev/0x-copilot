"""Run-scoped recorder that turns a real compaction into a transcript part.

We have had real context compaction for a long time -- ``summarization.py``,
``token_budget.py``, and the ``deepagents`` summarization middleware the graph
builder installs on every agent. What we did not have was any way for a user to
SEE it: history was folded, the model forgot, and the transcript said nothing.

``RuntimeApiEventType.COMPRESSION_NOTE`` and
:meth:`RuntimeEventProducer.append_compression_note` already existed for exactly
this and had no caller. This module is the caller: a per-run ContextVar-bound
recorder, mirroring :class:`agent_runtime.capabilities.citations.CitationLedger`
one-for-one (bind at run start, reach it from anywhere in the run's call stack,
no-op when unbound, unbind in the handler's ``finally``).

Two properties are load-bearing and neither is decoration:

* **Idempotent per compaction.** The middleware seam observes the SAME folded
  request on every subsequent model call of the run -- ``_get_effective_messages``
  replays the stored summarization event -- so a recorder that emitted per
  observation would emit one divider per model call after the first fold.
  :attr:`CompactionNotice.compaction_id` is the summary message's id, and the
  recorder emits once per id.
* **Never fails a run.** Compaction visibility is a transcript nicety; a
  producer failure here must not kill a run that is otherwise healthy, exactly
  as a citation emit does not.
"""

from __future__ import annotations

import logging
from contextvars import ContextVar
from typing import TYPE_CHECKING

from pydantic import Field

from agent_runtime.context.memory.constants import Values
from agent_runtime.execution.contracts import RuntimeContract

if TYPE_CHECKING:  # pragma: no cover - typing-only imports
    from agent_runtime.api.events import RuntimeEventProducer
    from runtime_api.schemas import RunRecord


_LOGGER = logging.getLogger(__name__)


class CompactionNotice(RuntimeContract):
    """One observed fold of the conversation, in the numbers a reader needs.

    ``before``/``after`` are measured on the two message lists that actually
    exist at the model-call seam: the run's full state history (before) and the
    list the model is about to be sent (after). They are not estimates of what
    a policy intended to do -- they are what was done.
    """

    #: Stable identity of the fold. The summary message's id, which the
    #: middleware replays unchanged on every later model call of the run.
    compaction_id: str = Field(min_length=1, max_length=256)
    before_tokens: int = Field(ge=0)
    after_tokens: int = Field(ge=0)
    messages_before: int = Field(ge=0)
    messages_after: int = Field(ge=0)
    strategy: str = Field(default=Values.CompressionStrategy.SUMMARIZE, min_length=1)
    trigger: str = Field(default=Values.CompactionTrigger.TOKEN_THRESHOLD, min_length=1)

    @property
    def messages_folded(self) -> int:
        """How many messages the fold removed from the model's view."""

        return max(self.messages_before - self.messages_after, 0)

    def is_material(self) -> bool:
        """False when nothing was actually folded away.

        A summary message with no eviction behind it is not a boundary the user
        should see: the transcript would carry a divider for a moment at which
        the model lost nothing.
        """

        return self.messages_folded > 0 and self.after_tokens < self.before_tokens


class ContextCompactionRecorder:
    """Per-run, idempotent emitter of ``compression_note`` transcript parts.

    The worker builds one per run, binds it via :meth:`bind_for_run`, and clears
    it via :meth:`unbind` on teardown -- the CitationLedger contract exactly.
    Callers inside the graph reach it with :meth:`notice`, which is a silent
    no-op when nothing is bound (replay, eval, unit tests, subagent-only graphs
    constructed outside a run).
    """

    def __init__(
        self,
        *,
        run: "RunRecord",
        producer: "RuntimeEventProducer",
    ) -> None:
        """Bind the recorder to one run record and the run's event producer."""

        self._run = run
        self._producer = producer
        self._emitted: set[str] = set()

    @property
    def run_id(self) -> str:
        """Return the run id this recorder is scoped to."""

        return self._run.run_id

    async def record(self, notice: CompactionNotice) -> bool:
        """Emit one ``compression_note`` for *notice*; ``True`` when it landed.

        Returns ``False`` -- without raising -- when the notice is immaterial,
        already emitted for this run, or the producer rejected it. A run whose
        transcript is missing a divider is a worse transcript; a run killed by
        its own annotation is a worse product.
        """

        if not notice.is_material():
            return False
        if notice.compaction_id in self._emitted:
            return False
        # Marked BEFORE the await: two concurrent model calls (a supervisor and
        # a subagent replaying the same fold) must not both get past the guard
        # while the first is suspended on the producer.
        self._emitted.add(notice.compaction_id)
        try:
            await self._producer.append_compression_note(
                run=self._run,
                before_tokens=notice.before_tokens,
                after_tokens=notice.after_tokens,
                strategy=notice.strategy,
                trigger=notice.trigger,
                messages_before=notice.messages_before,
                messages_after=notice.messages_after,
            )
        except Exception:  # noqa: BLE001 - a transcript note never fails a run
            _LOGGER.warning(
                "could not record the context-compaction note for run %s",
                self._run.run_id,
                exc_info=True,
            )
            return False
        return True

    @classmethod
    async def notice(cls, notice: CompactionNotice) -> bool:
        """Resolve the active recorder and record *notice*; no-op when unbound."""

        recorder = _COMPACTION_RECORDER_CTX.get(None)
        if recorder is None:
            return False
        return await recorder.record(notice)

    @classmethod
    def bind_for_run(cls, recorder: "ContextCompactionRecorder") -> object:
        """Set the active recorder; return the token for restoration."""

        return _COMPACTION_RECORDER_CTX.set(recorder)

    @classmethod
    def unbind(cls, token: object) -> None:
        """Restore the previous recorder token. Safe with the bind result."""

        _COMPACTION_RECORDER_CTX.reset(token)  # type: ignore[arg-type]

    @classmethod
    def active(cls) -> "ContextCompactionRecorder | None":
        """Return the active recorder or ``None`` (test helper / debugging)."""

        return _COMPACTION_RECORDER_CTX.get(None)


_COMPACTION_RECORDER_CTX: ContextVar[ContextCompactionRecorder | None] = ContextVar(
    "context_compaction_recorder",
    default=None,
)


__all__ = ("CompactionNotice", "ContextCompactionRecorder")
