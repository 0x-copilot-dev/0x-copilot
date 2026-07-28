"""Monotonic, content-free attestation of one provider invocation lifecycle."""

from __future__ import annotations

from enum import StrEnum
from typing import Self

from pydantic import model_validator

from agent_runtime.execution.contracts import RuntimeContract
from agent_runtime.execution.model_invocation.contracts import (
    ModelDispatchState,
    ModelFailureSignal,
    ModelStreamState,
    ProviderFailureObservation,
)


class ProviderLifecycleEvent(StrEnum):
    """Facts observable at the concrete provider/model boundary."""

    DISPATCH_STARTED = "dispatch_started"
    DISPATCH_NOT_ACCEPTED = "dispatch_not_accepted"
    DISPATCH_ACKNOWLEDGED = "dispatch_acknowledged"
    STREAM_STARTED = "stream_started"
    VISIBLE_TEXT = "visible_text"
    TOOL_CALL_CONTENT = "tool_call_content"
    USAGE_OBSERVED = "usage_observed"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"


class ProviderTerminalState(StrEnum):
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"


class ProviderLifecycleTransitionError(ValueError):
    """Raised when an observer reports an impossible or regressive transition."""


class ProviderAttemptLifecycle(RuntimeContract):
    """Provider progress attested without retaining prompts, output, or exceptions."""

    dispatch_started: bool = False
    dispatch_state: ModelDispatchState = ModelDispatchState.BEFORE_DISPATCH
    stream_started: bool = False
    visible_text_observed: bool = False
    tool_call_content_observed: bool = False
    usage_observed: bool = False
    terminal_state: ProviderTerminalState | None = None
    failure_signal: ModelFailureSignal | None = None

    @property
    def visible_output_observed(self) -> bool:
        return self.visible_text_observed or self.tool_call_content_observed

    @property
    def stream_state(self) -> ModelStreamState:
        if self.visible_output_observed:
            return ModelStreamState.VISIBLE_OUTPUT
        if self.stream_started:
            return ModelStreamState.STARTED_NO_VISIBLE_OUTPUT
        return ModelStreamState.NOT_STARTED

    @model_validator(mode="after")
    def _validate_attestation(self) -> Self:
        if (
            self.dispatch_state is ModelDispatchState.ACCEPTED
            and not self.dispatch_started
        ):
            raise ValueError("accepted dispatch requires dispatch_started")
        if (
            self.stream_started
            and self.dispatch_state is not ModelDispatchState.ACCEPTED
        ):
            raise ValueError("stream start requires accepted dispatch")
        if (
            self.visible_output_observed
            and self.dispatch_state is not ModelDispatchState.ACCEPTED
        ):
            raise ValueError("visible content requires accepted dispatch")
        if (
            self.usage_observed
            and self.dispatch_state is not ModelDispatchState.ACCEPTED
        ):
            raise ValueError("usage requires accepted dispatch")
        if self.terminal_state is ProviderTerminalState.FAILED:
            if self.failure_signal is None:
                raise ValueError("failed lifecycle requires a sanitized failure signal")
        elif self.failure_signal is not None:
            raise ValueError("failure signal is valid only for a failed lifecycle")
        return self

    def failure_observation(self) -> ProviderFailureObservation:
        """Return the existing F10 classifier input from attested progress."""

        if self.terminal_state is not ProviderTerminalState.FAILED:
            raise ValueError("failure observation requires a failed lifecycle")
        assert self.failure_signal is not None
        return ProviderFailureObservation(
            signal=self.failure_signal,
            dispatch_state=self.dispatch_state,
            stream_state=self.stream_state,
        )


