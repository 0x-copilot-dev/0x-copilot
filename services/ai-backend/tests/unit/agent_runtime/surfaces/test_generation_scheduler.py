"""Unit tests for :class:`SurfaceGenerationScheduler` + the projector seam (PRD-07).

Covers AC5 (per-run cap), AC6 (``SURFACE_SPEC_MODEL`` unset ⇒ zero scheduling),
the success path (put → emit), the failure path (record_failure → no emit), and
that the projector schedules only on a ladder miss.
"""

from __future__ import annotations

from collections.abc import Coroutine
from typing import Any

from agent_runtime.capabilities.surfaces.generator import (
    GenFailure,
    GenToolDescriptor,
    SurfaceGenerationScheduler,
    build_surface_generation_scheduler,
)
from agent_runtime.capabilities.surfaces.projector import SurfaceProjector
from agent_runtime.capabilities.surfaces.shape_hash import output_shape_hash
from agent_runtime.capabilities.surfaces.spec_models import (
    SurfaceSpec,
    validate_surface_spec,
)
from agent_runtime.capabilities.surfaces.store import InMemorySurfaceSpecStore, SpecKey

_DESCRIPTOR = GenToolDescriptor(name="get_thing")


def _spec() -> SurfaceSpec:
    return validate_surface_spec(
        {
            "spec_version": 1,
            "archetype": "record",
            "source": {"server": "customsvc", "tool": "get_thing"},
            "title_path": "thing.name",
        }
    )


class FakeGenerator:
    def __init__(self, result: object, *, skill_version: int = 1) -> None:
        self._result = result
        self.skill_version = skill_version
        self.calls = 0

    async def generate(
        self, *, server: str, tool_descriptor: object, sample_output: object
    ) -> object:
        self.calls += 1
        return self._result


class _Harness:
    def __init__(self, result: object, *, max_per_run: int = 5) -> None:
        self.store = InMemorySurfaceSpecStore()
        self.scheduled: list[Coroutine[Any, Any, None]] = []
        self.emitted: list[dict[str, object]] = []
        self.generator = FakeGenerator(result)

        async def _emit(payload):
            self.emitted.append(dict(payload))

        self.scheduler = SurfaceGenerationScheduler(
            generator=self.generator,  # type: ignore[arg-type]
            store=self.store,
            emit=_emit,
            model_id="fake-nano",
            schedule=self.scheduled.append,
            max_per_run=max_per_run,
        )

    def schedule(self, output: dict[str, object]) -> None:
        self.scheduler.maybe_schedule(
            server="customsvc",
            tool="get_thing",
            tool_descriptor=_DESCRIPTOR,
            output=output,
            surface_uri="record://customsvc/get_thing/1",
        )

    def close_pending(self) -> None:
        """Discard collected-but-unrun coroutines (avoids 'never awaited')."""

        for coro in self.scheduled:
            coro.close()

    def key_for(self, output: dict[str, object]) -> SpecKey:
        return SpecKey.build(
            server="customsvc",
            tool="get_thing",
            output_shape_hash=output_shape_hash(output),
            skill_version=1,
        )


class TestSchedulerSuccess:
    async def test_success_puts_spec_and_emits_event(self) -> None:
        harness = _Harness(_spec())
        output = {"thing": {"id": "t-1", "name": "Widget"}}
        harness.schedule(output)

        assert len(harness.scheduled) == 1
        await harness.scheduled[0]

        assert harness.store.get_stored(harness.key_for(output)) is not None
        assert harness.store.get(server="customsvc", tool="get_thing") == _spec()
        assert len(harness.emitted) == 1
        payload = harness.emitted[0]
        assert payload["surface_uri"] == "record://customsvc/get_thing/1"
        assert payload["archetype"] == "record"
        assert payload["generator_model"] == "fake-nano"
        assert payload["skill_version"] == "1"
        assert payload["spec"]["title_path"] == "thing.name"


