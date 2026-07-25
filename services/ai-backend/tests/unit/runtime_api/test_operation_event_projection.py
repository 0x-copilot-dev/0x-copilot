from __future__ import annotations

import pytest

from agent_runtime.execution.contracts import StreamEventSource
from runtime_api.schemas.common import (
    RuntimeActivityKind,
    RuntimeApiEventType,
    RuntimeEventRedactionState,
)
from runtime_api.schemas.events import RuntimeEventPresentationProjector as P


@pytest.mark.parametrize(
    ("event_type", "payload", "expected"),
    [
        (
            RuntimeApiEventType.OPERATION_REQUESTED,
            {
                "v": 1,
                "operation_id": "op_123",
                "producer": "model",
                "capability": "github",
                "op": "get_issue",
                "args_digest": "a" * 64,
                "parent_operation_id": "op_parent",
                "canonical_args_ref": "operation://secret/args",
                "arguments": {"token": "never"},
            },
            {
                "v": 1,
                "operation_id": "op_123",
                "producer": "model",
                "capability": "github",
                "op": "get_issue",
                "args_digest": "a" * 64,
                "parent_operation_id": "op_parent",
            },
        ),
        (
            RuntimeApiEventType.OPERATION_CLASSIFIED,
            {
                "v": 1,
                "operation_id": "op_123",
                "effect_class": "none",
                "basis": "catalog",
                "confidence": 1.0,
                "reasons": ["raw-argument-never-rides"],
            },
            {
                "v": 1,
                "operation_id": "op_123",
                "effect_class": "none",
                "basis": "catalog",
                "confidence": 1.0,
            },
        ),
        (
            RuntimeApiEventType.OPERATION_COMPLETED,
            {
                "v": 1,
                "operation_id": "op_123",
                "outcome": "succeeded",
                "result_ref": "payload://immutable-result",
                "latency_ms": 12,
                "provider_result": {"secret": "never"},
            },
            {
                "v": 1,
                "operation_id": "op_123",
                "outcome": "succeeded",
                "result_ref": "payload://immutable-result",
                "latency_ms": 12,
            },
        ),
        (
            RuntimeApiEventType.OPERATION_FAILED,
            {
                "v": 1,
                "operation_id": "op_123",
                "failure_code": "operation_adapter_failed",
                "retryable": False,
                "exception": "provider-token=secret",
            },
            {
                "v": 1,
                "operation_id": "op_123",
                "failure_code": "operation_adapter_failed",
                "retryable": False,
            },
        ),
    ],
)
def test_operation_events_use_strict_reference_only_allowlists(
    event_type: RuntimeApiEventType,
    payload: dict[str, object],
    expected: dict[str, object],
) -> None:
    assert P.payload_for_event(event_type=event_type, payload=payload) == expected


@pytest.mark.parametrize(
    "event_type",
    [
        RuntimeApiEventType.OPERATION_REQUESTED,
        RuntimeApiEventType.OPERATION_CLASSIFIED,
        RuntimeApiEventType.OPERATION_COMPLETED,
        RuntimeApiEventType.OPERATION_FAILED,
    ],
)
def test_operation_events_are_ledger_activity(
    event_type: RuntimeApiEventType,
) -> None:
    assert (
        P.activity_kind_for(
            event_type=event_type,
            source=StreamEventSource.TOOL,
        )
        is RuntimeActivityKind.EVENT
    )


def test_only_a_real_result_ref_marks_completion_offloaded() -> None:
    without_ref = P._redaction_state_for(
        payload={
            "v": 1,
            "operation_id": "op_123",
            "outcome": "succeeded",
            "latency_ms": 1,
        },
        metadata={},
    )
    with_ref = P._redaction_state_for(
        payload={
            "v": 1,
            "operation_id": "op_123",
            "outcome": "succeeded",
            "result_ref": "payload://immutable-result",
            "latency_ms": 1,
        },
        metadata={},
    )
    assert without_ref is RuntimeEventRedactionState.REDACTED
    assert with_ref is RuntimeEventRedactionState.OFFLOADED
