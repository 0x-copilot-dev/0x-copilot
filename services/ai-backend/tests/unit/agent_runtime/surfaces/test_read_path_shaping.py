"""Rung 5 on the read path: the shaping call, wired.

The measured defect these tests exist for, in one payload. A connector whose
real answer is a JSON array arrives from ``langchain-mcp-adapters`` as::

    {"result": [{"type": "text", "text": "[{…}, {…}]", "id": "blk_1"}]}

The projector used to fall back to that wrapper when the decode landed on a
list, and rung 0 then inferred a perfectly valid table of ``ID`` / ``Type`` /
``Text`` over ``items_path: "result"`` — the *adapter's* own fields, with the
entire connector payload in one cell. It looks like it worked. Five of eight
measured MCP payload shapes did this.

Two things had to change and both are asserted here: the floor stops binding the
envelope (deterministic, no model, no credential), and the model is actually
asked about the payloads the floor cannot bind — with the provenance the receipt
lane reads, ``selected`` when the values stay the connector's and ``generated``
when the model wrote them.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping

import pytest

from agent_runtime.capabilities.operations.contracts import (
    OperationPresentationOutcome,
)
from agent_runtime.capabilities.operations.presentation import (
    SurfaceLedgerOperationOutcomePresenter,
)
from agent_runtime.capabilities.surfaces.generator import (
    GenToolDescriptor,
    ShapingCredentials,
    SpecCompletionResult,
    SurfaceSpecGenerator,
)
from agent_runtime.capabilities.surfaces.projector import (
    SurfaceProjector,
)
from agent_runtime.capabilities.surfaces.shape_request import (
    ReadPathShaper,
    ShapedSurfaceBuilder,
    build_read_path_shaper,
)
from agent_runtime.capabilities.surfaces.spec_models import (
    SurfaceArchetype,
    SurfaceDotPath,
    SurfaceSource,
    SurfaceSpecRung,
)
from agent_runtime.surfaces_v2.emitter import WorkLedgerEmitter

_SERVER = "linear"
_TOOL = "list_issues"
_DESCRIPTOR = GenToolDescriptor(name=_TOOL, description="List Linear issues.")

#: What the connector actually returned — a JSON array at the root.
CONNECTOR_ROWS: list[dict[str, object]] = [
    {
        "identifier": "ENG-1421",
        "title": "Fix login redirect loop",
        "state": "In Progress",
    },
    {"identifier": "ENG-1422", "title": "Ship the floor", "state": "Done"},
]

#: The envelope columns the broken floor produced, spelled out so a regression
#: is named rather than merely "not equal to the good answer".
ENVELOPE_LABELS = {"ID", "Type", "Text"}


def array_at_root_output() -> dict[str, object]:
    """The measured wire shape: a JSON array inside one MCP text block."""

    return {
        "result": [{"type": "text", "text": json.dumps(CONNECTOR_ROWS), "id": "blk_1"}]
    }


def prose_output() -> dict[str, object]:
    """A payload with no structure at all — what ``generate`` mode exists for."""

    return {
        "result": [
            {
                "type": "text",
                "text": (
                    "Two incidents are open. PAR-9 is a payment timeout at high "
                    "priority; PAR-11 is a slow dashboard at low priority."
                ),
            }
        ]
    }


def select_answer(items_path: str = "items") -> dict[str, object]:
    """A rendering answer that points INTO the payload — values stay the connector's."""

    return {
        "render": True,
        "archetype": "table",
        "title": "Open issues",
        "binding": {
            "mode": "select",
            "items_path": items_path,
            "columns": [
                {"label": "Issue", "path": "identifier"},
                {"label": "Summary", "path": "title"},
                {"label": "Status", "path": "state"},
            ],
        },
    }


def generate_answer() -> dict[str, object]:
    """A rendering answer whose rows the model wrote itself."""

    return {
        "render": True,
        "archetype": "table",
        "title": "Open incidents",
        "binding": {
            "mode": "generate",
            "rows": [
                {"ref": "PAR-9", "priority": "High"},
                {"ref": "PAR-11", "priority": "Low"},
            ],
            "columns": [
                {"label": "Incident", "path": "ref"},
                {"label": "Priority", "path": "priority"},
            ],
        },
    }


