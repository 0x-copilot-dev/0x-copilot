"""Provider-backed catalog for compact subagent definitions."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

from pydantic import ValidationError

from agent_runtime.agent.contracts import AgentRuntimeContext, RuntimeErrorCode
from agent_runtime.agent.errors import AgentRuntimeError
from agent_runtime.subagents.constants import Keys, Messages
from agent_runtime.subagents.contracts import (
    SubagentDefinition,
    SubagentError,
    SubagentErrorCode,
    SubagentValueNormalizer,
)

RawSubagentDefinition = SubagentDefinition | Mapping[str, object]


class SubagentDefinitionProvider(Protocol):
    """Adapter boundary for subagent catalog metadata."""

    def list_subagent_definitions(self) -> Sequence[RawSubagentDefinition]:
        """Return compact definitions registered by this provider."""


@dataclass(frozen=True)
class RegisteredSubagent:
    """A validated definition paired with the provider that registered it."""

    provider: SubagentDefinitionProvider
    definition: SubagentDefinition


@dataclass(frozen=True)
class DynamicSubagentCatalog:
    """Lists permission-filtered subagents and resolves selected subagents."""

    providers: Sequence[SubagentDefinitionProvider]

    def __post_init__(self) -> None:
        for provider in self.providers:
            if not callable(getattr(provider, Keys.Method.LIST_SUBAGENT_DEFINITIONS, None)):
                raise AgentRuntimeError(
                    RuntimeErrorCode.DEPENDENCY_ERROR,
                    Messages.Catalog.MISSING_LIST_DEFINITIONS,
                    retryable=False,
                )

    def list_subagent_definitions(
        self,
        context: AgentRuntimeContext,
    ) -> tuple[SubagentDefinition, ...]:
        """Return compact definitions visible to the request context."""

        runtime_context = SubagentContextParser.coerce(context)
        entries = self._collect_entries()
        duplicate_name = SubagentCatalogResolver.first_duplicate_name(entries)
        if duplicate_name is not None:
            raise AgentRuntimeError(
                RuntimeErrorCode.CONFIGURATION_ERROR,
                Messages.Catalog.DUPLICATE_SUBAGENT_NAME,
                retryable=False,
                correlation_id=runtime_context.trace_id,
            )

        definitions = (
            entry.definition
            for entry in entries
            if SubagentPermissionPolicy.is_definition_visible(runtime_context, entry.definition)
        )
        return tuple(sorted(definitions, key=lambda definition: definition.name))

    def list_available_subagents(self, context: object) -> tuple[SubagentDefinition, ...]:
        """Runtime port adapter returning model-visible compact subagent definitions."""

        return self.list_subagent_definitions(SubagentContextParser.coerce(context))

    def resolve_subagent(
        self,
        name: str,
        context: AgentRuntimeContext,
    ) -> RegisteredSubagent | SubagentError:
        """Resolve a selected stable subagent name after rechecking visibility."""

        runtime_context = SubagentContextParser.coerce(context)
        try:
            normalized_name = SubagentValueNormalizer.normalize_slug(name, Keys.Field.NAME)
        except ValueError:
            return SubagentError(
                code=SubagentErrorCode.SUBAGENT_UNAVAILABLE,
                safe_message=Messages.Catalog.REQUESTED_SUBAGENT_UNKNOWN,
                correlation_id=runtime_context.trace_id,
            )

        entries = self._collect_entries()
        matching_entries = [
            entry for entry in entries if entry.definition.name == normalized_name
        ]
        if not matching_entries:
            return SubagentError(
                code=SubagentErrorCode.SUBAGENT_UNAVAILABLE,
                safe_message=Messages.Catalog.REQUESTED_SUBAGENT_UNKNOWN,
                correlation_id=runtime_context.trace_id,
            )
        if len(matching_entries) > 1:
            return SubagentError(
                code=SubagentErrorCode.SUBAGENT_UNAVAILABLE,
                safe_message=Messages.Catalog.REQUESTED_SUBAGENT_DUPLICATE,
                correlation_id=runtime_context.trace_id,
            )

        entry = matching_entries[0]
        if not entry.definition.enabled:
            return SubagentError(
                code=SubagentErrorCode.SUBAGENT_UNAVAILABLE,
                safe_message=Messages.Catalog.REQUESTED_SUBAGENT_DISABLED,
                correlation_id=runtime_context.trace_id,
            )
        if not SubagentPermissionPolicy.is_definition_visible(runtime_context, entry.definition):
            return SubagentError(
                code=SubagentErrorCode.SUBAGENT_UNAVAILABLE,
                safe_message=Messages.Catalog.REQUESTED_SUBAGENT_UNKNOWN,
                correlation_id=runtime_context.trace_id,
            )
        return entry

    def _collect_entries(self) -> tuple[RegisteredSubagent, ...]:
        entries: list[RegisteredSubagent] = []
        for provider in self.providers:
            try:
                raw_definitions = provider.list_subagent_definitions()
            except AgentRuntimeError:
                raise
            except Exception as exc:
                raise AgentRuntimeError(
                    RuntimeErrorCode.CAPABILITY_LOAD_ERROR,
                    Messages.Catalog.DEFINITIONS_LOAD_FAILED,
                    retryable=True,
                ) from exc

            for raw_definition in raw_definitions:
                try:
                    definition = (
                        raw_definition
                        if isinstance(raw_definition, SubagentDefinition)
                        else SubagentDefinition.model_validate(raw_definition)
                    )
                except ValidationError as exc:
                    raise AgentRuntimeError(
                        RuntimeErrorCode.CONFIGURATION_ERROR,
                        Messages.Catalog.INVALID_DEFINITION,
                        retryable=False,
                    ) from exc
                entries.append(RegisteredSubagent(provider=provider, definition=definition))
        return tuple(entries)


class SubagentPermissionPolicy:
    """Visibility rules for model-selectable subagents."""

    @classmethod
    def is_definition_visible(
        cls,
        context: AgentRuntimeContext,
        definition: SubagentDefinition,
    ) -> bool:
        if not definition.enabled:
            return False
        if not definition.required_scopes:
            return True
        return definition.required_scopes.issubset(context.permission_scopes)


class SubagentCatalogResolver:
    """Deterministic lookup helpers for registered subagent definitions."""

    @classmethod
    def first_duplicate_name(cls, entries: Sequence[RegisteredSubagent]) -> str | None:
        counts = Counter(entry.definition.name for entry in entries)
        duplicate_names = sorted(name for name, count in counts.items() if count > 1)
        if not duplicate_names:
            return None
        return duplicate_names[0]


class SubagentContextParser:
    """Runtime context parser for catalog boundaries."""

    @classmethod
    def coerce(cls, context: object) -> AgentRuntimeContext:
        if isinstance(context, AgentRuntimeContext):
            return context
        try:
            return AgentRuntimeContext.model_validate(context)
        except ValidationError as exc:
            raise AgentRuntimeError(
                RuntimeErrorCode.VALIDATION_ERROR,
                Messages.Catalog.INVALID_CONTEXT,
                retryable=False,
            ) from exc
