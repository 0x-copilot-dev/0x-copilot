"""Authoritative gateway adaptation for product-owned built-in tools.

The model-facing built-ins predate the universal operation gateway.  This
module is the one reusable bridge that lets those tools retain their exact
legacy result contracts while, in enforcement mode, executing through the
gateway exactly once.  It deliberately exposes no transport, executor, or
surface-emission capability.

Shadow observation remains owned by ``OperationShadowProbe`` at the existing
assembly boundary.  Running the adapter only in ``enforce`` avoids duplicate
events while making the real execution path authoritative when enforcement is
enabled.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Generic, TypeVar

from agent_runtime.capabilities.operations.builtin_catalog import (
    DEFAULT_BUILTIN_OPERATION_CATALOG,
    BuiltinOperationCatalog,
    BuiltinOperationCatalogEntry,
)
from agent_runtime.capabilities.operations.catalog import (
    DEFAULT_OPERATION_DESCRIPTORS,
)
from agent_runtime.capabilities.operations.context import (
    OperationContext,
    OperationRequestFactory,
)
from agent_runtime.capabilities.operations.contracts import (
    GateResolution,
    OperationClassification,
    OperationGatewayMode,
    OperationRawResult,
    ProposedEffect,
)
from agent_runtime.capabilities.operations.descriptors import (
    OperationDescriptorRegistry,
)
from agent_runtime.capabilities.operations.gateway import OperationGateway
from agent_runtime.surfaces_v2.entities import OperationDescriptor, OperationRequest
from agent_runtime.surfaces_v2.ledger_models import (
    GateKind,
    OperationOutcome,
)

_T = TypeVar("_T")


class BuiltinOperationDescriptorError(RuntimeError):
    """A changed built-in was not declared by the reviewed operation catalog."""


class BuiltinOperationUnavailable(RuntimeError):
    """The gateway blocked a built-in before its legacy implementation ran."""

    def __init__(self, safe_message: str) -> None:
        super().__init__(safe_message)
        self.safe_message = safe_message


class BuiltinGatewayGateResolver:
    """Trust existing product-owned built-in guards, never a model assertion.

    Capability and policy checks for these tools already occur at their narrow
    legacy seam (for example, the dynamic loader rechecks its card and the
    row-set proposal port rechecks the policy snapshot).  The universal
    gateway still owns classification, operation identity, and disposition.
    Authentication and grant gates are intentionally *not* admitted here:
    those require a designated adapter, not a built-in compatibility bridge.
    """

    _ADMITTED_GATES = frozenset({GateKind.CAPABILITY, GateKind.POLICY})

    async def resolve(
        self,
        *,
        request: OperationRequest,
        descriptor: OperationDescriptor,
        classification: OperationClassification,
    ) -> GateResolution:
        del request, classification
        unsupported = tuple(
            gate
            for gate in descriptor.required_gate_kinds
            if gate not in self._ADMITTED_GATES
        )
        if unsupported:
            return GateResolution(
                allowed=False,
                gate_kind=unsupported[0],
                safe_summary=(
                    f"Needs {unsupported[0].value}; no external change was made."
                ),
            )
        return GateResolution(allowed=True)


@dataclass(frozen=True)
class BuiltinGatewayResult(Generic[_T]):
    """Either the exact legacy result or a safe gateway-blocked outcome."""

    value: _T | None
    outcome: OperationOutcome
    safe_summary: str

    @property
    def completed(self) -> bool:
        return self.value is not None and self.outcome is OperationOutcome.SUCCEEDED


@dataclass(frozen=True)
class BuiltinOperationAdapter:
    """One exact catalog-backed built-in invocation path.

    ``execute`` does not serialize, copy, or normalize the legacy result.  The
    caller receives the exact object returned by the existing tool while the
    gateway only receives a bounded safe summary.  This is crucial for
    byte-compatible tool results and for LangGraph interrupt values.
    """

    tool_name: str
    catalog: BuiltinOperationCatalog = DEFAULT_BUILTIN_OPERATION_CATALOG
    descriptors: OperationDescriptorRegistry = DEFAULT_OPERATION_DESCRIPTORS

    def __post_init__(self) -> None:
        self._entry()

    def entry(self) -> BuiltinOperationCatalogEntry:
        """Return the exact reviewed catalog and descriptor pair or fail closed."""

        return self._entry()

    def _entry(self) -> BuiltinOperationCatalogEntry:
        entry = self.catalog.resolve_tool_name(self.tool_name)
        if entry is None:
            raise BuiltinOperationDescriptorError(
                f"builtin tool is absent from operation catalog: {self.tool_name}"
            )
        if self.descriptors.resolve_entry(entry.capability, entry.op) is None:
            raise BuiltinOperationDescriptorError(
                "builtin operation lacks an exact descriptor: "
                f"{entry.capability}.{entry.op}"
            )
        return entry

    async def execute(
        self,
        *,
        arguments: Mapping[str, object],
        legacy: Callable[[], Awaitable[_T]],
        safe_summary: str,
    ) -> BuiltinGatewayResult[_T]:
        """Execute a pure/internal built-in once through the gateway when live.

        Off and shadow modes deliberately call the legacy implementation.  The
        assembly seam supplies shadow telemetry, which prevents a nested tool
        wrapper from producing duplicate operation events.  Enforcement mode
        is the only mode allowed to call the gateway authoritatively.
        """

        context = OperationContext.active()
        if context is None or context.mode is not OperationGatewayMode.ENFORCE:
            return BuiltinGatewayResult(
                value=await legacy(),
                outcome=OperationOutcome.SUCCEEDED,
                safe_summary=safe_summary,
            )

        entry = self._entry()
        request = OperationRequestFactory.create(
            capability=entry.capability,
            op=entry.op,
            arguments=dict(arguments),
        )
        captured = _CapturedReadAdapter(legacy=legacy, safe_summary=safe_summary)
        disposition = await OperationGateway(
            descriptors=self.descriptors,
            gates=BuiltinGatewayGateResolver(),
        ).invoke(request, captured)
        if disposition.outcome is OperationOutcome.SUCCEEDED and captured.completed:
            return BuiltinGatewayResult(
                value=captured.value,
                outcome=disposition.outcome,
                safe_summary=disposition.agent_summary,
            )
        return BuiltinGatewayResult(
            value=None,
            outcome=disposition.outcome,
            safe_summary=disposition.agent_summary,
        )


@dataclass
class _CapturedReadAdapter(Generic[_T]):
    """Gateway read adapter which retains, but never emits, legacy result bytes."""

    legacy: Callable[[], Awaitable[_T]]
    safe_summary: str
    value: _T | None = None
    completed: bool = False

    async def execute_read(self, request: OperationRequest) -> OperationRawResult:
        del request
        self.value = await self.legacy()
        self.completed = True
        return OperationRawResult(safe_summary=self.safe_summary)

    async def build_proposal(self, request: OperationRequest) -> ProposedEffect:
        del request
        raise BuiltinOperationUnavailable(
            "This built-in cannot construct an external effect proposal."
        )


def require_builtin_descriptor(
    tool_name: str,
    *,
    catalog: BuiltinOperationCatalog = DEFAULT_BUILTIN_OPERATION_CATALOG,
    descriptors: OperationDescriptorRegistry = DEFAULT_OPERATION_DESCRIPTORS,
) -> BuiltinOperationCatalogEntry:
    """Architecture-gate helper used by changed callable construction and tests."""

    return BuiltinOperationAdapter(
        tool_name=tool_name,
        catalog=catalog,
        descriptors=descriptors,
    ).entry()


__all__ = (
    "BuiltinGatewayGateResolver",
    "BuiltinGatewayResult",
    "BuiltinOperationAdapter",
    "BuiltinOperationDescriptorError",
    "BuiltinOperationUnavailable",
    "require_builtin_descriptor",
)
