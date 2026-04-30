"""Explicit loader for dynamically selected MCP servers."""

from __future__ import annotations

import asyncio
from collections import Counter
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import TypeVar

from pydantic import ValidationError

from enterprise_search_ai.agent.errors import AgentRuntimeError
from enterprise_search_ai.mcp.cards import (
    LoadedMcpServer,
    McpConnectionMetadata,
    McpLoadError,
    McpLoadErrorCode,
    McpLoadRequest,
    McpLoadResult,
    McpLoadWarning,
    McpResourceDescriptor,
    McpServerHealth,
    McpToolDescriptor,
    McpTransport,
    McpValueNormalizer,
    McpWarningCode,
)
from enterprise_search_ai.mcp.client import (
    McpAuthError,
    McpClient,
    McpClientError,
    McpConnectionError,
    McpTimeoutError,
    RawMcpConnectionMetadata,
)
from enterprise_search_ai.mcp.constants import Defaults, Keys, Messages
from enterprise_search_ai.mcp.permissions import McpPermissionPolicy
from enterprise_search_ai.mcp.registry import DynamicMcpRegistry, RegisteredMcpServer

_T = TypeVar("_T")
SUPPORTED_TRANSPORTS = frozenset({McpTransport.STDIO, McpTransport.SSE, McpTransport.HTTP})


