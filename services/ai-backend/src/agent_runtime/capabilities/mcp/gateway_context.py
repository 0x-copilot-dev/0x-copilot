"""Trusted MCP operation-gateway composition and run binding.

This is intentionally separate from the transport adapter module.  It owns the
full gateway object and derives the adapter-visible neutral execution contract
only at the middleware boundary.
"""

from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass, field

from agent_runtime.capabilities.actions.policy import ConnectorWritePolicyOverrides
from agent_runtime.capabilities.mcp.execution_services import (
    McpOperationArgumentStorePort,
    McpOperationExecutionServices,
    McpOperationResultStorePort,
)
from agent_runtime.capabilities.operations.classifier import OperationClassifier
from agent_runtime.capabilities.operations.context import OperationContext
from agent_runtime.capabilities.operations.descriptors import (
    OperationDescriptorRegistry,
)
from agent_runtime.capabilities.operations.gateway import OperationGateway
from agent_runtime.effects.contracts import EffectActorIdentity, EffectStageScope
from agent_runtime.effects.staging import EffectStager

__operation_boundary__ = "presentation"


@dataclass(frozen=True)
class McpOperationGatewayServices:
    """Trusted composition dependencies for the canonical MCP route."""

    gateway: OperationGateway
    descriptors: OperationDescriptorRegistry
    classifier: OperationClassifier
    stager: EffectStager
    stage_scope: EffectStageScope
    stage_author: EffectActorIdentity
    result_store: McpOperationResultStorePort
    argument_store: McpOperationArgumentStorePort
    browser_plans: object | None = None
    connector_overrides: ConnectorWritePolicyOverrides = field(
        default_factory=ConnectorWritePolicyOverrides
    )

    @property
    def execution(self) -> McpOperationExecutionServices:
        """Return the only contract that may enter transport adapter code."""

        return McpOperationExecutionServices(
            descriptors=self.descriptors,
            classifier=self.classifier,
            stager=self.stager,
            stage_scope=self.stage_scope,
            stage_author=self.stage_author,
            result_store=self.result_store,
            argument_store=self.argument_store,
            browser_plans=self.browser_plans,
            connector_overrides=self.connector_overrides,
        )

    def __post_init__(self) -> None:
        if self.stage_author.principal_ref != self.stage_scope.owner_ref and (
            self.stage_author.actor.value == "user"
        ):
            raise ValueError("a user stage author must match the stage owner")


_MCP_OPERATION_SERVICES: ContextVar[McpOperationGatewayServices | None] = ContextVar(
    "mcp_operation_gateway_services", default=None
)


class McpOperationGatewayContext:
    """Run-scoped binding for the trusted canonical MCP route."""

    @classmethod
    def bind_for_run(
        cls, services: McpOperationGatewayServices
    ) -> Token[McpOperationGatewayServices | None]:
        return _MCP_OPERATION_SERVICES.set(services)

    @classmethod
    def unbind(cls, token: Token[McpOperationGatewayServices | None]) -> None:
        _MCP_OPERATION_SERVICES.reset(token)

    @classmethod
    def active(cls) -> McpOperationGatewayServices | None:
        return _MCP_OPERATION_SERVICES.get()

    @classmethod
    def canonical(cls) -> McpOperationGatewayServices | None:
        """Return services only while durable operation execution is active."""

        context = OperationContext.active()
        services = cls.active()
        if context is None or not context.canonical_arguments_durable:
            return None
        return services


def is_enforced_mcp_gateway_active() -> bool:
    """Compatibility predicate for the now-unconditional canonical MCP route."""

    return McpOperationGatewayContext.canonical() is not None


__all__ = [
    "McpOperationGatewayContext",
    "McpOperationGatewayServices",
    "is_enforced_mcp_gateway_active",
]
