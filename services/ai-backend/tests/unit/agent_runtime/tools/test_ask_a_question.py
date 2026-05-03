from __future__ import annotations

import asyncio

from agent_runtime.capabilities.tools.builtin.ask_a_question import (
    AskAQuestionInput,
    AskAQuestionTool,
)
from agent_runtime.execution.contracts import AgentRuntimeContext


class TestAskAQuestionTool:
    def test_emits_approval_payload_and_returns_answer(
        self,
        runtime_context_admin: AgentRuntimeContext,
    ) -> None:
        captured: dict[str, object] = {}

        def fake_interrupt(payload: dict[str, object]) -> dict[str, object]:
            captured.update(payload)
            return {"decision": "approved", "answer": "Tokyo"}

        tool = AskAQuestionTool(
            runtime_context=runtime_context_admin,
            interrupt_handler=fake_interrupt,
        )

        result = asyncio.run(
            tool.ainvoke(
                {
                    "question": "Where would you like to travel?",
                    "hint": "Pick a city",
                    "options": ("Tokyo", "Paris"),
                }
            )
        )

        assert captured["api_event_type"] == "approval_requested"
        assert captured["approval_kind"] == "ask_a_question"
        assert captured["question"] == "Where would you like to travel?"
        assert captured["options"] == ["Tokyo", "Paris"]
        assert captured["status"] == "pending"
        assert isinstance(captured["approval_id"], str)
        assert result == {"ok": True, "decision": "approved", "answer": "Tokyo"}

    def test_returns_rejection_when_user_declines(
        self,
        runtime_context_admin: AgentRuntimeContext,
    ) -> None:
        def fake_interrupt(payload: dict[str, object]) -> dict[str, object]:
            return {"decision": "rejected", "answer": None}

        tool = AskAQuestionTool(
            runtime_context=runtime_context_admin,
            interrupt_handler=fake_interrupt,
        )

        result = asyncio.run(tool.ainvoke({"question": "Pick one"}))

        assert result["ok"] is False
        assert result["decision"] == "rejected"

    def test_rejects_empty_question(
        self,
        runtime_context_admin: AgentRuntimeContext,
    ) -> None:
        def fake_interrupt(payload: dict[str, object]) -> dict[str, object]:
            raise AssertionError("interrupt must not fire for invalid input")

        tool = AskAQuestionTool(
            runtime_context=runtime_context_admin,
            interrupt_handler=fake_interrupt,
        )

        result = asyncio.run(tool.ainvoke({"question": ""}))

        assert result["ok"] is False
        assert "question" in result["message"].lower()

    def test_string_input_is_treated_as_question(
        self,
        runtime_context_admin: AgentRuntimeContext,
    ) -> None:
        captured: dict[str, object] = {}

        def fake_interrupt(payload: dict[str, object]) -> dict[str, object]:
            captured.update(payload)
            return {"decision": "approved", "answer": "ok"}

        tool = AskAQuestionTool(
            runtime_context=runtime_context_admin,
            interrupt_handler=fake_interrupt,
        )

        result = asyncio.run(tool.ainvoke("Are you sure?"))

        assert captured["question"] == "Are you sure?"
        assert result["answer"] == "ok"

    def test_input_contract_normalizes_options(self) -> None:
        parsed = AskAQuestionInput.model_validate(
            {"question": "Pick one", "options": ["a", "b"]}
        )
        assert parsed.options == ("a", "b")
