"""A remedy is offered only when repeating could change the outcome.

The defect this guards against: a failure card that names nothing ("0xCopilot
couldn't complete this step.") capped with an unconditional action the system
cannot perform. Both halves are fixed by carrying the typed code and an honest
`retryable` to the client.
"""

from __future__ import annotations

from agent_runtime.api.presentation_templates import (
    DeterministicTemplates,
    _ErrorRetryability,
)
from runtime_api.schemas import RuntimeApiEventType


class FailureEventMixin:
    """Builds the failure payloads the runtime actually emits."""

    @staticmethod
    def _render(**payload: object) -> dict[str, object]:
        rendered = DeterministicTemplates.render(
            event_type=RuntimeApiEventType.RUN_FAILED,
            payload=payload,
            timeline_fields={},
            group_key=None,
        )
        assert rendered is not None
        return rendered


class TestRetryability:
    def test_unknown_code_is_not_retryable(self) -> None:
        # Fail closed: never promise an action we cannot back.
        assert _ErrorRetryability.for_code(None) is False
        assert _ErrorRetryability.for_code("SOMETHING_NEW") is False

    def test_transient_failures_are_retryable(self) -> None:
        assert _ErrorRetryability.for_code("RUN_WORKER_LOST") is True
        assert _ErrorRetryability.for_code("external-service-error") is True

    def test_a_grant_shaped_failure_is_not_retryable(self) -> None:
        # Repeating these is guaranteed to fail identically; the remedy is a
        # permission or an attached folder, never a rerun.
        assert _ErrorRetryability.for_code("PERMISSION_DENIED") is False
        assert _ErrorRetryability.for_code("workspace_unavailable") is False

    def test_an_explicit_payload_value_wins_over_the_table(self) -> None:
        assert _ErrorRetryability.for_code("RUN_WORKER_LOST", False) is False
        assert _ErrorRetryability.for_code("PERMISSION_DENIED", True) is True


class TestFailureCardCarriesTheTypedCause(FailureEventMixin):
    def test_named_cause_and_retryability_reach_the_card(self) -> None:
        card = self._render(error_code="RUN_WORKER_LOST")

        assert card["title"] == "Run interrupted"
        assert card["code"] == "RUN_WORKER_LOST"
        assert card["retryable"] is True

    def test_a_non_retryable_failure_says_so(self) -> None:
        card = self._render(error_code="PERMISSION_DENIED")

        assert card["title"] == "Not allowed"
        assert card["retryable"] is False

    def test_an_uncoded_failure_still_refuses_to_promise_a_retry(self) -> None:
        card = self._render()

        assert card["title"] == "Step failed"
        assert card.get("code") is None
        assert card["retryable"] is False
