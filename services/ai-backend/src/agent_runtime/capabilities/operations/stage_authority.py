"""One-use authority for adapters that stage model-visible external effects.

``OperationContext.operation_scope`` tracks parentage only.  It is intentionally
public so nested work can preserve lineage, and therefore must never authorize
an effect.  This module instead makes a capability valid only when the
``OperationGateway`` registers that exact object after request validation,
classification, gate resolution, and audit-event setup.

The registry is closed over by the minting function.  ``GatewayStageCapability``
cannot be normally constructed; even a reflective lookalike has no registry
binding and fails closed.  There is deliberately no public mint or bind API.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable
from weakref import WeakKeyDictionary

from agent_runtime.capabilities.operations.context import OperationContext
from agent_runtime.capabilities.operations.errors import OperationStageCapabilityError

if TYPE_CHECKING:
    from agent_runtime.capabilities.operations.contracts import ProposedEffect
    from agent_runtime.surfaces_v2.entities import OperationRequest


@dataclass(frozen=True)
class _CapabilityBinding:
    operation_id: str
    args_digest: str
    run_id: str
    org_id: str
    user_id: str
    conversation_id: str


def _new_authority() -> tuple[
    type["GatewayStageCapability"],
    Callable[["OperationRequest"], "GatewayStageCapability"],
]:
    """Create a registry that cannot be reconstructed by importing a symbol."""

    bindings: WeakKeyDictionary[GatewayStageCapability, _CapabilityBinding] = (
        WeakKeyDictionary()
    )

    class GatewayStageCapability:
        """Opaque gateway-issued evidence; direct construction creates no authority."""

        __slots__ = ("__weakref__",)

        def __new__(cls) -> GatewayStageCapability:
            del cls
            raise TypeError("stage capability is minted only by OperationGateway")

        def _consume_for(self, request: OperationRequest) -> None:
            binding = bindings.pop(self, None)
            if binding is None:
                raise OperationStageCapabilityError()
            context = OperationContext.require()
            identity = context.identity
            if (
                request.operation_id != binding.operation_id
                or request.args_digest != binding.args_digest
                or request.run_id != binding.run_id
                or identity.run_id != binding.run_id
                or identity.org_id != binding.org_id
                or identity.user_id != binding.user_id
                or identity.conversation_id != binding.conversation_id
            ):
                raise OperationStageCapabilityError()

    def mint(request: OperationRequest) -> GatewayStageCapability:
        """Register one capability after the gateway has admitted this request."""

        context = OperationContext.require()
        identity = context.identity
        if request.run_id != identity.run_id:
            raise OperationStageCapabilityError()
        capability = object.__new__(GatewayStageCapability)
        bindings[capability] = _CapabilityBinding(
            operation_id=request.operation_id,
            args_digest=request.args_digest,
            run_id=identity.run_id,
            org_id=identity.org_id,
            user_id=identity.user_id,
            conversation_id=identity.conversation_id,
        )
        return capability

    return GatewayStageCapability, mint


GatewayStageCapability, _mint_gateway_stage_capability = _new_authority()
# A second factory would create a separate registry and undermine the single
# gateway authority.  Keep the closure state reachable only through the mint
# function imported by ``operations.gateway``.
del _new_authority


@runtime_checkable
class GatewayStageCapabilityAdapter(Protocol):
    """An adapter whose proposal construction requires gateway authority."""

    async def build_proposal_with_capability(
        self, request: OperationRequest, capability: GatewayStageCapability
    ) -> ProposedEffect:
        """Build one proposal only after consuming the supplied capability."""


__all__ = ("GatewayStageCapability", "GatewayStageCapabilityAdapter")
