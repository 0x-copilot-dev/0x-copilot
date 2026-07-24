"""``ShapeRequestCoordinator`` — the PRD-B4 "Suggest a shape" endpoint.

Real ``InMemoryRuntimeApiStore`` + ``RuntimeEventProducer`` (the projector
allow-lists run exactly as in production); a fake ``SpecCompletionPort`` where a
shaping model is needed — never a live model; an injected ``schedule`` seam so the
invited attempt's asyncio task is driven deterministically. Pins:

* a valid request returns 202 + emits ``shape.requested``; draining the task
  emits ``view.derived {shaped}`` → ``shape.resolved {shaped}`` and a metered
  ``usage.recorded {purpose: shape_request}`` event;
* flag-off is 404 and appends nothing (byte-identical);
* wrong-tenant / run-surface mismatch 404; already-shaped 409; a second in-flight
  request 409; no shaping model 422 with nothing ledgered;
* a runner crash resolves ``no_fit`` (never a hung "requested").
"""

from __future__ import annotations

import asyncio
import json

import pytest

from agent_runtime.api.conversation_coordinator import ConversationCoordinator
from agent_runtime.api.events import RuntimeEventProducer
from agent_runtime.api.run_coordinator import RunCoordinator
from agent_runtime.api.shape_request_coordinator import ShapeRequestCoordinator
from agent_runtime.execution.contracts import StreamEventSource
from agent_runtime.execution.models import ModelConfigResolver
from agent_runtime.settings import RuntimeSettings
from runtime_adapters.in_memory import InMemoryRuntimeApiStore
from runtime_api.http.errors import RuntimeApiError
from runtime_api.schemas import (
    CreateConversationRequest,
    CreateRunRequest,
    RunRecord,
    RuntimeApiEventType,
)

_FLAG_ON = {"SURFACES_V2": "true"}

_VALID_CANDIDATE: dict[str, object] = {
    "spec_version": 1,
    "archetype": "record",
    "title_path": "issue.title",
}

_SURFACE_ID = "record://customsrv/custom_tool/x"


class _FakeCompletion:
    def __init__(self, candidate: dict[str, object] | None = None) -> None:
        self._candidate = candidate if candidate is not None else dict(_VALID_CANDIDATE)

    async def complete(self, *, system: str, user: str):
        from agent_runtime.capabilities.surfaces.generator import SpecCompletionResult

        return SpecCompletionResult(
            candidate=dict(self._candidate),
            raw_text=json.dumps(self._candidate),
            model="openai:gpt-5.4-mini",
            input_tokens=100,
            output_tokens=40,
        )


class _RecordingScheduler:
    """Creates real tasks but lets the test drain them deterministically."""

    def __init__(self) -> None:
        self.tasks: list[asyncio.Task] = []

    def __call__(self, coro):
        task = asyncio.ensure_future(coro)
        self.tasks.append(task)
        return task

    async def drain(self) -> None:
        if self.tasks:
            await asyncio.gather(*self.tasks)


class _GatedScheduler(_RecordingScheduler):
    """Holds every scheduled task pending until ``release`` is called."""

    def __init__(self) -> None:
        super().__init__()
        self.gate = asyncio.Event()

    def __call__(self, coro):
        async def _wrapped() -> None:
            await self.gate.wait()
            await coro

        task = asyncio.ensure_future(_wrapped())
        self.tasks.append(task)
        return task

    async def release(self) -> None:
        self.gate.set()
        await self.drain()


class _ExplodingStore:
    """A store whose ``put`` raises — used to force a runner crash."""

    def get(self, *, server: str, tool: str):  # pragma: no cover - unused
        return None

    def get_stored(self, key):  # pragma: no cover - unused
        return None

    def put(self, key, stored) -> None:
        raise RuntimeError("boom")

    def record_failure(self, key, reason, raw_output) -> None:  # pragma: no cover
        pass

    def has_failure(self, key) -> bool:  # pragma: no cover
        return False


