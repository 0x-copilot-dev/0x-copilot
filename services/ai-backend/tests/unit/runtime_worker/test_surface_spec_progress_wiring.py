"""The worker's emit closure really does put ``surface_spec_requested`` on the wire.

The domain tests for the progress signal inject their own ``emit``, so they
prove the scheduler CALLS it — and nothing else. Every dark-feature defect this
subsystem has had lived on the other side of that seam: the credential the
worker never passed, the ledger event the worker converted by a value the wire
enum did not carry. A test that supplies the sink cannot see any of them.

So this drives the REAL seam: a real ``RuntimeRunHandler`` builds the real
scheduler through ``_build_surface_generation_scheduler``, and the assertions
are on the envelopes that reached the event store — the same rows a client
replays.

Three facts, one per failure mode:

* the progress event is appended, under the registered type, carrying the
  projected two-key payload — the append path is where an unregistered type
  dies;
* it is followed by the terminal ``surface_spec_generated`` on the SAME channel,
  so the pair cannot drift apart;
* the v2 Work Ledger hook fires only for the terminal event. The ledger records
  what a run DID, and "a shaping call started" is not a derived view — folding
  the progress signal in would mint a second ``view.derived`` per surface with
  no spec behind it.
"""

from __future__ import annotations

import os
from collections.abc import Mapping

import pytest

from agent_runtime.api.conversation_coordinator import ConversationCoordinator
from agent_runtime.api.events import RuntimeEventProducer
from agent_runtime.api.run_coordinator import RunCoordinator
from agent_runtime.capabilities.surfaces.generator import (
    GenToolDescriptor,
    ShapingCredentials,
    SurfaceGenerationScheduler,
)
from agent_runtime.capabilities.surfaces.spec_models import (
    SurfaceSpec,
    validate_surface_spec,
)
from agent_runtime.execution.models import ModelConfigResolver
from agent_runtime.settings import RuntimeSettings
from agent_runtime.surfaces_v2.emitter import WorkLedgerEmitter
from runtime_adapters.in_memory import InMemoryRuntimeApiStore
from runtime_api.schemas import (
    CreateConversationRequest,
    CreateRunRequest,
    RuntimeActivityKind,
    RuntimeApiEventType,
    RuntimeEventVisibility,
)
from runtime_worker.handlers.run import RuntimeRunHandler

_ORG_ID = "org_surface_progress"
_USER_ID = "user_surface_progress"
_BYOK_KEY = "sk-unit-test-surface-progress-0000000000"
_SERVER = "customsvc"
_TOOL = "get_thing"
_DESCRIPTOR = GenToolDescriptor(name=_TOOL)
_SURFACE_URI = "record://customsvc/get_thing/1"
_OUTPUT: dict[str, object] = {"thing": {"id": "t-1", "name": "Widget"}}
#: Pinned so the shaping model id is not read off the run's provider default.
_SHAPING_MODEL_ID = "anthropic:claude-haiku-4-5"
_REQUESTED = RuntimeApiEventType.SURFACE_SPEC_REQUESTED
_GENERATED = RuntimeApiEventType.SURFACE_SPEC_GENERATED


def _spec() -> SurfaceSpec:
    return validate_surface_spec(
        {
            "spec_version": 1,
            "archetype": "record",
            "source": {"server": _SERVER, "tool": _TOOL},
            "title_path": "thing.name",
        }
    )


class _PoliciesResolver:
    """Backend policy snapshot: a BYOK key so the shaping model can build."""

    async def resolve(self, *, org_id: str, user_id: str) -> dict[str, object]:
        return {"privacy": {}, "provider_keys": {"anthropic": _BYOK_KEY}}


class _FakeGenerator:
    """Stands in for the shaping model so no provider is ever reached."""

    skill_version = 1

    def __init__(self, result: object) -> None:
        self._result = result
        self.calls = 0

    async def generate(
        self, *, server: str, tool_descriptor: object, sample_output: object
    ) -> object:
        self.calls += 1
        return self._result