class ProviderLifecycleReducer:
    """Pure reducer that permits idempotence but rejects state regression."""

    def reduce(
        self,
        state: ProviderAttemptLifecycle,
        event: ProviderLifecycleEvent,
        *,
        failure_signal: ModelFailureSignal | None = None,
    ) -> ProviderAttemptLifecycle:
        if state.terminal_state is not None:
            if self._is_idempotent_terminal(state, event, failure_signal):
                return state
            raise ProviderLifecycleTransitionError(
                "provider lifecycle cannot advance after a terminal event"
            )
        if event is not ProviderLifecycleEvent.FAILED and failure_signal is not None:
            raise ProviderLifecycleTransitionError(
                "failure_signal is accepted only with the failed event"
            )
        values = state.model_dump()
        if event is ProviderLifecycleEvent.DISPATCH_STARTED:
            if state.dispatch_started:
                return state
            values["dispatch_started"] = True
            values["dispatch_state"] = ModelDispatchState.UNKNOWN
        elif event is ProviderLifecycleEvent.DISPATCH_ACKNOWLEDGED:
            self._require(state.dispatch_started, "dispatch acknowledgement")
            values["dispatch_state"] = ModelDispatchState.ACCEPTED
        elif event is ProviderLifecycleEvent.DISPATCH_NOT_ACCEPTED:
            self._require(state.dispatch_started, "dispatch rejection")
            self._require(not state.stream_started, "dispatch rejection")
            values["dispatch_state"] = ModelDispatchState.NOT_ACCEPTED
        elif event is ProviderLifecycleEvent.STREAM_STARTED:
            self._require(
                state.dispatch_state is ModelDispatchState.ACCEPTED, "stream start"
            )
            values["stream_started"] = True
        elif event in {
            ProviderLifecycleEvent.VISIBLE_TEXT,
            ProviderLifecycleEvent.TOOL_CALL_CONTENT,
        }:
            self._require(
                state.dispatch_state is ModelDispatchState.ACCEPTED, "visible content"
            )
            field = (
                "visible_text_observed"
                if event is ProviderLifecycleEvent.VISIBLE_TEXT
                else "tool_call_content_observed"
            )
            values[field] = True
        elif event is ProviderLifecycleEvent.USAGE_OBSERVED:
            self._require(
                state.dispatch_state is ModelDispatchState.ACCEPTED, "usage observation"
            )
            values["usage_observed"] = True
        elif event is ProviderLifecycleEvent.COMPLETED:
            self._require(
                state.dispatch_state is ModelDispatchState.ACCEPTED, "completion"
            )
            values["terminal_state"] = ProviderTerminalState.COMPLETED
        elif event is ProviderLifecycleEvent.CANCELLED:
            values["terminal_state"] = ProviderTerminalState.CANCELLED
        elif event is ProviderLifecycleEvent.FAILED:
            values["terminal_state"] = ProviderTerminalState.FAILED
            values["failure_signal"] = failure_signal or ModelFailureSignal.UNKNOWN
        else:  # pragma: no cover - closed enum guards this
            raise ProviderLifecycleTransitionError("unsupported lifecycle event")
        return ProviderAttemptLifecycle.model_validate(values)

    def refine_cache_rejection(
        self,
        state: ProviderAttemptLifecycle,
    ) -> ProviderAttemptLifecycle:
        """Apply late typed proof that dispatch rejected cache metadata.

        Provider callbacks can report a generic failure before the SDK raises
        to F10. This refinement is monotonic only while no acknowledgement,
        stream, content, tool call, or usage has been observed.
        """

        self._require(state.dispatch_started, "cache rejection")
        self._require(
            state.dispatch_state is not ModelDispatchState.ACCEPTED,
            "cache rejection",
        )
        self._require(not state.stream_started, "cache rejection")
        self._require(not state.visible_output_observed, "cache rejection")
        self._require(not state.usage_observed, "cache rejection")
        self._require(
            state.terminal_state
            in {
                None,
                ProviderTerminalState.FAILED,
            },
            "cache rejection",
        )
        values = state.model_dump()
        values["dispatch_state"] = ModelDispatchState.NOT_ACCEPTED
        values["terminal_state"] = ProviderTerminalState.FAILED
        values["failure_signal"] = ModelFailureSignal.REQUEST_INVALID
        return ProviderAttemptLifecycle.model_validate(values)

    @staticmethod
    def _require(condition: bool, event_name: str) -> None:
        if not condition:
            raise ProviderLifecycleTransitionError(
                f"{event_name} was reported before its prerequisite"
            )

    @staticmethod
    def _is_idempotent_terminal(
        state: ProviderAttemptLifecycle,
        event: ProviderLifecycleEvent,
        failure_signal: ModelFailureSignal | None,
    ) -> bool:
        expected = {
            ProviderTerminalState.COMPLETED: ProviderLifecycleEvent.COMPLETED,
            ProviderTerminalState.CANCELLED: ProviderLifecycleEvent.CANCELLED,
            ProviderTerminalState.FAILED: ProviderLifecycleEvent.FAILED,
        }[state.terminal_state]  # type: ignore[index]
        return event is expected and (
            event is not ProviderLifecycleEvent.FAILED
            or failure_signal is None
            or failure_signal is state.failure_signal
        )


__all__ = (
    "ProviderAttemptLifecycle",
    "ProviderLifecycleEvent",
    "ProviderLifecycleReducer",
    "ProviderLifecycleTransitionError",
    "ProviderTerminalState",
)
