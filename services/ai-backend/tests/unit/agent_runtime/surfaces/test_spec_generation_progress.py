"""``surface_spec_requested`` — the progress half of surface-spec generation.

Shaping a Studio surface costs a SECOND model call, awaited to completion inside
:meth:`SurfaceGenerationScheduler._generate`. Until this event existed the
runtime emitted nothing until that call returned, so a client had no state to
tie progress to and the user read dead air for the whole generation.

What these tests pin, in the order the properties matter:

* the signal is emitted **before** the model call, not alongside or after it —
  a "started" event that lands with the result is not progress;
* it carries exactly the frozen two-key payload the client is written against;
* it **fails open**. A progress signal must never be able to fail a run, so an
  emit that raises leaves generation, storage and the terminal
  ``surface_spec_generated`` upgrade completely unaffected. This is not
  hypothetical: the generation task outlives its run by design, and a late emit
  meets a closed SSE stream and a sealed ledger.

Ordering is asserted against a single shared timeline that both the emit
closure and the fake generator append to. Comparing two independent counters
would pass on an emit that raced the model call.
"""

from __future__ import annotations

from collections.abc import Coroutine, Mapping
from typing import Any

import pytest

from agent_runtime.capabilities.surfaces.generator import (
    GenFailure,
    GenToolDescriptor,
    SurfaceGenerationScheduler,
)
from agent_runtime.capabilities.surfaces.spec_models import (
    SurfaceSpec,
    validate_surface_spec,
)
from agent_runtime.capabilities.surfaces.store import InMemorySurfaceSpecStore, SpecKey
from agent_runtime.capabilities.surfaces.shape_hash import output_shape_hash
from runtime_api.schemas import RuntimeApiEventType

_SERVER = "customsvc"
_TOOL = "get_thing"
_DESCRIPTOR = GenToolDescriptor(name=_TOOL)
_MODEL_ID = "fake-nano"
_SURFACE_URI = "record://customsvc/get_thing/1"
_OUTPUT: dict[str, object] = {"thing": {"id": "t-1", "name": "Widget"}}
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


class ProgressHarnessMixin:
    """A scheduler whose emit and model call write to ONE ordered timeline."""

    class TimelineGenerator:
        """Stands in for :class:`SurfaceSpecGenerator`, recording when it ran."""

        def __init__(
            self, timeline: list[str], result: object, *, skill_version: int = 1
        ) -> None:
            self._timeline = timeline
            self._result = result
            self.skill_version = skill_version
            self.calls = 0

        async def generate(
            self, *, server: str, tool_descriptor: object, sample_output: object
        ) -> object:
            self.calls += 1
            self._timeline.append("model_call")
            return self._result

    class Harness:
        def __init__(
            self,
            result: object,
            *,
            raise_on: RuntimeApiEventType | None = None,
            model_id: str = _MODEL_ID,
        ) -> None:
            self.timeline: list[str] = []
            self.emitted: list[tuple[RuntimeApiEventType, dict[str, object]]] = []
            self.store = InMemorySurfaceSpecStore()
            self.scheduled: list[Coroutine[Any, Any, None]] = []
            self.generator = ProgressHarnessMixin.TimelineGenerator(
                self.timeline, result
            )

            async def _emit(
                event_type: RuntimeApiEventType, payload: Mapping[str, object]
            ) -> None:
                self.timeline.append(f"emit:{event_type.value}")
                if event_type is raise_on:
                    raise RuntimeError("the SSE stream for this run is closed")
                self.emitted.append((event_type, dict(payload)))

            self.scheduler = SurfaceGenerationScheduler(
                generator=self.generator,  # type: ignore[arg-type]
                store=self.store,
                emit=_emit,
                model_id=model_id,
                schedule=self.scheduled.append,
            )

        def schedule(self) -> None:
            self.scheduler.maybe_schedule(
                server=_SERVER,
                tool=_TOOL,
                tool_descriptor=_DESCRIPTOR,
                output=_OUTPUT,
                surface_uri=_SURFACE_URI,
            )

        async def drain(self) -> None:
            for coro in self.scheduled:
                await coro
            self.scheduled.clear()

        def payloads(self, event_type: RuntimeApiEventType) -> list[dict[str, object]]:
            return [
                payload for emitted, payload in self.emitted if emitted is event_type
            ]

        @staticmethod
        def key() -> SpecKey:
            return SpecKey.build(
                server=_SERVER,
                tool=_TOOL,
                output_shape_hash=output_shape_hash(_OUTPUT),
                skill_version=1,
            )


