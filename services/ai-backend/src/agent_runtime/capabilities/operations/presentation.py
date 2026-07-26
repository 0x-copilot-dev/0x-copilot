"""Default v2 presentation adapter for transport-neutral operation outcomes.

The universal operation gateway calls this adapter only after a provider
adapter has durably stored a completed read.  It preserves the existing v2
ledger and surface projection semantics while keeping provider packages free of
``WorkLedgerEmitter`` and ``SurfaceProjector`` imports.
"""

from __future__ import annotations

from agent_runtime.capabilities.operations.contracts import (
    OperationPresentationOutcome,
)
from agent_runtime.capabilities.surfaces.generator import (
    GenToolDescriptor,
    SurfaceGenerationScheduler,
)
from agent_runtime.capabilities.surfaces.projector import SurfaceProjector
from agent_runtime.surfaces_v2.emitter import WorkLedgerEmitter


class SurfaceLedgerOperationOutcomePresenter:
    """Project a read outcome through the existing v2 ledger/surface ladder."""

    async def present(self, outcome: OperationPresentationOutcome) -> None:
        """Emit exactly the legacy-compatible read/surface facts when bound.

        The run-scoped emitter remains the source of truth for the event
        sequence.  The operation contract is deliberately not MCP-shaped:
        ``capability`` and ``op`` are the generic descriptor identity.
        """

        emitter = WorkLedgerEmitter.active()
        if emitter is None:
            return
        scheduler = SurfaceGenerationScheduler.active()
        projector = (
            SurfaceProjector(store=scheduler.store, scheduler=scheduler)
            if scheduler is not None
            else SurfaceProjector()
        )
        envelope = projector.resolve(
            outcome.capability,
            outcome.op,
            outcome.output,
            call_id=outcome.operation_id,
            tool_descriptor=GenToolDescriptor(name=outcome.op),
        )
        await emitter.on_tool_result(
            server_name=outcome.capability,
            tool_name=outcome.op,
            call_id=outcome.operation_id,
            output=outcome.output,
            surface=(
                envelope.model_dump(mode="json", exclude_none=True)
                if envelope is not None
                else None
            ),
            surface_uri=envelope.surface_uri if envelope is not None else None,
            latency_ms=outcome.latency_ms,
        )


__all__ = ("SurfaceLedgerOperationOutcomePresenter",)
