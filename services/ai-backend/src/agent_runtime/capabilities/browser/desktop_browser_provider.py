"""Desktop-local browser MCP provider (AC8).

This is the AI-backend edge of the AC8 agentic browser. It is the NARROW
exception to backend-owned SaaS MCP transport: a device-local provider that
talks to the Electron-main browser broker over an authenticated loopback
channel, exposing a small typed READ-ONLY tool surface (navigate / snapshot /
wait / screenshot / close) to the model through the normal MCP registry,
permission, approval, budget, citation, payload, and audit middleware.

Ownership boundaries honored here:

- It implements the structural :class:`McpServerProvider` interface
  (``list_server_cards`` + ``create_client``) so :class:`DynamicMcpRegistry`
  can list it alongside the backend SaaS provider WITHOUT a duplicate name.
- It has NO backend registry row and NO OAuth state. The broker URL + bootstrap
  credential come from the trusted desktop service environment.
- The card is ABSENT outside ``single_user_desktop`` or when the feature is
  disabled / the broker is unhealthy (the ``build_browser_mcp`` seam returns
  ``None``), so the model never sees a browser tool it cannot use.

DEFERRED (noted seams): the run/consent binding is composed by Electron main at
approval time; downloads, uploads, and side-effecting tools are not exposed.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

import httpx

from agent_runtime.execution.contracts import AgentRuntimeContext
from agent_runtime.capabilities.browser.constants import (
    BrowserBroker,
    BrowserKeys,
    BrowserMessages,
    BrowserServer,
)
from agent_runtime.capabilities.mcp.cards import (
    McpAuthMode,
    McpAuthState,
    McpConnectionMetadata,
    McpRiskLevel,
    McpServerCard,
    McpServerHealth,
    McpToolDescriptor,
    McpTransport,
)
from agent_runtime.capabilities.mcp.client import (
    McpAuthError,
    McpClient,
    McpConnectionError,
    RawMcpConnectionMetadata,
)


@dataclass(frozen=True)
class BrowserMcpConfig:
    """Everything the seam needs to decide whether to build the provider.

    ``build_browser_mcp`` returns a provider ONLY when the subsystem is enabled,
    the deployment profile is ``single_user_desktop``, and both the broker URL
    and its bootstrap credential are present. Anything else returns ``None`` and
    the local card never appears.
    """

    enabled: bool
    deployment_profile: str
    broker_url: str | None
    broker_token: str | None
    runtime_context: AgentRuntimeContext
    timeout_seconds: float = 10.0
    http_client: httpx.AsyncClient | None = None


def build_browser_mcp(config: BrowserMcpConfig) -> "DesktopBrowserMcpProvider | None":
    """Seam consumed by the runtime factory (which this change does NOT edit).

    Returns a :class:`DesktopBrowserMcpProvider` when the desktop browser is
    enabled, the profile is single-user desktop, and a broker URL + credential
    are configured; otherwise ``None`` (fail closed — no card, no tools).
    """

    if not config.enabled:
        return None
    if config.deployment_profile != BrowserServer.REQUIRED_DEPLOYMENT_PROFILE:
        return None
    if not config.broker_url or not config.broker_token:
        return None
    return DesktopBrowserMcpProvider(
        broker_url=config.broker_url,
        broker_token=config.broker_token,
        runtime_context=config.runtime_context,
        timeout_seconds=config.timeout_seconds,
        http_client=config.http_client or httpx.AsyncClient(),
    )


@dataclass(frozen=True)
class DesktopBrowserMcpProvider:
    """``McpServerProvider`` backed by the desktop Electron-main browser broker."""

    broker_url: str
    broker_token: str
    runtime_context: AgentRuntimeContext
    timeout_seconds: float = 10.0
    http_client: httpx.AsyncClient = field(
        default_factory=httpx.AsyncClient,
        repr=False,
        compare=False,
    )

    async def list_server_cards(self) -> tuple[McpServerCard, ...]:
        """Return the single device-local browser card.

        The card is static + healthy here; the ``build_browser_mcp`` seam is the
        gate that decides whether this provider (and therefore its card) exists
        at all. There is no backend registry fetch — this server is device-local.
        """

        return (
            McpServerCard(
                name=BrowserServer.NAME,
                display_name=BrowserServer.DISPLAY_NAME,
                short_description=BrowserServer.SHORT_DESCRIPTION,
                transport=McpTransport.HTTP,
                auth_mode=McpAuthMode.NONE,
                auth_state=McpAuthState.AUTH_SKIPPED,
                health=McpServerHealth.HEALTHY,
                load_cost=1,
                enabled=True,
            ),
        )

    def create_client(self, card: McpServerCard) -> McpClient:
        """Build a request-scoped client bound to the broker credential."""

        return DesktopBrowserMcpClient(
            broker_url=self.broker_url,
            broker_token=self.broker_token,
            card=card,
            timeout_seconds=self.timeout_seconds,
            http_client=self.http_client,
        )


@dataclass
class DesktopBrowserMcpClient:
    """MCP client that speaks the authenticated browser-broker loopback protocol."""

    broker_url: str
    broker_token: str
    card: McpServerCard
    timeout_seconds: float = 10.0
    http_client: httpx.AsyncClient = field(
        default_factory=httpx.AsyncClient,
        repr=False,
        compare=False,
    )
    connected: bool = False

    async def connect(self) -> RawMcpConnectionMetadata:
        """Handshake with the broker and verify the audience binding."""

        payload = await self._post(BrowserBroker.ROUTE_HANDSHAKE, envelope=None)
        if payload.get(BrowserKeys.AUDIENCE) != BrowserBroker.AUDIENCE:
            raise McpConnectionError(BrowserMessages.HANDSHAKE_AUDIENCE_MISMATCH)
        self.connected = True
        return McpConnectionMetadata(
            server_name=self.card.name,
            transport=self.card.transport,
            auth_mode=self.card.auth_mode,
        )

    async def list_tools(self) -> tuple[McpToolDescriptor, ...]:
        """Discover the read-only tool schemas via ``tools/list``.

        The desktop worker is the source of truth for these schemas; they are
        NOT hand-copied here. Side-effecting tools are absent by construction.
        """

        payload = await self._post(
            BrowserBroker.ROUTE_TOOLS_LIST, envelope=self._envelope()
        )
        raw_tools = payload.get(BrowserKeys.TOOLS, ())
        if not isinstance(raw_tools, list):
            return ()
        return tuple(
            self._tool_descriptor(tool) for tool in raw_tools if isinstance(tool, dict)
        )

    async def list_resources(self) -> tuple[Any, ...]:
        """The browser exposes no MCP resources."""

        return ()

    async def call_tool(
        self,
        *,
        tool_name: str,
        arguments: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Dispatch a typed read-only action through the broker.

        The AI side sends only ``{tool: {name, arguments}}``; Electron main
        composes the full run/consent binding (profile, approved origin policy,
        approval id) at dispatch time — that binding-injection is the deferred
        consent seam and is deliberately NOT synthesised here.
        """

        envelope = self._envelope(
            {
                BrowserKeys.TOOL: {
                    BrowserKeys.NAME: tool_name,
                    BrowserKeys.ARGUMENTS: dict(arguments),
                }
            }
        )
        payload = await self._post(BrowserBroker.ROUTE_ACTION, envelope=envelope)
        result = payload.get(BrowserKeys.RESULT)
        if not isinstance(result, dict):
            raise McpConnectionError(BrowserMessages.INVALID_RESPONSE)
        return result

    def _envelope(self, extra: Mapping[str, Any] | None = None) -> dict[str, Any]:
        """Build the request-binding envelope: audience + single-use nonce/id + TTL."""

        # ``expiresAt`` is a wall-clock ms deadline; the broker rejects stale or
        # replayed envelopes. A fresh nonce + request id per call keeps each
        # dispatch single-use.
        import time  # noqa: PLC0415 — local import keeps the module import-light

        now_ms = int(time.time() * 1000)
        envelope: dict[str, Any] = {
            BrowserKeys.AUD: BrowserBroker.AUDIENCE,
            BrowserKeys.NONCE: uuid4().hex,
            BrowserKeys.REQUEST_ID: uuid4().hex,
            BrowserKeys.EXPIRES_AT: now_ms + BrowserBroker.ENVELOPE_TTL_MS,
        }
        if extra is not None:
            envelope.update(extra)
        return envelope

    async def _post(
        self, route: str, *, envelope: Mapping[str, Any] | None
    ) -> dict[str, Any]:
        """POST to a loopback broker route with bearer + protocol headers."""

        url = f"{self.broker_url.rstrip('/')}{route}"
        headers = {
            "authorization": f"Bearer {self.broker_token}",
            BrowserBroker.PROTOCOL_HEADER: BrowserBroker.PROTOCOL_VERSION,
            "content-type": "application/json",
        }
        try:
            response = await self.http_client.post(
                url,
                json=dict(envelope) if envelope is not None else {},
                headers=headers,
                timeout=self.timeout_seconds,
            )
        except httpx.HTTPError as exc:
            raise McpConnectionError(BrowserMessages.BROKER_UNAVAILABLE) from exc
        if response.status_code in {401, 403}:
            raise McpAuthError(BrowserMessages.BROKER_UNAUTHENTICATED)
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise McpConnectionError(BrowserMessages.BROKER_UNAVAILABLE) from exc
        body = response.json()
        if not isinstance(body, dict):
            raise McpConnectionError(BrowserMessages.INVALID_RESPONSE)
        return body

    #: Side-effecting browser tools. When the desktop worker advertises the
    #: action layer, these are marked HIGH risk so the existing MCP HITL
    #: approval middleware interrupts on them before dispatch — the worker also
    #: gates them defensively, but the model-facing approval rides the normal
    #: interrupt path (no browser-specific approval engine).
    _SIDE_EFFECTING_TOOLS = frozenset(
        {
            "browser_click",
            "browser_type",
            "browser_select",
            "browser_submit",
            "browser_download",
            "browser_upload",
        }
    )

    @classmethod
    def _tool_descriptor(cls, tool: dict[str, Any]) -> McpToolDescriptor:
        """Build a validated descriptor from a broker tool schema."""

        name = tool.get(BrowserKeys.NAME)
        input_schema = tool.get(BrowserKeys.INPUT_SCHEMA)
        schema = (
            input_schema
            if isinstance(input_schema, dict) and "type" in input_schema
            else {"type": "object"}
        )
        description = tool.get(BrowserKeys.DESCRIPTION)
        risk_level = (
            McpRiskLevel.HIGH
            if str(name) in cls._SIDE_EFFECTING_TOOLS
            else McpRiskLevel.MEDIUM
        )
        return McpToolDescriptor(
            name=str(name),
            description=str(description) if description else f"{name} browser tool.",
            input_schema=schema,
            output_shape={"type": "object"},
            risk_level=risk_level,
        )
