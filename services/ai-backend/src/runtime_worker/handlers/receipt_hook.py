"""Worker-side run-receipt emission hook (PRD-E1 §3).

The single seam both terminal handlers (``run`` + ``cancel``) route through to
append the run receipt before termination. It builds the runtime-facing
``ReceiptEmitFn`` closure (mapping an A1 ledger event-type *value* onto the wire
enum, exactly as ``_build_work_ledger_emitter`` does) so the pure
``surfaces_v2.receipt`` stays free of any ``runtime_api`` import, then delegates
to the best-effort :class:`ReceiptEmitter`.

Ordering rationale (SDR §7 S6): ``RuntimeSseAdapter.stream`` stops on terminal
run status, so the two receipt events MUST be appended before the terminal
lifecycle event — i.e. this runs immediately BEFORE
``RunTerminationCoordinator.terminate``. Emission is gated on ``SURFACES_V2``;
flag-off it is a no-op (byte-identical to today).
"""

from __future__ import annotations

from collections.abc import Mapping

from agent_runtime.api.events import RuntimeEventProducer
from agent_runtime.api.ports import EventStorePort
from agent_runtime.execution.contracts import StreamEventSource
from agent_runtime.surfaces_v2.receipt import ReceiptEmitter
from runtime_api.schemas import RunRecord
from runtime_api.schemas.common import RuntimeApiEventType


async def emit_receipt_if_enabled(
    *,
    enabled: bool,
    event_producer: RuntimeEventProducer,
    event_store: EventStorePort,
    run: RunRecord,
) -> None:
    """Fold the run's ledger + append the receipt events, when ``enabled``.

    Best-effort: :meth:`ReceiptEmitter.emit_for_run` swallows every exception, so
    a fold/append failure logs and never blocks the caller's termination.
    """

    if not enabled:
        return

    async def _emit(
        event_type_value: str,
        payload: Mapping[str, object],
        summary: str | None,
    ) -> None:
        await event_producer.append_api_event(
            run=run,
            source=StreamEventSource.SYSTEM,
            event_type=RuntimeApiEventType(str(event_type_value)),
            summary=summary,
            payload=dict(payload),
        )

    emitter = ReceiptEmitter(emit=_emit, event_store=event_store)
    await emitter.emit_for_run(run=run)


__all__ = ["emit_receipt_if_enabled"]