class TestSchedulerFailure:
    async def test_genfailure_records_and_emits_nothing(self) -> None:
        harness = _Harness(
            GenFailure(reason="lint failed", raw_output="{}", attempts=2)
        )
        output = {"thing": {"id": "t-1", "name": "Widget"}}
        harness.schedule(output)
        await harness.scheduled[0]

        assert harness.store.has_failure(harness.key_for(output)) is True
        assert harness.store.get(server="customsvc", tool="get_thing") is None
        assert harness.emitted == []


class TestSchedulerCapAndDedup:
    def test_sixth_distinct_miss_is_not_scheduled(self) -> None:
        harness = _Harness(_spec(), max_per_run=5)
        for i in range(6):
            harness.schedule({f"field{i}": i})
        assert len(harness.scheduled) == 5
        harness.close_pending()

    def test_same_shape_dedupes_to_one(self) -> None:
        harness = _Harness(_spec())
        output = {"thing": {"id": "t-1", "name": "Widget"}}
        harness.schedule(output)
        harness.schedule(output)
        harness.schedule({"thing": {"id": "t-2", "name": "Other"}})  # same shape
        assert len(harness.scheduled) == 1
        harness.close_pending()

    def test_recorded_failure_is_not_rescheduled(self) -> None:
        harness = _Harness(_spec())
        output = {"thing": {"id": "t-1", "name": "Widget"}}
        harness.store.record_failure(harness.key_for(output), "prior", "{}")
        harness.schedule(output)
        assert harness.scheduled == []


class TestProjectorSeam:
    def test_no_scheduler_never_schedules(self) -> None:
        # Byte-compatible: resolve works, nothing is scheduled.
        envelope = SurfaceProjector().resolve(
            "customsvc", "do_thing", {"widget": {"id": "w-1"}}
        )
        assert envelope is not None
        assert envelope.state.spec is None

    def test_schedules_on_miss_only(self) -> None:
        calls: list[tuple[str, str]] = []

        class _RecordingScheduler:
            def maybe_schedule(
                self, *, server, tool, tool_descriptor, output, surface_uri
            ):
                calls.append((server, tool))

        projector = SurfaceProjector(scheduler=_RecordingScheduler())
        # Builtin hit ⇒ no scheduling.
        projector.resolve(
            "linear",
            "get_issue",
            {"issue": {"id": "1", "title": "t", "url": "https://x"}},
        )
        assert calls == []
        # Miss ⇒ schedule once.
        projector.resolve("customsvc", "do_thing", {"widget": {"id": "w-1"}})
        assert calls == [("customsvc", "do_thing")]


class TestSchedulerFactory:
    def test_disabled_when_model_unset(self) -> None:
        store = InMemorySurfaceSpecStore()

        async def _emit(payload):  # pragma: no cover - never called
            return None

        assert (
            build_surface_generation_scheduler(store=store, emit=_emit, environ={})
            is None
        )
        assert (
            build_surface_generation_scheduler(
                store=store, emit=_emit, environ={"SURFACE_SPEC_MODEL": "  "}
            )
            is None
        )

    def test_enabled_with_injected_completion(self) -> None:
        store = InMemorySurfaceSpecStore()

        async def _emit(payload):  # pragma: no cover - not exercised here
            return None

        class _StubCompletion:
            async def complete(self, *, system, user):  # pragma: no cover
                raise AssertionError("not called")

        scheduler = build_surface_generation_scheduler(
            store=store,
            emit=_emit,
            environ={"SURFACE_SPEC_MODEL": "anthropic:claude-haiku-4-5"},
            completion=_StubCompletion(),
        )
        assert isinstance(scheduler, SurfaceGenerationScheduler)

    def test_max_per_run_from_env(self) -> None:
        assert SurfaceGenerationScheduler.max_per_run_from_env({}) == 5
        assert (
            SurfaceGenerationScheduler.max_per_run_from_env(
                {"SURFACE_SPEC_MAX_GEN_PER_RUN": "3"}
            )
            == 3
        )
        assert (
            SurfaceGenerationScheduler.max_per_run_from_env(
                {"SURFACE_SPEC_MAX_GEN_PER_RUN": "nonsense"}
            )
            == 5
        )
