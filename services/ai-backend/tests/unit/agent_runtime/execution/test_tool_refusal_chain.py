"""A refused tool call, reproduced end to end through the real components.

The reported failure, from journey phase CB-7: a per-run tool cap set in
Settings correctly refused two ``web_search`` calls, and the client rendered
each refusal as a tool card reading "Failed". The budget was working exactly as
configured, and the run it governed looked broken.

That is the same defect CB-5 fixed one layer down for a declined workspace
capability, and this reproduces the tool-gate version of it with nothing stubbed
between the refusal and the two projections the UI reads: the real
``_surface_rejection`` message, the real ``tool_result`` classifier, and the
real presentation generator.

A hand-written fixture would hide the break that matters here. The marker rides
on ``additional_kwargs``, which ``StreamMessageParser`` drops from an
object-shaped message unless it is read off the message itself — so a test that
passed a dict would stay green while every live run kept saying "Failed".
"""

from __future__ import annotations

from typing import Any, cast

from langchain.agents.middleware.types import ToolCallRequest
from langchain_core.messages import ToolMessage

from agent_runtime.api.constants import Keys, Values
from agent_runtime.api.presentation import PresentationGenerator
from agent_runtime.capabilities.middleware.runtime_tool_control import (
    _surface_rejection,
)
from agent_runtime.capabilities.task_policy import ToolPolicyRejected
from agent_runtime.execution.tool_errors import BudgetExceeded, ToolBudgetRejected
from agent_runtime.execution.tool_refusals import ToolRefusals
from runtime_api.schemas import RuntimeApiEventType
from runtime_worker.stream_tools import StreamMessageProcessor


#: The real hard-cap sentence, from ``ToolBudgetReject.safe_message``: written
#: as an instruction because the behaviour we want is "stop and answer now".
_REFUSAL = (
    "The tool call budget for 'web_search' is exhausted (4 of 4 calls used). "
    "Do not call this tool again; finalize now with what you have."
)


class RefusedToolCallMixin:
    """Drives the real chain for a tool call refused at the gate."""

    @staticmethod
    def _request(*, name: str = "web_search") -> ToolCallRequest:
        return ToolCallRequest(
            tool_call={
                "name": name,
                "args": {"query": "0xcopilot launch"},
                "id": "toolu_01W7",
                "type": "tool_call",
            },
            tool=None,
            state={},
            runtime=cast(Any, object()),
        )

    @classmethod
    def _surfaced(cls, rejection: ToolBudgetRejected) -> ToolMessage:
        return _surface_rejection(rejection, request=cls._request())

    @classmethod
    def _tool_result_payload(
        cls,
        rejection: ToolBudgetRejected | None = None,
    ) -> dict[str, object]:
        message = cls._surfaced(rejection or ToolBudgetRejected(_REFUSAL))
        return StreamMessageProcessor.tool_result_payload(message)


class TestARefusedCallNeverBecomesAFailure(RefusedToolCallMixin):
    def test_the_published_status_is_unavailable(self) -> None:
        payload = self._tool_result_payload()

        assert payload[Keys.Field.STATUS] == Values.Status.UNAVAILABLE

    def test_both_refusal_families_classify_the_same_way(self) -> None:
        for rejection in (
            ToolBudgetRejected(_REFUSAL),
            ToolPolicyRejected("This tool call duplicates prior work."),
        ):
            payload = self._tool_result_payload(rejection)
            assert payload[Keys.Field.STATUS] == Values.Status.UNAVAILABLE, (
                f"{type(rejection).__name__} regressed to {payload[Keys.Field.STATUS]}"
            )

    def test_each_family_keeps_its_own_typed_code(self) -> None:
        # The remedies differ — one needs a bigger cap, the other a revised
        # plan — so collapsing them to one code would lose what to tell the user.
        assert self._tool_result_payload()["error_code"] == "tool_budget_exceeded"
        assert (
            self._tool_result_payload(ToolPolicyRejected("dupe"))["error_code"]
            == "tool_policy_rejected"
        )

    def test_the_card_carries_the_refusal_sentence_not_the_model_envelope(
        self,
    ) -> None:
        payload = self._tool_result_payload()

        # The model reads "<error_class>: <message>\nHints: {...}". The user
        # must not.
        assert payload["safe_message"] == _REFUSAL
        assert "ToolBudgetRejected" not in str(payload["safe_message"])
        assert "Hints:" not in str(payload["safe_message"])

    def test_the_model_still_sees_an_error_result(self) -> None:
        # Not a contradiction with the above — two consumers, two truths. The
        # call returned no data, and LangChain has only success/error to say so.
        message = self._surfaced(ToolBudgetRejected(_REFUSAL))

        assert message.status == "error"
        assert "finalize now" in str(message.content)

    def test_the_fatal_escalation_stays_a_failure(self) -> None:
        # The guard escalates to BudgetExceeded once the model has clearly
        # stopped respecting the refusals. That one really does end the run,
        # and softening it here would hide a genuine fault.
        assert ToolRefusals.code_for_exception(BudgetExceeded("looping")) is None
        assert ToolRefusals.marker_for(BudgetExceeded("looping")) is None

    def test_an_ordinary_tool_failure_is_untouched(self) -> None:
        payload = StreamMessageProcessor.tool_result_payload(
            ToolMessage(
                content="Error: the connector returned 500",
                tool_call_id="toolu_02",
                name="web_search",
                status="error",
            )
        )

        assert payload[Keys.Field.STATUS] == Values.Status.FAILED

    def test_a_forged_marker_on_tool_output_is_not_honoured(self) -> None:
        # This seam moves a result OUT of the failure taxonomy, so a marker it
        # did not author must fail closed rather than launder a real failure.
        payload = StreamMessageProcessor.tool_result_payload(
            ToolMessage(
                content="Error: the connector returned 500",
                tool_call_id="toolu_03",
                name="web_search",
                status="error",
                additional_kwargs={
                    ToolRefusals.MARKER_KEY: {"code": "not_a_declared_code"}
                },
            )
        )

        assert payload[Keys.Field.STATUS] == Values.Status.FAILED


class TestTheCardIsNeutralAndOffersNothing(RefusedToolCallMixin):
    @classmethod
    def _card(cls) -> dict[str, object]:
        card = PresentationGenerator().preliminary_presentation_for_event(
            event_type=RuntimeApiEventType.TOOL_RESULT,
            payload=dict(cls._tool_result_payload()),
            metadata={},
            timeline_fields={},
        )
        assert card is not None
        return dict(card)

    def test_it_reads_as_neither_done_nor_failed(self) -> None:
        card = self._card()

        assert card["status_label"] == "Not available"
        # `kind` drives the failure taxonomy downstream; "error" here is what
        # put a working budget under a run-level alarm.
        assert card["kind"] == "result"

    def test_it_offers_no_retry_it_cannot_honour(self) -> None:
        # The cap holds for the rest of the run: repeating is guaranteed to
        # lose, so no remedy is drawn.
        assert self._card()["retryable"] is False

    def test_the_refusal_sentence_survives_to_the_user(self) -> None:
        assert "finalize now" in str(self._card()["summary"])