@dataclass(frozen=True)
class McpLoader:
    """Connects to a selected MCP server and validates discovered descriptors."""

    registry: DynamicMcpRegistry
    timeout_seconds: float = Defaults.TIMEOUT_SECONDS
    max_tool_descriptors: int = Defaults.MAX_TOOL_DESCRIPTORS
    max_resource_descriptors: int = Defaults.MAX_RESOURCE_DESCRIPTORS

    async def load_server(self, request: McpLoadRequest) -> McpLoadResult:
        """Load a selected MCP server while rechecking permissions and validation."""

        runtime_context = request.runtime_context
        resolution = self.registry.resolve_server(request.server_name)
        if isinstance(resolution, McpLoadError):
            return McpLoaderHelpers.result_from_error(resolution, runtime_context.trace_id)

        card = resolution.card
        if card.transport not in SUPPORTED_TRANSPORTS:
            return McpLoadResult.fail(
                McpLoadErrorCode.UNSUPPORTED_TRANSPORT,
                Messages.Loader.UNSUPPORTED_TRANSPORT,
                server_name=card.name,
                correlation_id=runtime_context.trace_id,
            )
        if not McpPermissionPolicy.is_server_card_authorized(runtime_context, card):
            return McpLoadResult.fail(
                McpLoadErrorCode.PERMISSION_DENIED,
                Messages.Loader.UNAUTHORIZED_SERVER,
                server_name=card.name,
                correlation_id=runtime_context.trace_id,
            )

        try:
            client = resolution.provider.create_client(card)
            metadata = await self._connect(client, resolution)
            raw_tools = await self._call_client(client.list_tools)
            raw_resources = await self._call_client(client.list_resources)
        except (McpTimeoutError, TimeoutError, asyncio.TimeoutError):
            return McpLoadResult.fail(
                McpLoadErrorCode.TIMEOUT,
                Messages.Loader.TIMEOUT,
                retryable=True,
                server_name=card.name,
                correlation_id=runtime_context.trace_id,
            )
        except (McpAuthError, PermissionError):
            return McpLoadResult.fail(
                McpLoadErrorCode.AUTH_FAILURE,
                Messages.Loader.AUTH_FAILED,
                retryable=False,
                server_name=card.name,
                correlation_id=runtime_context.trace_id,
            )
        except (McpConnectionError, ConnectionError):
            return McpLoadResult.fail(
                McpLoadErrorCode.CONNECTION_FAILED,
                Messages.Loader.CONNECTION_FAILED,
                retryable=True,
                server_name=card.name,
                correlation_id=runtime_context.trace_id,
            )
        except ValidationError:
            return McpLoadResult.fail(
                McpLoadErrorCode.MALFORMED_DESCRIPTOR,
                Messages.Loader.INVALID_CONNECTION_METADATA,
                retryable=False,
                server_name=card.name,
                correlation_id=runtime_context.trace_id,
            )
        except (AgentRuntimeError, McpClientError, Exception):
            return McpLoadResult.fail(
                McpLoadErrorCode.CONNECTION_FAILED,
                Messages.Loader.LOAD_FAILED,
                retryable=True,
                server_name=card.name,
                correlation_id=runtime_context.trace_id,
            )

        raw_tools = McpLoaderHelpers.coerce_raw_sequence(raw_tools)
        raw_resources = McpLoaderHelpers.coerce_raw_sequence(raw_resources)
        if raw_tools is None or raw_resources is None:
            return McpLoadResult.fail(
                McpLoadErrorCode.MALFORMED_DESCRIPTOR,
                Messages.Loader.DESCRIPTORS_INVALID,
                retryable=False,
                server_name=card.name,
                correlation_id=runtime_context.trace_id,
            )

        if len(raw_tools) > self.max_tool_descriptors:
            return McpLoadResult.fail(
                McpLoadErrorCode.LOAD_BUDGET_EXCEEDED,
                Messages.Loader.TOOL_BUDGET_EXCEEDED,
                retryable=False,
                server_name=card.name,
                correlation_id=runtime_context.trace_id,
            )
        if len(raw_resources) > self.max_resource_descriptors:
            return McpLoadResult.fail(
                McpLoadErrorCode.LOAD_BUDGET_EXCEEDED,
                Messages.Loader.RESOURCE_BUDGET_EXCEEDED,
                retryable=False,
                server_name=card.name,
                correlation_id=runtime_context.trace_id,
            )

        parsed_tools = McpLoaderHelpers.parse_tools(raw_tools)
        if isinstance(parsed_tools, McpLoadErrorCode):
            return McpLoadResult.fail(
                parsed_tools,
                McpLoaderHelpers.safe_descriptor_message(parsed_tools),
                server_name=card.name,
                correlation_id=runtime_context.trace_id,
            )

        parsed_resources = McpLoaderHelpers.parse_resources(raw_resources)
        if isinstance(parsed_resources, McpLoadErrorCode):
            return McpLoadResult.fail(
                parsed_resources,
                McpLoaderHelpers.safe_descriptor_message(parsed_resources),
                server_name=card.name,
                correlation_id=runtime_context.trace_id,
            )

        duplicate_tool_name = McpLoaderHelpers.first_duplicate_name(
            [tool.name for tool in parsed_tools]
        )
        if duplicate_tool_name is not None:
            return McpLoadResult.fail(
                McpLoadErrorCode.DUPLICATE_DESCRIPTOR_NAME,
                Messages.Loader.DUPLICATE_TOOL_NAMES,
                server_name=card.name,
                correlation_id=runtime_context.trace_id,
            )

        duplicate_resource_name = McpLoaderHelpers.first_duplicate_name(
            [resource.name for resource in parsed_resources]
        )
        if duplicate_resource_name is not None:
            return McpLoadResult.fail(
                McpLoadErrorCode.DUPLICATE_DESCRIPTOR_NAME,
                Messages.Loader.DUPLICATE_RESOURCE_NAMES,
                server_name=card.name,
                correlation_id=runtime_context.trace_id,
            )

        local_collision = McpLoaderHelpers.first_local_tool_collision(
            parsed_tools,
            request.local_tool_names,
        )
        if local_collision is not None:
            return McpLoadResult.fail(
                McpLoadErrorCode.LOCAL_TOOL_COLLISION,
                Messages.Loader.LOCAL_TOOL_COLLISION,
                server_name=card.name,
                correlation_id=runtime_context.trace_id,
            )

        warnings = ()
        if card.health == McpServerHealth.DEGRADED:
            warnings = (
                McpLoadWarning(
                    code=McpWarningCode.SERVER_DEGRADED,
                    safe_message=Messages.Loader.SERVER_DEGRADED,
                ),
            )

        return McpLoadResult.ok(
            LoadedMcpServer(
                server_card=card,
                tools=parsed_tools,
                resources=parsed_resources,
                connection_metadata=metadata,
                warnings=warnings,
            )
        )

    async def load_server_by_name(
        self,
        *,
        server_name: str,
        runtime_context: object,
        local_tool_names: object = (),
    ) -> McpLoadResult:
        """Parse an untrusted model request before loading a selected server."""

        try:
            request = McpLoadRequest(
                server_name=server_name,
                runtime_context=runtime_context,
                local_tool_names=local_tool_names,
            )
        except ValidationError:
            return McpLoadResult.fail(
                McpLoadErrorCode.INVALID_SERVER_NAME,
                Messages.Loader.STABLE_SERVER_NAME_REQUIRED,
                server_name=McpLoaderHelpers.safe_server_name(server_name),
            )
        return await self.load_server(request)

    async def _connect(
        self,
        client: McpClient,
        resolution: RegisteredMcpServer,
    ) -> McpConnectionMetadata:
        raw_metadata = await self._call_client(client.connect)
        return McpLoaderHelpers.metadata_from_raw(raw_metadata, resolution)

    async def _call_client(self, call: Callable[[], Awaitable[_T]]) -> _T:
        return await asyncio.wait_for(call(), timeout=self.timeout_seconds)


