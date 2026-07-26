"""Presentation-free dependency contracts for MCP transport execution."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Protocol

from pydantic import Field, field_validator

from agent_runtime.capabilities.actions.policy import ConnectorWritePolicyOverrides
from agent_runtime.capabilities.operations.classifier import OperationClassifier
from agent_runtime.capabilities.operations.contracts import OperationRequest
from agent_runtime.capabilities.operations.descriptors import (
    OperationDescriptorRegistry,
)
from agent_runtime.effects.contracts import EffectActorIdentity, EffectStageScope
from agent_runtime.effects.staging import EffectStager
from agent_runtime.execution.contracts import RuntimeContract

_RESULT_REF = re.compile(r"^operation://(op_[0-9a-f-]+)/result$")


class McpOperationStoredResult(RuntimeContract):
    """The bounded model-facing form of a persisted MCP read result."""

    result_ref: str = Field(min_length=1, max_length=2048)
    model_output: dict[str, object]

    @field_validator("result_ref")
    @classmethod
    def _result_ref_is_operation_result(cls, value: str) -> str:
        match = _RESULT_REF.fullmatch(value)
        if match is None:
            raise ValueError("MCP read results require an operation result reference")
        return value


class McpOperationResultStorePort(Protocol):
    """Persist a read result before its ``operation.completed`` event is emitted."""

    async def store_read_result(
        self,
        *,
        request: OperationRequest,
        output: Mapping[str, object],
    ) -> McpOperationStoredResult:
        """Return an immutable result ref and bounded model-visible projection."""


class McpOperationArgumentStorePort(Protocol):
    """Durably retain canonical MCP arguments before the gateway can dispatch."""

    async def persist(
        self,
        *,
        ref: str,
        digest: str,
        canonical_bytes: bytes,
    ) -> None:
        """Persist exactly one digest-pinned canonical argument body."""

    async def resolve(
        self,
        *,
        ref: str,
        digest: str,
    ) -> bytes | None:
        """Return exactly the stored body, or ``None`` when it is unavailable."""


@dataclass(frozen=True)
class McpOperationExecutionServices:
    """Narrow dependencies visible to MCP adapters.

    This contract deliberately excludes the generic gateway and every
    presentation capability. Transport code can persist a neutral result or
    stage an effect, but cannot own an outcome surface.
    """

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

    def __post_init__(self) -> None:
        if self.stage_author.principal_ref != self.stage_scope.owner_ref and (
            self.stage_author.actor.value == "user"
        ):
            raise ValueError("a user stage author must match the stage owner")


__all__ = [
    "McpOperationArgumentStorePort",
    "McpOperationExecutionServices",
    "McpOperationResultStorePort",
    "McpOperationStoredResult",
]
