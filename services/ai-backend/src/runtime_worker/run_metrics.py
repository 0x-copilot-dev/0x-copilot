"""Assistant response metrics collected during runtime execution.

Sub-PRD 01a — token extraction is now centralized in
:mod:`agent_runtime.observability.token_usage`. This module is the
worker-side accumulator: it observes a normalized usage value object
per chunk, dedupes per-AIMessage, and materializes per-call /
per-run / per-subagent records.

Sub-PRD 01b — per-call slots also carry a typed
:class:`UsageAttributionContext` built at emit time by the streaming
executor. ``model_call_usage_records`` materializes the attribution
columns (``subagent_id``, ``connector_slug``, ``purpose``,
``originating_tool_call_id``, ``originating_tool_name``) from the
stamped context. The slot's own ``task_id`` continues to come from
LangGraph chunk metadata (no more ``next(iter(active_subagent_tasks))``
arbitration).

The provider-coupled walker that used to live here as
``TokenUsageExtractor`` is gone. Use
:class:`agent_runtime.observability.token_usage.TokenUsageExtractorRegistry`
to obtain an extractor for a given provider slug.
"""

from __future__ import annotations

from datetime import datetime, timezone

from agent_runtime.execution.contracts import JsonObject
from agent_runtime.observability.attribution import (
    UsageAttributionContext,
)
from agent_runtime.observability.token_usage import (
    NormalizedTokenUsage,
    TokenUsageExtractorRegistry,
)
from agent_runtime.persistence.records import (
    RuntimeModelCallUsageRecord,
    RuntimeRunUsageRecord,
)
from runtime_api.schemas import (
    AssistantPerformanceMetrics,
    AssistantSubagentUsageRollup,
    AssistantUsageMetrics,
    RunRecord,
)


class _PerCallSlot:
    """One per-AIMessage usage bucket inside ``PerCallTokenAccumulator``.

    Holds the latest provider-reported counts for a single LLM call.
    Counts are *merged* on each ``observe`` (field-wise max) because
    providers stream cumulative usage across chunks of the same
    AIMessage.

    Sub-PRD 01b: the slot also carries a typed
    :class:`UsageAttributionContext` stamped by the streaming executor
    at emit time. The context populates the ``subagent_id``,
    ``connector_slug``, ``purpose``, and ``originating_tool_*`` columns
    on the materialized row. ``task_id`` is read from the context too
    so the row's per-task attribution comes from chunk metadata, not
    from worker-local set arbitration.
    """

    __slots__ = (
        "message_id",
        "usage",
        "started_at",
        "completed_at",
        "context",
    )

    def __init__(
        self,
        *,
        message_id: str,
        started_at: datetime | None = None,
    ) -> None:
        self.message_id = message_id
        self.usage: NormalizedTokenUsage = NormalizedTokenUsage()
        self.started_at = started_at
        self.completed_at: datetime | None = None
        self.context: UsageAttributionContext | None = None

    # Convenience accessors so existing call sites (and tests) can read
    # the context's fields off the slot directly. ``task_id`` and the
    # other attribution fields fall through to the stamped context;
    # callers that consult them before context is stamped see ``None``.

    @property
    def task_id(self) -> str | None:
        return self.context.task_id if self.context is not None else None

    @property
    def subagent_id(self) -> str | None:
        return self.context.subagent_slug if self.context is not None else None

    @property
    def connector_slug(self) -> str | None:
        return self.context.connector_slug if self.context is not None else None

    @property
    def purpose(self) -> str:
        return self.context.purpose.value if self.context is not None else "main"

    @property
    def originating_tool_call_id(self) -> str | None:
        return (
            self.context.originating_tool_call_id if self.context is not None else None
        )

    @property
    def originating_tool_name(self) -> str | None:
        return self.context.originating_tool_name if self.context is not None else None

    # Convenience accessors so callers and tests can read individual
    # kinds without unpacking ``usage`` every time. Keeps prior call
    # sites working after the slot's internal storage moved to a
    # single value object.
    @property
    def input_tokens(self) -> int:
        return self.usage.input_tokens

    @property
    def output_tokens(self) -> int:
        return self.usage.output_tokens

    @property
    def cached_input_tokens(self) -> int:
        return self.usage.cached_input_tokens

    @property
    def cache_creation_input_tokens(self) -> int:
        return self.usage.cache_creation_input_tokens

    @property
    def reasoning_tokens(self) -> int:
        return self.usage.reasoning_tokens

    @property
    def audio_input_tokens(self) -> int:
        return self.usage.audio_input_tokens

    @property
    def audio_output_tokens(self) -> int:
        return self.usage.audio_output_tokens

    @property
    def total_tokens(self) -> int:
        return self.usage.total_tokens


