"""Tests for bounding tool results before LangChain creates ToolMessage."""

from __future__ import annotations

from hashlib import sha256
import json

import pytest

from agent_runtime.context.memory import (
    ContextCompressionStrategy,
    ContextPayloadManager,
    TokenBudgetPolicy,
)
from agent_runtime.context.tool_result_admission import (
    ToolResultAdmissionAdapter,
)


def _small_policy() -> TokenBudgetPolicy:
    # recent_context_tokens == 1,000; approximately 4,000 characters.
    return TokenBudgetPolicy(
        max_input_tokens=4_000,
        recent_context_ratio=0.25,
        summary_threshold_ratio=0.85,
    )


def test_small_result_remains_byte_identical_and_does_not_write() -> None:
    writes: list[str] = []
    adapter = ToolResultAdmissionAdapter(
        lambda content: writes.append(content) or "/large_tool_results/unused",
        policy=_small_policy(),
    )

    admitted = adapter.admit("small result", trace_id="trace-1")

    assert admitted.strategy is ContextCompressionStrategy.INLINE
    assert admitted.model_content == "small result"
    assert admitted.event_content == "small result"
    assert admitted.output_ref is None
    assert admitted.preview is None
    assert writes == []


def test_large_one_line_result_is_stored_before_bounded_admission() -> None:
    writes: list[str] = []
    unique_tail = "UNIQUE_TAIL_DO_NOT_ADMIT"
    raw = '{"results":"' + ("sensitive-result-" * 2_000) + unique_tail + '"}'
    reference = "/large_tool_results/" + "a" * 64
    adapter = ToolResultAdmissionAdapter(
        lambda content: writes.append(content) or reference,
        policy=_small_policy(),
    )

    admitted = adapter.admit(raw, trace_id="trace-1")

    assert writes == [raw]
    assert admitted.strategy is ContextCompressionStrategy.OFFLOAD
    assert admitted.output_ref == reference
    assert admitted.source_digest == sha256(raw.encode("utf-8")).hexdigest()
    assert admitted.preview is not None
    assert len(admitted.preview) <= ContextPayloadManager.PREVIEW_CHAR_LIMIT
    assert len(admitted.model_content) <= admitted.model_content_limit_chars
    assert reference in admitted.model_content
    assert admitted.preview in admitted.model_content
    # Neither the admission contract nor its event projection retains the tail.
    assert unique_tail not in admitted.model_content
    assert unique_tail not in admitted.event_content
    assert unique_tail not in admitted.model_dump_json()


def test_structured_results_use_deterministic_canonical_order() -> None:
    writes: list[str] = []
    adapter = ToolResultAdmissionAdapter(
        lambda content: writes.append(content) or "/large_tool_results/result",
        policy=TokenBudgetPolicy(
            max_input_tokens=8,
            recent_context_ratio=0.25,
            summary_threshold_ratio=0.85,
        ),
    )

    admitted = adapter.admit(
        {"z": [3, 2, 1], "a": {"b": True}},
        trace_id="trace-1",
    )

    assert writes == ['{"a":{"b":true},"z":[3,2,1]}']
    assert json.loads(writes[0]) == {"a": {"b": True}, "z": [3, 2, 1]}
    assert admitted.output_ref == "/large_tool_results/result"


def test_empty_string_is_a_valid_bounded_inline_result() -> None:
    adapter = ToolResultAdmissionAdapter(
        lambda _content: pytest.fail("empty output must not be offloaded"),
        policy=_small_policy(),
    )

    admitted = adapter.admit("", trace_id="trace-1")

    assert admitted.model_content == ""
    assert admitted.event.before_tokens == 0
    assert admitted.event.after_tokens == 0


def test_offload_failure_propagates_instead_of_returning_raw_result() -> None:
    def fail_writer(_content: str) -> str:
        raise OSError("object store unavailable")

    adapter = ToolResultAdmissionAdapter(fail_writer, policy=_small_policy())

    with pytest.raises(OSError, match="object store unavailable"):
        adapter.admit("x" * 10_000, trace_id="trace-1")


def test_context_payload_preview_is_character_bounded_for_minified_output() -> None:
    raw = "x" * 20_000
    managed = ContextPayloadManager.prepare_tool_output(
        content=raw,
        policy=_small_policy(),
        trace_id="trace-1",
        offload_writer=lambda _content: "/large_tool_results/result",
    )

    assert managed.strategy is ContextCompressionStrategy.OFFLOAD
    assert managed.preview == "x" * ContextPayloadManager.PREVIEW_CHAR_LIMIT
