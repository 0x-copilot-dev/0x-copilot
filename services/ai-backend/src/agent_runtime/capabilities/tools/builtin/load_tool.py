"""Built-in callable that lets the model lazily load full tool specs."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from pydantic import Field, ValidationError

from agent_runtime.execution.contracts import AgentRuntimeContext, RuntimeContract
from agent_runtime.capabilities.tools.cards import ToolLoadErrorCode, ToolLoadResult
from agent_runtime.capabilities.tools.constants import Keys, Messages
from agent_runtime.capabilities.tools.loader import ToolLoader


class LoadToolInput(RuntimeContract):
    """Input contract for the model-facing load-tool helper."""

    tool_name: str = Field(min_length=1)


@dataclass(frozen=True)
class LoadToolSpecTool:
    """Small adapter that can be wrapped by LangChain tool primitives."""

    loader: ToolLoader
    runtime_context: AgentRuntimeContext
    name: str = Keys.Builtin.LOAD_TOOL_SPEC
    description: str = Messages.Builtin.LOAD_TOOL_SPEC_DESCRIPTION

    def invoke(
        self, raw_input: LoadToolInput | Mapping[str, Any] | str
    ) -> dict[str, Any]:
        """Return a JSON-serializable loaded spec or typed safe error."""

        parsed_input = self._parse_input(raw_input, self.runtime_context.trace_id)
        if isinstance(parsed_input, ToolLoadResult):
            return parsed_input.model_dump(
                mode=Keys.Serialization.JSON, exclude_none=True
            )

        result = self.loader.load_tool_by_name(
            tool_name=parsed_input.tool_name,
            runtime_context=self.runtime_context,
        )
        return result.model_dump(mode=Keys.Serialization.JSON, exclude_none=True)

    def __call__(
        self, raw_input: LoadToolInput | Mapping[str, Any] | str
    ) -> dict[str, Any]:
        """Delegate to ``invoke``."""
        return self.invoke(raw_input)

    @classmethod
    def _parse_input(
        cls,
        raw_input: LoadToolInput | Mapping[str, Any] | str,
        correlation_id: str,
    ) -> LoadToolInput | ToolLoadResult:
        """Return a validated input model or a ``ToolLoadResult`` failure on invalid input."""
        if isinstance(raw_input, LoadToolInput):
            return raw_input
        if isinstance(raw_input, str):
            raw_input = {Keys.Fields.TOOL_NAME: raw_input}

        try:
            return LoadToolInput.model_validate(raw_input)
        except ValidationError:
            return ToolLoadResult.fail(
                ToolLoadErrorCode.INVALID_TOOL_NAME,
                Messages.Errors.TOOL_NAME_REQUIRED,
                correlation_id=correlation_id,
            )
