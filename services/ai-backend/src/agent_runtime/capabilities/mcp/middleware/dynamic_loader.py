"""Built-in callable that lets the model explicitly load MCP servers."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass, field as dataclass_field
from typing import Any

from pydantic import Field, ValidationError

from agent_runtime.execution.contracts import AgentRuntimeContext, RuntimeContract
from agent_runtime.capabilities.mcp.cards import McpLoadErrorCode, McpLoadResult
from agent_runtime.capabilities.mcp.catalog import (
    McpCatalogBuilder,
    McpCatalogError,
    McpCatalogPublisher,
)
from agent_runtime.capabilities.mcp.constants import Keys, Messages, Values
from agent_runtime.capabilities.mcp.loader import McpLoader

_LOGGER = logging.getLogger(__name__)


class LoadMcpServerInput(RuntimeContract):
    """Input contract for the model-facing MCP load helper."""

    server_name: str = Field(min_length=1)


@dataclass(frozen=True)
class LoadMcpServerTool:
    """Small adapter that can be wrapped by LangChain tool primitives.

    When a ``catalog`` publisher is wired, a successful load is materialized as
    browsable files under ``/mcp/<server>/`` and the model receives a short
    POINTER — paths, counts, and what to do next — instead of every descriptor.
    That is the whole P0: a 52-tool, 70 KB, zero-newline Linear descriptor is
    unreachable as a tool result (offloaded to a blob whose preview, offsets and
    grep all dead-end), and is trivially reachable as ~53 small files.

    Without a catalog the tool behaves exactly as before and returns the full
    ``McpLoadResult`` payload — the seam is additive, so a runtime that has not
    composed the ``/mcp/`` route is unchanged.

    Discovery grants nothing either way: invoking a tool still goes through the
    ``call_mcp_tool`` gateway, which carries the PDP decision and the approval
    interrupt.
    """

    loader: McpLoader
    runtime_context: AgentRuntimeContext
    local_tool_names: frozenset[str] = dataclass_field(default_factory=frozenset)
    catalog: McpCatalogPublisher | None = None
    name: str = Values.ToolName.LOAD_MCP_SERVER
    description: str = Messages.Middleware.LOAD_MCP_SERVER_TOOL_DESCRIPTION

    async def ainvoke(
        self,
        raw_input: LoadMcpServerInput | Mapping[str, Any] | str,
    ) -> dict[str, Any]:
        """Return a catalog pointer (or descriptors) or a typed safe error."""

        parsed_input = LoadMcpServerInputParser.parse(
            raw_input, self.runtime_context.trace_id
        )
        if isinstance(parsed_input, McpLoadResult):
            return parsed_input.model_dump(mode="json", exclude_none=True)

        result = await self.loader.load_server_by_name(
            server_name=parsed_input.server_name,
            runtime_context=self.runtime_context,
            local_tool_names=self.local_tool_names,
        )
        return McpLoadResultPresenter.present(
            result,
            catalog=self.catalog,
            correlation_id=self.runtime_context.trace_id,
        )

    async def __call__(
        self,
        raw_input: LoadMcpServerInput | Mapping[str, Any] | str,
    ) -> dict[str, Any]:
        """Delegate to ``ainvoke``."""
        return await self.ainvoke(raw_input)


class McpLoadResultPresenter:
    """Turn a load result into the payload the model actually receives."""

    @classmethod
    def present(
        cls,
        result: McpLoadResult,
        *,
        catalog: McpCatalogPublisher | None,
        correlation_id: str,
    ) -> dict[str, Any]:
        """Publish to the catalog and return a pointer, else the legacy payload.

        Failures are already small and typed, so they pass straight through.
        """

        loaded_server = result.loaded_server
        if catalog is None or loaded_server is None:
            return result.model_dump(mode="json", exclude_none=True)
        try:
            server_catalog = McpCatalogBuilder.build(loaded_server)
            catalog.publish(server_catalog)
        except McpCatalogError:
            # A descriptor we refuse to publish is a descriptor we refuse to
            # hand the model: falling back to the raw payload would restore the
            # unreachable blob AND carry whatever the scanner objected to.
            _LOGGER.warning(
                "MCP catalog refused: server=%s trace=%s",
                loaded_server.server_card.name,
                correlation_id,
                exc_info=True,
            )
            return McpLoadResult.fail(
                McpLoadErrorCode.MALFORMED_DESCRIPTOR,
                Messages.Loader.DESCRIPTORS_INVALID,
                retryable=False,
                server_name=loaded_server.server_card.name,
                correlation_id=correlation_id,
            ).model_dump(mode="json", exclude_none=True)
        payload: dict[str, Any] = server_catalog.pointer.model_dump(
            mode="json", exclude_none=True
        )
        if loaded_server.warnings:
            payload[Keys.Field.WARNINGS] = [
                warning.model_dump(mode="json", exclude_none=True)
                for warning in loaded_server.warnings
            ]
        return payload


class LoadMcpServerInputParser:
    """Parser for untrusted MCP load tool input."""

    @classmethod
    def parse(
        cls,
        raw_input: LoadMcpServerInput | Mapping[str, Any] | str,
        correlation_id: str,
    ) -> LoadMcpServerInput | McpLoadResult:
        """Validate ``raw_input`` into a typed request; return a failure result on bad server name."""
        if isinstance(raw_input, LoadMcpServerInput):
            return raw_input
        if isinstance(raw_input, str):
            raw_input = {Keys.Field.SERVER_NAME: raw_input}
        elif isinstance(raw_input, Mapping):
            raw_input = {Keys.Field.SERVER_NAME: raw_input.get(Keys.Field.SERVER_NAME)}

        try:
            return LoadMcpServerInput.model_validate(raw_input)
        except ValidationError:
            return McpLoadResult.fail(
                McpLoadErrorCode.INVALID_SERVER_NAME,
                Messages.Loader.STABLE_SERVER_NAME_REQUIRED,
                correlation_id=correlation_id,
            )
