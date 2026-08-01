"""A declined capability must not enter the failure taxonomy.

These tests pin the seam that the original bug crossed: the Deep Agents backend
protocol renders a populated ``error`` as ``(text, "error")``, so a designed
"no folder is attached" answer and a crashed tool arrive identically. If the
classifier stops distinguishing them, a correct policy decision propagates as a
run-level failure again.
"""

from __future__ import annotations

from agent_runtime.api.constants import Keys, Values
from agent_runtime.capabilities.workspace.policy_answers import (
    WorkspacePolicyAnswerCode,
    WorkspacePolicyAnswers,
)
from runtime_worker.stream_tools import StreamMessageProcessor


class TestWorkspacePolicyAnswers:
    def test_declared_answer_resolves_to_its_typed_code(self) -> None:
        assert (
            WorkspacePolicyAnswers.code_for(WorkspacePolicyAnswers.UNAVAILABLE)
            is WorkspacePolicyAnswerCode.UNAVAILABLE
        )

    def test_surrounding_whitespace_does_not_hide_a_policy_answer(self) -> None:
        padded = f"\n  {WorkspacePolicyAnswers.UNAVAILABLE}  \n"
        assert WorkspacePolicyAnswers.is_policy_answer(padded)

    def test_a_caught_exception_message_stays_a_fault(self) -> None:
        # The gateway's own failure copy: a real exception was swallowed, so it
        # must keep its failure classification rather than be excused as policy.
        assert not WorkspacePolicyAnswers.is_policy_answer(
            "The requested workspace directory is unavailable."
        )

    def test_non_string_input_is_not_a_policy_answer(self) -> None:
        assert not WorkspacePolicyAnswers.is_policy_answer(None)
        assert not WorkspacePolicyAnswers.is_policy_answer({"error": "x"})

    def test_the_middleware_error_prefix_does_not_hide_a_policy_answer(self) -> None:
        # The read-family tools render `content=f"Error: {result.error}"`. An
        # exact-match classifier misses that and re-buries the policy answer in
        # the failure taxonomy — which is exactly what a hand-written fixture
        # failed to catch.
        assert WorkspacePolicyAnswers.is_policy_answer(
            f"Error: {WorkspacePolicyAnswers.UNAVAILABLE}"
        )

    def test_matches_the_real_middleware_rendering(self) -> None:
        """Read the INSTALLED middleware, not an assumption about it.

        Drives the real tombstone backend and formats its result the way
        ``deepagents.middleware.filesystem`` does, so an upstream change to
        either the message or the rendering fails here instead of silently
        restoring the false alarm in production.
        """
        from agent_runtime.capabilities.workspace.deep_backend import (
            WorkspaceTombstoneBackend,
        )

        result = WorkspaceTombstoneBackend().ls("/workspace/")

        assert result.error is not None
        # Both renderings the middleware actually uses.
        assert WorkspacePolicyAnswers.is_policy_answer(result.error)
        assert WorkspacePolicyAnswers.is_policy_answer(f"Error: {result.error}")


class FilesystemToolMessageMixin:
    """Builds the message shape the Deep Agents filesystem middleware emits."""

    @staticmethod
    def _tool_message(content: str) -> dict[str, object]:
        # What ``filesystem.py`` produces for a populated ``LsResult.error``:
        # the text plus LangChain's ``error`` status — identical whether the
        # backend declined by policy or genuinely crashed.
        return {
            Keys.Field.NAME: "ls",
            Keys.Field.TOOL_CALL_ID: "call_1",
            Keys.Field.STATUS: "error",
            Keys.Field.CONTENT: content,
        }


class TestToolResultClassification(FilesystemToolMessageMixin):
    def test_policy_answer_is_not_stamped_failed(self) -> None:
        payload = StreamMessageProcessor.tool_result_payload(
            self._tool_message(WorkspacePolicyAnswers.UNAVAILABLE)
        )

        assert payload[Keys.Field.STATUS] == Values.Status.UNAVAILABLE
        assert payload[Keys.Field.STATUS] != Values.Status.FAILED

    def test_the_read_family_rendering_is_classified_too(self) -> None:
        # `ls` — the tool from the original report — renders with the prefix.
        payload = StreamMessageProcessor.tool_result_payload(
            self._tool_message(f"Error: {WorkspacePolicyAnswers.UNAVAILABLE}")
        )

        assert payload[Keys.Field.STATUS] == Values.Status.UNAVAILABLE
        assert payload["error_code"] == WorkspacePolicyAnswerCode.UNAVAILABLE.value

    def test_policy_answer_carries_its_typed_code_and_message(self) -> None:
        payload = StreamMessageProcessor.tool_result_payload(
            self._tool_message(WorkspacePolicyAnswers.UNAVAILABLE)
        )

        assert payload["error_code"] == WorkspacePolicyAnswerCode.UNAVAILABLE.value
        assert payload["safe_message"] == WorkspacePolicyAnswers.UNAVAILABLE

    def test_a_genuine_tool_error_is_still_failed(self) -> None:
        payload = StreamMessageProcessor.tool_result_payload(
            self._tool_message("The requested workspace directory is unavailable.")
        )

        assert payload[Keys.Field.STATUS] == Values.Status.FAILED
