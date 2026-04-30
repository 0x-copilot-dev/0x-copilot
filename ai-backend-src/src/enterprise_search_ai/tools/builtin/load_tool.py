"""Built-in callable that lets the model lazily load full tool specs."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from pydantic import Field, ValidationError

from enterprise_search_ai.agent.contracts import AgentRuntimeContext, RuntimeContract
from enterprise_search_ai.tools.cards import ToolLoadErrorCode, ToolLoadResult
from enterprise_search_ai.tools.loader import ToolLoader


class LoadToolInput(RuntimeContract):
    """Input contract for the model-facing load-tool helper."""

    tool_name: str = Field(min_length=1)


@dataclass(frozen=True)
class LoadToolSpecTool:
    """Small adapter that can be wrapped by LangChain tool primitives."""

    loader: ToolLoader
    runtime_context: AgentRuntimeContext
    name: str = "load_tool_spec"
    description: str = (
        "Load the full schema and instructions for an authorized tool by stable name."
    )

    def invoke(self, raw_input: LoadToolInput | Mapping[str, Any] | str) -> dict[str, Any]:
        """Return a JSON-serializable loaded spec or typed safe error."""

        parsed_input = _parse_input(raw_input, self.runtime_context.trace_id)
        if isinstance(parsed_input, ToolLoadResult):
            return parsed_input.model_dump(mode="json", exclude_none=True)

        result = self.loader.load_tool_by_name(
            tool_name=parsed_input.tool_name,
            runtime_context=self.runtime_context,
        )
        return result.model_dump(mode="json", exclude_none=True)

    def __call__(self, raw_input: LoadToolInput | Mapping[str, Any] | str) -> dict[str, Any]:
        return self.invoke(raw_input)


def _parse_input(
    raw_input: LoadToolInput | Mapping[str, Any] | str,
    correlation_id: str,
) -> LoadToolInput | ToolLoadResult:
    if isinstance(raw_input, LoadToolInput):
        return raw_input
    if isinstance(raw_input, str):
        raw_input = {"tool_name": raw_input}

    try:
        return LoadToolInput.model_validate(raw_input)
    except ValidationError:
        return ToolLoadResult.fail(
            ToolLoadErrorCode.INVALID_TOOL_NAME,
            "Tools must be requested by stable name.",
            correlation_id=correlation_id,
        )