class ShapeRequestMixin:
    ORG = "org_123"
    USER = "user_123"

    async def _setup(
        self,
        *,
        environ: dict[str, str] | None = None,
        provider: str = "openai",
        completion: _FakeCompletion | None = None,
        schedule=None,
    ) -> tuple[
        InMemoryRuntimeApiStore,
        RuntimeEventProducer,
        ShapeRequestCoordinator,
        RunRecord,
    ]:
        store = InMemoryRuntimeApiStore()
        settings = RuntimeSettings.load(
            environ={
                "OPENAI_API_KEY": "sk-test",
                "RUNTIME_DEFAULT_PROVIDER": "openai",
                "RUNTIME_DEFAULT_MODEL": "gpt-5.4-mini",
            }
        )
        producer = RuntimeEventProducer(persistence=store, event_store=store)
        run_coordinator = RunCoordinator(
            persistence=store,
            queue=store,
            event_producer=producer,
            settings=settings,
            model_resolver=ModelConfigResolver(settings),
        )
        conv_coordinator = ConversationCoordinator(
            persistence=store, settings=settings, run_coordinator=run_coordinator
        )
        conversation = await conv_coordinator.create_conversation(
            CreateConversationRequest(org_id=self.ORG, user_id=self.USER, title="S")
        )
        run_response = await run_coordinator.create_run(
            CreateRunRequest(
                conversation_id=conversation.conversation_id,
                org_id=self.ORG,
                user_id=self.USER,
                user_input="Read the issue.",
                model={"provider": "openai", "model_name": "gpt-5.4-mini"},
            )
        )
        run = store.runs[run_response.run_id]
        if provider != "openai":
            run = run.model_copy(update={"model_provider": provider})
            store.runs[run_response.run_id] = run
        coordinator = ShapeRequestCoordinator(
            persistence=store,
            event_store=store,
            event_producer=producer,
            completion=completion or _FakeCompletion(),
            environ=environ if environ is not None else dict(_FLAG_ON),
            schedule=schedule,
        )
        return store, producer, coordinator, run

    @staticmethod
    async def _append_generic_surface(
        producer: RuntimeEventProducer,
        run: RunRecord,
        *,
        surface_id: str = _SURFACE_ID,
        connector: str = "customsrv",
        op: str = "custom_tool",
        tier: str = "generic",
        basis: str = "schema",
    ) -> None:
        await producer.append_api_event(
            run=run,
            source=StreamEventSource.SYSTEM,
            event_type=RuntimeApiEventType.SURFACE_CREATED,
            payload={
                "v": 1,
                "surface_id": surface_id,
                "kind": "record",
                "source": {"connector": connector, "op": op},
                "title": "ENG-1 Fix",
                "payload_ref": "call:call_1",
            },
        )
        await producer.append_api_event(
            run=run,
            source=StreamEventSource.SYSTEM,
            event_type=RuntimeApiEventType.VIEW_DERIVED,
            payload={"v": 1, "surface_id": surface_id, "tier": tier, "basis": basis},
        )
        await producer.append_api_event(
            run=run,
            source=StreamEventSource.SYSTEM,
            event_type=RuntimeApiEventType.TOOL_RESULT,
            payload={
                "surface": {
                    "surface_uri": surface_id,
                    "archetype": "record",
                    "state": {"data": {"issue": {"id": "ENG-1", "title": "Fix"}}},
                }
            },
        )

    @staticmethod
    async def _events(store: InMemoryRuntimeApiStore, run: RunRecord) -> list:
        return await store.list_events_after(
            org_id=ShapeRequestMixin.ORG, run_id=run.run_id, after_sequence=0
        )

    @staticmethod
    def _types(events: list) -> list[str]:
        return [e.event_type for e in events]


class TestHappyPath(ShapeRequestMixin):
    async def test_returns_202_and_emits_shape_requested_then_resolved(self) -> None:
        scheduler = _RecordingScheduler()
        store, producer, coordinator, run = await self._setup(schedule=scheduler)
        await self._append_generic_surface(producer, run)

        accepted = await coordinator.request_shape(
            org_id=self.ORG,
            user_id=self.USER,
            run_id=run.run_id,
            surface_id=_SURFACE_ID,
        )
        assert accepted.surface_id == _SURFACE_ID
        assert accepted.status == "requested"

        # shape.requested emitted synchronously (before scheduling).
        types = self._types(await self._events(store, run))
        assert RuntimeApiEventType.SHAPE_REQUESTED in types

        await scheduler.drain()
        events = await self._events(store, run)
        types = self._types(events)
        # Success sequence: view.derived {shaped} → shape.resolved {shaped}.
        assert RuntimeApiEventType.SHAPE_RESOLVED in types
        resolved = [
            e for e in events if e.event_type == RuntimeApiEventType.SHAPE_RESOLVED
        ]
        assert resolved[-1].payload["outcome"] == "shaped"
        derived = [
            e
            for e in events
            if e.event_type == RuntimeApiEventType.VIEW_DERIVED
            and e.payload.get("basis") == "generated"
        ]
        assert derived, "a shaped/generated view.derived must be emitted"
        # Metered with purpose=shape_request + the surface attributed.
        usage = [
            e for e in events if e.event_type == RuntimeApiEventType.USAGE_RECORDED
        ]
        assert usage, "the invited attempt is metered"
        assert usage[-1].payload["purpose"] == "shape_request"
        assert usage[-1].payload["surface_id"] == _SURFACE_ID


