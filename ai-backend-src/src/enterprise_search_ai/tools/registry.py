"""Provider-backed registry for compact dynamic tool cards."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

from pydantic import ValidationError

from enterprise_search_ai.agent.contracts import AgentRuntimeContext, RuntimeErrorCode
from enterprise_search_ai.agent.errors import AgentRuntimeError
from enterprise_search_ai.tools.cards import (
    LoadedToolSpec,
    ToolCard,
    ToolLoadError,
    ToolLoadErrorCode,
)
from enterprise_search_ai.tools.permissions import is_card_authorized

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
            if not callable(getattr(provider, "list_tool_cards", None)):
                raise AgentRuntimeError(
                    RuntimeErrorCode.DEPENDENCY_ERROR,
                    "Tool provider is missing list_tool_cards().",
                    retryable=False,
                )
            if not callable(getattr(provider, "load_tool_spec", None)):
                raise AgentRuntimeError(
                    RuntimeErrorCode.DEPENDENCY_ERROR,
                    "Tool provider is missing load_tool_spec().",
                    retryable=False,
                )

    def list_tool_cards(self, context: AgentRuntimeContext) -> tuple[ToolCard, ...]:
        """Return compact cards visible to the request context."""

        runtime_context = _coerce_context(context)
        entries = self._collect_entries()
        duplicate_name = _first_duplicate_name(entries)
        if duplicate_name is not None:
            raise AgentRuntimeError(
                RuntimeErrorCode.CONFIGURATION_ERROR,
                "Multiple tools are registered with the same name.",
                retryable=False,
                correlation_id=runtime_context.trace_id,
            )

        cards = (
            entry.card for entry in entries if is_card_authorized(runtime_context, entry.card)
        )
        return tuple(sorted(cards, key=lambda card: card.name))

    def list_available_tools(self, context: object) -> tuple[ToolCard, ...]:
        """Runtime port adapter returning model-visible compact cards."""

        return self.list_tool_cards(_coerce_context(context))

    def resolve_tool(self, name: str) -> RegisteredTool | ToolLoadError:
        """Resolve a selected stable tool name to exactly one provider entry."""

        entries = self._collect_entries()
        matching_entries = [entry for entry in entries if entry.card.name == name]
        if not matching_entries:
            return ToolLoadError(
                code=ToolLoadErrorCode.UNKNOWN_TOOL,
                safe_message="Requested tool is not available.",
                tool_name=name,
            )
        if len(matching_entries) > 1:
            return ToolLoadError(
                code=ToolLoadErrorCode.DUPLICATE_TOOL_NAME,
                safe_message="Requested tool name is registered more than once.",
                tool_name=name,
            )

        entry = matching_entries[0]
        if not entry.card.enabled:
            return ToolLoadError(
                code=ToolLoadErrorCode.TOOL_DISABLED,
                safe_message="Requested tool is disabled.",
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
                    "Tool cards could not be loaded.",
                    retryable=True,
                ) from exc

            for raw_card in raw_cards:
                try:
                    card = raw_card if isinstance(raw_card, ToolCard) else ToolCard.model_validate(raw_card)
                except ValidationError as exc:
                    raise AgentRuntimeError(
                        RuntimeErrorCode.CONFIGURATION_ERROR,
                        "Tool card metadata is invalid.",
                        retryable=False,
                    ) from exc
                entries.append(RegisteredTool(provider=provider, card=card))
        return tuple(entries)


def _first_duplicate_name(entries: Sequence[RegisteredTool]) -> str | None:
    counts = Counter(entry.card.name for entry in entries)
    duplicate_names = sorted(name for name, count in counts.items() if count > 1)
    if not duplicate_names:
        return None
    return duplicate_names[0]


def _coerce_context(context: object) -> AgentRuntimeContext:
    if isinstance(context, AgentRuntimeContext):
        return context
    try:
        return AgentRuntimeContext.model_validate(context)
    except ValidationError as exc:
        raise AgentRuntimeError(
            RuntimeErrorCode.VALIDATION_ERROR,
            "Runtime context is invalid.",
            retryable=False,
        ) from exc