class PerCallTokenAccumulator:
    """Per-AIMessage token bucket keyed by ``message.id`` (B2).

    The streaming loop calls ``observe(usage, message_id=...)`` once
    per chunk that carries usage. The accumulator dedupes by
    ``message_id`` so the per-call row is emitted exactly once per
    LLM call regardless of how many stream chunks the provider sent.

    ``finalized_calls()`` yields the closed slots — calls whose
    AIMessage has been seen with usage at least once and is now ready
    to be written to ``runtime_model_call_usage``. ``mark_completed``
    flips a slot from in-flight to closed; the streaming executor
    calls it when it emits the ``MODEL_CALL_COMPLETED`` event.
    """

    def __init__(self) -> None:
        self._slots: dict[str, _PerCallSlot] = {}
        self._completed_message_ids: set[str] = set()

    def observe(
        self,
        usage: NormalizedTokenUsage,
        *,
        message_id: str,
        context: UsageAttributionContext | None = None,
        started_at: datetime | None = None,
    ) -> _PerCallSlot:
        """Merge ``usage`` into the slot for ``message_id`` and stamp
        ``context`` (if provided).

        Sub-PRD 01b: ``context`` replaces the prior per-arg
        ``task_id=...`` stamping. The context carries every attribution
        dimension; the slot reads via property accessors. A subsequent
        ``observe`` with a different context overwrites — by design,
        the LATEST emit's context wins (a stream chunk shouldn't ever
        change attribution mid-message, but if the streaming executor
        re-stamps with refined context closer to message close, that's
        the more accurate stamp).
        """

        slot = self._slots.get(message_id)
        if slot is None:
            slot = _PerCallSlot(message_id=message_id, started_at=started_at)
            self._slots[message_id] = slot
        if context is not None:
            slot.context = context
        # Field-wise max preserves the prior "last write wins for
        # cumulative providers" semantic while protecting against a
        # mid-stream chunk that reported a smaller running total than
        # a later one.
        slot.usage = slot.usage.merge(usage)
        return slot

    def mark_completed(self, message_id: str, *, completed_at: datetime) -> bool:
        """Return True iff ``message_id`` was newly transitioned to completed."""

        if message_id in self._completed_message_ids:
            return False
        slot = self._slots.get(message_id)
        if slot is None:
            return False
        slot.completed_at = completed_at
        self._completed_message_ids.add(message_id)
        return True

    def has_seen(self, message_id: str) -> bool:
        return message_id in self._slots

    def slot(self, message_id: str) -> _PerCallSlot | None:
        return self._slots.get(message_id)

    def finalized_calls(self) -> tuple[_PerCallSlot, ...]:
        return tuple(
            slot
            for message_id, slot in self._slots.items()
            if message_id in self._completed_message_ids
        )

    def subagent_rollup(self, task_id: str) -> AssistantSubagentUsageRollup:
        """Sum per-call usage attributed to ``task_id`` (B2 spec §2.3).

        Sub-PRD 01b: ``slot.task_id`` is read from the slot's stamped
        :class:`UsageAttributionContext` (chunk-namespace-derived) —
        no more worker-local set arbitration.

        The wire schema (``AssistantSubagentUsageRollup``) carries
        input / output / cached_input / total only — the four new
        kinds aren't surfaced to the FE until 01d. The captured rows
        (``runtime_model_call_usage``) DO carry them, so per-subagent
        SQL queries can already access them.
        """

        input_tokens = 0
        output_tokens = 0
        cached_input_tokens = 0
        total_tokens = 0
        call_count = 0
        for slot in self._slots.values():
            if slot.task_id != task_id:
                continue
            input_tokens += slot.usage.input_tokens
            output_tokens += slot.usage.output_tokens
            cached_input_tokens += slot.usage.cached_input_tokens
            total_tokens += slot.usage.total_tokens
            call_count += 1
        return AssistantSubagentUsageRollup(
            input=input_tokens,
            output=output_tokens,
            cached_input=cached_input_tokens,
            total=total_tokens,
            call_count=call_count,
        )


