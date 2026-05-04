"""Unit tests for B5's :class:`ConversationContextBuilder`.

The builder is pure: every input is a record, every output is a record.
Tests assert reconciliation invariants and the "unknown context window"
fallback path that the panel renders when pricing isn't seeded.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from agent_runtime.api.usage_service import ConversationContextBuilder
from agent_runtime.persistence.records import (
    CompressionEventRecord,
    ModelPricingRecord,
    RuntimeModelCallUsageRecord,
    RuntimeRunUsageRecord,
)


def _now() -> datetime:
    return datetime(2026, 5, 4, 12, 0, tzinfo=timezone.utc)


def _run(
    *,
    input_tokens: int = 1_000,
    output_tokens: int = 200,
    cached_input_tokens: int = 0,
) -> RuntimeRunUsageRecord:
    completed = _now()
    return RuntimeRunUsageRecord(
        id="run-1",
        org_id="org_a",
        user_id="user_1",
        conversation_id="conv-1",
        run_id="run-1",
        model_provider="openai",
        model_name="gpt-5.4-mini",
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cached_input_tokens=cached_input_tokens,
        total_tokens=input_tokens + output_tokens + cached_input_tokens,
        chunk_count=1,
        duration_ms=1000,
        started_at=completed - timedelta(seconds=1),
        completed_at=completed,
        status="completed",
    )


def _call(
    *,
    id: str,
    input_tokens: int,
    output_tokens: int,
    cached_input_tokens: int = 0,
    subagent_id: str | None = None,
    task_id: str | None = None,
) -> RuntimeModelCallUsageRecord:
    return RuntimeModelCallUsageRecord(
        id=id,
        org_id="org_a",
        run_id="run-1",
        conversation_id="conv-1",
        trace_id="trace-1",
        task_id=task_id,
        subagent_id=subagent_id,
        model_provider="openai",
        model_name="gpt-5.4-mini",
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cached_input_tokens=cached_input_tokens,
        total_tokens=input_tokens + output_tokens + cached_input_tokens,
        duration_ms=400,
    )


def _pricing(*, context_window: int | None = 128_000) -> ModelPricingRecord:
    return ModelPricingRecord(
        provider="openai",
        model_name="gpt-5.4-mini",
        effective_from=_now() - timedelta(days=30),
        input_per_1m_micro_usd=1_000_000,
        output_per_1m_micro_usd=2_000_000,
        context_window_tokens=context_window,
        pricing_version="2026-q1",
    )


class TestEmptyConversation:
    def test_no_runs_returns_zero_slice_and_unknown_headroom(self) -> None:
        response = ConversationContextBuilder.build(
            provider="openai",
            model_name="gpt-5.4-mini",
            latest_run=None,
            per_call_rows=(),
            compression_events=(),
            pricing=None,
        )
        assert response.current.last_run_id is None
        assert response.current.input_tokens == 0
        assert response.current.headroom_pct is None
        assert response.current.available_tokens is None
        assert response.breakdown.by_call == ()
        assert response.breakdown.by_subagent == ()
        assert response.breakdown.compression_events == ()


class TestHeadroom:
    def test_headroom_pct_is_integer_when_pricing_known(self) -> None:
        # 1_000 input + 0 cached used out of 100_000 ⇒ 99% headroom.
        response = ConversationContextBuilder.build(
            provider="openai",
            model_name="gpt-5.4-mini",
            latest_run=_run(input_tokens=1_000),
            per_call_rows=(),
            compression_events=(),
            pricing=_pricing(context_window=100_000),
        )
        assert response.current.headroom_pct == 99
        assert response.current.available_tokens == 99_000
        assert response.model.context_window_tokens == 100_000

    def test_unknown_model_returns_null_headroom(self) -> None:
        response = ConversationContextBuilder.build(
            provider="openai",
            model_name="gpt-5.4-mini",
            latest_run=_run(input_tokens=1_000),
            per_call_rows=(),
            compression_events=(),
            pricing=None,
        )
        assert response.current.headroom_pct is None
        assert response.current.available_tokens is None
        assert response.model.context_window_tokens is None

    def test_estimator_overrun_clamps_to_zero(self) -> None:
        # If a run somehow uses more than the window (estimator drift,
        # mid-run token count revision), headroom is 0, not negative.
        response = ConversationContextBuilder.build(
            provider="openai",
            model_name="gpt-5.4-mini",
            latest_run=_run(input_tokens=200_000),
            per_call_rows=(),
            compression_events=(),
            pricing=_pricing(context_window=100_000),
        )
        assert response.current.headroom_pct == 0
        assert response.current.available_tokens == 0


class TestBreakdown:
    def test_by_call_preserves_order_and_token_split(self) -> None:
        response = ConversationContextBuilder.build(
            provider="openai",
            model_name="gpt-5.4-mini",
            latest_run=_run(input_tokens=300, cached_input_tokens=100),
            per_call_rows=(
                _call(id="c1", input_tokens=100, output_tokens=50),
                _call(
                    id="c2",
                    input_tokens=200,
                    output_tokens=80,
                    cached_input_tokens=100,
                ),
            ),
            compression_events=(),
            pricing=_pricing(),
        )
        ids = [row.event_id for row in response.breakdown.by_call]
        assert ids == ["c1", "c2"]
        # Reconciliation: sum(by_call.input + cached_input) ==
        # current.input + current.cached_input.
        sum_call = sum(
            row.input + row.cached_input for row in response.breakdown.by_call
        )
        assert sum_call == (
            response.current.input_tokens + response.current.cached_input_tokens
        )

    def test_by_subagent_groups_calls_by_subagent_id(self) -> None:
        response = ConversationContextBuilder.build(
            provider="openai",
            model_name="gpt-5.4-mini",
            latest_run=_run(),
            per_call_rows=(
                _call(id="c1", input_tokens=100, output_tokens=50),
                _call(
                    id="c2",
                    input_tokens=200,
                    output_tokens=80,
                    subagent_id="sub-a",
                ),
                _call(
                    id="c3",
                    input_tokens=80,
                    output_tokens=10,
                    subagent_id="sub-a",
                ),
                _call(
                    id="c4",
                    input_tokens=400,
                    output_tokens=200,
                    subagent_id="sub-b",
                ),
            ),
            compression_events=(),
            pricing=_pricing(),
        )
        # Sorted by total descending; sub-b > sub-a in total tokens.
        names = [row.subagent_id for row in response.breakdown.by_subagent]
        assert names == ["sub-b", "sub-a"]
        sub_a = next(
            row for row in response.breakdown.by_subagent if row.subagent_id == "sub-a"
        )
        assert sub_a.call_count == 2
        assert sub_a.total == 280 + 90  # totals from c2 and c3

    def test_compression_events_pass_through(self) -> None:
        event = CompressionEventRecord(
            run_id="run-1",
            org_id="org_a",
            before_tokens=50_000,
            after_tokens=12_000,
            strategy="summarize-oldest",
        )
        response = ConversationContextBuilder.build(
            provider="openai",
            model_name="gpt-5.4-mini",
            latest_run=_run(),
            per_call_rows=(),
            compression_events=(event,),
            pricing=_pricing(),
        )
        assert len(response.breakdown.compression_events) == 1
        row = response.breakdown.compression_events[0]
        assert row.before == 50_000
        assert row.after == 12_000
        assert row.strategy == "summarize-oldest"
