"""PRD-E1 — worker-side receipt emission wiring.

At every terminal path the run handler folds the run's ledger and appends
``surface.created {kind: receipt}`` + ``receipt.emitted`` BEFORE the terminal
lifecycle event (the SSE stream stops on terminal status). Gated on
``SURFACES_V2``: flag-off ⇒ zero receipt events (byte-identical to today, the
true gate being the untouched ``test_fake_model_run_stream`` keystone).
Emission is best-effort — a fold/append failure never blocks termination.
"""

from __future__ import annotations

from agent_runtime.api.run_termination import TerminationReason
from agent_runtime.execution.contracts import StreamEventSource
from agent_runtime.settings import RuntimeSettings
from runtime_adapters.in_memory import InMemoryRuntimeApiStore
from runtime_api.schemas import AgentRunStatus, RuntimeApiEventType
from runtime_worker.handlers.cancel import RuntimeCancelHandler
from runtime_worker.handlers.receipt_hook import emit_receipt_if_enabled
from runtime_worker.handlers.run import RuntimeRunHandler

from tests.unit.runtime_worker.test_runtime_worker import _TestHelpers

_ORG = "org_123"
_USER = "user_123"


def _settings(*, surfaces_v2: bool) -> RuntimeSettings:
    environ = {
        "OPENAI_API_KEY": "sk-test",
        "RUNTIME_DEFAULT_PROVIDER": "openai",
        "RUNTIME_DEFAULT_MODEL": "gpt-5.4-mini",
    }
    if surfaces_v2:
        environ["SURFACES_V2"] = "true"
    return RuntimeSettings.load(environ=environ)


class ReceiptEmissionMixin:
    async def _handler_and_run(
        self, *, surfaces_v2: bool
    ) -> tuple[RuntimeRunHandler, InMemoryRuntimeApiStore, object]:
        store = InMemoryRuntimeApiStore()
        settings = _settings(surfaces_v2=surfaces_v2)
        run_id = await _TestHelpers.create_queued_run(store, settings)
        handler = RuntimeRunHandler(
            persistence=store, event_store=store, settings=settings
        )
        run = await store.get_run(org_id=_ORG, run_id=run_id)
        await self._seed_ledger(handler, run)
        return handler, store, run

    async def _seed_ledger(self, handler: RuntimeRunHandler, run: object) -> None:
        """Append a read + an approved+applied single-artifact write."""

        producer = handler.event_producer
        await producer.append_api_event(
            run=run,
            source=StreamEventSource.SYSTEM,
            event_type=RuntimeApiEventType.READ_EXECUTED,
            summary="auto-ran (read)",
            payload={
                "v": 1,
                "call_id": "c1",
                "connector": "linear",
                "op": "get_issue",
                "latency_ms": 10,
                "payload_ref": "call:c1",
            },
        )
        await producer.append_api_event(
            run=run,
            source=StreamEventSource.SYSTEM,
            event_type=RuntimeApiEventType.WRITE_STAGED,
            payload={
                "v": 1,
                "stage_id": "s1",
                "surface_id": "surf_1",
                "target": {"connector": "linear", "op": "update_issue"},
                "proposal_ref": "draft://abcdef0123456789abcdef0123456789/v1",
            },
        )
        await producer.append_api_event(
            run=run,
            source=StreamEventSource.SYSTEM,
            event_type=RuntimeApiEventType.DECISION_RECORDED,
            payload={
                "v": 1,
                "stage_id": "s1",
                "decision": "approve",
                "scope": {"rev": 1},
                "actor": "user",
            },
        )
        await producer.append_api_event(
            run=run,
            source=StreamEventSource.SYSTEM,
            event_type=RuntimeApiEventType.WRITE_APPLIED,
            payload={"v": 1, "stage_id": "s1", "rev": 1, "result": "applied"},
        )

    @staticmethod
    async def _event_types(store: InMemoryRuntimeApiStore, run: object) -> list[str]:
        events = await store.list_events_after(
            org_id=_ORG, run_id=run.run_id, after_sequence=0
        )
        return [e.event_type.value for e in events]


