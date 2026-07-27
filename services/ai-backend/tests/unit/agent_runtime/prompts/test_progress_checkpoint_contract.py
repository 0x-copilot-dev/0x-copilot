"""Regression tests for the sole progress-checkpoint instruction source."""

from agent_runtime.execution.deep_agent_builder import format_web_subagent_suffix
from agent_runtime.prompts.runtime import DEFAULT_INSTRUCTIONS


def test_base_prompt_never_requests_a_tool_call_free_checkpoint() -> None:
    """A tool-call-free model message terminates the Deep Agents tool loop."""

    assert "plain-text message before calling another tool" not in DEFAULT_INSTRUCTIONS
    suffix = format_web_subagent_suffix(tool_call_budget=5)
    assert "SAME message" in suffix
    assert "Do NOT emit a checkpoint without" in suffix
