"""Regression tests for the runtime's presentation guidance."""

from agent_runtime.prompts.runtime import DEFAULT_INSTRUCTIONS


def test_delegated_final_answer_uses_a_compact_integrated_conclusion() -> None:
    """Cards own child status/details; the final answer must not duplicate them."""

    assert "interface already shows their dispatch, status" in DEFAULT_INSTRUCTIONS
    assert "Do not repeat their task-by-task reports" in DEFAULT_INSTRUCTIONS
    assert "one compact integrated conclusion in prose" in DEFAULT_INSTRUCTIONS
