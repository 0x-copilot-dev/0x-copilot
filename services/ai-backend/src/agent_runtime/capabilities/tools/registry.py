"""Provider-backed registry for compact dynamic tool cards."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

from pydantic import ValidationError

from agent_runtime.execution.contracts import AgentRuntimeContext, RuntimeErrorCode
from agent_runtime.execution.errors import AgentRuntimeError
from agent_runtime.validation import ValueNormalizer
from agent_runtime.capabilities.tools.cards import (
    LoadedToolSpec,
    ToolCard,
    ToolDisplayTemplate,
    ToolLoadError,
    ToolLoadErrorCode,
)
from agent_runtime.capabilities.tools.constants import Keys, Messages
from agent_runtime.capabilities.tools.permissions import ToolPermissionChecker

RawToolCard = ToolCard | Mapping[str, object]
RawLoadedToolSpec = LoadedToolSpec | Mapping[str, object]


class ToolSpecProvider(Protocol):
    """Connector adapter boundary for dynamic tool metadata and specs."""

    def list_tool_cards(self) -> Sequence[RawToolCard]:
        """Return compact cards registered by this provider."""

    def load_tool_spec(self, name: str) -> RawLoadedToolSpec:
        """Return the full spec for a registered tool name."""


@dataclass(frozen=True)
class RegisteredTool:
    """A validated card paired with the provider that owns the full spec."""

    provider: ToolSpecProvider
    card: ToolCard


@dataclass(frozen=True)
class DynamicToolRegistry:
    """Lists permission-filtered tool cards and resolves selected tools."""

    providers: Sequence[ToolSpecProvider]

    def __post_init__(self) -> None:
        for provider in self.providers:
            if not callable(getattr(provider, Keys.Methods.LIST_TOOL_CARDS, None)):
                raise AgentRuntimeError(
                    RuntimeErrorCode.DEPENDENCY_ERROR,
                    Messages.Errors.PROVIDER_MISSING_LIST_TOOL_CARDS,
                    retryable=False,
                )
            if not callable(getattr(provider, Keys.Methods.LOAD_TOOL_SPEC, None)):
                raise AgentRuntimeError(
                    RuntimeErrorCode.DEPENDENCY_ERROR,
                    Messages.Errors.PROVIDER_MISSING_LOAD_TOOL_SPEC,
                    retryable=False,
                )

    def list_tool_cards(self, context: AgentRuntimeContext) -> tuple[ToolCard, ...]:
        """Return compact cards visible to the request context."""

        runtime_context = ValueNormalizer.coerce_runtime_context(context)
        entries = self._collect_entries()
        duplicate_name = ValueNormalizer.first_duplicate_name(
            entry.card.name for entry in entries
        )
        if duplicate_name is not None:
            raise AgentRuntimeError(
                RuntimeErrorCode.CONFIGURATION_ERROR,
                Messages.Errors.DUPLICATE_TOOL_REGISTRATION,
                retryable=False,
                correlation_id=runtime_context.trace_id,
            )

        cards = (
            entry.card
            for entry in entries
            if ToolPermissionChecker.is_card_authorized(runtime_context, entry.card)
        )
        return tuple(sorted(cards, key=lambda card: card.name))

    def list_available_tools(self, context: object) -> tuple[ToolCard, ...]:
        """Runtime port adapter returning model-visible compact cards."""

        return self.list_tool_cards(ValueNormalizer.coerce_runtime_context(context))

    def display_for(self, tool_name: str) -> ToolDisplayTemplate | None:
        """Return the registered display template for ``tool_name``, or ``None``.

        Walks the providers' compact cards. Returns ``None`` when no card
        matches (unknown tool) or when the matching card was registered
        without a ``display`` template (the field is currently optional —
        Phase 5 of the polish-removal refactor makes it required).
        """

        for entry in self._collect_entries():
            if entry.card.name == tool_name:
                return entry.card.display
        return None

    def resolve_tool(self, name: str) -> RegisteredTool | ToolLoadError:
        """Resolve a selected stable tool name to exactly one provider entry."""

        entries = self._collect_entries()
        matching_entries = [entry for entry in entries if entry.card.name == name]
        if not matching_entries:
            return ToolLoadError(
                code=ToolLoadErrorCode.UNKNOWN_TOOL,
                safe_message=Messages.Errors.REQUESTED_TOOL_UNAVAILABLE,
                tool_name=name,
            )
        if len(matching_entries) > 1:
            return ToolLoadError(
                code=ToolLoadErrorCode.DUPLICATE_TOOL_NAME,
                safe_message=Messages.Errors.REQUESTED_TOOL_DUPLICATE,
                tool_name=name,
            )

        entry = matching_entries[0]
        if not entry.card.enabled:
            return ToolLoadError(
                code=ToolLoadErrorCode.TOOL_DISABLED,
                safe_message=Messages.Errors.REQUESTED_TOOL_DISABLED,
                tool_name=name,
            )
        return entry

    def _collect_entries(self) -> tuple[RegisteredTool, ...]:
        entries: list[RegisteredTool] = []
        for provider in self.providers:
            try:
                raw_cards = provider.list_tool_cards()
            except AgentRuntimeError:
                raise
            except Exception as exc:
                raise AgentRuntimeError(
                    RuntimeErrorCode.CAPABILITY_LOAD_ERROR,
                    Messages.Errors.TOOL_CARDS_LOAD_FAILED,
                    retryable=True,
                ) from exc

            for raw_card in raw_cards:
                try:
                    card = (
                        raw_card
                        if isinstance(raw_card, ToolCard)
                        else ToolCard.model_validate(raw_card)
                    )
                except ValidationError as exc:
                    raise AgentRuntimeError(
                        RuntimeErrorCode.CONFIGURATION_ERROR,
                        Messages.Errors.TOOL_CARD_METADATA_INVALID,
                        retryable=False,
                    ) from exc
                entries.append(RegisteredTool(provider=provider, card=card))
        return tuple(entries)
