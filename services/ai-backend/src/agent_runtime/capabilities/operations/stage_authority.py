"""Ephemeral operation-gateway authority for staging external effects.

``OperationContext.operation_scope`` carries lineage only.  A staged proposal
instead needs an object activated by the *currently executing*
``OperationGateway._invoke_once`` frame.  There is intentionally no minting
function: a reflected module attribute cannot create a usable capability.

The active binding is task-bound as well as request-bound.  ``ContextVar``
propagation into ``asyncio.create_task`` therefore does not grant the child
task authority to consume the parent's capability.
"""

from __future__ import annotations

import asyncio
import inspect
from contextvars import ContextVar, Token
from dataclasses import dataclass
from types import CodeType
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from agent_runtime.capabilities.operations.context import OperationContext
from agent_runtime.capabilities.operations.errors import OperationStageCapabilityError

if TYPE_CHECKING:
    from agent_runtime.capabilities.operations.contracts import ProposedEffect
    from agent_runtime.surfaces_v2.entities import OperationRequest


@dataclass
class _ActiveStageBinding:
    capability: GatewayStageCapability
    operation_id: str
    args_digest: str
    run_id: str
    org_id: str
    user_id: str
    conversation_id: str
    issuing_task: asyncio.Task[object] | None
    issuing_code: CodeType
    consumed: bool = False


_ACTIVE_STAGE_CAPABILITY: ContextVar[_ActiveStageBinding | None] = ContextVar(
    "operation_gateway_stage_capability", default=None
)


class GatewayStageCapability:
    """An opaque one-use capability that cannot be normally constructed."""

    __slots__ = ("__weakref__",)

    def __new__(cls) -> GatewayStageCapability:
        del cls
        raise TypeError("stage capability is activated only by OperationGateway")

    def _consume_for(self, request: OperationRequest) -> None:
        binding = _ACTIVE_STAGE_CAPABILITY.get()
        context = OperationContext.require()
        identity = context.identity
        if (
            binding is None
            or binding.capability is not self
            or binding.consumed
            or asyncio.current_task() is not binding.issuing_task
            or not _has_issuing_gateway_frame(binding.issuing_code)
            or request.operation_id != binding.operation_id
            or request.args_digest != binding.args_digest
            or request.run_id != binding.run_id
            or identity.run_id != binding.run_id
            or identity.org_id != binding.org_id
            or identity.user_id != binding.user_id
            or identity.conversation_id != binding.conversation_id
        ):
            raise OperationStageCapabilityError()
        binding.consumed = True


def _activate_gateway_stage_capability(
    request: OperationRequest,
    *,
    issuing_code: CodeType,
) -> tuple[GatewayStageCapability, Token[_ActiveStageBinding | None]]:
    """Activate one capability only when called directly by the gateway frame."""

    caller = inspect.currentframe()
    caller = caller.f_back if caller is not None else None
    if caller is None or caller.f_code is not issuing_code:
        raise OperationStageCapabilityError()
    context = OperationContext.require()
    identity = context.identity
    if request.run_id != identity.run_id:
        raise OperationStageCapabilityError()
    capability = object.__new__(GatewayStageCapability)
    binding = _ActiveStageBinding(
        capability=capability,
        operation_id=request.operation_id,
        args_digest=request.args_digest,
        run_id=identity.run_id,
        org_id=identity.org_id,
        user_id=identity.user_id,
        conversation_id=identity.conversation_id,
        issuing_task=asyncio.current_task(),
        issuing_code=issuing_code,
    )
    return capability, _ACTIVE_STAGE_CAPABILITY.set(binding)


def _deactivate_gateway_stage_capability(
    token: Token[_ActiveStageBinding | None],
) -> None:
    """Restore the parent's authority binding after gateway proposal work."""

    _ACTIVE_STAGE_CAPABILITY.reset(token)


def _has_issuing_gateway_frame(issuing_code: CodeType) -> bool:
    frame = inspect.currentframe()
    frame = frame.f_back if frame is not None else None
    while frame is not None:
        if frame.f_code is issuing_code:
            return True
        frame = frame.f_back
    return False


@runtime_checkable
class GatewayStageCapabilityAdapter(Protocol):
    """An adapter whose proposal construction requires gateway authority."""

    async def build_proposal_with_capability(
        self, request: OperationRequest, capability: GatewayStageCapability
    ) -> ProposedEffect:
        """Build one proposal only after consuming the supplied capability."""


__all__ = ("GatewayStageCapability", "GatewayStageCapabilityAdapter")
