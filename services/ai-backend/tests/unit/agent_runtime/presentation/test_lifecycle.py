"""Replay-prefix and lifecycle journeys for the B3 Canvas projection."""

from __future__ import annotations

from agent_runtime.presentation.lifecycle import (
    CanvasLifecycleProjection,
    CanvasLifecycleState,
    CanvasSubjectKind,
)


def _event(sequence_no: int, event_type: str, **payload: object) -> dict[str, object]:
    return {
        "sequence_no": sequence_no,
        "event_type": event_type,
        "payload": payload,
    }


def test_artifact_requires_explicit_presentation_decision_and_revisions_keep_identity() -> (
    None
):
    events = [
        _event(1, "artifact.created", artifact_id="art_1", kind="code", revision=1),
        _event(2, "artifact.revised", artifact_id="art_1", revision=2),
        _event(
            3,
            "artifact.presentation_decided",
            artifact_id="art_1",
            decision="canvas",
        ),
        _event(4, "artifact.revised", artifact_id="art_1", revision=3),
    ]
    before = CanvasLifecycleProjection.fold(events[:2])
    after = CanvasLifecycleProjection.fold(events)
    assert before.lifecycle is CanvasLifecycleState.ASSEMBLING
    assert before.tabs == ()
    assert [(subject.key, subject.revision) for subject in after.tabs] == [
        ("artifact:art_1", 3)
    ]
    assert after.active_subject_key == "artifact:art_1"


def test_stage_and_gate_park_then_terminal_receipt_never_steals_active_subject() -> (
    None
):
    events = [
        _event(
            1,
            "surface.created",
            surface_id="surface://read",
            kind="table",
            title="Deals",
        ),
        _event(
            2,
            "write.staged",
            stage_id="stage_1",
            display_target="Update deal",
            revision=1,
        ),
        _event(3, "gate.opened", gate_id="gate_1"),
        _event(4, "gate.resolved", gate_id="gate_1"),
        _event(5, "write.applied", stage_id="stage_1"),
        _event(
            6,
            "surface.created",
            surface_id="receipt://run",
            kind="receipt",
            title="Run receipt",
        ),
        _event(7, "receipt.emitted", surface_id="receipt://run"),
        _event(8, "run_completed", status="completed"),
    ]
    parked = CanvasLifecycleProjection.fold(events[:3])
    completed = CanvasLifecycleProjection.fold(events)
    assert parked.lifecycle is CanvasLifecycleState.PARKED
    assert parked.pending_subject_keys == ("effect:stage_1", "gate:gate_1")
    assert [(subject.kind, subject.key) for subject in completed.tabs] == [
        (CanvasSubjectKind.EFFECT, "effect:stage_1"),
        (CanvasSubjectKind.SURFACE, "surface:surface://read"),
    ]
    assert completed.active_subject_key == "effect:stage_1"
    assert completed.terminal_receipt is not None
    assert completed.terminal_receipt.kind is CanvasSubjectKind.RECEIPT


def test_chat_only_complete_empty_and_failed_are_explicit_terminal_states() -> None:
    chat_only = CanvasLifecycleProjection.fold(
        [_event(1, "final_response"), _event(2, "run_completed", status="completed")]
    )
    empty = CanvasLifecycleProjection.fold(
        [_event(1, "run_completed", status="completed")]
    )
    failed = CanvasLifecycleProjection.fold(
        [
            _event(1, "operation.failed", safe_message="Safe failure"),
            _event(2, "run_failed"),
        ]
    )
    assert chat_only.lifecycle is CanvasLifecycleState.CHAT_ONLY
    assert empty.lifecycle is CanvasLifecycleState.COMPLETE_EMPTY
    assert (failed.lifecycle, failed.failure) == (
        CanvasLifecycleState.FAILED,
        "Safe failure",
    )


def test_retry_error_is_replay_safe_and_does_not_make_a_canvas_subject() -> None:
    projection = CanvasLifecycleProjection.fold(
        [
            _event(1, "tool_call_started"),
            _event(2, "tool_result", status="failed", error_message="Tool unavailable"),
            _event(3, "run_completed", status="failed"),
        ]
    )
    assert projection.lifecycle is CanvasLifecycleState.FAILED
    assert projection.failure == "Tool unavailable"
    assert projection.tabs == ()


def test_every_replay_prefix_is_deterministic_and_preserves_tab_order() -> None:
    events = [
        _event(1, "artifact.created", artifact_id="art_b", kind="document", revision=1),
        _event(
            2, "artifact.presentation_decided", artifact_id="art_b", decision="canvas"
        ),
        _event(
            3, "surface.created", surface_id="surface://a", kind="record", title="A"
        ),
        _event(4, "artifact.revised", artifact_id="art_b", revision=2),
        _event(5, "final_response"),
        _event(6, "run_completed", status="completed"),
    ]
    tab_orders: list[tuple[str, ...]] = []
    for end in range(len(events) + 1):
        first = CanvasLifecycleProjection.fold(events[:end])
        replayed = CanvasLifecycleProjection.fold(list(events[:end]))
        assert first == replayed
        tab_orders.append(tuple(subject.key for subject in first.tabs))
    assert tab_orders[2] == ("artifact:art_b",)
    assert tab_orders[-1] == ("artifact:art_b", "surface:surface://a")
