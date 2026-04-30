"""Lazy full-spec loader for dynamically selected tools."""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import ValidationError

from agent_runtime.execution.errors import AgentRuntimeError
from agent_runtime.capabilities.tools.cards import (
    LoadedToolSpec,
    ToolCard,
    ToolLoadError,
    ToolLoadErrorCode,
    ToolLoadRequest,
    ToolLoadResult,
    ToolValueNormalizer,
)
from agent_runtime.capabilities.tools.constants import Keys, Messages
from agent_runtime.capabilities.tools.permissions import ToolPermissionChecker
from agent_runtime.capabilities.tools.registry import DynamicToolRegistry, RegisteredTool


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

        if not ToolPermissionChecker.is_card_authorized(runtime_context, resolution.card):
            return ToolLoadResult.fail(
                ToolLoadErrorCode.PERMISSION_DENIED,
                Messages.Errors.TOOL_PERMISSION_DENIED,
                tool_name=resolution.card.name,
                correlation_id=runtime_context.trace_id,
            )

        try:
            raw_spec = resolution.provider.load_tool_spec(resolution.card.name)
        except AgentRuntimeError:
            return ToolLoadResult.fail(
                ToolLoadErrorCode.CONNECTOR_UNAVAILABLE,
                Messages.Errors.CONNECTOR_LOAD_FAILED,
                retryable=True,
                tool_name=resolution.card.name,
                correlation_id=runtime_context.trace_id,
            )
        except Exception:
            return ToolLoadResult.fail(
                ToolLoadErrorCode.CONNECTOR_UNAVAILABLE,
                Messages.Errors.CONNECTOR_LOAD_FAILED,
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
                Messages.Errors.TOOL_SPEC_INVALID,
                tool_name=resolution.card.name,
                correlation_id=runtime_context.trace_id,
            )

        spec_error = self._validate_loaded_spec_matches_card(loaded_spec, resolution)
        if spec_error is not None:
            return ToolLoadResult.fail(
                ToolLoadErrorCode.MALFORMED_TOOL_SPEC,
                spec_error,
                tool_name=resolution.card.name,
                correlation_id=runtime_context.trace_id,
            )

        if not ToolPermissionChecker.is_policy_authorized(
            runtime_context,
            loaded_spec.permission_policy,
        ):
            return ToolLoadResult.fail(
                ToolLoadErrorCode.PERMISSION_DENIED,
                Messages.Errors.TOOL_PERMISSION_DENIED,
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
                Messages.Errors.TOOL_NAME_REQUIRED,
                tool_name=self._safe_tool_name(tool_name),
            )
        return self.load_tool(request)

    @classmethod
    def _validate_loaded_spec_matches_card(
        cls,
        loaded_spec: LoadedToolSpec,
        resolution: RegisteredTool,
    ) -> str | None:
        card: ToolCard = resolution.card
        policy = loaded_spec.permission_policy
        if loaded_spec.name != card.name:
            return Messages.SpecMismatch.NAME
        if policy.connector != card.connector:
            return Messages.SpecMismatch.CONNECTOR
        if policy.required_scopes != card.required_scopes:
            return Messages.SpecMismatch.PERMISSIONS
        if policy.risk_level != card.risk_level:
            return Messages.SpecMismatch.RISK
        return None

    @classmethod
    def _safe_tool_name(cls, tool_name: str) -> str | None:
        try:
            return ToolValueNormalizer.normalize_slug(tool_name, Keys.Fields.TOOL_NAME)
        except ValueError:
            return None