class TestGuards(ShapeRequestMixin):
    async def test_flag_off_is_404_and_no_events(self) -> None:
        scheduler = _RecordingScheduler()
        store, producer, coordinator, run = await self._setup(
            environ={}, schedule=scheduler
        )
        await self._append_generic_surface(producer, run)
        before = len(await self._events(store, run))
        with pytest.raises(RuntimeApiError) as exc:
            await coordinator.request_shape(
                org_id=self.ORG,
                user_id=self.USER,
                run_id=run.run_id,
                surface_id=_SURFACE_ID,
            )
        assert exc.value.http_status == 404
        assert scheduler.tasks == []
        assert len(await self._events(store, run)) == before

    async def test_wrong_tenant_is_404(self) -> None:
        _store, producer, coordinator, run = await self._setup()
        await self._append_generic_surface(producer, run)
        with pytest.raises(RuntimeApiError) as exc:
            await coordinator.request_shape(
                org_id=self.ORG,
                user_id="someone_else",
                run_id=run.run_id,
                surface_id=_SURFACE_ID,
            )
        assert exc.value.http_status == 404

    async def test_run_surface_mismatch_is_404(self) -> None:
        _store, producer, coordinator, run = await self._setup()
        await self._append_generic_surface(producer, run)
        with pytest.raises(RuntimeApiError) as exc:
            await coordinator.request_shape(
                org_id=self.ORG,
                user_id=self.USER,
                run_id=run.run_id,
                surface_id="record://ghost/none/y",
            )
        assert exc.value.http_status == 404

    async def test_already_shaped_is_409(self) -> None:
        _store, producer, coordinator, run = await self._setup()
        await self._append_generic_surface(
            producer, run, tier="shaped", basis="registry"
        )
        with pytest.raises(RuntimeApiError) as exc:
            await coordinator.request_shape(
                org_id=self.ORG,
                user_id=self.USER,
                run_id=run.run_id,
                surface_id=_SURFACE_ID,
            )
        assert exc.value.http_status == 409

    async def test_in_flight_is_409(self) -> None:
        scheduler = _GatedScheduler()
        store, producer, coordinator, run = await self._setup(schedule=scheduler)
        await self._append_generic_surface(producer, run)

        first = await coordinator.request_shape(
            org_id=self.ORG,
            user_id=self.USER,
            run_id=run.run_id,
            surface_id=_SURFACE_ID,
        )
        assert first.status == "requested"
        # The task is gated (pending) ⇒ a second request 409s.
        with pytest.raises(RuntimeApiError) as exc:
            await coordinator.request_shape(
                org_id=self.ORG,
                user_id=self.USER,
                run_id=run.run_id,
                surface_id=_SURFACE_ID,
            )
        assert exc.value.http_status == 409
        # Only ONE shape.requested was emitted (the 409 ledgered nothing new).
        requested = [
            e
            for e in await self._events(store, run)
            if e.event_type == RuntimeApiEventType.SHAPE_REQUESTED
        ]
        assert len(requested) == 1
        await scheduler.release()

    async def test_shaping_unavailable_is_422_and_nothing_ledgered(self) -> None:
        scheduler = _RecordingScheduler()
        # Flag on (so the surface loads) but a provider with no cheap default ⇒
        # resolve_model_id is None ⇒ 422 checked BEFORE emitting shape.requested.
        store, producer, coordinator, run = await self._setup(
            provider="openrouter", schedule=scheduler
        )
        await self._append_generic_surface(producer, run)
        before = self._types(await self._events(store, run))
        with pytest.raises(RuntimeApiError) as exc:
            await coordinator.request_shape(
                org_id=self.ORG,
                user_id=self.USER,
                run_id=run.run_id,
                surface_id=_SURFACE_ID,
            )
        assert exc.value.http_status == 422
        after = self._types(await self._events(store, run))
        assert RuntimeApiEventType.SHAPE_REQUESTED not in after
        assert before == after
        assert scheduler.tasks == []


class TestCrash(ShapeRequestMixin):
    async def test_runner_crash_resolves_no_fit(self) -> None:
        # A store whose put raises makes the runner crash after a successful
        # generation; the coordinator must resolve no_fit, never hang "requested".
        scheduler = _RecordingScheduler()
        store, producer, coordinator, run = await self._setup(schedule=scheduler)
        await self._append_generic_surface(producer, run)
        # Force the runner's store.put to raise.
        coordinator._build_runner = _patched_build_runner(  # type: ignore[attr-defined]
            coordinator, _ExplodingStore()
        )

        await coordinator.request_shape(
            org_id=self.ORG,
            user_id=self.USER,
            run_id=run.run_id,
            surface_id=_SURFACE_ID,
        )
        await scheduler.drain()

        events = await self._events(store, run)
        resolved = [
            e for e in events if e.event_type == RuntimeApiEventType.SHAPE_RESOLVED
        ]
        assert resolved, "a crash still resolves the request"
        assert resolved[-1].payload["outcome"] == "no_fit"


def _patched_build_runner(coordinator: ShapeRequestCoordinator, store) -> object:
    """Return a ``_build_runner`` bound method that swaps in a crashing store."""

    original = coordinator._build_runner

    def _build(**kwargs):
        runner = original(**kwargs)
        return runner.__class__(
            generator=runner.generator,
            store=store,
            emit=runner.emit,
            model_id=runner.model_id,
        )

    return _build
