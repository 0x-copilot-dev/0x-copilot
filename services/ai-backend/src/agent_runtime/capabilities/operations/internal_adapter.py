"""Authoritative gateway adapter for reviewed internal operations.

Internal operations still have meaningful authority boundaries even though they
do not create an external effect.  This adapter keeps the legacy callable's
exact result object while making request identity, descriptor classification,
gate resolution, idempotency, and operation events originate from the
universal :class:`OperationGateway`.

It deliberately admits only exact descriptors whose effect class is ``none``
or ``internal_reversible``.  A caller cannot use this convenience adapter to
turn an external effect into an inline action.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Generic, TypeVar

from agent_runtime.capabilities.operations.builtin_adapter import (
    BuiltinGatewayGateResolver,
)
from agent_runtime.capabilities.operations.catalog import (
    DEFAULT_OPERATION_DESCRIPTORS,
)
from agent_runtime.capabilities.operations.context import (
    OperationContext,
    OperationRequestFactory,
)
from agent_runtime.capabilities.operations.contracts import (
    OperationGateResolver,
    OperationRawResult,
    ProposedEffect,
)
from agent_runtime.capabilities.operations.descriptors import (
    OperationDescriptorRegistry,
)
from agent_runtime.capabilities.operations.gateway import OperationGateway
from agent_runtime.surfaces_v2.ledger_models import EffectClass, OperationOutcome

_T = TypeVar("_T")
_INLINE_EFFECTS = frozenset({EffectClass.NONE, EffectClass.INTERNAL_REVERSIBLE})


class InternalOperationDescriptorError(RuntimeError):
    """The caller attempted to execute an operation outside the inline domain."""


@dataclass(frozen=True)
class InternalOperationResult(Generic[_T]):
    """The exact legacy value on success, or a safe blocked/failed disposition."""

    value: _T | None
    outcome: OperationOutcome
    safe_summary: str

    @property
    def completed(self) -> bool:
        return self.value is not None and self.outcome is OperationOutcome.SUCCEEDED


@dataclass(frozen=True)
class InternalOperationAdapter:
    """Execute one exact reviewed internal operation through the gateway."""

    capability: str
    op: str
    descriptors: OperationDescriptorRegistry = DEFAULT_OPERATION_DESCRIPTORS
    gates: OperationGateResolver = field(default_factory=BuiltinGatewayGateResolver)

    def __post_init__(self) -> None:
        entry = self.descriptors.resolve_entry(self.capability, self.op)
        if entry is None:
            raise InternalOperationDescriptorError(
                f"internal operation lacks an exact descriptor: {self.capability}.{self.op}"
            )
        if entry.descriptor.effect_class not in _INLINE_EFFECTS:
            raise InternalOperationDescriptorError(
                "internal operation adapter refuses a non-inline descriptor: "
                f"{self.capability}.{self.op}"
            )

    async def invoke(
        self,
        *,
        arguments: Mapping[str, object],
        legacy: Callable[[], Awaitable[_T]],
        safe_summary: str,
    ) -> InternalOperationResult[_T]:
        """Run the legacy callable exactly once through the active gateway.

        Callers choose their shadow/off behavior before reaching this method.
        Requiring an active enforcing context makes an accidental direct fallback
        impossible at the authoritative seam.
        """

        OperationContext.require()
        request = OperationRequestFactory.create(
            capability=self.capability,
            op=self.op,
            arguments=dict(arguments),
        )
        captured = _CapturedInternalRead(legacy=legacy, safe_summary=safe_summary)
        disposition = await OperationGateway(
            descriptors=self.descriptors,
            gates=self.gates,
        ).invoke(request, captured)
        if disposition.outcome is OperationOutcome.SUCCEEDED and captured.completed:
            return InternalOperationResult(
                value=captured.value,
                outcome=disposition.outcome,
                safe_summary=disposition.agent_summary,
            )
        return InternalOperationResult(
            value=None,
            outcome=disposition.outcome,
            safe_summary=disposition.agent_summary,
        )


@dataclass
class _CapturedInternalRead(Generic[_T]):
    """Gateway read adapter that never exposes an effect/apply capability."""

    legacy: Callable[[], Awaitable[_T]]
    safe_summary: str
    value: _T | None = None
    completed: bool = False

    async def execute_read(self, request: object) -> OperationRawResult:
        del request
        self.value = await self.legacy()
        self.completed = True
        return OperationRawResult(safe_summary=self.safe_summary)

    async def build_proposal(self, request: object) -> ProposedEffect:
        del request
        raise InternalOperationDescriptorError(
            "internal operations cannot construct an effect proposal"
        )


__all__ = (
    "InternalOperationAdapter",
    "InternalOperationDescriptorError",
    "InternalOperationResult",
)
