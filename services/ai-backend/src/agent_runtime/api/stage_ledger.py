"""Transport adapter for the pure :class:`WriteStager` (PRD-D1).

The stager (``agent_runtime.surfaces_v2.staging``) is pure domain and never
imports ``runtime_api``; it emits + reads events through the injected
:class:`~agent_runtime.surfaces_v2.staging.StageLedgerPort`. This adapter is the
one place that binds that port to the transport: it maps a raw
``LedgerEventType`` string to the ``RuntimeApiEventType`` transport enum and
appends via ``RuntimeEventProducer`` (whose projector allow-list re-filters the
payload), and reads a run's events via ``EventStorePort.list_events_after``.

Mirrors how ``GateLedger`` keeps gate payload logic out of the transport while
the emission itself lives in the ``runtime_api``-aware layer.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from agent_runtime.api.events import RuntimeEventProducer
from agent_runtime.execution.contracts import StreamEventSource
from runtime_api.schemas import RuntimeApiEventType


@dataclass(frozen=True)
class RuntimeStageLedger:
    """Concrete ``StageLedgerPort`` over ``RuntimeEventProducer`` (D1)."""

    event_producer: RuntimeEventProducer

    async def emit(
        self,
        *,
        run: object,
        event_type_value: str,
        payload: Mapping[str, object],
        summary: str | None,
    ) -> object:
        """Append one v2 ledger event; return the persisted (projected) envelope."""

        return await self.event_producer.append_api_event(
            run=run,  # type: ignore[arg-type]
            source=StreamEventSource.RUNTIME,
            event_type=RuntimeApiEventType(event_type_value),
            payload=dict(payload),
            summary=summary,
        )

    async def list_events(self, *, org_id: str, run_id: str) -> Sequence[object]:
        """Return every persisted event for a run (ascending ``sequence_no``)."""

        return await self.event_producer.event_store.list_events_after(
            org_id=org_id, run_id=run_id, after_sequence=0
        )


__all__ = ["RuntimeStageLedger"]
