"""Runtime transport adapter for the pure universal-effect ledger.

``agent_runtime.effects`` only knows how to append and replay structural stage
facts.  This adapter binds that port to the existing append-only runtime event
store, preserving the one ordered event stream used by SSE and receipts.  It
does not resolve proposal content, make a claim, or dispatch an executor.

Semantic idempotency is deliberately delegated to ``EventStorePort``: one
stable event id is derived from ``(run_id, idempotency_key)`` and the request
fingerprint is retained as safe metadata.  All durable adapters already return
the original envelope on an identical retry and reject a changed body for the
same id.  There is therefore no process-local idempotency map to lose on a
restart.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import hashlib

from agent_runtime.api.events import RuntimeEventProducer
from agent_runtime.api.ledger_seal import LedgerAmendment, LedgerAmendmentReason
from agent_runtime.effects.contracts import EffectStageScope
from agent_runtime.effects.errors import EffectStageIdempotencyConflict
from agent_runtime.effects.ports import StructuralEvent
from agent_runtime.execution.contracts import StreamEventSource
from agent_runtime.persistence.ports import RuntimeEventIdempotencyConflict
from agent_runtime.surfaces_v2.ledger_ids import LedgerIdCodec
from agent_runtime.surfaces_v2.ledger_models import (
    LedgerEventType,
    WorkLedgerVocabulary,
)
from runtime_api.schemas import RunRecord, RuntimeApiEventType

_EFFECT_EVENT_TYPES = frozenset(
    {
        LedgerEventType.EFFECT_STAGED.value,
        LedgerEventType.EFFECT_PROJECTION_BOUND.value,
        LedgerEventType.EFFECT_REVISED.value,
        LedgerEventType.EFFECT_DECISION_RECORDED.value,
        LedgerEventType.EFFECT_CLAIMED.value,
        LedgerEventType.EFFECT_APPLIED.value,
        LedgerEventType.EFFECT_INDETERMINATE.value,
        LedgerEventType.EFFECT_RECONCILED.value,
    }
)
_FINGERPRINT_METADATA_KEY = "effect_stage_request_fingerprint"


@dataclass(frozen=True)
class RuntimeEffectLedger:
    """Run-bound implementation of ``EffectStageLedgerPort``.

    The composition root supplies a trusted ``RunRecord`` and owner reference.
    A caller cannot use this adapter to append to another run or silently change
    the owner embedded in an effect-stage fold.
    """

    event_producer: RuntimeEventProducer
    run: RunRecord
    owner_ref: str
    append_metadata: Mapping[str, object] | None = None

    async def list_stage_events(
        self,
        *,
        scope: EffectStageScope,
        stage_id: str,
    ) -> Sequence[StructuralEvent]:
        """Return only this stage's canonical effect events in stream order."""

        self._assert_scope(scope)
        envelopes = await self.event_producer.event_store.list_events_after(
            org_id=self.run.org_id,
            run_id=self.run.run_id,
            after_sequence=0,
        )
        return tuple(
            StructuralEvent(
                run_id=envelope.run_id,
                ledger_id=LedgerIdCodec.format(envelope.run_id, envelope.sequence_no),
                sequence_no=envelope.sequence_no,
                event_type=envelope.event_type.value,
                payload=dict(envelope.payload),
                created_at=envelope.created_at.isoformat(),
            )
            for envelope in envelopes
            if (
                envelope.event_type.value in _EFFECT_EVENT_TYPES
                and envelope.payload.get("stage_id") == stage_id
            )
        )

    async def append_stage_event(
        self,
        *,
        scope: EffectStageScope,
        event_type: str,
        payload: Mapping[str, object],
        idempotency_key: str,
        request_fingerprint: str,
    ) -> StructuralEvent:
        """Append one schema-checked effect fact or return its exact replay.

        The event id binds the semantic mutation key.  The fingerprint makes a
        changed request fail closed even if its visible ledger payload happened
        to be identical.  Neither the idempotency key nor any proposal bytes are
        exposed through the user-visible event payload.
        """

        self._assert_scope(scope)
        if event_type not in _EFFECT_EVENT_TYPES:
            raise ValueError("runtime effect ledger only accepts effect events")
        try:
            validated = WorkLedgerVocabulary.validate_payload(event_type, payload)
            safe_payload = validated.model_dump(
                mode="json", by_alias=True, exclude_none=True
            )
            envelope = await self.event_producer.append_api_event(
                run=self.run,
                source=StreamEventSource.RUNTIME,
                event_type=RuntimeApiEventType(event_type),
                payload=safe_payload,
                metadata={
                    **(dict(self.append_metadata) if self.append_metadata else {}),
                    _FINGERPRINT_METADATA_KEY: request_fingerprint,
                },
                # ``effect.reconciled`` is the one intrinsically post-hoc fact
                # in this vocabulary: an indeterminate write's true disposition
                # cannot be known while its run is still open, so D12 repair
                # settles it long after the seal. Every other effect event is
                # causal and stays inside the prefix. See ``ledger_seal``.
                amendment=(
                    LedgerAmendment(
                        reason=LedgerAmendmentReason.RECONCILIATION,
                        amends=LedgerIdCodec.format(
                            self.run.run_id, self.run.latest_sequence_no
                        ),
                    )
                    if event_type == LedgerEventType.EFFECT_RECONCILED.value
                    else None
                ),
                event_id=self._event_id(idempotency_key),
            )
        except RuntimeEventIdempotencyConflict as error:
            raise EffectStageIdempotencyConflict() from error

        return StructuralEvent(
            run_id=envelope.run_id,
            ledger_id=LedgerIdCodec.format(envelope.run_id, envelope.sequence_no),
            sequence_no=envelope.sequence_no,
            event_type=envelope.event_type.value,
            payload=dict(envelope.payload),
            created_at=envelope.created_at.isoformat(),
        )

    def _assert_scope(self, scope: EffectStageScope) -> None:
        if scope.run_id != self.run.run_id or scope.owner_ref != self.owner_ref:
            raise ValueError("effect stage scope does not match the bound runtime run")

    def _event_id(self, idempotency_key: str) -> str:
        """Return a stable opaque transport id without exposing the caller key."""

        material = f"{self.run.run_id}\x00{idempotency_key}".encode("utf-8")
        return f"effevt_{hashlib.sha256(material).hexdigest()}"


__all__ = ["RuntimeEffectLedger"]
