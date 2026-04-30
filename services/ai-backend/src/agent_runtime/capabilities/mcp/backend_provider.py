"""Backend-backed MCP provider for production registry integration."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

import httpx

from agent_runtime.execution.contracts import AgentRuntimeContext
from agent_runtime.capabilities.mcp.cards import (
    McpAuthState,
    McpConnectionMetadata,
    McpResourceDescriptor,
    McpServerCard,
    McpToolDescriptor,
)
from agent_runtime.capabilities.mcp.client import McpAuthError, McpClient, RawMcpConnectionMetadata
from agent_runtime.capabilities.mcp.middleware.auth_mcp import McpAuthSession


@dataclass(frozen=True)
class BackendMcpProvider:
    """MCP provider that reads safe card metadata from the core backend."""

    backend_url: str
    runtime_context: AgentRuntimeContext
    auth_redirect_uri: str
    timeout_seconds: float = 10

    def list_server_cards(self) -> tuple[McpServerCard, ...]:
        response = httpx.get(
            f"{self.backend_url.rstrip('/')}/internal/v1/mcp/cards",
            params={"org_id": self.runtime_context.org_id, "user_id": self.runtime_context.user_id},
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        payload = response.json()
        return tuple(McpServerCard.model_validate(card) for card in payload.get("servers", ()))

    def create_client(self, card: McpServerCard) -> McpClient:
        return BackendMcpClient(
            backend_url=self.backend_url,
            runtime_context=self.runtime_context,
            card=card,
            timeout_seconds=self.timeout_seconds,
        )

    def create_auth_session(
        self,
        *,
        server_id: str,
        runtime_context: AgentRuntimeContext,
    ) -> McpAuthSession:
        response = httpx.post(
            f"{self.backend_url.rstrip('/')}/internal/v1/mcp/servers/{server_id}/auth/start",
            json={
                "org_id": runtime_context.org_id,
                "user_id": runtime_context.user_id,
                "redirect_uri": self.auth_redirect_uri,
            },
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        payload = response.json()
        card = self._card_by_server_id_or_name(server_id)
        return McpAuthSession(
            server_id=str(payload["server_id"]),
            server_name=card.name,
            display_name=card.display_name or card.name,
            auth_url=str(payload["auth_url"]),
            expires_at=datetime.fromisoformat(str(payload["expires_at"])),
        )

    def _card_by_server_id_or_name(self, value: str) -> McpServerCard:
        for card in self.list_server_cards():
            if card.server_id == value or card.name == value:
                return card
        return McpServerCard(
            server_id=value,
            name=value,
            display_name=value,
            short_description="MCP server requires authentication.",
            transport="http",
            auth_mode="oauth2",
            auth_state="unauthenticated",
            health="healthy",
            load_cost=1,
        )


@dataclass
class BackendMcpClient:
    """MCP client that resolves credentials through backend-owned state."""

    backend_url: str
    runtime_context: AgentRuntimeContext
    card: McpServerCard
    timeout_seconds: float = 10
    server_url: str | None = None

    async def connect(self) -> RawMcpConnectionMetadata:
        if self.card.auth_state not in {McpAuthState.AUTHENTICATED, McpAuthState.AUTH_SKIPPED}:
            raise McpAuthError("MCP server is not authenticated.")
        server_id = self.card.server_id or self.card.name
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.post(
                f"{self.backend_url.rstrip('/')}/internal/v1/mcp/servers/{server_id}/client-session",
                params={"org_id": self.runtime_context.org_id, "user_id": self.runtime_context.user_id},
            )
        response.raise_for_status()
        payload = response.json()
        if payload.get("auth_state") != McpAuthState.AUTHENTICATED.value:
            raise McpAuthError("MCP server is not authenticated.")
        self.server_url = str(payload["url"]).rstrip("/")
        return McpConnectionMetadata(
            server_name=self.card.name,
            transport=self.card.transport,
            auth_mode=self.card.auth_mode,
        )

    async def list_tools(self) -> tuple[McpToolDescriptor | dict[str, Any], ...]:
        return await self._get_descriptor_list("/tools")

    async def list_resources(self) -> tuple[McpResourceDescriptor | dict[str, Any], ...]:
        return await self._get_descriptor_list("/resources")

    async def _get_descriptor_list(self, path: str) -> tuple[dict[str, Any], ...]:
        if self.server_url is None:
            await self.connect()
        assert self.server_url is not None
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.get(f"{self.server_url}{path}")
        response.raise_for_status()
        payload = response.json()
        if isinstance(payload, dict):
            values = payload.get(path.strip("/"), ())
        else:
            values = payload
        if not isinstance(values, list):
            return ()
        return tuple(item for item in values if isinstance(item, dict))