class AssistantRunMetrics:
    """Accumulate timing, chunk, and exact provider usage for one assistant run."""

    PERFORMANCE_KEY = "performance_metrics"

    def __init__(
        self,
        *,
        started_at: datetime,
        provider: str = "",
    ) -> None:
        self.started_at = started_at
        self.provider = provider
        self._extractor = TokenUsageExtractorRegistry.for_provider(provider)
        self.first_token_at: datetime | None = None
        self.chunk_count = 0
        self.usage: NormalizedTokenUsage = NormalizedTokenUsage()
        self.per_call = PerCallTokenAccumulator()

    @classmethod
    def from_run(cls, run: RunRecord) -> "AssistantRunMetrics":
        """Create metrics from the persisted run start timestamp + provider."""

        return cls(
            started_at=run.started_at or datetime.now(timezone.utc),
            provider=run.model_provider,
        )

    # Convenience accessors so existing call sites that read individual
    # kinds off ``metrics.input_tokens`` keep working.
    @property
    def input_tokens(self) -> int:
        return self.usage.input_tokens

    @property
    def output_tokens(self) -> int:
        return self.usage.output_tokens

    @property
    def cached_input_tokens(self) -> int:
        return self.usage.cached_input_tokens

    @property
    def cache_creation_input_tokens(self) -> int:
        return self.usage.cache_creation_input_tokens

    @property
    def reasoning_tokens(self) -> int:
        return self.usage.reasoning_tokens

    @property
    def audio_input_tokens(self) -> int:
        return self.usage.audio_input_tokens

    @property
    def audio_output_tokens(self) -> int:
        return self.usage.audio_output_tokens

    @property
    def total_tokens(self) -> int:
        return self.usage.total_tokens

    def record_model_delta(self, delta: str) -> None:
        """Record a non-empty top-level model text chunk."""

        if delta == "":
            return
        now = datetime.now(timezone.utc)
        self.chunk_count += 1
        if self.first_token_at is None:
            self.first_token_at = now

    def record_usage_from(
        self,
        value: object,
        *,
        message_id: str | None = None,
        context: UsageAttributionContext | None = None,
    ) -> None:
        """Capture provider token usage when present on a stream object.

        Sub-PRD 01b: ``context`` replaces the prior ``task_id=`` arg.
        The streaming executor builds a
        :class:`UsageAttributionContext` from chunk metadata + ledger
        state and hands it in. The context stamps onto the slot; the
        row builder reads attribution columns from it.

        The extractor returns a :class:`NormalizedTokenUsage` or
        ``None`` (no usage block on this chunk — nothing to record).
        """

        usage = self._extractor.extract(value)
        if usage is None:
            return
        self.usage = usage  # last-write-wins replace at run level.
        if message_id is not None:
            self.per_call.observe(
                usage,
                message_id=message_id,
                context=context,
                started_at=datetime.now(timezone.utc),
            )

    def model_call_usage_records(
        self,
        run: RunRecord,
        *,
        trace_id: str,
    ) -> tuple[RuntimeModelCallUsageRecord, ...]:
        """Build one ``runtime_model_call_usage`` row per finalized call (B2).

        Sub-PRD 01b: attribution columns (``task_id``, ``subagent_id``,
        ``connector_slug``, ``purpose``, ``originating_tool_*``) come
        from the slot's stamped :class:`UsageAttributionContext`. Slots
        without a context (e.g. recorded before the streaming executor
        had a chance to build one) fall back to the ``Purpose.MAIN``
        defaults the column has.
        """

        records: list[RuntimeModelCallUsageRecord] = []
        for slot in self.per_call.finalized_calls():
            completed_at = slot.completed_at or datetime.now(timezone.utc)
            duration_ms = (
                self._duration_ms(slot.started_at, completed_at)
                if slot.started_at is not None
                else 0
            )
            records.append(
                RuntimeModelCallUsageRecord(
                    id=slot.message_id,
                    org_id=run.org_id,
                    run_id=run.run_id,
                    conversation_id=run.conversation_id,
                    trace_id=trace_id,
                    task_id=slot.task_id,
                    subagent_id=slot.subagent_id,
                    model_provider=run.model_provider,
                    model_name=run.model_name,
                    connector_slug=slot.connector_slug,
                    purpose=slot.purpose,
                    originating_tool_call_id=slot.originating_tool_call_id,
                    originating_tool_name=slot.originating_tool_name,
                    input_tokens=slot.usage.input_tokens,
                    output_tokens=slot.usage.output_tokens,
                    cached_input_tokens=slot.usage.cached_input_tokens,
                    cache_creation_input_tokens=slot.usage.cache_creation_input_tokens,
                    reasoning_tokens=slot.usage.reasoning_tokens,
                    audio_input_tokens=slot.usage.audio_input_tokens,
                    audio_output_tokens=slot.usage.audio_output_tokens,
                    total_tokens=slot.usage.total_tokens,
                    duration_ms=duration_ms,
                    created_at=completed_at,
                )
            )
        return tuple(records)

    def to_payload(self, *, completed_at: datetime | None = None) -> JsonObject:
        """Return the public JSON metrics payload."""

        end = completed_at or datetime.now(timezone.utc)
        duration_ms = self._duration_ms(self.started_at, end)
        first_token_ms = (
            self._duration_ms(self.started_at, self.first_token_at)
            if self.first_token_at is not None
            else None
        )
        output_per_second = self._tokens_per_second(
            output_tokens=self.usage.output_tokens,
            duration_ms=duration_ms,
        )
        usage_payload = self._usage_payload(output_per_second=output_per_second)
        return AssistantPerformanceMetrics(
            started_at=self.started_at,
            completed_at=end,
            duration_ms=duration_ms,
            chunk_count=self.chunk_count,
            first_chunk_at=self.first_token_at,
            first_chunk_ms=first_token_ms,
            usage=usage_payload,
        ).model_dump(mode="json", exclude_none=True)

    @classmethod
    def metadata(cls, metrics: JsonObject) -> JsonObject:
        """Return the assistant message/event metadata wrapper."""

        return {cls.PERFORMANCE_KEY: metrics}

    @classmethod
    def with_payload(cls, payload: JsonObject, metrics: JsonObject) -> JsonObject:
        """Attach metrics to an existing event payload."""

        return {**payload, cls.PERFORMANCE_KEY: metrics}

    def to_usage_record(
        self,
        run: RunRecord,
        *,
        completed_at: datetime,
        status: str,
    ) -> RuntimeRunUsageRecord:
        """Build the per-run usage row at ``RUN_COMPLETED`` time (B1).

        Reads from the same accumulator that backs ``to_payload`` so
        the denormalized row and the event payload always agree.
        Token fields default to 0 when the provider didn't report
        usage (the row is still useful for ``runs_count`` / latency
        aggregates).
        """

        duration_ms = self._duration_ms(self.started_at, completed_at)
        first_token_ms = (
            self._duration_ms(self.started_at, self.first_token_at)
            if self.first_token_at is not None
            else None
        )
        return RuntimeRunUsageRecord(
            id=run.run_id,
            org_id=run.org_id,
            user_id=run.user_id,
            conversation_id=run.conversation_id,
            run_id=run.run_id,
            assistant_id=getattr(run.runtime_context, "assistant_id", None),
            model_provider=run.model_provider,
            model_name=run.model_name,
            input_tokens=self.usage.input_tokens,
            output_tokens=self.usage.output_tokens,
            cached_input_tokens=self.usage.cached_input_tokens,
            cache_creation_input_tokens=self.usage.cache_creation_input_tokens,
            reasoning_tokens=self.usage.reasoning_tokens,
            audio_input_tokens=self.usage.audio_input_tokens,
            audio_output_tokens=self.usage.audio_output_tokens,
            total_tokens=self.usage.total_tokens,
            chunk_count=self.chunk_count,
            first_token_ms=first_token_ms,
            duration_ms=duration_ms,
            started_at=self.started_at,
            completed_at=completed_at,
            status=status,
            created_at=completed_at,
        )

    def chunk_has_usage(self, value: object) -> bool:
        """Return True iff this chunk carries a usage block.

        Used by the streaming executor to gate
        ``MODEL_CALL_COMPLETED`` emission — only emit on a chunk that
        actually closed the call. Sub-PRD 01a moved this from a
        free-standing class method on ``TokenUsageExtractor`` to an
        instance method on the metrics object, so the provider-aware
        extractor is the one making the decision.
        """

        return self._extractor.extract(value) is not None

    def _usage_payload(
        self,
        *,
        output_per_second: float | None,
    ) -> AssistantUsageMetrics | None:
        u = self.usage
        if (
            u.input_tokens == 0
            and u.output_tokens == 0
            and u.cached_input_tokens == 0
            and output_per_second is None
        ):
            return None
        # Wire shape unchanged in 01a — 01d adds reasoning/cache_creation/audio.
        return AssistantUsageMetrics(
            input=u.input_tokens or None,
            output=u.output_tokens or None,
            total=u.total_tokens or None,
            cached_input=u.cached_input_tokens or None,
            output_per_second=output_per_second,
        )

    @staticmethod
    def _duration_ms(started_at: datetime, completed_at: datetime | None) -> int:
        if completed_at is None:
            return 0
        return max(0, round((completed_at - started_at).total_seconds() * 1000))

    @staticmethod
    def _tokens_per_second(
        *,
        output_tokens: int,
        duration_ms: int,
    ) -> float | None:
        if output_tokens <= 0 or duration_ms <= 0:
            return None
        return round(output_tokens / (duration_ms / 1000), 2)
