"""The reported bug, reproduced end to end through the real components.

Every unit test around this change stubs one seam or another. This one stubs
nothing between the workspace backend and the two projections the UI reads: the
real ``WorkspaceTombstoneBackend`` answer, rendered the way the real Deep Agents
filesystem middleware renders it, through the real ``tool_result`` classifier,
the real presentation generator, and the real lifecycle fold.

It exists because a hand-written tool-message fixture DID hide a real break: the
read-family tools prefix their content with ``Error: ``, which an exact-match
classifier missed, so the policy answer was still stamped ``failed`` on the only
path that matters.

Reproduces: `ls /workspace/` with no folder attached, agent recovers and answers.
Expected: no failure anywhere, canvas says "answered in chat", no action offered.
"""

from __future__ import annotations

from agent_runtime.api.constants import Keys, Values
from agent_runtime.api.presentation import PresentationGenerator
from agent_runtime.capabilities.workspace.deep_backend import (
    WorkspaceTombstoneBackend,
)
from agent_runtime.presentation.lifecycle import (
    CanvasLifecycleProjection,
    CanvasLifecycleState,
)
from runtime_api.schemas import RuntimeApiEventType
from runtime_worker.stream_tools import StreamMessageProcessor


class DeclinedCapabilityRunMixin:
    """Drives the real chain for a declined workspace read."""

    #: How `deepagents.middleware.filesystem` renders a backend error for the
    #: read family (ls / read_file / glob / grep). The write family emits the
    #: bare message; both must classify identically.
    @staticmethod
    def _rendered(error: str, *, prefixed: bool) -> str:
        return f"Error: {error}" if prefixed else error

    @classmethod
    def _tool_result_payload(cls, *, prefixed: bool) -> dict[str, object]:
        result = WorkspaceTombstoneBackend().ls("/workspace/")
        assert result.error is not None
        return StreamMessageProcessor.tool_result_payload(
            {
                Keys.Field.NAME: "ls",
                Keys.Field.TOOL_CALL_ID: "call_1",
                Keys.Field.STATUS: "error",
                Keys.Field.CONTENT: cls._rendered(result.error, prefixed=prefixed),
            }
        )

    @staticmethod
    def _recovered_run(payload: dict[str, object]) -> CanvasLifecycleProjection:
        # The screenshot's run: the step is declined, the agent explains, done.
        return CanvasLifecycleProjection.fold(
            [
                {"sequence_no": 1, "event_type": "tool_call_started", "payload": {}},
                {"sequence_no": 2, "event_type": "tool_result", "payload": payload},
                {"sequence_no": 3, "event_type": "final_response", "payload": {}},
                {
                    "sequence_no": 4,
                    "event_type": "run_completed",
                    "payload": {"status": "completed"},
                },
            ]
        )


class TestDeclinedCapabilityNeverBecomesAFailure(DeclinedCapabilityRunMixin):
    def test_both_middleware_renderings_classify_as_unavailable(self) -> None:
        for prefixed in (True, False):
            payload = self._tool_result_payload(prefixed=prefixed)
            assert payload[Keys.Field.STATUS] == Values.Status.UNAVAILABLE, (
                f"prefixed={prefixed} regressed to {payload[Keys.Field.STATUS]}"
            )

    def test_the_card_is_neutral_and_offers_nothing(self) -> None:
        card = PresentationGenerator().preliminary_presentation_for_event(
            event_type=RuntimeApiEventType.TOOL_RESULT,
            payload=dict(self._tool_result_payload(prefixed=True)),
            metadata={},
            timeline_fields={},
        )

        assert card is not None
        assert card["status_label"] == "Not available"
        assert card["title"] == "Not available here"
        # Nothing to retry: attaching a folder is the remedy, not a rerun.
        assert card["retryable"] is False
        # The backend's own sentence survives to the user verbatim.
        assert "Create an artifact or download instead" in str(card["summary"])

    def test_the_canvas_says_answered_in_chat_with_no_alarm(self) -> None:
        projection = self._recovered_run(self._tool_result_payload(prefixed=True))

        assert projection.lifecycle is CanvasLifecycleState.CHAT_ONLY
        # No failure text at all — there was no failure.
        assert projection.failure is None
