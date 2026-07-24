"""PRD-A3 D4 — handler wiring for the Work Ledger emitter.

The handler builds a run-scoped :class:`WorkLedgerEmitter` only when
``SURFACES_V2`` is on (flag-off ⇒ ``None`` ⇒ no binding ⇒ zero v2 events, the
byte-identical posture whose true gate is the untouched
``test_fake_model_run_stream`` keystone). When on, the emitter's ``EmitFn``
maps a raw ledger value to the wire ``RuntimeApiEventType`` and appends through
the same event producer everything else uses.
"""

from __future__ import annotations

from agent_runtime.settings import RuntimeSettings
from agent_runtime.surfaces_v2.emitter import WorkLedgerEmitter
from agent_runtime.surfaces_v2.ledger_models import LedgerEventType
from runtime_adapters.in_memory import InMemoryRuntimeApiStore
from runtime_api.schemas import RuntimeApiEventType
from runtime_worker.handlers.run import RuntimeRunHandler

from tests.unit.runtime_worker.test_runtime_worker import _TestHelpers


def _settings(*, surfaces_v2: bool) -> RuntimeSettings:
    # E3 flipped SURFACES_V2 default ON, so flag-off must be requested explicitly
    # (the kill switch) rather than by omission.
    environ = {
        "OPENAI_API_KEY": "sk-test",
        "RUNTIME_DEFAULT_PROVIDER": "openai",
        "RUNTIME_DEFAULT_MODEL": "gpt-5.4-mini",
        "SURFACES_V2": "true" if surfaces_v2 else "false",
    }
    return RuntimeSettings.load(environ=environ)


class SurfacesV2HandlerMixin:
    async def _handler_and_run(
        self, *, surfaces_v2: bool
    ) -> tuple[RuntimeRunHandler, InMemoryRuntimeApiStore, object]:
        store = InMemoryRuntimeApiStore()
        settings = _settings(surfaces_v2=surfaces_v2)
        run_id = await _TestHelpers.create_queued_run(store, settings)
        handler = RuntimeRunHandler(
            persistence=store,
            event_store=store,
            settings=settings,
        )
        run = await store.get_run(org_id="org_123", run_id=run_id)
        return handler, store, run


class TestBuildWorkLedgerEmitter(SurfacesV2HandlerMixin):
    async def test_flag_off_returns_none(self) -> None:
        handler, _store, run = await self._handler_and_run(surfaces_v2=False)
        assert handler._build_work_ledger_emitter(run) is None

    async def test_flag_on_returns_emitter(self) -> None:
        handler, _store, run = await self._handler_and_run(surfaces_v2=True)
        emitter = handler._build_work_ledger_emitter(run)
        assert isinstance(emitter, WorkLedgerEmitter)

    async def test_emit_fn_maps_ledger_value_to_wire_event(self) -> None:
        handler, store, run = await self._handler_and_run(surfaces_v2=True)
        emitter = handler._build_work_ledger_emitter(run)
        assert emitter is not None

        await emitter.emit(
            LedgerEventType.ACTION_CLASSIFIED.value,
            {
                "v": 1,
                "call_id": "c1",
                "connector": "linear",
                "op": "get_issue",
                "class": "unknown",
                "basis": "default",
            },
            None,
        )

        events = list(
            await store.list_events_after(
                org_id="org_123", run_id=run.run_id, after_sequence=0
            )
        )
        v2_events = [
            e for e in events if e.event_type is RuntimeApiEventType.ACTION_CLASSIFIED
        ]
        assert len(v2_events) == 1
        assert v2_events[0].payload["class"] == "unknown"

    async def test_bind_unbind_round_trip_via_handler_emitter(self) -> None:
        handler, _store, run = await self._handler_and_run(surfaces_v2=True)
        emitter = handler._build_work_ledger_emitter(run)
        assert emitter is not None

        token = WorkLedgerEmitter.bind_for_run(emitter)
        try:
            assert WorkLedgerEmitter.active() is emitter
        finally:
            WorkLedgerEmitter.unbind(token)
        assert WorkLedgerEmitter.active() is None
