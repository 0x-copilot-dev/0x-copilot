from __future__ import annotations

import pytest

from agent_runtime.execution.model_invocation.contracts import (
    ModelDispatchState,
    ModelFailureClass,
    ModelFailureSignal,
    ModelStreamState,
)
from agent_runtime.execution.model_invocation.lifecycle import (
    ProviderAttemptLifecycle,
    ProviderLifecycleEvent,
    ProviderLifecycleReducer,
    ProviderLifecycleTransitionError,
)
from agent_runtime.execution.model_invocation.policy import ProviderFailureClassifier


def test_lifecycle_is_monotonic_and_tool_content_counts_as_visible_output() -> None:
    reducer = ProviderLifecycleReducer()
    state = ProviderAttemptLifecycle()
    for event in (
        ProviderLifecycleEvent.DISPATCH_STARTED,
        ProviderLifecycleEvent.DISPATCH_ACKNOWLEDGED,
        ProviderLifecycleEvent.STREAM_STARTED,
        ProviderLifecycleEvent.TOOL_CALL_CONTENT,
        ProviderLifecycleEvent.USAGE_OBSERVED,
    ):
        state = reducer.reduce(state, event)
    failed = reducer.reduce(
        state,
        ProviderLifecycleEvent.FAILED,
        failure_signal=ModelFailureSignal.STREAM_INTERRUPTED,
    )

    observation = failed.failure_observation()
    assert observation.dispatch_state is ModelDispatchState.ACCEPTED
    assert observation.stream_state is ModelStreamState.VISIBLE_OUTPUT
    assert (
        ProviderFailureClassifier().classify(observation)
        is ModelFailureClass.STREAM_INTERRUPTED_AFTER_CONTENT
    )
    assert (
        reducer.reduce(
            failed,
            ProviderLifecycleEvent.FAILED,
            failure_signal=ModelFailureSignal.STREAM_INTERRUPTED,
        )
        is failed
    )


def test_unknown_progress_and_unknown_signal_classify_as_ambiguous() -> None:
    failed = ProviderLifecycleReducer().reduce(
        ProviderLifecycleReducer().reduce(
            ProviderAttemptLifecycle(), ProviderLifecycleEvent.DISPATCH_STARTED
        ),
        ProviderLifecycleEvent.FAILED,
    )
    observation = failed.failure_observation()
    assert observation.dispatch_state is ModelDispatchState.UNKNOWN
    assert observation.signal is ModelFailureSignal.UNKNOWN
    assert (
        ProviderFailureClassifier().classify(observation)
        is ModelFailureClass.AMBIGUOUS_PROVIDER_STATE
    )


@pytest.mark.parametrize(
    "events",
    [
        (ProviderLifecycleEvent.STREAM_STARTED,),
        (
            ProviderLifecycleEvent.DISPATCH_STARTED,
            ProviderLifecycleEvent.VISIBLE_TEXT,
        ),
        (
            ProviderLifecycleEvent.DISPATCH_STARTED,
            ProviderLifecycleEvent.USAGE_OBSERVED,
        ),
    ],
)
def test_impossible_lifecycle_order_is_rejected(
    events: tuple[ProviderLifecycleEvent, ...],
) -> None:
    reducer = ProviderLifecycleReducer()
    state = ProviderAttemptLifecycle()
    with pytest.raises(ProviderLifecycleTransitionError):
        for event in events:
            state = reducer.reduce(state, event)


def test_terminal_lifecycle_cannot_regress_or_change_outcome() -> None:
    reducer = ProviderLifecycleReducer()
    cancelled = reducer.reduce(
        ProviderAttemptLifecycle(), ProviderLifecycleEvent.CANCELLED
    )
    with pytest.raises(ProviderLifecycleTransitionError):
        reducer.reduce(cancelled, ProviderLifecycleEvent.COMPLETED)