class McpLoaderHelpers:
    """Helper methods for parsing and comparing MCP load output."""

    @classmethod
    def metadata_from_raw(
        cls,
        raw_metadata: RawMcpConnectionMetadata,
        resolution: RegisteredMcpServer,
    ) -> McpConnectionMetadata:
        card = resolution.card
        if raw_metadata is None:
            return McpConnectionMetadata(
                server_name=card.name,
                transport=card.transport,
                auth_mode=card.auth_mode,
            )
        if isinstance(raw_metadata, McpConnectionMetadata):
            return raw_metadata
        return McpConnectionMetadata.model_validate(raw_metadata)

    @classmethod
    def coerce_raw_sequence(cls, raw_value: object) -> Sequence[object] | None:
        if isinstance(raw_value, (str, bytes)) or not isinstance(raw_value, Sequence):
            return None
        return raw_value

    @classmethod
    def parse_tools(
        cls,
        raw_tools: Sequence[object],
    ) -> tuple[McpToolDescriptor, ...] | McpLoadErrorCode:
        try:
            return tuple(
                raw_tool
                if isinstance(raw_tool, McpToolDescriptor)
                else McpToolDescriptor.model_validate(raw_tool)
                for raw_tool in raw_tools
            )
        except (TypeError, ValidationError):
            return McpLoadErrorCode.MALFORMED_DESCRIPTOR

    @classmethod
    def parse_resources(
        cls,
        raw_resources: Sequence[object],
    ) -> tuple[McpResourceDescriptor, ...] | McpLoadErrorCode:
        try:
            return tuple(
                raw_resource
                if isinstance(raw_resource, McpResourceDescriptor)
                else McpResourceDescriptor.model_validate(raw_resource)
                for raw_resource in raw_resources
            )
        except (TypeError, ValidationError):
            return McpLoadErrorCode.MALFORMED_DESCRIPTOR

    @classmethod
    def first_duplicate_name(cls, names: Sequence[str]) -> str | None:
        counts = Counter(names)
        duplicate_names = sorted(name for name, count in counts.items() if count > 1)
        if not duplicate_names:
            return None
        return duplicate_names[0]

    @classmethod
    def first_local_tool_collision(
        cls,
        tools: Sequence[McpToolDescriptor],
        local_tool_names: frozenset[str],
    ) -> str | None:
        collisions = sorted({tool.name for tool in tools}.intersection(local_tool_names))
        if not collisions:
            return None
        return collisions[0]

    @classmethod
    def result_from_error(cls, error: McpLoadError, correlation_id: str) -> McpLoadResult:
        return McpLoadResult.fail(
            error.code,
            error.safe_message,
            retryable=error.retryable,
            server_name=error.server_name,
            correlation_id=correlation_id,
        )

    @classmethod
    def safe_descriptor_message(cls, code: McpLoadErrorCode) -> str:
        if code == McpLoadErrorCode.MALFORMED_DESCRIPTOR:
            return Messages.Loader.DESCRIPTORS_INVALID
        return Messages.Loader.DESCRIPTORS_LOAD_FAILED

    @classmethod
    def safe_server_name(cls, server_name: str) -> str | None:
        try:
            return McpValueNormalizer.normalize_slug(server_name, Keys.Field.SERVER_NAME)
        except ValueError:
            return None
