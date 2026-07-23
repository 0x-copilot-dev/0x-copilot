"""Unit tests for B2's per-call token accumulator + MODEL_CALL_COMPLETED emit."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest

from agent_runtime.execution.contracts import AgentRuntimeContext
from agent_runtime.observability.attribution import (
    Purpose,
    UsageAttributionContext,
)
from agent_runtime.observability.token_usage import NormalizedTokenUsage
from runtime_api.schemas import (
    AssistantSubagentUsageRollup,
    RunRecord,
    RuntimeApiEventType,
)
from runtime_worker.run_metrics import (
    AssistantRunMetrics,
    PerCallTokenAccumulator,
)
from runtime_worker.streaming_executor import (
    StreamingExecutor,
    _MessageIdExtractor,
)


def _usage(
    *,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cached_input_tokens: int = 0,
) -> NormalizedTokenUsage:
    """Test-helper: build a normalized usage value object inline.

    Sub-PRD 01a replaced the dict-shaped argument on
    ``PerCallTokenAccumulator.observe`` with a typed value object.
    Tests construct it directly rather than going through a provider
    extractor — the extractor path is covered separately in
    ``tests/unit/agent_runtime/observability/test_token_usage_extractors.py``.
    """

    return NormalizedTokenUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cached_input_tokens=cached_input_tokens,
    )


def _subagent_context(
    *,
    task_id: str,
    subagent_slug: str = "researcher",
) -> UsageAttributionContext:
    """Test-helper: build a SUBAGENT_WORK attribution context.

    Sub-PRD 01b: ``PerCallTokenAccumulator.observe`` now takes a
    :class:`UsageAttributionContext` instead of bare ``task_id=``.
    The context's invariants require ``subagent_slug`` and ``task_id``
    when purpose is SUBAGENT_WORK.
    """

    return UsageAttributionContext(
        org_id="org_a",
        user_id="user_1",
        run_id="run-1",
        conversation_id="conv-1",
        trace_id="trace-1",
        purpose=Purpose.SUBAGENT_WORK,
        task_id=task_id,
        subagent_slug=subagent_slug,
    )


def _run_record() -> RunRecord:
    return RunRecord(
        run_id="run-1",
        org_id="org_a",
        user_id="user_1",
        conversation_id="conv-1",
        user_message_id="msg-user-1",
        model_provider="openai",
        model_name="gpt-5.4-mini",
        trace_id="trace-1",
        runtime_context=AgentRuntimeContext(
            org_id="org_a",
            user_id="user_1",
            roles=["employee"],
            model_profile={
                "provider": "openai",
                "model_name": "gpt-5.4-mini",
                "max_input_tokens": 128000,
                "timeout_seconds": 30,
                "temperature": 0,
                "supports_streaming": True,
            },
            run_id="run-1",
            trace_id="trace-1",
        ),
        started_at=datetime(2026, 5, 4, 10, 0, tzinfo=timezone.utc),
    )


class TestPerCallTokenAccumulator:
    def test_buckets_keyed_by_message_id(self) -> None:
        acc = PerCallTokenAccumulator()
        acc.observe(_usage(input_tokens=10, output_tokens=20), message_id="msg-a")
        acc.observe(_usage(input_tokens=5, output_tokens=7), message_id="msg-b")
        slot_a = acc.slot("msg-a")
        slot_b = acc.slot("msg-b")
        assert slot_a is not None and slot_a.input_tokens == 10
        assert slot_b is not None and slot_b.input_tokens == 5

    def test_observe_takes_field_wise_max(self) -> None:
        # Sub-PRD 01a: providers stream cumulative usage; field-wise max
        # protects against a mid-stream chunk that under-reported the
        # running total (e.g. a partial-tool-call chunk that the
        # adapter forgot to populate fully).
        acc = PerCallTokenAccumulator()
        acc.observe(_usage(input_tokens=5), message_id="msg-a")
        acc.observe(
            _usage(input_tokens=12, output_tokens=30),
            message_id="msg-a",
        )
        slot = acc.slot("msg-a")
        assert slot is not None
        assert slot.input_tokens == 12
        assert slot.output_tokens == 30
        # total_tokens is computed: 12 input + 30 output = 42.
        assert slot.total_tokens == 42

    def test_mark_completed_is_idempotent(self) -> None:
        acc = PerCallTokenAccumulator()
        acc.observe(_usage(input_tokens=1, output_tokens=2), message_id="msg-a")
        completed_at = datetime(2026, 5, 4, 10, 5, tzinfo=timezone.utc)
        assert acc.mark_completed("msg-a", completed_at=completed_at) is True
        assert acc.mark_completed("msg-a", completed_at=completed_at) is False

    def test_mark_completed_unknown_message_returns_false(self) -> None:
        acc = PerCallTokenAccumulator()
        assert (
            acc.mark_completed("msg-missing", completed_at=datetime.now(timezone.utc))
            is False
        )

    def test_subagent_rollup_only_includes_tagged_calls(self) -> None:
        acc = PerCallTokenAccumulator()
        # Orchestrator-scope: no context → slot.task_id is None.
        acc.observe(
            _usage(input_tokens=100, output_tokens=200),
            message_id="msg-main",
        )
        # Subagent-scope: context carries task_id="task-x".
        acc.observe(
            _usage(input_tokens=10, output_tokens=20),
            message_id="msg-sub-1",
            context=_subagent_context(task_id="task-x"),
        )
        acc.observe(
            _usage(input_tokens=5, output_tokens=7),
            message_id="msg-sub-2",
            context=_subagent_context(task_id="task-x"),
        )
        rollup = acc.subagent_rollup("task-x")
        assert isinstance(rollup, AssistantSubagentUsageRollup)
        assert rollup.input == 15
        assert rollup.output == 27
        # total_tokens is computed = input + output = 15 + 27.
        assert rollup.total == 42
        assert rollup.call_count == 2

    def test_subagent_rollup_zero_when_no_calls(self) -> None:
        acc = PerCallTokenAccumulator()
        acc.observe(_usage(input_tokens=1), message_id="msg-a")
        rollup = acc.subagent_rollup("task-missing")
        assert rollup.call_count == 0
        assert rollup.input == 0
        assert rollup.total == 0

    def test_finalized_calls_excludes_inflight_slots(self) -> None:
        acc = PerCallTokenAccumulator()
        acc.observe(_usage(input_tokens=1, output_tokens=2), message_id="msg-a")
        acc.observe(_usage(input_tokens=3, output_tokens=4), message_id="msg-b")
        acc.mark_completed("msg-a", completed_at=datetime.now(timezone.utc))
        finalized = acc.finalized_calls()
        assert {slot.message_id for slot in finalized} == {"msg-a"}


class TestAssistantRunMetricsPerCall:
    def test_record_usage_bumps_per_call_when_message_id(self) -> None:
        metrics = AssistantRunMetrics(
            started_at=datetime(2026, 5, 4, 10, 0, tzinfo=timezone.utc)
        )
        metrics.record_usage_from(
            {"usage_metadata": {"input_tokens": 12, "output_tokens": 30}},
            message_id="msg-a",
        )
        # Run-level total reflects the merge.
        assert metrics.input_tokens == 12
        assert metrics.output_tokens == 30
        # Per-call slot stamped with same numbers.
        slot = metrics.per_call.slot("msg-a")
        assert slot is not None and slot.input_tokens == 12

    def test_reconciliation_invariant(self) -> None:
        """sum(per_call) == run_total — B2 acceptance criterion."""

        metrics = AssistantRunMetrics(
            started_at=datetime(2026, 5, 4, 10, 0, tzinfo=timezone.utc)
        )
        metrics.record_usage_from(
            {"usage_metadata": {"input_tokens": 10, "output_tokens": 20}},
            message_id="msg-a",
        )
        metrics.record_usage_from(
            {"usage_metadata": {"input_tokens": 5, "output_tokens": 7}},
            message_id="msg-b",
            context=_subagent_context(task_id="task-x"),
        )
        # Provider streams cumulative; the run-level total tracks the
        # latest call's numbers (not the sum across calls).
        assert metrics.input_tokens == 5
        assert metrics.output_tokens == 7
        # The PER-CALL accumulator gives the per-call breakdown.
        per_call_input = sum(
            slot.input_tokens for slot in metrics.per_call._slots.values()
        )
        assert per_call_input == 15

    def test_model_call_records_built_from_finalized_slots(self) -> None:
        metrics = AssistantRunMetrics(
            started_at=datetime(2026, 5, 4, 10, 0, tzinfo=timezone.utc)
        )
        metrics.record_usage_from(
            {"usage_metadata": {"input_tokens": 10, "output_tokens": 20}},
            message_id="msg-a",
        )
        metrics.per_call.mark_completed(
            "msg-a", completed_at=datetime(2026, 5, 4, 10, 5, tzinfo=timezone.utc)
        )
        records = metrics.model_call_usage_records(_run_record(), trace_id="trace-1")
        assert len(records) == 1
        record = records[0]
        assert record.id == "msg-a"
        assert record.input_tokens == 10
        assert record.output_tokens == 20
        assert record.run_id == "run-1"
        assert record.trace_id == "trace-1"

    def test_per_call_row_carries_user_id_from_run(self) -> None:
        # PRD-A2 FR-G — every per-call row is attributed to the run's user so
        # E3's per-user rollups read it directly (surface_id stays None here).
        metrics = AssistantRunMetrics(
            started_at=datetime(2026, 5, 4, 10, 0, tzinfo=timezone.utc)
        )
        metrics.record_usage_from(
            {"usage_metadata": {"input_tokens": 4, "output_tokens": 8}},
            message_id="msg-a",
        )
        metrics.per_call.mark_completed(
            "msg-a", completed_at=datetime(2026, 5, 4, 10, 5, tzinfo=timezone.utc)
        )
        records = metrics.model_call_usage_records(_run_record(), trace_id="trace-1")
        assert records[0].user_id == "user_1"
        assert records[0].surface_id is None


class TestMessageIdExtractor:
    def test_extracts_from_object_id(self) -> None:
        class _Chunk:
            id = "msg-x"

        assert _MessageIdExtractor.extract(_Chunk()) == "msg-x"

    def test_extracts_from_nested_message(self) -> None:
        class _Inner:
            id = "msg-y"

        class _Chunk:
            message = _Inner()

        assert _MessageIdExtractor.extract(_Chunk()) == "msg-y"

    def test_extracts_from_dict_data(self) -> None:
        chunk = {"data": {"id": "msg-z"}}
        assert _MessageIdExtractor.extract(chunk) == "msg-z"

    def test_returns_none_when_absent(self) -> None:
        assert _MessageIdExtractor.extract({}) is None
        assert _MessageIdExtractor.extract(object()) is None


class _FakeEventProducer:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def append_api_event(self, **kwargs: Any) -> None:
        self.calls.append(kwargs)


class TestModelCallCompletedEmit:
    @pytest.mark.asyncio
    async def test_emits_once_per_message_id(self) -> None:
        producer = _FakeEventProducer()
        metrics = AssistantRunMetrics(
            started_at=datetime(2026, 5, 4, 10, 0, tzinfo=timezone.utc)
        )
        # Record usage for msg-a so the slot exists.
        chunk_with_usage = {
            "id": "msg-a",
            "usage_metadata": {"input_tokens": 5, "output_tokens": 7},
        }
        metrics.record_usage_from(chunk_with_usage, message_id="msg-a")

        await StreamingExecutor._maybe_emit_model_call_completed(
            run=_run_record(),
            metrics=metrics,
            event_producer=producer,
            message_id="msg-a",
            source=chunk_with_usage,
        )
        # Second emission for the same id is a no-op.
        await StreamingExecutor._maybe_emit_model_call_completed(
            run=_run_record(),
            metrics=metrics,
            event_producer=producer,
            message_id="msg-a",
            source=chunk_with_usage,
        )

        assert len(producer.calls) == 1
        call = producer.calls[0]
        assert call["event_type"] is RuntimeApiEventType.MODEL_CALL_COMPLETED
        assert call["payload"]["message_id"] == "msg-a"
        assert call["payload"]["performance_metrics"]["usage"]["input"] == 5

    @pytest.mark.asyncio
    async def test_skips_when_chunk_carries_no_usage(self) -> None:
        producer = _FakeEventProducer()
        metrics = AssistantRunMetrics(
            started_at=datetime(2026, 5, 4, 10, 0, tzinfo=timezone.utc)
        )
        await StreamingExecutor._maybe_emit_model_call_completed(
            run=_run_record(),
            metrics=metrics,
            event_producer=producer,
            message_id="msg-a",
            source={"id": "msg-a"},  # no usage_metadata
        )
        assert producer.calls == []

    @pytest.mark.asyncio
    async def test_skips_when_message_id_is_none(self) -> None:
        producer = _FakeEventProducer()
        metrics = AssistantRunMetrics(
            started_at=datetime(2026, 5, 4, 10, 0, tzinfo=timezone.utc)
        )
        await StreamingExecutor._maybe_emit_model_call_completed(
            run=_run_record(),
            metrics=metrics,
            event_producer=producer,
            message_id=None,
            source={"usage_metadata": {"input_tokens": 1}},
        )
        assert producer.calls == []
