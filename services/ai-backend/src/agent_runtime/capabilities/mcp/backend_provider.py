"""MCP provider and client that proxy calls through the core backend's internal API."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
import os
from typing import Any, ClassVar

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
    McpClientError,
    McpConnectionError,
    McpLeaseError,
    McpResourceDiscoveryPage,
    McpTimeoutError,
    McpToolDiscoveryPage,
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
            connector_slug=card.connector_slug,
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
    lease: str | None = None
    initialized: bool = False
    request_id: int = 0
    _MAX_DISCOVERY_PAGES: ClassVar[int] = 100
    _MIN_LEASE_LENGTH: ClassVar[int] = 16
    _MAX_LEASE_LENGTH: ClassVar[int] = 512
    _LEASE_FAILURE_CODES: ClassVar[frozenset[str]] = frozenset(
        {
            "ambiguous_transport_failure",
            "auth_required",
            "lease_invalid",
            "lease_stale_pre_dispatch",
            "lease_wrong_owner",
            "pool_saturated",
            "server_unavailable",
        }
    )
    _LEASE_FAILURE_STATUSES: ClassVar[dict[str, int]] = {
        "lease_stale_pre_dispatch": 409,
        "lease_wrong_owner": 403,
        "lease_invalid": 400,
        "pool_saturated": 429,
        "server_unavailable": 503,
        "ambiguous_transport_failure": 503,
        "auth_required": 401,
    }
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
        if self.lease is None:
            await self._acquire_lease()
        try:
            await self._initialize()
        except BaseException:
            await self._discard_lease()
            raise
        return McpConnectionMetadata(
            server_name=self.card.name,
            transport=self.card.transport,
            auth_mode=self.card.auth_mode,
        )

    async def list_tools(self) -> tuple[McpToolDescriptor | dict[str, Any], ...]:
        """Fetch every tools/list page for non-paginated protocol callers."""
        items: list[McpToolDescriptor | dict[str, Any]] = []
        cursor: str | None = None
        seen: set[str] = set()
        for _ in range(self._MAX_DISCOVERY_PAGES):
            page = await self.list_tools_page(cursor=cursor)
            items.extend(page.items)
            if page.next_cursor is None:
                return tuple(items)
            if page.next_cursor in seen:
                raise McpConnectionError("MCP tools/list cursor repeated.")
            seen.add(page.next_cursor)
            cursor = page.next_cursor
        raise McpConnectionError("MCP tools/list exceeded the page limit.")

    async def list_tools_page(
        self,
        *,
        cursor: str | None,
    ) -> McpToolDiscoveryPage:
        """Fetch one tools/list page and preserve its continuation cursor."""
        params = None if cursor is None else {Keys.Field.CURSOR: cursor}
        result = await self._rpc_result(Values.JsonRpcMethod.LIST_TOOLS, params)
        tools = result.get(Keys.Field.TOOLS, ())
        if not isinstance(tools, list):
            tools = []
        return McpToolDiscoveryPage(
            items=tuple(
                self._tool_descriptor(tool) for tool in tools if isinstance(tool, dict)
            ),
            next_cursor=result.get(Keys.Field.NEXT_CURSOR),
        )

    async def list_resources(
        self,
    ) -> tuple[McpResourceDescriptor | dict[str, Any], ...]:
        """Fetch every resources/list page for non-paginated protocol callers."""
        items: list[McpResourceDescriptor | dict[str, Any]] = []
        cursor: str | None = None
        seen: set[str] = set()
        for _ in range(self._MAX_DISCOVERY_PAGES):
            page = await self.list_resources_page(cursor=cursor)
            items.extend(page.items)
            if page.next_cursor is None:
                return tuple(items)
            if page.next_cursor in seen:
                raise McpConnectionError("MCP resources/list cursor repeated.")
            seen.add(page.next_cursor)
            cursor = page.next_cursor
        raise McpConnectionError("MCP resources/list exceeded the page limit.")

    async def list_resources_page(
        self,
        *,
        cursor: str | None,
    ) -> McpResourceDiscoveryPage:
        """Fetch one resources/list page and preserve its continuation cursor."""
        try:
            params = None if cursor is None else {Keys.Field.CURSOR: cursor}
            result = await self._rpc_result(
                Values.JsonRpcMethod.LIST_RESOURCES,
                params,
            )
        except McpUnsupportedMethodError:
            # resources/list is optional in the MCP spec; graceful degradation.
            return McpResourceDiscoveryPage()
        resources = result.get(Keys.Field.RESOURCES, ())
        if not isinstance(resources, list):
            resources = []
        return McpResourceDiscoveryPage(
            items=tuple(
                self._resource_descriptor(resource)
                for resource in resources
                if isinstance(resource, dict)
            ),
            next_cursor=result.get(Keys.Field.NEXT_CURSOR),
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

    async def _rpc_result(
        self, method: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Build and send a JSON-RPC request; return the ``result`` dict or ``{}``."""
        if self.lease is None:
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

    async def _rpc(
        self,
        payload: dict[str, Any],
        *,
        allow_lease_reacquire: bool = True,
    ) -> dict[str, Any]:
        """POST through the backend proxy, replaying only explicit pre-dispatch failures."""
        try:
            return await self._post_rpc(payload)
        except McpLeaseError as exc:
            if not allow_lease_reacquire or not exc.redispatch_safe:
                raise
            replay_required = await self._recover_lease_for_replay(payload)
            if not replay_required:
                return {}
            return await self._rpc(payload, allow_lease_reacquire=False)

    async def _post_rpc(self, payload: dict[str, Any]) -> dict[str, Any]:
        """POST one JSON-RPC envelope through the backend proxy and unwrap it."""
        server_id = self.card.server_id or self.card.name
        if self.lease is None:
            raise McpLeaseError("lease_unknown")
        try:
            response = await self.http_client.post(
                f"{self.backend_url.rstrip('/')}"
                f"{Values.Route.INTERNAL_MCP_RPC.format(server_id=server_id)}",
                json={
                    Keys.Field.ORG_ID: self.runtime_context.org_id,
                    Keys.Field.USER_ID: self.runtime_context.user_id,
                    Keys.Field.LEASE: self.lease,
                    Keys.JsonRpc.PAYLOAD: payload,
                },
                headers=BackendMcpServiceAuth.headers(self.runtime_context),
                timeout=self.timeout_seconds,
            )
        except httpx.TimeoutException as exc:
            raise McpTimeoutError("MCP JSON-RPC request timed out.") from exc
        except httpx.HTTPError as exc:
            raise McpConnectionError("MCP JSON-RPC request failed.") from exc
        lease_error = self._lease_error_from_response(response)
        if lease_error is not None:
            if isinstance(lease_error, McpLeaseError):
                raise McpLeaseError(
                    lease_error.code,
                    redispatch_safe=lease_error.redispatch_safe,
                    acquisition_safe=True,
                )
            raise lease_error
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

    async def _acquire_lease(self) -> None:
        """Acquire one backend-owned opaque lease without exposing transport details."""
        server_id = self.card.server_id or self.card.name
        try:
            response = await self.http_client.post(
                f"{self.backend_url.rstrip('/')}/internal/v1/mcp/servers/{server_id}/client-session",
                params={
                    Keys.Field.ORG_ID: self.runtime_context.org_id,
                    Keys.Field.USER_ID: self.runtime_context.user_id,
                },
                headers=BackendMcpServiceAuth.headers(self.runtime_context),
                timeout=self.timeout_seconds,
            )
        except httpx.TimeoutException as exc:
            raise McpTimeoutError("MCP client-session request timed out.") from exc
        except httpx.HTTPError as exc:
            raise McpConnectionError("MCP client-session request failed.") from exc
        lease_error = self._lease_error_from_response(response)
        if lease_error is not None:
            raise lease_error
        if response.status_code in {401, 403}:
            raise McpAuthError("MCP server is not authenticated.")
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise McpConnectionError("MCP client-session request failed.") from exc
        payload = response.json()
        lease = payload.get(Keys.Field.LEASE) if isinstance(payload, dict) else None
        if (
            not isinstance(lease, str)
            or lease != lease.strip()
            or not self._MIN_LEASE_LENGTH <= len(lease) <= self._MAX_LEASE_LENGTH
        ):
            raise McpConnectionError("MCP client-session returned an invalid lease.")
        self.lease = lease
        self.initialized = False

    async def aclose(self, *, cancel: bool = False) -> None:
        """Release the opaque lease; never expose backend transport or credentials."""
        lease = self.lease
        self.lease = None
        self.initialized = False
        if lease is None:
            return
        server_id = self.card.server_id or self.card.name
        try:
            response = await self.http_client.post(
                f"{self.backend_url.rstrip('/')}"
                f"{Values.Route.INTERNAL_MCP_CLIENT_SESSION_RELEASE.format(server_id=server_id)}",
                json={
                    Keys.Field.ORG_ID: self.runtime_context.org_id,
                    Keys.Field.USER_ID: self.runtime_context.user_id,
                    Keys.Field.LEASE: lease,
                    Keys.Field.CANCEL: cancel,
                },
                headers=BackendMcpServiceAuth.headers(self.runtime_context),
                timeout=self.timeout_seconds,
            )
        except httpx.TimeoutException as exc:
            raise McpTimeoutError("MCP client-session release timed out.") from exc
        except httpx.HTTPError as exc:
            raise McpConnectionError("MCP client-session release failed.") from exc
        lease_error = self._lease_error_from_response(response)
        if lease_error is not None:
            raise lease_error
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise McpConnectionError("MCP client-session release failed.") from exc

    async def _recover_lease_for_replay(self, payload: Mapping[str, Any]) -> bool:
        """Transition to a new lease and return whether the original RPC needs replay."""
        await self._discard_lease()
        await self._acquire_lease()
        method = payload.get(Keys.JsonRpc.METHOD)
        if method == Values.JsonRpcMethod.INITIALIZED:
            # The replacement handshake includes the notification. Replaying
            # the original notification would be a second, needless dispatch.
            await self._initialize(allow_lease_reacquire=False)
            return False
        # Replaying ``initialize`` is itself the handshake. Every other RPC,
        # requires a fully initialized replacement session before its one
        # permitted replay.
        if method != Values.JsonRpcMethod.INITIALIZE:
            await self._initialize(allow_lease_reacquire=False)
        return True

    async def _discard_lease(self) -> None:
        """Best-effort cancellation for a lease already proven safe to replace."""
        try:
            await self.aclose(cancel=True)
        except McpClientError:
            # A stale/unknown release cannot make a backend-confirmed
            # pre-dispatch replay unsafe. The local capability is cleared by
            # ``aclose`` before it performs I/O.
            pass

    async def _initialize(self, *, allow_lease_reacquire: bool = True) -> None:
        """Send the MCP initialize handshake over the currently held lease."""
        if self.initialized:
            return
        await self._rpc_result_with_retry_policy(
            Values.JsonRpcMethod.INITIALIZE,
            {
                Keys.JsonRpc.PROTOCOL_VERSION: Values.McpClientInfo.PROTOCOL_VERSION,
                Keys.JsonRpc.CAPABILITIES: {},
                Keys.JsonRpc.CLIENT_INFO: {
                    Keys.Field.NAME: Values.McpClientInfo.NAME,
                    Keys.Field.VERSION: Values.McpClientInfo.VERSION,
                },
            },
            allow_lease_reacquire=allow_lease_reacquire,
        )
        await self._rpc(
            {
                Keys.JsonRpc.JSONRPC: Values.JsonRpc.VERSION,
                Keys.JsonRpc.METHOD: Values.JsonRpcMethod.INITIALIZED,
            },
            allow_lease_reacquire=allow_lease_reacquire,
        )
        self.initialized = True

    async def _rpc_result_with_retry_policy(
        self,
        method: str,
        params: dict[str, Any],
        *,
        allow_lease_reacquire: bool,
    ) -> dict[str, Any]:
        self.request_id += 1
        payload = {
            Keys.JsonRpc.JSONRPC: Values.JsonRpc.VERSION,
            Keys.JsonRpc.ID: self.request_id,
            Keys.JsonRpc.METHOD: method,
            Keys.JsonRpc.PARAMS: params,
        }
        response = await self._rpc(
            payload,
            allow_lease_reacquire=allow_lease_reacquire,
        )
        error = response.get(Keys.JsonRpc.ERROR)
        if isinstance(error, dict):
            if self._is_method_not_found(error):
                raise McpUnsupportedMethodError("MCP JSON-RPC method is not supported.")
            raise McpConnectionError("MCP JSON-RPC request failed.")
        result = response.get(Keys.JsonRpc.RESULT)
        return result if isinstance(result, dict) else {}

    @staticmethod
    def _lease_error_from_response(response: httpx.Response) -> McpClientError | None:
        """Strictly parse the backend's bounded typed lease-failure envelope."""
        try:
            body = response.json()
        except (TypeError, ValueError):
            return None
        detail = body.get("detail") if isinstance(body, dict) else None
        if not isinstance(detail, dict):
            return None
        code = detail.get("code")
        if (
            not isinstance(code, str)
            or code not in BackendMcpClient._LEASE_FAILURE_CODES
        ):
            return None
        if response.status_code != BackendMcpClient._LEASE_FAILURE_STATUSES[code]:
            return McpLeaseError("lease_protocol_error")
        if code == "auth_required":
            return McpAuthError("MCP server is not authenticated.")
        redispatch_safe = (
            response.status_code == 409
            and code == "lease_stale_pre_dispatch"
            and detail.get("redispatch_safe") is True
        )
        return McpLeaseError(code, redispatch_safe=redispatch_safe)

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
        from agent_runtime.capabilities.mcp.annotations import (  # noqa: PLC0415
            McpToolAnnotations,
            McpToolAnnotationsRegistry,
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
        # PRD-C1 — capture the raw MCP ``annotations`` as untrusted classifier
        # hints on the per-run annotations registry (composite key: this
        # server's slug + tool). Registry-only (never a descriptor field) so
        # every existing payload stays byte-identical. Skipped unless
        # ``annotations`` is a mapping; garbage is tolerated (no entry). The
        # server is registered with ``self.card.name`` (the read side passes the
        # model-supplied ``server_name``); both normalize through ``server_slug``.
        raw_annotations = tool.get("annotations")
        if isinstance(raw_annotations, Mapping):
            McpToolAnnotationsRegistry.register(
                self.card.name,
                name,
                McpToolAnnotations.from_wire(raw_annotations),
            )
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