class _RecordingLedgerEmitter:
    """A ``WorkLedgerEmitter`` stand-in that records which hooks fired."""

    def __init__(self) -> None:
        self.spec_generated_payloads: list[Mapping[str, object]] = []

    async def on_spec_generated(self, *, payload: Mapping[str, object]) -> None:
        self.spec_generated_payloads.append(dict(payload))


class SurfaceProgressWiringMixin:
    """Builds the production scheduler off a real handler and a real open run."""

    @staticmethod
    def _settings() -> RuntimeSettings:
        # ``load`` merges ``services/ai-backend/.env`` BEFORE ``environ``, and
        # that file is gitignored — so without pinning ``env_file`` this test is
        # green on a developer laptop and a different test on every CI runner.
        return RuntimeSettings.load(
            env_file=os.devnull,
            environ={
                "RUNTIME_DEFAULT_PROVIDER": "anthropic",
                "RUNTIME_DEFAULT_MODEL": "claude-haiku-4-5",
            },
        )

    @staticmethod
    def _shaping_env(monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SURFACE_SPEC_MODEL", _SHAPING_MODEL_ID)
        monkeypatch.setattr(
            "agent_runtime.execution.deep_agent_builder.build_chat_model_from_id",
            lambda model_id, *, extra_kwargs=None: object(),
        )

    async def _open_run(
        self, store: InMemoryRuntimeApiStore, settings: RuntimeSettings
    ) -> str:
        """Create a queued run and leave it OPEN.

        Deliberately not driven to completion: the terminal event seals the
        run's causal prefix, and a causal append after the seal raises. The
        generation task really can outlive its run — that is what
        ``_emit_requested``'s fail-open posture is for — but a wiring test
        should observe the ordinary path, not the degraded one.
        """

        resolver = _PoliciesResolver()
        run_coordinator = RunCoordinator(
            persistence=store,
            queue=store,
            event_producer=RuntimeEventProducer(persistence=store, event_store=store),
            settings=settings,
            model_resolver=ModelConfigResolver(settings=settings),
            user_policies_resolver=resolver,
        )
        conversation = await ConversationCoordinator(
            persistence=store,
            settings=settings,
            run_coordinator=run_coordinator,
        ).create_conversation(
            CreateConversationRequest(
                org_id=_ORG_ID, user_id=_USER_ID, assistant_id="assistant_surface"
            )
        )
        response = await run_coordinator.create_run(
            CreateRunRequest(
                conversation_id=conversation.conversation_id,
                org_id=_ORG_ID,
                user_id=_USER_ID,
                user_input="list the rows",
                model={"provider": "anthropic", "model_name": "claude-haiku-4-5"},
            )
        )
        return response.run_id

    async def _generate_once(
        self,
        store: InMemoryRuntimeApiStore,
        settings: RuntimeSettings,
        run_id: str,
        *,
        result: object,
    ) -> _FakeGenerator:
        """Drive one refinement through the handler's own emit closure."""

        handler = RuntimeRunHandler(
            persistence=store,
            event_store=store,
            settings=settings,
            user_policies_resolver=_PoliciesResolver(),
        )
        scheduler = handler._build_surface_generation_scheduler(
            store.runs[run_id],
            credentials=ShapingCredentials(provider_keys={"anthropic": _BYOK_KEY}),
        )
        assert isinstance(scheduler, SurfaceGenerationScheduler)

        generator = _FakeGenerator(result)
        scheduled: list[object] = []
        # Substituting the generator (not the emit) keeps the seam under test —
        # the worker's closure — untouched, while keeping the model out of it.
        scheduler._generator = generator  # type: ignore[assignment]
        scheduler._schedule = scheduled.append  # type: ignore[assignment]
        scheduler.maybe_schedule(
            server=_SERVER,
            tool=_TOOL,
            tool_descriptor=_DESCRIPTOR,
            output=_OUTPUT,
            surface_uri=_SURFACE_URI,
        )
        for coro in scheduled:
            await coro  # type: ignore[misc]
        return generator

    @staticmethod
    def _events(
        store: InMemoryRuntimeApiStore, run_id: str, event_type: RuntimeApiEventType
    ) -> list[object]:
        return [
            event
            for event in store.events_by_run[run_id]
            if event.event_type == event_type
        ]


class TestTheWorkerEmitsTheProgressSignal(SurfaceProgressWiringMixin):
    async def test_the_requested_event_reaches_the_event_store(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._shaping_env(monkeypatch)
        settings = self._settings()
        store = InMemoryRuntimeApiStore()
        run_id = await self._open_run(store, settings)

        await self._generate_once(store, settings, run_id, result=_spec())

        requested = self._events(store, run_id, _REQUESTED)
        assert len(requested) == 1
        assert requested[0].payload == {
            "surface_id": _SURFACE_URI,
            "model_id": _SHAPING_MODEL_ID,
        }
        assert requested[0].summary == "Preparing a view"
        # A progress signal a client never receives is not progress. The SYSTEM
        # source defaults to USER visibility, but "defaults to" is exactly the
        # kind of fact that changes under someone else's refactor.
        assert requested[0].visibility == RuntimeEventVisibility.USER
        assert requested[0].activity_kind == RuntimeActivityKind.EVENT

    async def test_the_pair_lands_in_order_on_one_channel(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._shaping_env(monkeypatch)
        settings = self._settings()
        store = InMemoryRuntimeApiStore()
        run_id = await self._open_run(store, settings)

        await self._generate_once(store, settings, run_id, result=_spec())

        surface_events = [
            event
            for event in store.events_by_run[run_id]
            if event.event_type in {_REQUESTED, _GENERATED}
        ]
        assert [event.event_type for event in surface_events] == [
            _REQUESTED,
            _GENERATED,
        ]
        # Same run, same producer, monotonic sequence — one channel, so a client
        # resuming from ``after_sequence`` cannot receive one without the other.
        assert surface_events[0].sequence_no < surface_events[1].sequence_no


class TestTheLedgerOnlyFoldsTheTerminalEvent(SurfaceProgressWiringMixin):
    async def test_the_progress_signal_is_not_folded_as_a_derived_view(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._shaping_env(monkeypatch)
        settings = self._settings()
        store = InMemoryRuntimeApiStore()
        run_id = await self._open_run(store, settings)

        emitter = _RecordingLedgerEmitter()
        token = WorkLedgerEmitter.bind_for_run(emitter)  # type: ignore[arg-type]
        try:
            await self._generate_once(store, settings, run_id, result=_spec())
        finally:
            WorkLedgerEmitter.unbind(token)

        # Exactly one fold, and it is the terminal event's — identified by a key
        # only that payload carries.
        assert len(emitter.spec_generated_payloads) == 1
        assert emitter.spec_generated_payloads[0]["surface_uri"] == _SURFACE_URI


class TestNothingDependsOnTheSignal(SurfaceProgressWiringMixin):
    async def test_an_append_that_raises_does_not_stop_the_upgrade(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The realistic failure: the generation task outlives its run, so the
        # progress append meets a sealed ledger. The upgrade — the thing the
        # user actually sees — must still land.
        self._shaping_env(monkeypatch)
        settings = self._settings()
        store = InMemoryRuntimeApiStore()
        run_id = await self._open_run(store, settings)

        original = RuntimeEventProducer.append_api_event
        refusals: list[object] = []

        async def refusing_append(self, **kwargs: object) -> object:
            if kwargs.get("event_type") is _REQUESTED:
                refusals.append(kwargs.get("payload"))
                raise RuntimeError("ledger sealed")
            return await original(self, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(
            RuntimeEventProducer, "append_api_event", refusing_append, raising=True
        )

        generator = await self._generate_once(store, settings, run_id, result=_spec())

        # The append really was attempted and really did raise. Without this the
        # test would also pass on a build that stopped emitting the signal.
        assert refusals == [{"surface_id": _SURFACE_URI, "model_id": _SHAPING_MODEL_ID}]
        assert generator.calls == 1
        assert self._events(store, run_id, _REQUESTED) == []
        assert len(self._events(store, run_id, _GENERATED)) == 1
