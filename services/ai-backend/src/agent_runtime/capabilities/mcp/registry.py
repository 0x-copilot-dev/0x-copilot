"""Provider-backed registry for compact MCP server cards."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import logging
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
from agent_runtime.capabilities.mcp.constants import Keys, Messages
from agent_runtime.capabilities.mcp.permissions import McpPermissionPolicy

_LOGGER = logging.getLogger(__name__)

RawMcpServerCard = McpServerCard | Mapping[str, object]


class McpServerCardRejection:
    """Naming and reporting for a server card that failed validation.

    Shared by every site that validates a card (the registry itself and each
    provider that pre-validates), so a rejection reads the same wherever it
    happens and no site has to reinvent how to name an unparseable card.

    It exists because the card CANNOT be parsed, so nothing typed is available
    to identify it — and a log line that cannot say which connector broke is
    precisely why this class of failure took a full reproduction to diagnose.
    """

    _UNIDENTIFIED = "<unidentified card>"

    @classmethod
    def identify(cls, raw_card: object) -> str:
        """Best-effort identity for an unvalidated card."""
        if isinstance(raw_card, McpServerCard):
            return raw_card.server_id or raw_card.name
        if isinstance(raw_card, Mapping):
            for key in (Keys.Field.SERVER_ID, Keys.Field.NAME):
                value = raw_card.get(key)
                if isinstance(value, str) and value.strip():
                    return value
        return cls._UNIDENTIFIED

    @classmethod
    def describe(cls, exc: ValidationError) -> str:
        """Render which fields rejected the card, without their values.

        Deliberately omits pydantic's ``input``: a card carries connector
        metadata, and a log line is not the place to widen what that exposes.
        The field plus the reason is what makes this actionable.
        """
        return "; ".join(
            f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}"
            for error in exc.errors()
        )

    @classmethod
    def report(cls, raw_card: object, exc: ValidationError, source: str) -> None:
        """Log a skipped card at ERROR, naming the card, source and fields."""
        _LOGGER.error(
            "%s %s (from %s): %s",
            Messages.Registry.INVALID_SERVER_CARD,
            cls.identify(raw_card),
            source,
            cls.describe(exc),
        )


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
        """Validate that every provider implements the required MCP adapter interface."""
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
        """Fetch and validate cards from every registered provider.

        A card that fails validation is SKIPPED, not fatal. Agent construction
        lists MCP servers unconditionally (``acreate_agent_runtime`` gathers
        five registries before the model is ever contacted), so raising here
        meant one malformed row left the agent unbuildable for every run in
        every conversation — including conversations that use no MCP tool at
        all. A runtime that is happy to start with zero MCP servers can start
        with one fewer; refusing to is a blast radius, not a safety property.

        The skip is loud on purpose: it is logged at ERROR with the card's
        identity and the validating field, and the connector is simply absent
        from the model's tool set. Silence here would read as "you have no
        Gmail connector" rather than "your Gmail connector is broken".

        A provider that fails WHOLESALE still raises. That is a dependency
        failure (the backend is unreachable), not one bad row, and quietly
        dropping every connector mid-conversation would be its own silent
        wrong answer.
        """
        entries: list[RegisteredMcpServer] = []
        for provider in self.providers:
            try:
                raw_cards = await provider.list_server_cards()
            except AgentRuntimeError:
                raise
            except Exception as exc:
                # `raise ... from exc` alone loses the cause: the worker logs
                # `error_class` plus the OUTER traceback, so the reason a
                # provider failed never reached the log and the failure was
                # only diagnosable by re-issuing the call by hand.
                _LOGGER.error(
                    "MCP provider %s failed to list server cards: %s: %s",
                    type(provider).__name__,
                    type(exc).__name__,
                    exc,
                )
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
                    McpServerCardRejection.report(
                        raw_card, exc, type(provider).__name__
                    )
                    continue
                entries.append(RegisteredMcpServer(provider=provider, card=card))
        return tuple(entries)
