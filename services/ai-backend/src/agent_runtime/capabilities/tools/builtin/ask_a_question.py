"""Built-in tool that pauses the agent to ask the human user a question."""

from __future__ import annotations

import secrets
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from langgraph.types import interrupt as langgraph_interrupt
from pydantic import Field, ValidationError, field_validator

from agent_runtime.api.constants import Keys, Values
from agent_runtime.execution.contracts import AgentRuntimeContext, RuntimeContract
from agent_runtime.prompts.tools import ASK_A_QUESTION_TOOL_DESCRIPTION


class _Fields:
    """Field name constants for ask_a_question payloads and validators."""

    HEADER = "header"
    QUESTION = "question"
    HINT = "hint"
    OPTIONS = "options"
    MULTI_SELECT = "multi_select"
    ALLOW_FREE_TEXT = "allow_free_text"
    ANSWER = "answer"
    SELECTED = "selected"
    FREE_TEXT = "free_text"
    LABEL = "label"
    DESCRIPTION = "description"
    RECOMMENDED = "recommended"


class _Defaults:
    """Default values for ask_a_question contracts."""

    ALLOW_FREE_TEXT = True
    MULTI_SELECT = False
    HEADER = "Quick question"


class _Limits:
    """Length and count caps for ask_a_question contracts."""

    HEADER_MAX = 24
    QUESTION_MAX = 2000
    HINT_MAX = 2000
    OPTION_LABEL_MAX = 80
    OPTION_DESCRIPTION_MAX = 240
    OPTION_COUNT_MAX = 8


class _Messages:
    """Safe public messages returned to the agent on resume failures."""

    QUESTION_REQUIRED = "A non-empty `question` is required."
    USER_DECLINED = "The user declined to answer."


class QuestionOption(RuntimeContract):
    """Structured option chip for ask_a_question."""

    label: str = Field(min_length=1, max_length=_Limits.OPTION_LABEL_MAX)
    description: str | None = Field(
        default=None, max_length=_Limits.OPTION_DESCRIPTION_MAX
    )
    recommended: bool = False


class AskAQuestionInput(RuntimeContract):
    """Input contract for the ask_a_question built-in tool."""

    question: str = Field(min_length=1, max_length=_Limits.QUESTION_MAX)
    hint: str | None = Field(default=None, max_length=_Limits.HINT_MAX)
    header: str | None = Field(default=None, max_length=_Limits.HEADER_MAX)
    options: tuple[QuestionOption, ...] = ()
    multi_select: bool = _Defaults.MULTI_SELECT
    allow_free_text: bool = _Defaults.ALLOW_FREE_TEXT

    @field_validator(_Fields.OPTIONS, mode="before")
    @classmethod
    def _normalize_options(cls, value: object) -> object:
        """Coerce plain string entries to ``QuestionOption`` for backwards compatibility."""

        if value is None:
            return ()
        if not isinstance(value, list | tuple):
            return value
        normalized: list[object] = []
        for entry in value:
            if isinstance(entry, str):
                normalized.append({_Fields.LABEL: entry})
            else:
                normalized.append(entry)
        if len(normalized) > _Limits.OPTION_COUNT_MAX:
            normalized = normalized[: _Limits.OPTION_COUNT_MAX]
        return tuple(normalized)


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
        payload: dict[str, Any] = {
            Keys.Field.API_EVENT_TYPE: "approval_requested",
            Keys.Field.EVENT_TYPE: "approval_requested",
            Keys.Field.APPROVAL_ID: approval_id,
            "action_id": approval_id,
            Keys.Field.APPROVAL_KIND: Values.ApprovalKind.ASK_A_QUESTION,
            _Fields.HEADER: parsed.header,
            _Fields.QUESTION: parsed.question,
            _Fields.HINT: parsed.hint,
            _Fields.OPTIONS: [
                option.model_dump(mode="json") for option in parsed.options
            ],
            _Fields.MULTI_SELECT: parsed.multi_select,
            _Fields.ALLOW_FREE_TEXT: parsed.allow_free_text,
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
        # Per-invocation suffix: trace_id is stable across multiple ask_a_question
        # calls inside the same run/trace, which would collapse them to a single
        # approval row + UI card. token_hex makes each request its own entity in
        # the approvals table and the message stream.
        return f"ask_a_question:{self.runtime_context.run_id}:{secrets.token_hex(8)}"

    @classmethod
    def _resume_result(cls, resume: object) -> dict[str, Any]:
        decision = None
        answer: str | None = None
        selected: list[str] = []
        free_text: str | None = None
        if isinstance(resume, Mapping):
            raw_decision = resume.get(Keys.Field.DECISION)
            decision = raw_decision if isinstance(raw_decision, str) else None
            raw_selected = resume.get(_Fields.SELECTED)
            if isinstance(raw_selected, list | tuple):
                selected = [
                    str(item).strip()
                    for item in raw_selected
                    if isinstance(item, str) and item.strip()
                ]
            raw_free = resume.get(_Fields.FREE_TEXT)
            if isinstance(raw_free, str):
                free_text = raw_free.strip() or None
            raw_answer = resume.get(_Fields.ANSWER)
            if isinstance(raw_answer, str):
                answer = raw_answer.strip() or None
        elif isinstance(resume, str):
            free_text = resume.strip() or None
            decision = "approved" if free_text else None

        if answer is None:
            answer = cls._compose_answer(selected=selected, free_text=free_text)

        approved = decision in {"approved", "approve"} and answer is not None
        if approved:
            return {
                "ok": True,
                "decision": "approved",
                _Fields.ANSWER: answer,
                _Fields.SELECTED: selected,
                _Fields.FREE_TEXT: free_text,
            }
        return {
            "ok": False,
            "decision": decision or "rejected",
            "message": _Messages.USER_DECLINED,
        }

    @staticmethod
    def _compose_answer(*, selected: list[str], free_text: str | None) -> str | None:
        parts: list[str] = []
        parts.extend(selected)
        if free_text:
            parts.append(free_text)
        if not parts:
            return None
        return ", ".join(parts)


class AskAQuestionInputParser:
    """Parser for untrusted ask_a_question tool input."""

    @classmethod
    def parse(
        cls, raw_input: AskAQuestionInput | Mapping[str, Any] | str
    ) -> AskAQuestionInput | dict[str, Any]:
        if isinstance(raw_input, AskAQuestionInput):
            return raw_input
        if isinstance(raw_input, str):
            raw_input = {_Fields.QUESTION: raw_input}
        try:
            return AskAQuestionInput.model_validate(raw_input)
        except ValidationError:
            return {
                "ok": False,
                "decision": "rejected",
                "message": _Messages.QUESTION_REQUIRED,
            }
