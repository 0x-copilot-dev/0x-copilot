"""Lazy full-spec loader for dynamically selected tools."""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import ValidationError

from enterprise_search_ai.agent.errors import AgentRuntimeError
from enterprise_search_ai.tools.cards import (
    LoadedToolSpec,
    ToolCard,
    ToolLoadError,
    ToolLoadErrorCode,
    ToolLoadRequest,
    ToolLoadResult,
    normalize_slug,
)
from enterprise_search_ai.tools.permissions import is_card_authorized, is_policy_authorized
from enterprise_search_ai.tools.registry import DynamicToolRegistry, RegisteredTool


@dataclass(frozen=True)
class ToolLoader:
    """Resolves a selected compact card into a validated loaded tool spec."""

    registry: DynamicToolRegistry

    def load_tool(self, request: ToolLoadRequest) -> ToolLoadResult:
        """Load a selected tool while rechecking permissions and validation."""

        runtime_context = request.runtime_context
        resolution = self.registry.resolve_tool(request.tool_name)
        if isinstance(resolution, ToolLoadError):
            return ToolLoadResult.fail(
                resolution.code,
                resolution.safe_message,
                retryable=resolution.retryable,
                tool_name=resolution.tool_name,
                correlation_id=runtime_context.trace_id,
            )

        if not is_card_authorized(runtime_context, resolution.card):
            return ToolLoadResult.fail(
                ToolLoadErrorCode.PERMISSION_DENIED,
                "You do not have access to this tool.",
                tool_name=resolution.card.name,
                correlation_id=runtime_context.trace_id,
            )

        try:
            raw_spec = resolution.provider.load_tool_spec(resolution.card.name)
        except AgentRuntimeError:
            return ToolLoadResult.fail(
                ToolLoadErrorCode.CONNECTOR_UNAVAILABLE,
                "The connector could not load this tool right now.",
                retryable=True,
                tool_name=resolution.card.name,
                correlation_id=runtime_context.trace_id,
            )
        except Exception:
            return ToolLoadResult.fail(
                ToolLoadErrorCode.CONNECTOR_UNAVAILABLE,
                "The connector could not load this tool right now.",
                retryable=True,
                tool_name=resolution.card.name,
                correlation_id=runtime_context.trace_id,
            )

        try:
            loaded_spec = (
                raw_spec
                if isinstance(raw_spec, LoadedToolSpec)
                else LoadedToolSpec.model_validate(raw_spec)
            )
        except ValidationError:
            return ToolLoadResult.fail(
                ToolLoadErrorCode.MALFORMED_TOOL_SPEC,
                "The selected tool has an invalid specification.",
                tool_name=resolution.card.name,
                correlation_id=runtime_context.trace_id,
            )

        spec_error = _validate_loaded_spec_matches_card(loaded_spec, resolution)
        if spec_error is not None:
            return ToolLoadResult.fail(
                ToolLoadErrorCode.MALFORMED_TOOL_SPEC,
                spec_error,
                tool_name=resolution.card.name,
                correlation_id=runtime_context.trace_id,
            )

        if not is_policy_authorized(runtime_context, loaded_spec.permission_policy):
            return ToolLoadResult.fail(
                ToolLoadErrorCode.PERMISSION_DENIED,
                "You do not have access to this tool.",
                tool_name=resolution.card.name,
                correlation_id=runtime_context.trace_id,
            )

        return ToolLoadResult.ok(loaded_spec)

    def load_tool_by_name(
        self,
        *,
        tool_name: str,
        runtime_context: object,
    ) -> ToolLoadResult:
        """Parse an untrusted model request before loading the selected tool."""

        try:
            request = ToolLoadRequest(
                tool_name=tool_name,
                runtime_context=runtime_context,
            )
        except ValidationError:
            return ToolLoadResult.fail(
                ToolLoadErrorCode.INVALID_TOOL_NAME,
                "Tools must be requested by stable name.",
                tool_name=_safe_tool_name(tool_name),
            )
        return self.load_tool(request)


def _validate_loaded_spec_matches_card(
    loaded_spec: LoadedToolSpec,
    resolution: RegisteredTool,
) -> str | None:
    card: ToolCard = resolution.card
    policy = loaded_spec.permission_policy
    if loaded_spec.name != card.name:
        return "The selected tool returned a mismatched specification."
    if policy.connector != card.connector:
        return "The selected tool returned mismatched connector metadata."
    if policy.required_scopes != card.required_scopes:
        return "The selected tool returned mismatched permission metadata."
    if policy.risk_level != card.risk_level:
        return "The selected tool returned mismatched risk metadata."
    return None


def _safe_tool_name(tool_name: str) -> str | None:
    try:
        return normalize_slug(tool_name, "tool_name")
    except ValueError:
        return None