class TestReceiptEmissionOrdering(ReceiptEmissionMixin):
    async def test_terminal_emits_surface_created_then_receipt_emitted_before_run_completed(
        self,
    ) -> None:
        handler, store, run = await self._handler_and_run(surfaces_v2=True)
        completed = await store.update_run_status(
            run_id=run.run_id, status=AgentRunStatus.COMPLETED
        )
        await handler._emit_receipt_then_terminate(
            run=completed,
            terminal_status=AgentRunStatus.COMPLETED,
            reason=TerminationReason.NORMAL_COMPLETION,
            summary="Run completed",
        )
        types = await self._event_types(store, run)
        sc = types.index("surface.created")
        re = types.index("receipt.emitted")
        rc = types.index("run_completed")
        assert sc < re < rc

    async def test_reemission_upserts_stable_surface_id(self) -> None:
        handler, store, run = await self._handler_and_run(surfaces_v2=True)
        for _ in range(2):
            await emit_receipt_if_enabled(
                enabled=True,
                event_producer=handler.event_producer,
                event_store=store,
                run=run,
            )
        events = await store.list_events_after(
            org_id=_ORG, run_id=run.run_id, after_sequence=0
        )
        emitted = [e for e in events if e.event_type.value == "receipt.emitted"]
        assert len(emitted) == 2
        assert all(
            e.payload["surface_id"] == f"receipt://{run.run_id}" for e in emitted
        )
        created = [
            e
            for e in events
            if e.event_type.value == "surface.created"
            and e.payload.get("kind") == "receipt"
        ]
        assert all(
            e.payload["surface_id"] == f"receipt://{run.run_id}" for e in created
        )


class TestAllTerminalStatusesEmit(ReceiptEmissionMixin):
    async def test_failed_and_timed_out_emit_receipt(self) -> None:
        for status, reason in (
            (AgentRunStatus.FAILED, TerminationReason.NORMAL_COMPLETION),
            (AgentRunStatus.TIMED_OUT, TerminationReason.RUN_TIMEOUT),
        ):
            handler, store, run = await self._handler_and_run(surfaces_v2=True)
            terminal = await store.update_run_status(run_id=run.run_id, status=status)
            await handler._emit_receipt_then_terminate(
                run=terminal,
                terminal_status=status,
                reason=reason,
                summary="done",
            )
            types = await self._event_types(store, run)
            assert "receipt.emitted" in types

    async def test_cancel_handler_emits_receipt(self, monkeypatch) -> None:
        monkeypatch.setenv("SURFACES_V2", "true")
        store = InMemoryRuntimeApiStore()
        settings = _settings(surfaces_v2=True)
        run_id = await _TestHelpers.create_queued_run(store, settings)
        run = await store.get_run(org_id=_ORG, run_id=run_id)
        # Seed a read so the receipt is non-trivial.
        producer = RuntimeCancelHandler(
            persistence=store, event_store=store
        ).event_producer
        await producer.append_api_event(
            run=run,
            source=StreamEventSource.SYSTEM,
            event_type=RuntimeApiEventType.READ_EXECUTED,
            payload={
                "v": 1,
                "call_id": "c1",
                "connector": "linear",
                "op": "get_issue",
                "latency_ms": 10,
                "payload_ref": "call:c1",
            },
        )
        handler = RuntimeCancelHandler(persistence=store, event_store=store)
        from runtime_api.schemas import RuntimeCancelCommand

        await handler.handle(
            RuntimeCancelCommand(
                org_id=_ORG,
                run_id=run_id,
                requested_by_user_id=_USER,
                reason="user_cancel",
            )
        )
        events = await store.list_events_after(
            org_id=_ORG, run_id=run_id, after_sequence=0
        )
        types = [e.event_type.value for e in events]
        assert "receipt.emitted" in types
        rc = types.index("run_cancelled")
        re = types.index("receipt.emitted")
        assert re < rc


class TestFlagOffAndFailSafe(ReceiptEmissionMixin):
    async def test_flag_off_emits_nothing(self) -> None:
        handler, store, run = await self._handler_and_run(surfaces_v2=False)
        completed = await store.update_run_status(
            run_id=run.run_id, status=AgentRunStatus.COMPLETED
        )
        await handler._emit_receipt_then_terminate(
            run=completed,
            terminal_status=AgentRunStatus.COMPLETED,
            reason=TerminationReason.NORMAL_COMPLETION,
            summary="Run completed",
        )
        types = await self._event_types(store, run)
        assert "receipt.emitted" not in types
        assert "surface.created" not in types
        # The terminal event still fired.
        assert "run_completed" in types

    async def test_emitter_exception_never_blocks_termination(self) -> None:
        handler, store, run = await self._handler_and_run(surfaces_v2=True)
        completed = await store.update_run_status(
            run_id=run.run_id, status=AgentRunStatus.COMPLETED
        )

        async def _raise(*_args, **_kwargs):
            raise RuntimeError("store exploded")

        # A read failure inside the emitter is swallowed; terminate still runs.
        store.list_events_after = _raise  # type: ignore[method-assign]
        await handler._emit_receipt_then_terminate(
            run=completed,
            terminal_status=AgentRunStatus.COMPLETED,
            reason=TerminationReason.NORMAL_COMPLETION,
            summary="Run completed",
        )
        # Restore so we can read the store.
        del store.list_events_after  # type: ignore[attr-defined]
        types = await self._event_types(store, run)
        assert "receipt.emitted" not in types
        assert "run_completed" in types
