"""MCP provider and client that proxy calls through the core backend's internal API."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
import os
from typing import Any

from copilot_service_contracts.headers import (
    ORG_HEADER,
    SERVICE_TOKEN_HEADER,
    USER_HEADER,
)
import httpx

from agent_runtime.capabilities.http_pool import BackendHttpPool
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
    """McpServerProvider that fetches server cards and auth sessions from the backend.

    ``http_client`` defaults to the process-shared :class:`BackendHttpPool`
    instance so connection pooling + keep-alive amortize TLS across calls.
    Tests inject a fake client through this field directly — the field is
    the substitution seam, the pool is the production default.
    """

    backend_url: str
    runtime_context: AgentRuntimeContext
    auth_redirect_uri: str
    timeout_seconds: float = 10
    http_client: httpx.AsyncClient = field(
        default_factory=BackendHttpPool.get,
        repr=False,
        compare=False,
    )

    async def list_server_cards(self) -> tuple[McpServerCard, ...]:
        """Fetch compact server cards from the backend and strip required_scopes."""
        response = await self.http_client.get(
            f"{self.backend_url.rstrip('/')}/internal/v1/mcp/cards",
            params={
                Keys.Field.ORG_ID: self.runtime_context.org_id,
                Keys.Field.USER_ID: self.runtime_context.user_id,
            },
            headers=BackendMcpServiceAuth.headers(self.runtime_context),
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        payload = response.json()
        return tuple(
            self._runtime_visible_card(card) for card in payload.get("servers", ())
        )

    def create_client(self, card: McpServerCard) -> McpClient:
        """Instantiate a BackendMcpClient for the given server card.

        Threads the same ``http_client`` so a test's injected fake reaches
        the child client without a second setup step.
        """
        return BackendMcpClient(
            backend_url=self.backend_url,
            runtime_context=self.runtime_context,
            card=card,
            timeout_seconds=self.timeout_seconds,
            http_client=self.http_client,
        )

    async def create_auth_session(
        self,
        *,
        server_id: str,
        runtime_context: AgentRuntimeContext,
    ) -> McpAuthSession:
        """Start an OAuth session for ``server_id`` and return the auth URL."""
        card = await self._card_by_server_id_or_name(server_id)
        resolved_server_id = card.server_id or card.name
        response = await self.http_client.post(
            f"{self.backend_url.rstrip('/')}/internal/v1/mcp/servers/{resolved_server_id}/auth/start",
            json={
                Keys.Field.ORG_ID: runtime_context.org_id,
                Keys.Field.USER_ID: runtime_context.user_id,
                Keys.Field.REDIRECT_URI: self.auth_redirect_uri,
            },
            headers=BackendMcpServiceAuth.headers(runtime_context),
            timeout=self.timeout_seconds,
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
        """Validate the raw backend card and clear ``required_scopes`` for runtime use.

        The backend is the authority on scope enforcement; the runtime side
        strips required_scopes so it never double-enforces them.
        """
        parsed = McpServerCard.model_validate(card)
        return parsed.model_copy(update={Keys.Field.REQUIRED_SCOPES: frozenset()})

    async def _card_by_server_id_or_name(self, value: str) -> McpServerCard:
        """Resolve a server_id-or-name to a card; raise if no match is found."""
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
    """MCP client that resolves credentials through backend-owned state.

    ``http_client`` defaults to the process-shared :class:`BackendHttpPool`
    so JSON-RPC tool calls reuse the same TLS connection across a run.
    """

    backend_url: str
    runtime_context: AgentRuntimeContext
    card: McpServerCard
    timeout_seconds: float = 10
    server_url: str | None = None
    initialized: bool = False
    request_id: int = 0
    http_client: httpx.AsyncClient = field(
        default_factory=BackendHttpPool.get,
        repr=False,
        compare=False,
    )

    async def connect(self) -> RawMcpConnectionMetadata:
        """Open a client session via the backend proxy and run the MCP initialize handshake."""
        if self.card.auth_state not in {
            McpAuthState.AUTHENTICATED,
            McpAuthState.AUTH_SKIPPED,
        }:
            raise McpAuthError("MCP server is not authenticated.")
        server_id = self.card.server_id or self.card.name
        response = await self.http_client.post(
            f"{self.backend_url.rstrip('/')}/internal/v1/mcp/servers/{server_id}/client-session",
            params={
                Keys.Field.ORG_ID: self.runtime_context.org_id,
                Keys.Field.USER_ID: self.runtime_context.user_id,
            },
            headers=BackendMcpServiceAuth.headers(self.runtime_context),
            timeout=self.timeout_seconds,
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
        """Fetch tool descriptors via ``tools/list`` and build typed ``McpToolDescriptor`` objects."""
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
        """Fetch resource descriptors via ``resources/list``; return empty tuple if unsupported."""
        try:
            result = await self._rpc_result(Values.JsonRpcMethod.LIST_RESOURCES)
        except McpUnsupportedMethodError:
            # resources/list is optional in the MCP spec; graceful degradation.
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
        """Invoke ``tool_name`` on the connected server and return the raw JSON-RPC result."""
        return await self._rpc_result(
            Values.JsonRpcMethod.CALL_TOOL,
            {
                Keys.Field.NAME: tool_name,
                Keys.Field.ARGUMENTS: dict(arguments),
            },
        )

    async def _initialize(self) -> None:
        """Send the MCP initialize + notifications/initialized handshake; idempotent."""
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
        """Build and send a JSON-RPC request; return the ``result`` dict or ``{}``."""
        if self.server_url is None and method != Values.JsonRpcMethod.INITIALIZE:
            # Lazy connect: ``initialize`` itself drives ``connect`` first, so only
            # subsequent methods need this guard.
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
        """Return True when the JSON-RPC error code equals the -32601 method-not-found sentinel.

        Tolerates both int and string-encoded codes as some proxies stringify them.
        """
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
        """POST a JSON-RPC envelope through the backend proxy and unwrap the result."""
        server_id = self.card.server_id or self.card.name
        try:
            response = await self.http_client.post(
                f"{self.backend_url.rstrip('/')}"
                f"{Values.Route.INTERNAL_MCP_RPC.format(server_id=server_id)}",
                json={
                    Keys.Field.ORG_ID: self.runtime_context.org_id,
                    Keys.Field.USER_ID: self.runtime_context.user_id,
                    Keys.JsonRpc.PAYLOAD: payload,
                },
                headers=BackendMcpServiceAuth.headers(self.runtime_context),
                timeout=self.timeout_seconds,
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
        """Build a validated ``McpToolDescriptor`` from raw server data, synthesising display metadata."""
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
        # Synthesise a deterministic display template at descriptor-build time so
        # the presentation layer never needs an LLM call for MCP tools. The server
        # card's ``display_name`` (or ``name`` as fallback) becomes the connector label.
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
        # Register on the per-run lookup so the presentation layer can resolve
        # display templates for ``call_mcp_tool`` dispatcher events. ``register``
        # is a no-op when no registry is bound (replay / eval / tests).
        McpDisplayRegistryContext.register(name, display)
        # PRD-06 D3c — parse the MCP tool ``annotations.readOnlyHint``. Absent
        # ``annotations`` ⇒ ``None`` (fail-closed: treated as acting under
        # ``read`` mode); present ⇒ the boolean hint.
        annotations = tool.get("annotations")
        read_only: bool | None = None
        if isinstance(annotations, dict) and "readOnlyHint" in annotations:
            read_only = bool(annotations["readOnlyHint"])
        return McpToolDescriptor(
            name=name,
            description=self._optional_string(tool.get("description"))
            or f"{name} MCP tool.",
            input_schema=input_schema,
            output_shape=output_shape,
            risk_level=McpRiskLevel.MEDIUM,
            display=display,
            read_only=read_only,
        )

    def _resource_descriptor(self, resource: dict[str, Any]) -> McpResourceDescriptor:
        """Build a validated ``McpResourceDescriptor`` from raw server data."""
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
        """Return ``value`` if it is a dict, else a bare ``{type: object}`` schema."""
        if isinstance(value, dict):
            return value
        return {Keys.Schema.TYPE: Values.SchemaType.OBJECT}

    @staticmethod
    def _required_string(payload: dict[str, Any], key: str, fallback: str) -> str:
        """Return the stripped string value for ``key``, or ``fallback`` when absent or blank."""
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        return fallback

    @staticmethod
    def _optional_string(value: object) -> str | None:
        """Return the stripped string, or ``None`` when blank or non-string."""
        if isinstance(value, str) and value.strip():
            return value.strip()
        return None


class BackendMcpServiceAuth:
    """Service-auth header builder for backend MCP internal-API calls."""

    @staticmethod
    def headers(runtime_context: AgentRuntimeContext) -> dict[str, str]:
        """Return service-token headers when ``ENTERPRISE_SERVICE_TOKEN`` is set; else ``{}``."""
        token = os.environ.get("ENTERPRISE_SERVICE_TOKEN", "").strip()
        if not token:
            return {}
        return {
            SERVICE_TOKEN_HEADER: token,
            ORG_HEADER: runtime_context.org_id,
            USER_HEADER: runtime_context.user_id,
        }
