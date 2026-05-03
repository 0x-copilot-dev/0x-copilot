"""Built-in tool that pauses the agent to ask the human user a question."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from langgraph.types import interrupt as langgraph_interrupt
from pydantic import Field, ValidationError

from agent_runtime.api.constants import Keys, Values
from agent_runtime.execution.contracts import AgentRuntimeContext, RuntimeContract
from agent_runtime.prompts.tools import ASK_A_QUESTION_TOOL_DESCRIPTION


class AskAQuestionInput(RuntimeContract):
    """Input contract for the ask_a_question built-in tool."""

    question: str = Field(min_length=1, max_length=2000)
    hint: str | None = Field(default=None, max_length=2000)
    options: tuple[str, ...] = ()


@dataclass(frozen=True)
class AskAQuestionTool:
    """Pause execution via a LangGraph interrupt and wait for a human answer."""

    runtime_context: AgentRuntimeContext
    interrupt_handler: Callable[[dict[str, Any]], object] = langgraph_interrupt
    name: str = Values.Tool.ASK_A_QUESTION
    description: str = ASK_A_QUESTION_TOOL_DESCRIPTION

    async def ainvoke(
        self, raw_input: AskAQuestionInput | Mapping[str, Any] | str
    ) -> dict[str, Any]:
        parsed = AskAQuestionInputParser.parse(raw_input)
        if isinstance(parsed, dict):
            return parsed

        approval_id = self._approval_id()
        payload = {
            Keys.Field.API_EVENT_TYPE: "approval_requested",
            Keys.Field.EVENT_TYPE: "approval_requested",
            Keys.Field.APPROVAL_ID: approval_id,
            "action_id": approval_id,
            Keys.Field.APPROVAL_KIND: Values.ApprovalKind.ASK_A_QUESTION,
            "question": parsed.question,
            "hint": parsed.hint,
            "options": list(parsed.options),
            Keys.Field.STATUS: "pending",
            "message": parsed.question,
        }
        resume = self.interrupt_handler(payload)
        return self._resume_result(resume)

    async def __call__(
        self, raw_input: AskAQuestionInput | Mapping[str, Any] | str
    ) -> dict[str, Any]:
        return await self.ainvoke(raw_input)

    def _approval_id(self) -> str:
        return f"ask_a_question:{self.runtime_context.run_id}:{self.runtime_context.trace_id}"

    @staticmethod
    def _resume_result(resume: object) -> dict[str, Any]:
        decision = None
        answer: str | None = None
        if isinstance(resume, Mapping):
            raw_decision = resume.get(Keys.Field.DECISION)
            decision = raw_decision if isinstance(raw_decision, str) else None
            raw_answer = resume.get("answer")
            answer = raw_answer.strip() if isinstance(raw_answer, str) else None
        elif isinstance(resume, str):
            answer = resume.strip() or None
            decision = "approved" if answer else None

        approved = decision in {"approved", "approve"} and answer is not None
        if approved:
            return {
                "ok": True,
                "decision": "approved",
                "answer": answer,
            }
        return {
            "ok": False,
            "decision": decision or "rejected",
            "message": "The user declined to answer.",
        }


class AskAQuestionInputParser:
    """Parser for untrusted ask_a_question tool input."""

    @classmethod
    def parse(
        cls, raw_input: AskAQuestionInput | Mapping[str, Any] | str
    ) -> AskAQuestionInput | dict[str, Any]:
        if isinstance(raw_input, AskAQuestionInput):
            return raw_input
        if isinstance(raw_input, str):
            raw_input = {"question": raw_input}
        try:
            return AskAQuestionInput.model_validate(raw_input)
        except ValidationError:
            return {
                "ok": False,
                "decision": "rejected",
                "message": "A non-empty `question` is required.",
            }
