"""Built-in callable that lets the model explicitly load MCP servers."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from pydantic import Field, ValidationError

from enterprise_search_ai.agent.contracts import AgentRuntimeContext, RuntimeContract
from enterprise_search_ai.mcp.cards import McpLoadErrorCode, McpLoadResult
from enterprise_search_ai.mcp.constants import Keys, Messages, Values
from enterprise_search_ai.mcp.loader import McpLoader


class LoadMcpServerInput(RuntimeContract):
    """Input contract for the model-facing MCP load helper."""

    server_name: str = Field(min_length=1)
    local_tool_names: frozenset[str] = Field(default_factory=frozenset)


@dataclass(frozen=True)
class LoadMcpServerTool:
    """Small adapter that can be wrapped by LangChain tool primitives."""

    loader: McpLoader
    runtime_context: AgentRuntimeContext
    name: str = Values.ToolName.LOAD_MCP_SERVER
    description: str = Messages.Middleware.LOAD_MCP_SERVER_TOOL_DESCRIPTION

    async def ainvoke(
        self,
        raw_input: LoadMcpServerInput | Mapping[str, Any] | str,
    ) -> dict[str, Any]:
        """Return JSON-serializable descriptors or a typed safe error."""

        parsed_input = LoadMcpServerInputParser.parse(raw_input, self.runtime_context.trace_id)
        if isinstance(parsed_input, McpLoadResult):
            return parsed_input.model_dump(mode="json", exclude_none=True)

        result = await self.loader.load_server_by_name(
            server_name=parsed_input.server_name,
            runtime_context=self.runtime_context,
            local_tool_names=parsed_input.local_tool_names,
        )
        return result.model_dump(mode="json", exclude_none=True)

    async def __call__(
        self,
        raw_input: LoadMcpServerInput | Mapping[str, Any] | str,
    ) -> dict[str, Any]:
        return await self.ainvoke(raw_input)


class LoadMcpServerInputParser:
    """Parser for untrusted MCP load tool input."""

    @classmethod
    def parse(
        cls,
        raw_input: LoadMcpServerInput | Mapping[str, Any] | str,
        correlation_id: str,
    ) -> LoadMcpServerInput | McpLoadResult:
        if isinstance(raw_input, LoadMcpServerInput):
            return raw_input
        if isinstance(raw_input, str):
            raw_input = {Keys.Field.SERVER_NAME: raw_input}

        try:
            return LoadMcpServerInput.model_validate(raw_input)
        except ValidationError:
            return McpLoadResult.fail(
                McpLoadErrorCode.INVALID_SERVER_NAME,
                Messages.Loader.STABLE_SERVER_NAME_REQUIRED,
                correlation_id=correlation_id,
            )