class FakeCompletion:
    """Returns pre-canned shaping answers and counts the calls made."""

    def __init__(self, answers: list[object]) -> None:
        self._answers = list(answers)
        self.prompts: list[tuple[str, str]] = []

    @property
    def calls(self) -> int:
        return len(self.prompts)

    async def complete(self, *, system: str, user: str) -> SpecCompletionResult:
        self.prompts.append((system, user))
        answer = self._answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return SpecCompletionResult(
            candidate=answer,
            raw_text=json.dumps(answer, default=str),
            model="fake-nano",
            input_tokens=80,
            output_tokens=24,
        )


class _RecordingScheduler:
    """The refinement seam, recorded so "one model call per read" is assertable."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def maybe_schedule(
        self,
        *,
        server: str,
        tool: str,
        tool_descriptor: object,
        output: object,
        surface_uri: str,
    ) -> None:
        self.calls.append(surface_uri)

    @property
    def store(self) -> None:
        return None


def shaper_for(answers: list[object]) -> tuple[ReadPathShaper, FakeCompletion]:
    completion = FakeCompletion(answers)
    generator = SurfaceSpecGenerator(completion=completion)
    return ReadPathShaper(generator=generator), completion


class TestTheFloorStopsBindingTheEnvelope:
    """No model, no credential — the half a user with no provider key still gets."""

    def test_a_root_array_no_longer_infers_a_table_over_the_wrapper(self) -> None:
        envelope = SurfaceProjector().resolve(
            _SERVER, _TOOL, array_at_root_output(), call_id="call_1"
        )

        assert envelope is not None
        # The old behaviour: a spec whose columns were the content block's own
        # fields, bound to ``items_path: "result"``. Both halves are gone.
        assert envelope.state.spec is None
        assert envelope.spec_rung is None

    def test_the_decoded_rows_are_what_ships_as_data(self) -> None:
        # ``state.data`` is the value any spec's paths bind against, so it has to
        # be the decoded document — not the wrapper the adapter added.
        envelope = SurfaceProjector().resolve(
            _SERVER, _TOOL, array_at_root_output(), call_id="call_1"
        )

        assert envelope is not None
        assert envelope.state.data == CONNECTOR_ROWS
        assert envelope.archetype is SurfaceArchetype.TABLE

    def test_a_mapping_payload_is_untouched_by_any_of_this(self) -> None:
        # The floor's own behaviour for every payload it could already bind must
        # be byte-identical: rung 0 still answers, still reports ``inferred``.
        envelope = SurfaceProjector().resolve(
            "customsvc", "do_thing", {"widget": {"id": "w-9", "name": "Left rail"}}
        )

        assert envelope is not None
        assert envelope.spec_rung is SurfaceSpecRung.INFERRED
        assert envelope.state.spec is not None


class TestTheModelIsAskedAndItsAnswerBinds:
    """The point of the task: a select answer over the measured payload."""

    async def test_a_select_answer_binds_the_connectors_fields(self) -> None:
        shaper, completion = shaper_for([select_answer()])

        envelope = await SurfaceProjector(shaper=shaper).project(
            _SERVER, _TOOL, array_at_root_output(), call_id="call_1"
        )

        assert envelope is not None
        spec = envelope.state.spec
        assert spec is not None
        assert completion.calls == 1
        labels = [column.label for column in spec.columns or []]
        assert labels == ["Issue", "Summary", "Status"]
        # Named explicitly: the defect was not "wrong labels", it was the
        # adapter's envelope rendered as if it were the connector's data.
        assert not ENVELOPE_LABELS.intersection(labels)
        assert spec.items_path != "result"

    async def test_every_cell_resolves_to_a_value_the_connector_returned(
        self,
    ) -> None:
        # A spec that binds is not the claim; a spec that binds THIS payload is.
        shaper, _ = shaper_for([select_answer()])

        envelope = await SurfaceProjector(shaper=shaper).project(
            _SERVER, _TOOL, array_at_root_output(), call_id="call_1"
        )

        assert envelope is not None
        spec = envelope.state.spec
        assert spec is not None and spec.items_path is not None
        found, rows = SurfaceDotPath.resolve(envelope.state.data, spec.items_path)
        assert found and isinstance(rows, list) and len(rows) == len(CONNECTOR_ROWS)
        cells = [
            [
                SurfaceDotPath.resolve(row, column.path)[1]
                for column in spec.columns or []
            ]
            for row in rows
        ]
        assert cells == [
            ["ENG-1421", "Fix login redirect loop", "In Progress"],
            ["ENG-1422", "Ship the floor", "Done"],
        ]

    async def test_a_select_answer_is_recorded_as_selected_not_generated(self) -> None:
        shaper, _ = shaper_for([select_answer()])

        envelope = await SurfaceProjector(shaper=shaper).project(
            _SERVER, _TOOL, array_at_root_output(), call_id="call_1"
        )

        assert envelope is not None
        assert envelope.spec_rung is SurfaceSpecRung.SELECTED

    async def test_a_generate_answer_ships_the_models_own_rows(self) -> None:
        shaper, _ = shaper_for([generate_answer()])

        envelope = await SurfaceProjector(shaper=shaper).project(
            _SERVER, "summarise", prose_output(), call_id="call_2"
        )

        assert envelope is not None
        assert envelope.spec_rung is SurfaceSpecRung.GENERATED
        spec = envelope.state.spec
        assert spec is not None and spec.items_path is not None
        found, rows = SurfaceDotPath.resolve(envelope.state.data, spec.items_path)
        assert found
        assert rows == [
            {"ref": "PAR-9", "priority": "High"},
            {"ref": "PAR-11", "priority": "Low"},
        ]


class TestTheThreeAnswersEndDifferently:
    """decline / unavailable / render are three outcomes, not two."""

    async def test_a_decline_produces_no_surface_at_all(self) -> None:
        shaper, _ = shaper_for([{"render": False, "reason": "an acknowledgement"}])

        envelope = await SurfaceProjector(shaper=shaper).project(
            _SERVER, "ack", {"result": [{"type": "text", "text": "ok"}]}
        )

        assert envelope is None

    async def test_an_unusable_answer_leaves_the_floor_exactly_as_it_was(self) -> None:
        # Not the same as a decline: nobody answered, so nothing was decided,
        # and deleting the surface on a broken prompt would be a silent outage.
        shaper, _ = shaper_for([{"render": True, "archetype": "wat"}, {"nonsense": 1}])
        floor = SurfaceProjector().resolve(
            _SERVER, _TOOL, array_at_root_output(), call_id="call_1"
        )

        envelope = await SurfaceProjector(shaper=shaper).project(
            _SERVER, _TOOL, array_at_root_output(), call_id="call_1"
        )

        assert envelope is not None and floor is not None
        assert envelope == floor

    async def test_a_provider_error_leaves_the_floor_exactly_as_it_was(self) -> None:
        shaper, _ = shaper_for([RuntimeError("no credential"), RuntimeError("again")])
        floor = SurfaceProjector().resolve(
            _SERVER, _TOOL, array_at_root_output(), call_id="call_1"
        )

        envelope = await SurfaceProjector(shaper=shaper).project(
            _SERVER, _TOOL, array_at_root_output(), call_id="call_1"
        )

        assert envelope is not None and floor is not None
        assert envelope == floor

    async def test_no_shaper_at_all_is_the_floor(self) -> None:
        # The uncredentialed user. ``project`` and ``resolve`` must agree.
        output = array_at_root_output()

        assert await SurfaceProjector().project(
            _SERVER, _TOOL, output, call_id="call_1"
        ) == SurfaceProjector().resolve(_SERVER, _TOOL, output, call_id="call_1")

    async def test_a_generator_that_raises_never_reaches_the_read(self) -> None:
        # ``SurfaceSpecGenerator.shape`` is total by contract, but the seam's
        # promise is stronger than "we believe it is": a display upgrade may not
        # fail a read no matter what breaks underneath it.
        class _Exploding:
            skill_version = 1

            async def shape(self, **_: object) -> object:
                raise RuntimeError("boom")

        shaper = ReadPathShaper(generator=_Exploding())  # type: ignore[arg-type]

        answer = await shaper.shape(
            server=_SERVER,
            tool=_TOOL,
            tool_descriptor=_DESCRIPTOR,
            payload=CONNECTOR_ROWS,
            source=SurfaceSource(server=_SERVER, tool=_TOOL),
        )

        assert answer is None


class TestOneModelCallPerSurface:
    """Cost is real: the two model rungs are mutually exclusive and memoised."""

    async def test_a_bindable_payload_never_reaches_the_shaper(self) -> None:
        shaper, completion = shaper_for([select_answer()])
        scheduler = _RecordingScheduler()

        envelope = await SurfaceProjector(shaper=shaper, scheduler=scheduler).project(
            "customsvc", "do_thing", {"widget": {"id": "w-9"}}
        )

        assert envelope is not None
        assert envelope.spec_rung is SurfaceSpecRung.INFERRED
        assert completion.calls == 0
        # …and it is the refinement seam that was invited instead.
        assert len(scheduler.calls) == 1

    async def test_an_unbindable_payload_never_reaches_the_refinement_seam(
        self,
    ) -> None:
        shaper, completion = shaper_for([select_answer()])
        scheduler = _RecordingScheduler()

        await SurfaceProjector(shaper=shaper, scheduler=scheduler).project(
            _SERVER, _TOOL, array_at_root_output(), call_id="call_1"
        )

        assert completion.calls == 1
        assert scheduler.calls == []

    async def test_the_same_shape_twice_costs_one_call(self) -> None:
        shaper, completion = shaper_for([select_answer()])
        projector = SurfaceProjector(shaper=shaper)

        first = await projector.project(
            _SERVER, _TOOL, array_at_root_output(), call_id="call_1"
        )
        second = await projector.project(
            _SERVER, _TOOL, array_at_root_output(), call_id="call_2"
        )

        assert completion.calls == 1
        assert first is not None and second is not None
        assert first.state.spec == second.state.spec

    async def test_concurrent_reads_of_one_shape_share_the_single_call(self) -> None:
        shaper, completion = shaper_for([select_answer()])
        projector = SurfaceProjector(shaper=shaper)

        results = await asyncio.gather(
            *(
                projector.project(
                    _SERVER, _TOOL, array_at_root_output(), call_id=f"call_{index}"
                )
                for index in range(4)
            )
        )

        assert completion.calls == 1
        assert all(envelope is not None for envelope in results)

    async def test_the_per_run_budget_stops_at_its_cap(self) -> None:
        completion = FakeCompletion([select_answer(), select_answer()])
        shaper = ReadPathShaper(
            generator=SurfaceSpecGenerator(completion=completion), max_per_run=1
        )
        projector = SurfaceProjector(shaper=shaper)

        await projector.project(_SERVER, _TOOL, array_at_root_output())
        # A different shape, so the memo cannot answer it.
        second = await projector.project(
            _SERVER,
            "other",
            {"result": [{"type": "text", "text": json.dumps([{"a": 1}])}]},
        )

        assert completion.calls == 1
        assert second is not None
        assert second.state.spec is None


class TestTheShapedSurfaceBuilder:
    """The translation the shaping contract deliberately refuses to make."""

    def test_a_mapping_payload_is_not_rewrapped(self) -> None:
        # Rewrapping would move every path the model wrote by one segment.
        payload = {"issues": [{"id": "1"}]}

        assert ShapedSurfaceBuilder.bindable(payload) is payload

    def test_a_root_array_gains_exactly_one_addressable_key(self) -> None:
        bound = ShapedSurfaceBuilder.bindable(CONNECTOR_ROWS)

        assert isinstance(bound, Mapping)
        assert list(bound) == ["items"]
        assert bound["items"] is CONNECTOR_ROWS

    async def test_no_model_authored_text_enters_a_selected_spec(self) -> None:
        # The guardrail: a spec has zero side-effectful members and carries no
        # literal the model wrote. ``title`` ("Open issues") must appear nowhere.
        shaper, _ = shaper_for([select_answer()])

        envelope = await SurfaceProjector(shaper=shaper).project(
            _SERVER, _TOOL, array_at_root_output()
        )

        assert envelope is not None and envelope.state.spec is not None
        dumped = json.dumps(envelope.state.spec.model_dump(mode="json"))
        assert "Open issues" not in dumped

    def test_a_record_archetype_gets_fields_not_columns(self) -> None:
        from agent_runtime.capabilities.surfaces.shaping_answer import (
            validate_shaping_answer,
        )

        answer = validate_shaping_answer(
            {
                "render": True,
                "archetype": "record",
                "title": "Deployment",
                "binding": {
                    "mode": "select",
                    "columns": [{"label": "Environment", "path": "environment"}],
                },
            }
        )
        shaped = ShapedSurfaceBuilder.build(
            surface=answer,  # type: ignore[arg-type]
            payload={"environment": "production"},
            source=SurfaceSource(server=_SERVER, tool=_TOOL),
        )

        assert shaped is not None and shaped.spec is not None
        assert shaped.spec.columns is None
        assert [field.label for field in shaped.spec.fields or []] == ["Environment"]


class TestTheLedgerRecordsWhoseValuesThoseAre:
    """Provenance is the reason ``select`` and ``generate`` are two answers."""

    async def _emitted(
        self, *, answers: list[object], output: dict[str, object], tool: str
    ) -> list[tuple[str, dict[str, object]]]:
        rows: list[tuple[str, dict[str, object]]] = []

        async def _emit(
            event_type: str, payload: Mapping[str, object], summary: str | None
        ) -> None:
            rows.append((event_type, dict(payload)))

        shaper, _ = shaper_for(answers)
        emitter_token = WorkLedgerEmitter.bind_for_run(WorkLedgerEmitter(emit=_emit))
        shaper_token = ReadPathShaper.bind_for_run(shaper)
        try:
            await SurfaceLedgerOperationOutcomePresenter().present(
                OperationPresentationOutcome(
                    operation_id="call_1",
                    capability=_SERVER,
                    op=tool,
                    result_ref=f"mcp/{_SERVER}/{tool}/call_1",
                    output=output,
                    latency_ms=12,
                )
            )
        finally:
            ReadPathShaper.unbind(shaper_token)
            WorkLedgerEmitter.unbind(emitter_token)
        return rows

    async def test_a_selected_view_says_selected_on_the_ledger(self) -> None:
        rows = await self._emitted(
            answers=[select_answer()], output=array_at_root_output(), tool=_TOOL
        )

        derived = [payload for name, payload in rows if name == "view.derived"]
        assert len(derived) == 1
        assert derived[0]["tier"] == "shaped"
        assert derived[0]["basis"] == "selected"

    async def test_the_created_surface_carries_the_connectors_columns(self) -> None:
        rows = await self._emitted(
            answers=[select_answer()], output=array_at_root_output(), tool=_TOOL
        )

        created = next(payload for name, payload in rows if name == "surface.created")
        state = created["state"]
        assert isinstance(state, Mapping)
        spec = state["spec"]
        assert isinstance(spec, Mapping)
        assert [column["label"] for column in spec["columns"]] == [
            "Issue",
            "Summary",
            "Status",
        ]

    async def test_a_generated_view_says_generated_on_the_ledger(self) -> None:
        rows = await self._emitted(
            answers=[generate_answer()], output=prose_output(), tool="summarise"
        )

        derived = [payload for name, payload in rows if name == "view.derived"]
        assert derived[0]["basis"] == "generated"

    async def test_a_decline_writes_the_read_but_no_surface(self) -> None:
        rows = await self._emitted(
            answers=[{"render": False}],
            output={"result": [{"type": "text", "text": "ok"}]},
            tool="ack",
        )

        names = [name for name, _ in rows]
        assert "read.executed" in names
        assert "surface.created" not in names
        assert "view.derived" not in names

    async def test_the_adapters_envelope_never_reaches_the_delivered_state(
        self,
    ) -> None:
        # The whole defect in one assertion: the content block's own keys must
        # not appear in what the renderer is handed.
        rows = await self._emitted(
            answers=[select_answer()], output=array_at_root_output(), tool=_TOOL
        )

        created = next(payload for name, payload in rows if name == "surface.created")
        state = created["state"]
        assert isinstance(state, Mapping)
        assert "blk_1" not in json.dumps(state["data"])


class TestShapingIsOffWhenNoModelResolves:
    """Degrade safely: no provider key means the floor, never a broken read."""

    def test_no_resolvable_model_builds_no_shaper(self) -> None:
        # ``SURFACE_SPEC_MODEL`` unset, ``SURFACES_V2`` unset ⇒ the resolver says
        # no, so nothing is constructed and the projector never gets a seam.
        assert build_read_path_shaper(environ={}) is None

    def test_surfaces_v2_on_without_a_provider_still_builds_nothing(self) -> None:
        # Shaping-on-by-default is gated on the run having a BYOK provider; a run
        # with none is honestly off rather than quietly attempting a model.
        assert (
            build_read_path_shaper(environ={"SURFACES_V2": "true"}, run_provider=None)
            is None
        )

    def test_an_injected_completion_needs_only_a_resolvable_model_id(self) -> None:
        shaper = build_read_path_shaper(
            environ={"SURFACE_SPEC_MODEL": "fake-nano"},
            completion=FakeCompletion([select_answer()]),
        )

        assert isinstance(shaper, ReadPathShaper)

    def test_the_per_run_cap_is_read_from_env(self) -> None:
        assert ReadPathShaper.max_per_run_from_env({}) == (
            ReadPathShaper.DEFAULT_MAX_PER_RUN
        )
        assert (
            ReadPathShaper.max_per_run_from_env({"SURFACE_SHAPE_MAX_PER_RUN": "2"}) == 2
        )
        # A junk or negative value falls back rather than disabling the rung by
        # accident — an unreadable budget is not an instruction.
        assert ReadPathShaper.max_per_run_from_env(
            {"SURFACE_SHAPE_MAX_PER_RUN": "-3"}
        ) == (ReadPathShaper.DEFAULT_MAX_PER_RUN)


class TestRungFiveGetsTheRunsCredential:
    """AC13 for the READ path. The refinement builder was pinned; this was not.

    ``extra_kwargs`` is the only channel a BYOK key travels on, and a packaged
    install has no provider key in its process env — so a rung-5 builder that
    drops the credential is a rung that never runs.
    """

    class RecordingFactory:
        def __init__(self) -> None:
            self.model_id: str | None = None
            self.extra_kwargs: Mapping[str, object] | None = None

        def __call__(
            self, model_id: str, *, extra_kwargs: Mapping[str, object] | None = None
        ) -> object:
            self.model_id = model_id
            self.extra_kwargs = extra_kwargs
            return object()

    def _build(
        self,
        monkeypatch: pytest.MonkeyPatch,
        *,
        environ: Mapping[str, str],
        credentials: ShapingCredentials | None,
    ) -> tuple[ReadPathShaper | None, "RecordingFactory"]:
        factory = self.RecordingFactory()
        monkeypatch.setattr(
            "agent_runtime.execution.deep_agent_builder.build_chat_model_from_id",
            factory,
        )
        shaper = build_read_path_shaper(environ=dict(environ), credentials=credentials)
        return (shaper, factory)

    def test_the_byok_key_reaches_the_shaping_model(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        shaper, factory = self._build(
            monkeypatch,
            environ={"SURFACE_SPEC_MODEL": "anthropic:claude-haiku-4-5"},
            credentials=ShapingCredentials(provider_keys={"anthropic": "sk-ant-user"}),
        )

        assert isinstance(shaper, ReadPathShaper)
        assert factory.model_id == "anthropic:claude-haiku-4-5"
        assert factory.extra_kwargs == {"api_key": "sk-ant-user"}

    def test_the_key_follows_the_shaping_provider_not_the_run_provider(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Handing a client the WRONG provider's key is worse than having none:
        # it authenticates as garbage instead of degrading honestly.
        _, factory = self._build(
            monkeypatch,
            environ={"SURFACE_SPEC_MODEL": "anthropic:claude-haiku-4-5"},
            credentials=ShapingCredentials(
                provider_keys={"openai": "sk-openai", "anthropic": "sk-ant"}
            ),
        )

        assert factory.extra_kwargs == {"api_key": "sk-ant"}

    def test_the_privacy_ratchet_reaches_the_shaping_model(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Rung 5 is a second outbound call carrying the same user data, so
        # workspace + user opt-out apply to it exactly as to the run model.
        _, factory = self._build(
            monkeypatch,
            environ={"SURFACE_SPEC_MODEL": "anthropic:claude-haiku-4-5"},
            credentials=ShapingCredentials(
                provider_keys={"anthropic": "sk-ant"},
                user_policies_json={"privacy": {"training_opt_out": True}},
                workspace_behavior_overrides={"training_data_opt_out": True},
            ),
        )

        assert factory.extra_kwargs == {
            "extra_headers": {"anthropic-disable-training": "true"},
            "api_key": "sk-ant",
        }

    def test_no_credential_passes_no_extra_kwargs(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _, factory = self._build(
            monkeypatch,
            environ={"SURFACE_SPEC_MODEL": "openai:gpt-5.4-mini"},
            credentials=None,
        )

        assert factory.model_id == "openai:gpt-5.4-mini"
        assert factory.extra_kwargs is None
