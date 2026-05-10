"""Backend-backed MCP provider for production registry integration."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
import os
from typing import Any

from enterprise_service_contracts.headers import (
    ORG_HEADER,
    SERVICE_TOKEN_HEADER,
    USER_HEADER,
)
import httpx

from agent_runtime.execution.contracts import AgentRuntimeContext, RuntimeErrorCode
from agent_runtime.execution.errors import AgentRuntimeError
from agent_runtime.capabilities.mcp.cards import (
    McpAuthState,
    McpConnectionMetadata,
    McpResourceAccessPolicy,
    McpResourceDescriptor,
    McpRiskLevel,
    McpServerCard,
    McpToolDescriptor,
)
from agent_runtime.capabilities.mcp.client import (
    McpAuthError,
    McpClient,
    McpConnectionError,
    McpTimeoutError,
    McpUnsupportedMethodError,
    RawMcpConnectionMetadata,
)
from agent_runtime.capabilities.mcp.constants import Keys, Values
from agent_runtime.capabilities.mcp.middleware.auth_mcp import McpAuthSession


@dataclass(frozen=True)
class BackendMcpProvider:
    """MCP provider that reads safe card metadata from the core backend."""

    backend_url: str
    runtime_context: AgentRuntimeContext
    auth_redirect_uri: str
    timeout_seconds: float = 10

    async def list_server_cards(self) -> tuple[McpServerCard, ...]:
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.get(
                f"{self.backend_url.rstrip('/')}/internal/v1/mcp/cards",
                params={
                    Keys.Field.ORG_ID: self.runtime_context.org_id,
                    Keys.Field.USER_ID: self.runtime_context.user_id,
                },
                headers=BackendMcpServiceAuth.headers(self.runtime_context),
            )
        response.raise_for_status()
        payload = response.json()
        return tuple(
            self._runtime_visible_card(card) for card in payload.get("servers", ())
        )

    def create_client(self, card: McpServerCard) -> McpClient:
        return BackendMcpClient(
            backend_url=self.backend_url,
            runtime_context=self.runtime_context,
            card=card,
            timeout_seconds=self.timeout_seconds,
        )

    async def create_auth_session(
        self,
        *,
        server_id: str,
        runtime_context: AgentRuntimeContext,
    ) -> McpAuthSession:
        card = await self._card_by_server_id_or_name(server_id)
        resolved_server_id = card.server_id or card.name
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.post(
                f"{self.backend_url.rstrip('/')}/internal/v1/mcp/servers/{resolved_server_id}/auth/start",
                json={
                    Keys.Field.ORG_ID: runtime_context.org_id,
                    Keys.Field.USER_ID: runtime_context.user_id,
                    Keys.Field.REDIRECT_URI: self.auth_redirect_uri,
                },
                headers=BackendMcpServiceAuth.headers(runtime_context),
            )
        response.raise_for_status()
        payload = response.json()
        return McpAuthSession(
            server_id=str(payload["server_id"]),
            server_name=card.name,
            display_name=card.display_name or card.name,
            auth_url=str(payload["auth_url"]),
            expires_at=datetime.fromisoformat(str(payload["expires_at"])),
        )

    @staticmethod
    def _runtime_visible_card(card: object) -> McpServerCard:
        parsed = McpServerCard.model_validate(card)
        return parsed.model_copy(update={Keys.Field.REQUIRED_SCOPES: frozenset()})

    async def _card_by_server_id_or_name(self, value: str) -> McpServerCard:
        for card in await self.list_server_cards():
            if card.server_id == value or card.name == value:
                return card
        raise AgentRuntimeError(
            RuntimeErrorCode.VALIDATION_ERROR,
            f"No MCP server card found for server_id or name '{value}'.",
            retryable=False,
        )


@dataclass
class BackendMcpClient:
    """MCP client that resolves credentials through backend-owned state."""

    backend_url: str
    runtime_context: AgentRuntimeContext
    card: McpServerCard
    timeout_seconds: float = 10
    server_url: str | None = None
    initialized: bool = False
    request_id: int = 0

    async def connect(self) -> RawMcpConnectionMetadata:
        if self.card.auth_state not in {
            McpAuthState.AUTHENTICATED,
            McpAuthState.AUTH_SKIPPED,
        }:
            raise McpAuthError("MCP server is not authenticated.")
        server_id = self.card.server_id or self.card.name
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.post(
                f"{self.backend_url.rstrip('/')}/internal/v1/mcp/servers/{server_id}/client-session",
                params={
                    Keys.Field.ORG_ID: self.runtime_context.org_id,
                    Keys.Field.USER_ID: self.runtime_context.user_id,
                },
                headers=BackendMcpServiceAuth.headers(self.runtime_context),
            )
        response.raise_for_status()
        payload = response.json()
        if payload.get(Keys.Field.AUTH_STATE) != McpAuthState.AUTHENTICATED.value:
            raise McpAuthError("MCP server is not authenticated.")
        self.server_url = str(payload[Keys.Field.URL]).rstrip("/")
        await self._initialize()
        return McpConnectionMetadata(
            server_name=self.card.name,
            transport=self.card.transport,
            auth_mode=self.card.auth_mode,
        )

    async def list_tools(self) -> tuple[McpToolDescriptor | dict[str, Any], ...]:
        result = await self._rpc_result(Values.JsonRpcMethod.LIST_TOOLS)
        tools = result.get(Keys.Field.TOOLS, ())
        if not isinstance(tools, list):
            return ()
        return tuple(
            self._tool_descriptor(tool) for tool in tools if isinstance(tool, dict)
        )

    async def list_resources(
        self,
    ) -> tuple[McpResourceDescriptor | dict[str, Any], ...]:
        try:
            result = await self._rpc_result(Values.JsonRpcMethod.LIST_RESOURCES)
        except McpUnsupportedMethodError:
            return ()
        resources = result.get(Keys.Field.RESOURCES, ())
        if not isinstance(resources, list):
            return ()
        return tuple(
            self._resource_descriptor(resource)
            for resource in resources
            if isinstance(resource, dict)
        )

    async def call_tool(
        self,
        *,
        tool_name: str,
        arguments: Mapping[str, Any],
    ) -> dict[str, Any]:
        return await self._rpc_result(
            Values.JsonRpcMethod.CALL_TOOL,
            {
                Keys.Field.NAME: tool_name,
                Keys.Field.ARGUMENTS: dict(arguments),
            },
        )

    async def _initialize(self) -> None:
        if self.initialized:
            return
        await self._rpc_result(
            Values.JsonRpcMethod.INITIALIZE,
            {
                Keys.JsonRpc.PROTOCOL_VERSION: Values.McpClientInfo.PROTOCOL_VERSION,
                Keys.JsonRpc.CAPABILITIES: {},
                Keys.JsonRpc.CLIENT_INFO: {
                    Keys.Field.NAME: Values.McpClientInfo.NAME,
                    Keys.Field.VERSION: Values.McpClientInfo.VERSION,
                },
            },
        )
        await self._rpc(
            {
                Keys.JsonRpc.JSONRPC: Values.JsonRpc.VERSION,
                Keys.JsonRpc.METHOD: Values.JsonRpcMethod.INITIALIZED,
            }
        )
        self.initialized = True

    async def _rpc_result(
        self, method: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        if self.server_url is None and method != Values.JsonRpcMethod.INITIALIZE:
            await self.connect()
        self.request_id += 1
        payload: dict[str, Any] = {
            Keys.JsonRpc.JSONRPC: Values.JsonRpc.VERSION,
            Keys.JsonRpc.ID: self.request_id,
            Keys.JsonRpc.METHOD: method,
        }
        if params is not None:
            payload[Keys.JsonRpc.PARAMS] = params
        response = await self._rpc(payload)
        error = response.get(Keys.JsonRpc.ERROR)
        if isinstance(error, dict):
            if self._is_method_not_found(error):
                raise McpUnsupportedMethodError("MCP JSON-RPC method is not supported.")
            raise McpConnectionError("MCP JSON-RPC request failed.")
        result = response.get(Keys.JsonRpc.RESULT)
        if not isinstance(result, dict):
            return {}
        return result

    @staticmethod
    def _is_method_not_found(error: dict[str, Any]) -> bool:
        code = error.get(Keys.Field.CODE)
        if isinstance(code, int):
            return code == Values.JsonRpcError.METHOD_NOT_FOUND
        if isinstance(code, str):
            try:
                return int(code) == Values.JsonRpcError.METHOD_NOT_FOUND
            except ValueError:
                return False
        return False

    async def _rpc(self, payload: dict[str, Any]) -> dict[str, Any]:
        server_id = self.card.server_id or self.card.name
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            try:
                response = await client.post(
                    f"{self.backend_url.rstrip('/')}"
                    f"{Values.Route.INTERNAL_MCP_RPC.format(server_id=server_id)}",
                    json={
                        Keys.Field.ORG_ID: self.runtime_context.org_id,
                        Keys.Field.USER_ID: self.runtime_context.user_id,
                        Keys.JsonRpc.PAYLOAD: payload,
                    },
                    headers=BackendMcpServiceAuth.headers(self.runtime_context),
                )
            except httpx.TimeoutException as exc:
                raise McpTimeoutError("MCP JSON-RPC request timed out.") from exc
            except httpx.HTTPError as exc:
                raise McpConnectionError("MCP JSON-RPC request failed.") from exc
        if response.status_code in {401, 403}:
            raise McpAuthError("MCP server is not authenticated.")
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise McpConnectionError("MCP JSON-RPC request failed.") from exc
        envelope = response.json()
        rpc_payload = (
            envelope.get(Keys.JsonRpc.PAYLOAD) if isinstance(envelope, dict) else None
        )
        if not isinstance(rpc_payload, dict):
            raise McpConnectionError("MCP JSON-RPC proxy returned an invalid response.")
        return rpc_payload

    def _tool_descriptor(self, tool: dict[str, Any]) -> McpToolDescriptor:
        name = self._required_string(
            tool, Keys.Field.NAME, Values.Placeholder.TOOL_NAME
        )
        input_schema = self._schema(
            tool.get(Keys.NativeDescriptor.INPUT_SCHEMA_CAMEL)
            or tool.get(Keys.Field.INPUT_SCHEMA)
        )
        output_shape = self._schema(
            tool.get(Keys.NativeDescriptor.OUTPUT_SCHEMA_CAMEL)
            or {Keys.Schema.TYPE: Values.SchemaType.OBJECT}
        )
        # Polish-removal Phase 2.A — synthesise a deterministic display
        # template at descriptor-build time so the presentation layer never
        # falls through to the polish LLM for MCP tools. Uses the server
        # card's ``display_name`` (or ``name`` as fallback) as the connector
        # label. See ``docs/refactor/01-presentation-polish-removal.md``.
        from agent_runtime.capabilities.middleware import (  # noqa: PLC0415
            DisplayMetadataMiddleware,
        )
        from agent_runtime.capabilities.mcp.descriptor_registry import (  # noqa: PLC0415
            McpDisplayRegistryContext,
        )

        connector_label = self.card.display_name or self.card.name
        display = DisplayMetadataMiddleware.synthesise_for_mcp(
            tool_name=name,
            connector=connector_label,
            input_schema=input_schema,
            output_shape=output_shape,
        )
        # Phase 2.B — register on the per-run lookup so
        # ``PresentationGenerator._resolve_tool_template`` can see this
        # template for ``call_mcp_tool`` dispatcher events. ``register`` is
        # a no-op when no registry is bound (replay / eval / tests that
        # don't need the lookup), so unconditional registration is safe.
        McpDisplayRegistryContext.register(name, display)
        return McpToolDescriptor(
            name=name,
            description=self._optional_string(tool.get("description"))
            or f"{name} MCP tool.",
            input_schema=input_schema,
            output_shape=output_shape,
            risk_level=McpRiskLevel.MEDIUM,
            display=display,
        )

    def _resource_descriptor(self, resource: dict[str, Any]) -> McpResourceDescriptor:
        name = self._required_string(
            resource, Keys.Field.NAME, Values.Placeholder.RESOURCE_NAME
        )
        uri = self._required_string(
            resource, Keys.Field.URI, f"{Values.UriScheme.MCP}://{name}"
        )
        return McpResourceDescriptor(
            uri=uri,
            name=name,
            mime_type=self._optional_string(
                resource.get(Keys.NativeDescriptor.MIME_TYPE_CAMEL)
            )
            or self._optional_string(resource.get(Keys.Field.MIME_TYPE))
            or Values.Mime.OCTET_STREAM,
            description=self._optional_string(resource.get("description"))
            or f"{name} MCP resource.",
            access_policy=McpResourceAccessPolicy(
                required_scopes=self.card.required_scopes,
                read_only=True,
            ),
        )

    @staticmethod
    def _schema(value: object) -> dict[str, Any]:
        if isinstance(value, dict):
            return value
        return {Keys.Schema.TYPE: Values.SchemaType.OBJECT}

    @staticmethod
    def _required_string(payload: dict[str, Any], key: str, fallback: str) -> str:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        return fallback

    @staticmethod
    def _optional_string(value: object) -> str | None:
        if isinstance(value, str) and value.strip():
            return value.strip()
        return None


class BackendMcpServiceAuth:
    """Service-auth header construction for backend MCP calls."""

    @staticmethod
    def headers(runtime_context: AgentRuntimeContext) -> dict[str, str]:
        token = os.environ.get("ENTERPRISE_SERVICE_TOKEN", "").strip()
        if not token:
            return {}
        return {
            SERVICE_TOKEN_HEADER: token,
            ORG_HEADER: runtime_context.org_id,
            USER_HEADER: runtime_context.user_id,
        }