class TestTheSignalPrecedesTheModelCall(ProgressHarnessMixin):
    async def test_requested_is_emitted_before_generation_runs(self) -> None:
        harness = self.Harness(_spec())
        harness.schedule()
        await harness.drain()

        assert harness.timeline == [
            f"emit:{_REQUESTED.value}",
            "model_call",
            f"emit:{_GENERATED.value}",
        ]

    async def test_scheduling_alone_emits_nothing(self) -> None:
        # The signal belongs to the scheduled TASK, never to scheduling. If it
        # moved into ``maybe_schedule`` it would put an await on the tool-call
        # path — the one thing refinement is architected never to do.
        harness = self.Harness(_spec())
        harness.schedule()

        assert len(harness.scheduled) == 1
        assert harness.timeline == []

        await harness.drain()

    async def test_a_run_that_never_schedules_emits_nothing(self) -> None:
        # "Nothing may depend on the new event arriving." A shape already known
        # to have failed is not re-attempted, so no progress is announced for a
        # generation that never happens.
        harness = self.Harness(_spec())
        harness.store.record_failure(harness.key(), "prior", "{}")
        harness.schedule()

        assert harness.scheduled == []
        assert harness.emitted == []


class TestTheContractPayload(ProgressHarnessMixin):
    async def test_the_payload_is_exactly_surface_id_and_model_id(self) -> None:
        harness = self.Harness(_spec())
        harness.schedule()
        await harness.drain()

        assert harness.payloads(_REQUESTED) == [
            {"surface_id": _SURFACE_URI, "model_id": _MODEL_ID}
        ]

    async def test_an_unknown_model_id_is_null_not_an_empty_string(self) -> None:
        # The contract types both fields ``string|null``. An empty string would
        # read to a client as a model actually named "", which is a fact the
        # runtime does not have.
        harness = self.Harness(_spec(), model_id="")
        harness.schedule()
        await harness.drain()

        assert harness.payloads(_REQUESTED) == [
            {"surface_id": _SURFACE_URI, "model_id": None}
        ]


class TestTheSignalFailsOpen(ProgressHarnessMixin):
    async def test_a_raising_emit_does_not_abort_generation(self) -> None:
        harness = self.Harness(_spec(), raise_on=_REQUESTED)
        harness.schedule()
        await harness.drain()

        # The signal was attempted and did raise — asserted, because "generation
        # succeeded" is also what a build that never emits looks like.
        assert harness.timeline[0] == f"emit:{_REQUESTED.value}"
        assert harness.payloads(_REQUESTED) == []
        # Everything downstream of the failed signal happened anyway: the model
        # ran, the spec was stored, and the terminal upgrade reached the client.
        assert harness.generator.calls == 1
        assert harness.store.get(server=_SERVER, tool=_TOOL) == _spec()
        generated = harness.payloads(_GENERATED)
        assert len(generated) == 1
        assert generated[0]["surface_uri"] == _SURFACE_URI

    async def test_a_raising_emit_is_logged_not_silently_dropped(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        # Fail-open is not fail-silent: a progress signal that never lands is
        # invisible from the client side, so the log line is the only evidence.
        harness = self.Harness(_spec(), raise_on=_REQUESTED)
        with caplog.at_level("WARNING"):
            harness.schedule()
            await harness.drain()

        assert any(
            "emit_requested_failed" in record.getMessage()
            and _SURFACE_URI in record.getMessage()
            for record in caplog.records
        )

    async def test_a_failed_generation_still_announced_the_attempt(self) -> None:
        # The signal describes the attempt, not its outcome. A GenFailure
        # records the failure and emits no upgrade, but the call really did
        # start, so the request stands alone with no terminal after it.
        #
        # THAT ASYMMETRY IS DELIBERATE AND THE CLIENT IS BUILT FOR IT. Because
        # this exit (and the raise above it) never emit `surface_spec_generated`,
        # an open generation can outlive its own model call — so the frame draws
        # the skeleton on `tier == "pending"` ALONE and lets the signal only
        # enrich its copy (`TcSurfaceFrame.tsx`, pinned by "never masks an
        # already-rendered surface with a stale signal"). If a future change
        # makes the client require this event to close, close it here first.
        harness = self.Harness(
            GenFailure(reason="lint failed", raw_output="{}", attempts=2)
        )
        harness.schedule()
        await harness.drain()

        assert harness.store.has_failure(harness.key()) is True
        assert len(harness.payloads(_REQUESTED)) == 1
        assert harness.payloads(_GENERATED) == []
