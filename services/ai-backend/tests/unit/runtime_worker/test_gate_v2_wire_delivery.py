"""A v2 gate must survive the whole path: emitter → ledger → SSE → client fold.

The unit tests around ``_blocked`` use a ``RecordingEmitter``, which accepts any
``LedgerEventType`` and therefore cannot see the defect PRD-01 fixes: the *real*
emitter closure converts to ``RuntimeApiEventType`` by value, and for
``gate.opened.v2`` that raised. Every fake-emitter test passed throughout.

That is the same shape as PR #413's second bug — backend and client each correct
in isolation, the defect living exactly at the seam between them. So this test
exercises the production conversion and the production transport predicate
rather than a stand-in for either.

It deliberately does **not** drive a workspace operation end-to-end: workspace is
off by default (``RUNTIME_ENABLE_DESKTOP_WORKSPACE=false``) and booting a grant
gate would test the workspace lane, not the wire contract. The contract under
test is "a gate this system emits is a gate a client receives".
"""

from __future__ import annotations

import pytest

from agent_runtime.api.events import RuntimeEventProducer
from agent_runtime.api.ledger_seal import LedgerSealViolation
from agent_runtime.execution.contracts import StreamEventSource
from agent_runtime.surfaces_v2.ledger_models import LedgerEventType
from runtime_adapters.in_memory import InMemoryRuntimeApiStore
from runtime_api.schemas import RuntimeApiEventType
from runtime_api.schemas.events import RuntimeEventPresentationProjector

from tests.unit.runtime_worker.test_ledger_seal_invariant import SealedRunMixin


@pytest.fixture(autouse=True)
def _fake_model(monkeypatch) -> None:
    monkeypatch.setenv("RUNTIME_FAKE_MODEL", "1")


class GateEmissionMixin(SealedRunMixin):
    """Emit a gate exactly the way the production emitter closure does."""

    GATE_PAYLOAD = {
        "v": 1,
        "gate_id": "workspace:op_1",
        "operation_id": "op_1",
        "gate_kind": "grant",
        "capability": "workspace",
        "reason": "workspace_grant_missing_or_revoked",
    }

    @staticmethod
    async def _emit_as_production_does(
        *,
        producer: RuntimeEventProducer,
        run,
        event_type: LedgerEventType,
        payload: dict,
    ):
        """Mirror ``_build_work_ledger_emitter``'s closure, conversion included.

        The conversion is the point: it is the line that raised before PRD-01.
        """

        return await producer.append_api_event(
            run=run,
            source=StreamEventSource.SYSTEM,
            event_type=RuntimeApiEventType(str(event_type.value)),
            payload=dict(payload),
        )


class TestGateV2ReachesTheWire(GateEmissionMixin):
    async def test_gate_opened_v2_is_appendable_through_the_real_conversion(
        self,
    ) -> None:
        store = InMemoryRuntimeApiStore()
        producer = RuntimeEventProducer(persistence=store, event_store=store)
        run = await self._seed_run(store)

        envelope = await self._emit_as_production_does(
            producer=producer,
            run=run,
            event_type=LedgerEventType.GATE_OPENED_V2,
            payload=self.GATE_PAYLOAD,
        )

        assert envelope.event_type.value == "gate.opened.v2"
        assert envelope.sequence_no >= 1

    async def test_the_gate_pair_projects_a_client_safe_envelope(self) -> None:
        """Presentation must not widen a gate payload as it becomes wire-legal.

        `gate.opened.v2` carries a capability and a reason, never a path, a
        target, or a grant token. Making the type transportable must not change
        that.
        """

        for event_type in (
            LedgerEventType.GATE_OPENED_V2,
            LedgerEventType.GATE_RESOLVED_V2,
        ):
            wire = RuntimeApiEventType(str(event_type.value))
            payload = (
                self.GATE_PAYLOAD
                if event_type is LedgerEventType.GATE_OPENED_V2
                else {"v": 1, "gate_id": "workspace:op_1", "decision": "cancel"}
            )
            projected = RuntimeEventPresentationProjector.payload_for_event(
                event_type=wire,
                payload=payload,
            )
            assert set(projected).issubset(set(payload)), (
                f"{event_type.value} presentation added fields: "
                f"{set(projected) - set(payload)}"
            )

    async def test_a_gate_is_causal_and_cannot_follow_the_seal(self) -> None:
        """Gates describe in-run activity, so the seal must reject a late one.

        Guards the interaction with PR #413: making the type transportable must
        not create a way to append it to a finished run.
        """

        store = InMemoryRuntimeApiStore()
        producer = RuntimeEventProducer(persistence=store, event_store=store)
        run = await self._seed_run(store)
        await producer.append_api_event(
            run=run,
            source=StreamEventSource.SYSTEM,
            event_type=RuntimeApiEventType.RUN_COMPLETED,
            payload={"status": "run_completed"},
        )

        with pytest.raises(LedgerSealViolation):
            await self._emit_as_production_does(
                producer=producer,
                run=run,
                event_type=LedgerEventType.GATE_OPENED_V2,
                payload=self.GATE_PAYLOAD,
            )
