"""Provider-backed registry for compact MCP server cards."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

from pydantic import ValidationError

from agent_runtime.execution.contracts import AgentRuntimeContext, RuntimeErrorCode
from agent_runtime.execution.errors import AgentRuntimeError
from agent_runtime.validation import ValueNormalizer
from agent_runtime.capabilities.mcp.cards import (
    McpLoadError,
    McpLoadErrorCode,
    McpServerCard,
    McpServerHealth,
)
from agent_runtime.capabilities.mcp.client import McpClientFactory
from agent_runtime.capabilities.mcp.constants import Messages
from agent_runtime.capabilities.mcp.permissions import McpPermissionPolicy

RawMcpServerCard = McpServerCard | Mapping[str, object]


class McpServerProvider(McpClientFactory, Protocol):
    """Adapter boundary for MCP server metadata and client creation."""

    async def list_server_cards(self) -> Sequence[RawMcpServerCard]:
        """Return compact server cards registered by this provider."""


@dataclass(frozen=True)
class RegisteredMcpServer:
    """A validated server card paired with its client factory."""

    provider: McpServerProvider
    card: McpServerCard


@dataclass(frozen=True)
class DynamicMcpRegistry:
    """Lists permission-filtered MCP cards and resolves selected servers."""

    providers: Sequence[McpServerProvider]

    def __post_init__(self) -> None:
        for provider in self.providers:
            if not callable(getattr(provider, "list_server_cards", None)):
                raise AgentRuntimeError(
                    RuntimeErrorCode.DEPENDENCY_ERROR,
                    Messages.Registry.MISSING_LIST_SERVER_CARDS,
                    retryable=False,
                )
            if not callable(getattr(provider, "create_client", None)):
                raise AgentRuntimeError(
                    RuntimeErrorCode.DEPENDENCY_ERROR,
                    Messages.Registry.MISSING_CREATE_CLIENT,
                    retryable=False,
                )

    async def list_server_cards(
        self, context: AgentRuntimeContext
    ) -> tuple[McpServerCard, ...]:
        """Return compact MCP cards visible to the request context."""

        runtime_context = ValueNormalizer.coerce_runtime_context(context)
        entries = await self._collect_entries()
        duplicate_name = ValueNormalizer.first_duplicate_name(
            entry.card.name for entry in entries
        )
        if duplicate_name is not None:
            raise AgentRuntimeError(
                RuntimeErrorCode.CONFIGURATION_ERROR,
                Messages.Registry.DUPLICATE_SERVER_NAME,
                retryable=False,
                correlation_id=runtime_context.trace_id,
            )

        cards = (
            entry.card
            for entry in entries
            if McpPermissionPolicy.is_server_card_visible(runtime_context, entry.card)
        )
        return tuple(sorted(cards, key=lambda card: card.name))

    async def list_available_servers(
        self, context: object
    ) -> tuple[McpServerCard, ...]:
        """Runtime port adapter returning model-visible compact server cards."""

        return await self.list_server_cards(
            ValueNormalizer.coerce_runtime_context(context)
        )

    async def resolve_server(self, name: str) -> RegisteredMcpServer | McpLoadError:
        """Resolve a selected stable server name to exactly one provider entry."""

        entries = await self._collect_entries()
        matching_entries = [entry for entry in entries if entry.card.name == name]
        if not matching_entries:
            return McpLoadError(
                code=McpLoadErrorCode.UNKNOWN_SERVER,
                safe_message=Messages.Registry.REQUESTED_SERVER_UNKNOWN,
                server_name=name,
            )
        if len(matching_entries) > 1:
            return McpLoadError(
                code=McpLoadErrorCode.DUPLICATE_SERVER_NAME,
                safe_message=Messages.Registry.REQUESTED_SERVER_DUPLICATE,
                server_name=name,
            )

        entry = matching_entries[0]
        if not entry.card.enabled or entry.card.health == McpServerHealth.DISABLED:
            return McpLoadError(
                code=McpLoadErrorCode.SERVER_DISABLED,
                safe_message=Messages.Registry.REQUESTED_SERVER_DISABLED,
                server_name=name,
            )
        if entry.card.health == McpServerHealth.UNAVAILABLE:
            return McpLoadError(
                code=McpLoadErrorCode.SERVER_UNHEALTHY,
                safe_message=Messages.Registry.REQUESTED_SERVER_UNAVAILABLE,
                retryable=True,
                server_name=name,
            )
        return entry

    async def _collect_entries(self) -> tuple[RegisteredMcpServer, ...]:
        entries: list[RegisteredMcpServer] = []
        for provider in self.providers:
            try:
                raw_cards = await provider.list_server_cards()
            except AgentRuntimeError:
                raise
            except Exception as exc:
                raise AgentRuntimeError(
                    RuntimeErrorCode.CAPABILITY_LOAD_ERROR,
                    Messages.Registry.CARDS_LOAD_FAILED,
                    retryable=True,
                ) from exc

            for raw_card in raw_cards:
                try:
                    card = (
                        raw_card
                        if isinstance(raw_card, McpServerCard)
                        else McpServerCard.model_validate(raw_card)
                    )
                except ValidationError as exc:
                    raise AgentRuntimeError(
                        RuntimeErrorCode.CONFIGURATION_ERROR,
                        Messages.Registry.INVALID_SERVER_CARD,
                        retryable=False,
                    ) from exc
                entries.append(RegisteredMcpServer(provider=provider, card=card))
        return tuple(entries)
