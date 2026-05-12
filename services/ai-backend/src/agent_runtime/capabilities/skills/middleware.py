"""Model-facing tools for virtual Skill loading."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from pydantic import Field, ValidationError

from agent_runtime.execution.contracts import RuntimeContract
from agent_runtime.execution.errors import AgentRuntimeError
from agent_runtime.capabilities.skills.virtual import (
    VirtualSkillBundle,
    VirtualSkillRegistry,
)
from agent_runtime.prompts.tools import LOAD_SKILL_TOOL_DESCRIPTION


class LoadSkillInput(RuntimeContract):
    """Input contract for loading a virtual Skill by stable name."""

    skill_name: str = Field(min_length=1)


@dataclass(frozen=True)
class LoadSkillTool:
    """Small adapter that lets the model load full Skill markdown on demand."""

    registry: VirtualSkillRegistry
    name: str = "load_skill"
    description: str = LOAD_SKILL_TOOL_DESCRIPTION

    async def ainvoke(
        self, raw_input: LoadSkillInput | Mapping[str, Any] | str
    ) -> dict[str, Any]:
        """Parse input, load the skill by name, and return a bundle payload or error dict."""
        parsed_input = LoadSkillInputParser.parse(raw_input)
        if isinstance(parsed_input, dict):
            return parsed_input
        try:
            bundle = await self.registry.load_skill_by_name(parsed_input.skill_name)
        except AgentRuntimeError as exc:
            return {
                "ok": False,
                "error": {
                    "code": exc.code.value,
                    "safe_message": exc.safe_message,
                    "retryable": exc.retryable,
                },
            }
        return self._bundle_payload(bundle)

    async def __call__(
        self, raw_input: LoadSkillInput | Mapping[str, Any] | str
    ) -> dict[str, Any]:
        """Delegate to ``ainvoke``."""
        return await self.ainvoke(raw_input)

    @classmethod
    def _bundle_payload(cls, bundle: VirtualSkillBundle) -> dict[str, Any]:
        """Serialise a ``VirtualSkillBundle`` to a JSON-ready dict with ``ok: True``."""
        payload = bundle.model_dump(mode="json")
        payload["ok"] = True
        return payload


class LoadSkillInputParser:
    """Parser for untrusted model input to the Skill loader."""

    @classmethod
    def parse(
        cls, raw_input: LoadSkillInput | Mapping[str, Any] | str
    ) -> LoadSkillInput | dict[str, Any]:
        """Validate ``raw_input`` to a typed request; return an error dict on invalid skill_name."""
        if isinstance(raw_input, LoadSkillInput):
            return raw_input
        if isinstance(raw_input, str):
            raw_input = {"skill_name": raw_input}
        try:
            return LoadSkillInput.model_validate(raw_input)
        except ValidationError:
            return {
                "ok": False,
                "error": {
                    "code": "invalid_skill_name",
                    "safe_message": "A stable skill_name is required.",
                    "retryable": False,
                },
            }
