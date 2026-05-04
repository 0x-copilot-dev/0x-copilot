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
            return {
                "decision": "approved",
                "selected": ["Tokyo"],
                "free_text": None,
            }

        tool = AskAQuestionTool(
            runtime_context=runtime_context_admin,
            interrupt_handler=fake_interrupt,
        )

        result = asyncio.run(
            tool.ainvoke(
                {
                    "question": "Where would you like to travel?",
                    "hint": "Pick a city",
                    "options": ["Tokyo", "Paris"],
                }
            )
        )

        assert captured["api_event_type"] == "approval_requested"
        assert captured["approval_kind"] == "ask_a_question"
        assert captured["question"] == "Where would you like to travel?"
        assert captured["options"] == [
            {"label": "Tokyo", "description": None, "recommended": False},
            {"label": "Paris", "description": None, "recommended": False},
        ]
        assert captured["multi_select"] is False
        assert captured["allow_free_text"] is True
        assert captured["status"] == "pending"
        assert isinstance(captured["approval_id"], str)
        assert result["ok"] is True
        assert result["decision"] == "approved"
        assert result["answer"] == "Tokyo"
        assert result["selected"] == ["Tokyo"]

    def test_structured_options_preserve_label_description_and_recommended(
        self,
        runtime_context_admin: AgentRuntimeContext,
    ) -> None:
        captured: dict[str, object] = {}

        def fake_interrupt(payload: dict[str, object]) -> dict[str, object]:
            captured.update(payload)
            return {"decision": "approved", "selected": ["Petrol + Automatic"]}

        tool = AskAQuestionTool(
            runtime_context=runtime_context_admin,
            interrupt_handler=fake_interrupt,
        )

        asyncio.run(
            tool.ainvoke(
                {
                    "question": "Powertrain?",
                    "header": "Pick a powertrain",
                    "options": [
                        {
                            "label": "Petrol + Automatic",
                            "description": "Smoother in city traffic.",
                            "recommended": True,
                        },
                        {"label": "Diesel + Manual"},
                    ],
                    "multi_select": False,
                    "allow_free_text": False,
                }
            )
        )

        assert captured["header"] == "Pick a powertrain"
        assert captured["multi_select"] is False
        assert captured["allow_free_text"] is False
        assert captured["options"] == [
            {
                "label": "Petrol + Automatic",
                "description": "Smoother in city traffic.",
                "recommended": True,
            },
            {
                "label": "Diesel + Manual",
                "description": None,
                "recommended": False,
            },
        ]

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
            return "Yes"

        tool = AskAQuestionTool(
            runtime_context=runtime_context_admin,
            interrupt_handler=fake_interrupt,
        )

        result = asyncio.run(tool.ainvoke("Are you sure?"))

        assert captured["question"] == "Are you sure?"
        assert result["ok"] is True
        assert result["answer"] == "Yes"

    def test_resume_with_legacy_answer_string_is_still_supported(
        self,
        runtime_context_admin: AgentRuntimeContext,
    ) -> None:
        def fake_interrupt(_payload: dict[str, object]) -> dict[str, object]:
            return {"decision": "approved", "answer": "Tokyo"}

        tool = AskAQuestionTool(
            runtime_context=runtime_context_admin,
            interrupt_handler=fake_interrupt,
        )

        result = asyncio.run(tool.ainvoke({"question": "Pick one"}))

        assert result["ok"] is True
        assert result["answer"] == "Tokyo"

    def test_input_contract_normalizes_string_options_to_structured(self) -> None:
        parsed = AskAQuestionInput.model_validate(
            {"question": "Pick one", "options": ["a", "b"]}
        )
        assert [option.label for option in parsed.options] == ["a", "b"]
        assert all(option.recommended is False for option in parsed.options)

    def test_input_contract_caps_option_count(self) -> None:
        parsed = AskAQuestionInput.model_validate(
            {
                "question": "Pick one",
                "options": [{"label": str(index)} for index in range(20)],
            }
        )
        assert len(parsed.options) == 8
