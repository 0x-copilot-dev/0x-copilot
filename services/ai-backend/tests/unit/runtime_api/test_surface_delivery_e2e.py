"""Hermetic end-to-end: an MCP read becomes a served, renderable surface.

**Why this file exists.** The projector already builds exactly the right object —
``SurfaceEnvelope{surface_uri, archetype, state{spec, source, data}}``. The
pipeline then took that object apart, shipped the pieces separately, and
reassembled them across four layers. Every one of those four hops was
independently broken in production while ~9,000 unit tests were green
(``docs/audit/generative-ui/FINDINGS.md``):

1. the transport allow-list silently STRIPPED ``spec`` from ``surface.created``;
2. ``payload_ref`` is always the anonymous sentinel, because the tool call id is
   structurally unobtainable in the tool layer (three doors verified shut on
   ``McpPresentedTool._call_id``);
3. the persisted ``tool_result.output`` is the MODEL-facing content half, doubly
   JSON-encoded, while the spec was inferred from the ARTIFACT half — two
   representations of one payload, and the spec bound to the wrong one;
4. the client's surface-id round trip dropped the state entirely.

Not one of those is a logic bug inside a function. Each is a *seam*, and the
existing tests inject past every seam: the emitter is tested with a fake emit
callable, the fold is tested on hand-built event dicts, the PRESENT stage is
tested with a recording presenter. Each end was proven; the path between them
was not walked, so the path was where the product broke. A live packaged-desktop
journey found all four in one afternoon.

**So this test walks the path, in-process.** No live model, no Electron, no
Postgres, no network — it runs in CI on every commit:

    a fake MCP tool result
      -> the REAL PRESENT middleware (``McpPresentMiddleware``)
      -> the REAL ``SurfaceLedgerOperationOutcomePresenter`` + ``SurfaceProjector``
      -> the REAL ``WorkLedgerEmitter`` built by the REAL run handler
      -> ``RuntimeEventProducer.append_api_event``
      -> the REAL ``ConversationQueryService.list_run_surfaces``
      -> assert the served state carries spec AND data.

Two rules keep it honest, and both are load-bearing:

* **It must go through ``append_api_event``.** That is where
  ``RuntimeEventPresentationProjector.payload_for_event`` — the transport
  allow-list — runs. A test that calls the emitter and the fold directly is
  precisely what stayed green while the allow-list dropped the spec on the
  floor. Asserting on an event the test itself constructed proves nothing about
  the wire.
* **It must not supply a ``tool_call_id``.** Every prior unit test passed one
  explicitly and therefore never entered the state production is always in. The
  live path has no id to give, so the surface is written with the anonymous
  sentinel — and its data has to reach the client anyway. Whether that happens
  because the state rides on ``surface.created`` or because something joins the
  reference afterwards is an implementation choice; that the data arrives is
  not, and it is what these tests assert.

The fix direction this locks in is "stop disassembling the object": the
projector already builds ``SurfaceEnvelope`` whole, so the fewer hops that take
it apart, the fewer seams exist to break. These tests are deliberately written
against the *delivered outcome* rather than against any one hop, so a future
change that removes a hop entirely keeps them green while a change that loses
the payload cannot.
"""

from __future__ import annotations

import json
from typing import Any

from langchain_core.tools import BaseTool
from langchain_mcp_adapters.tools import _convert_call_tool_result
from mcp.types import CallToolResult, TextContent

from agent_runtime.api.conversation_query_service import ConversationQueryService
from agent_runtime.capabilities.mcp.middleware.present_tool import (
    McpPresentMiddleware,
    PresentValues,
)
from agent_runtime.capabilities.policy.contracts import (
    Action,
    CapabilityDescriptor,
    CapabilityUrn,
    ConnectorState,
    Trust,
)
from agent_runtime.execution.contracts import StreamEventSource
from agent_runtime.execution.models import ModelConfigResolver
from agent_runtime.settings import RuntimeSettings
from agent_runtime.surfaces_v2.emitter import WorkLedgerEmitter
from runtime_adapters.in_memory import InMemoryRuntimeApiStore
from runtime_api.schemas import RuntimeApiEventType
from runtime_worker.handlers.run import RuntimeRunHandler

from tests.unit.runtime_worker.test_runtime_worker import _TestHelpers

_ORG = "org_123"
_USER = "user_123"

#: Sentinel for "persist the tool_result the way a live run does". Distinct from
#: ``None``, which means "do not persist that event at all" — the two are
#: different questions and collapsing them would lose the one that matters.
_FAITHFUL: Any = object()

#: A connector nothing has a curated spec for, so the ladder must reach its
#: deterministic inference floor to answer at all.
_UNKNOWN_CONNECTOR = "incidents"
_UNKNOWN_TOOL = "list_incidents"
_INCIDENTS = {
    "incidents": [
        {"id": "INC-1", "title": "Checkout 500s", "status": "open", "assignee": "sam"},
        {"id": "INC-2", "title": "Slow search", "status": "closed", "assignee": "ada"},
    ]
}

#: A connector that DOES have a curated spec (``builtin_specs/``), so the same
#: path is exercised over rung 1 — the twelve hand-authored specs that were
#: resolved backend-side and then never put on the wire (FINDINGS 3.1).
_CURATED_CONNECTOR = "linear"
_CURATED_TOOL = "list_issues"
_ISSUES = {
    "team": {"name": "Platform"},
    "issues": [
        {
            "identifier": "PLA-1",
            "title": "Ship the surface floor",
            "state": {"name": "In Progress"},
            "assignee": {"displayName": "Sam"},
            "updatedAt": "2026-08-04T10:00:00Z",
        },
        {
            "identifier": "PLA-2",
            "title": "Delete the no-spec copy",
            "state": {"name": "Todo"},
            "assignee": {"displayName": "Ada"},
            "updatedAt": "2026-08-03T09:00:00Z",
        },
    ],
}


class StubMcpTool(BaseTool):
    """An MCP tool at the shape ``langchain-mcp-adapters`` hands the runtime.

    ``response_format = "content_and_artifact"`` and a 2-tuple return: the model
    reads ``content``, the canvas is projected from ``artifact``. Keeping both
    halves distinct is the point — collapsing them here would hide exactly the
    defect this file exists to catch.
    """

    name: str = _UNKNOWN_TOOL
    description: str = "stub mcp read"
    response_format: str = "content_and_artifact"
    result: Any = ()

    def _run(self, *args: Any, **kwargs: Any) -> Any:
        raise AssertionError("MCP tools are driven asynchronously.")

    async def _arun(self, *args: Any, **kwargs: Any) -> Any:
        del args, kwargs
        return self.result


class Delivery:
    """One run's worth of delivered surface state, as a client would receive it."""

    __slots__ = ("events", "surfaces")

    def __init__(self, *, events: list[Any], surfaces: tuple[Any, ...]) -> None:
        self.events = events
        self.surfaces = surfaces

    def only_surface(self) -> Any:
        assert len(self.surfaces) == 1, [s.surface_id for s in self.surfaces]
        return self.surfaces[0]

    def state(self) -> dict[str, Any]:
        state = self.only_surface().state
        assert state is not None, "the surface was served with no state at all"
        return dict(state)

    def payload_of(self, event_type: RuntimeApiEventType) -> dict[str, Any]:
        """The PERSISTED payload — i.e. what survived the transport allow-list."""

        matches = [event for event in self.events if event.event_type is event_type]
        assert matches, (
            f"no {event_type.value} event was persisted; "
            f"got {[event.event_type.value for event in self.events]}"
        )
        return dict(matches[-1].payload)

    def persisted_spec(self) -> dict[str, Any] | None:
        """The spec on the stored ``surface.created``, wherever it is carried.

        Deliberately tolerant about the key, and only about the key. The
        property this file guards is that a resolved spec SURVIVES
        ``payload_for_event`` — an allow-list that does not name a key deletes
        it silently, at no layer, which is how a matched curated spec came to
        vanish between the projector and the screen. Whether the wire spells it
        ``spec`` or nests it in the carried ``state`` is the ledger contract's
        business, and that contract is owned elsewhere; pinning the spelling
        here would make this test a second, disagreeing authority on someone
        else's schema and would go red on a legitimate reshaping. Survival is
        the invariant, position is not.
        """

        created = self.payload_of(RuntimeApiEventType.SURFACE_CREATED)
        carried = created.get("state")
        if isinstance(carried, dict) and isinstance(carried.get("spec"), dict):
            return dict(carried["spec"])
        spec = created.get("spec")
        return dict(spec) if isinstance(spec, dict) else None


class SurfaceDeliveryHarness:
    """Drive one MCP read through every real layer between tool and client."""

    @staticmethod
    def settings(*, surfaces_v2: bool = True) -> RuntimeSettings:
        # E3 flipped SURFACES_V2 default ON, so the kill switch must be asked
        # for explicitly rather than by omission.
        return RuntimeSettings.load(
            environ={
                "OPENAI_API_KEY": "sk-test",
                "RUNTIME_DEFAULT_PROVIDER": "openai",
                "RUNTIME_DEFAULT_MODEL": "gpt-5.4-mini",
                "SURFACES_V2": "true" if surfaces_v2 else "false",
            }
        )

    @staticmethod
    def descriptor(connector: str, tool: str) -> CapabilityDescriptor:
        return CapabilityDescriptor(
            urn=CapabilityUrn.for_mcp(connector, tool),
            action=Action.READ,
            trust=Trust.TRUSTED,
            scopes=(),
            source="mcp",
            connector_state=ConnectorState.LIVE,
        )

    @staticmethod
    def structured_result(payload: dict[str, Any]) -> tuple[Any, Any]:
        """The tuple a server that sent ``structuredContent`` produces.

        The artifact half is the ``MCPToolArtifact`` wrapper, so ``output`` is
        ``{"structured_content": {...}}`` — the wrapper nothing in the repo used
        to peel, which made every curated ``items_path`` resolve against it and
        miss (FINDINGS 3.4).
        """

        return (
            [{"type": "text", "text": json.dumps(payload)}],
            {"structured_content": payload},
        )

    @staticmethod
    def text_only_result(payload: dict[str, Any]) -> tuple[Any, Any]:
        """The tuple for a server that answers in text only.

        Built by the library's own converter rather than hand-written, so the
        assumption the content fallback exists for — the artifact half is
        populated from ``structuredContent`` ALONE, hence ``None`` here — is
        pinned to ``langchain-mcp-adapters`` instead of to our belief about it.
        """

        return _convert_call_tool_result(
            CallToolResult(
                content=[TextContent(type="text", text=json.dumps(payload))],
                structuredContent=None,
                isError=False,
            )
        )

    @staticmethod
    def persisted_output(payload: dict[str, Any]) -> dict[str, Any]:
        """``tool_result.output`` exactly as a live run persists it.

        Not the structured payload: the half the MODEL read, which for an MCP
        tool is a content-block envelope whose text happens to be the same JSON,
        encoded twice. Taken from a real run's ``events.jsonl``.

        It is written faithfully so the run this harness simulates is the run
        production performs — but be clear about what that does and does not
        buy, because the honest version of this docstring is load-bearing. The
        delivered surface no longer reads this event at all: ``state`` rides
        whole on ``surface.created``, so the value here cannot change the
        outcome. Verified by mutation — replacing it with an opaque blob leaves
        every assertion in this file green.

        That is the fix working, not the test sleeping. Break 4.7b existed
        *because* the spec was inferred from one representation and the data
        fetched from another; carrying one object removes the second
        representation, and with it the class of defect. The invariant is
        therefore pinned directly by
        :class:`TestTheDeliveryDoesNotDependOnTheJoin` rather than implied here.
        """

        return {"content": json.dumps([{"type": "text", "text": json.dumps(payload)}])}

    async def deliver(
        self,
        *,
        connector: str = _UNKNOWN_CONNECTOR,
        tool: str = _UNKNOWN_TOOL,
        payload: dict[str, Any] | None = None,
        result: tuple[Any, Any] | None = None,
        surfaces_v2: bool = True,
        tool_result_output: dict[str, Any] | None = _FAITHFUL,
    ) -> Delivery:
        """Tool call in, served surfaces out — through every production layer.

        ``tool_result_output`` defaults to the shape a live run persists.
        ``None`` omits the ``tool_result`` event entirely, which is how
        :class:`TestTheDeliveryDoesNotDependOnTheJoin` asks whether the delivered
        state is self-sufficient.
        """

        body = _INCIDENTS if payload is None else payload
        settings = self.settings(surfaces_v2=surfaces_v2)
        store = InMemoryRuntimeApiStore()
        run_id = await _TestHelpers.create_queued_run(store, settings)
        handler = RuntimeRunHandler(
            persistence=store, event_store=store, settings=settings
        )
        run = await store.get_run(org_id=_ORG, run_id=run_id)

        # The REAL emitter the run handler builds. Its EmitFn closes over
        # ``append_api_event``, which is what puts the transport allow-list on
        # the path instead of beside it.
        emitter = handler._build_work_ledger_emitter(run)
        token = WorkLedgerEmitter.bind_for_run(emitter) if emitter is not None else None
        try:
            # No ``presenter=`` override: the production
            # SurfaceLedgerOperationOutcomePresenter and SurfaceProjector run.
            wrapped = McpPresentMiddleware().wrap(
                StubMcpTool(
                    name=tool,
                    result=result
                    if result is not None
                    else self.structured_result(body),
                ),
                self.descriptor(connector, tool),
            )
            # Deliberately NO ``tool_call_id`` — see the module docstring.
            await wrapped.ainvoke({})
        finally:
            if token is not None:
                WorkLedgerEmitter.unbind(token)

        # The worker persists the tool's result moments later, on the same run.
        # This is the other half of the old join, and the only place a real call
        # id exists at all.
        if tool_result_output is not None:
            await handler.event_producer.append_api_event(
                run=run,
                source=StreamEventSource.TOOL,
                event_type=RuntimeApiEventType.TOOL_RESULT,
                payload={
                    "tool_name": tool,
                    "call_id": "call_ZZUKgYPOoWjTiXWJ",
                    "status": "completed",
                    "output": self.persisted_output(body)
                    if tool_result_output is _FAITHFUL
                    else tool_result_output,
                },
            )

        events = list(
            await store.list_events_after(org_id=_ORG, run_id=run_id, after_sequence=0)
        )
        query_service = ConversationQueryService(
            persistence=store,
            event_store=store,
            settings=settings,
            model_resolver=ModelConfigResolver(settings),
        )
        response = await query_service.list_run_surfaces(
            org_id=_ORG, user_id=_USER, run_id=run_id
        )
        return Delivery(events=events, surfaces=response.surfaces)

    @staticmethod
    def resolve(row: Any, path: str) -> Any:
        """Walk a spec column's dot-path, the way a renderer does."""

        current = row
        for segment in path.split("."):
            if not isinstance(current, dict):
                return None
            current = current.get(segment)
        return current


class TestAnMcpReadBecomesARenderedSurface(SurfaceDeliveryHarness):
    """The headline: what the client is served is actually renderable."""

    async def test_the_read_produces_exactly_one_surface(self) -> None:
        delivery = await self.deliver()

        assert len(delivery.surfaces) == 1
        assert delivery.only_surface().kind == "table"

    async def test_the_served_state_carries_both_the_spec_and_the_data(self) -> None:
        """Neither half alone renders anything.

        A spec with no data draws its columns over zero rows ("The tool returned
        an empty payload"); data with no spec falls to the raw view. The product
        outcome requires both, so the assertion is both.
        """

        state = (await self.deliver()).state()

        assert state.get("spec") is not None, "the spec did not reach the client"
        assert state.get("data") is not None, "the data did not reach the client"

    async def test_the_spec_binds_to_the_data_it_was_served_with(self) -> None:
        """The two halves must be the same representation of one payload.

        This is FINDINGS 4.7b made executable. The spec is inferred from the
        artifact half; the run persists the content half. Both defects — the
        unpeeled ``structured_content`` wrapper and the doubly-encoded content
        envelope — leave a perfectly valid spec whose ``items_path`` resolves
        against nothing. A test that only asserts "spec present, data present"
        passes over both.
        """

        state = (await self.deliver()).state()
        spec = state["spec"]

        rows = self.resolve(state["data"], spec["items_path"])
        assert isinstance(rows, list) and len(rows) == 2, state["data"]

        resolved = {
            column["label"]: self.resolve(rows[0], column["path"])
            for column in spec["columns"]
        }
        assert all(value is not None for value in resolved.values()), resolved

    async def test_the_surface_hydrates_with_no_tool_call_id(self) -> None:
        """FINDINGS 4.7a: on the live path there is no id, and there never will be.

        ``McpPresentedTool._call_id`` documents three independent doors shut in
        the tool layer, so ``payload_ref`` is the anonymous sentinel on every
        real call. Asserting the sentinel *and* the data pins the ordered join
        that resolves it — if someone "fixes" the ref by inventing an id, this
        still passes only if the data still arrives.
        """

        delivery = await self.deliver()

        created = delivery.payload_of(RuntimeApiEventType.SURFACE_CREATED)
        assert created["payload_ref"].endswith(PresentValues.ANONYMOUS_CALL)
        assert delivery.state().get("data") is not None


class TestTheTransportDoesNotStripTheSpec(SurfaceDeliveryHarness):
    """Break #1, asserted where it happened: on the persisted event.

    ``payload_for_event`` rebuilds every payload from a per-event-type allow-list
    before it is stored, so a key the list forgets is gone from the wire and from
    replay. These read the payload back OUT of the store — never the dict the
    emitter was handed — because that difference is the entire bug.
    """

    async def test_the_persisted_surface_created_event_carries_the_spec(self) -> None:
        delivery = await self.deliver()

        spec = delivery.persisted_spec()
        assert spec is not None, (
            "surface.created lost its spec crossing the transport allow-list — "
            "the ledger will record a shaped view over a raw payload"
        )
        assert spec["archetype"] == "table"

    async def test_the_persisted_spec_is_the_one_the_client_is_served(self) -> None:
        """One spec, one identity — not two objects that happen to agree."""

        delivery = await self.deliver()

        assert delivery.state()["spec"] == delivery.persisted_spec()

    async def test_the_ledger_provenance_matches_what_was_rendered(self) -> None:
        """No ``tier: shaped`` over a screen showing raw JSON (PRD AC4).

        The ledger's claim about a run is a compliance record, not a label. When
        it says the view was shaped, a spec must actually have been served.
        """

        delivery = await self.deliver()

        derived = delivery.payload_of(RuntimeApiEventType.VIEW_DERIVED)
        assert derived["tier"] == "shaped"
        assert delivery.state().get("spec") is not None


class TestTheDeliveryDoesNotDependOnTheJoin(SurfaceDeliveryHarness):
    """The anti-disassembly invariant, stated as a property rather than a hop.

    The original design shipped ``SurfaceEnvelope`` in pieces and rebuilt it
    client-side by joining ``payload_ref -> call_id -> tool_result.output``.
    That join had to survive four hops and did not survive one: the ref is a
    constant sentinel (4.7a), and the output it points at is the wrong
    representation of the payload (4.7b). Both are *unfixable in place* — the
    call id does not exist in the tool layer, and the persisted output is the
    model's half by definition.

    So the join is not repaired here, it is removed: the surface carries its own
    state. These tests pin that, because "carries its own state" is the property
    that makes 4.7a and 4.7b non-defects instead of open bugs, and nothing else
    in the file would notice it silently regressing into a join again.
    """

    async def test_the_data_arrives_with_no_tool_result_event_at_all(self) -> None:
        """Delete the far side of the join; the surface must still render.

        If this ever goes red, delivery has been re-coupled to an event that on
        the live path carries an unjoinable ref and the wrong payload half.
        """

        delivery = await self.deliver(tool_result_output=None)

        assert RuntimeApiEventType.TOOL_RESULT not in {
            event.event_type for event in delivery.events
        }
        state = delivery.state()
        assert state.get("spec") is not None
        assert state.get("data") is not None

    async def test_an_opaque_tool_result_cannot_corrupt_the_served_data(self) -> None:
        """Present but useless — the live 4.7b shape, taken to its limit.

        The served rows must still resolve, because they never came from here.
        """

        delivery = await self.deliver(
            tool_result_output={"content": json.dumps([{"type": "text", "text": "?"}])}
        )
        state = delivery.state()

        rows = self.resolve(state["data"], state["spec"]["items_path"])
        assert isinstance(rows, list) and len(rows) == 2, state["data"]


class TestCuratedSpecsReachTheRenderer(SurfaceDeliveryHarness):
    """FINDINGS 3.1: twelve hand-authored specs matched, then dropped.

    A builtin match set the archetype, the title and a ledger basis of
    ``registry`` — and the spec itself went no further than the backend. The
    user saw "No spec matched", which was not merely unhelpful: it was false.
    """

    async def test_the_curated_linear_table_is_served_to_the_client(self) -> None:
        delivery = await self.deliver(
            connector=_CURATED_CONNECTOR, tool=_CURATED_TOOL, payload=_ISSUES
        )

        spec = delivery.state()["spec"]
        assert [column["label"] for column in spec["columns"]] == [
            "ID",
            "Title",
            "State",
            "Assignee",
            "Updated",
        ]

    async def test_the_curated_specs_paths_resolve_against_the_served_data(
        self,
    ) -> None:
        """Curated paths are nested (``state.name``, ``assignee.displayName``).

        A spec whose paths miss renders an empty table, which reads to a user as
        a broken connector rather than as a shaping failure.
        """

        state = (
            await self.deliver(
                connector=_CURATED_CONNECTOR, tool=_CURATED_TOOL, payload=_ISSUES
            )
        ).state()
        spec = state["spec"]

        rows = self.resolve(state["data"], spec["items_path"])
        assert isinstance(rows, list) and rows
        assert self.resolve(rows[0], "state.name") == "In Progress"
        assert self.resolve(rows[0], "assignee.displayName") == "Sam"

    async def test_a_registry_basis_is_only_claimed_over_a_real_registry_spec(
        self,
    ) -> None:
        delivery = await self.deliver(
            connector=_CURATED_CONNECTOR, tool=_CURATED_TOOL, payload=_ISSUES
        )

        derived = delivery.payload_of(RuntimeApiEventType.VIEW_DERIVED)
        assert derived["basis"] == "registry"
        assert delivery.state().get("spec") is not None


class TestATextOnlyConnectorStillRenders(SurfaceDeliveryHarness):
    """FINDINGS 3.4 / PRD 3.1: the artifact half is absent far more often than not.

    ``langchain-mcp-adapters`` fills the artifact from ``structuredContent``
    alone, so for every text-only MCP server it is ``None`` — and the stage used
    to return early on that, emitting no surface and no ledger event for a call
    that had succeeded and had a perfectly good answer.
    """

    async def test_a_result_with_no_artifact_half_still_reaches_the_client(
        self,
    ) -> None:
        delivery = await self.deliver(
            result=self.text_only_result(_INCIDENTS),
        )

        assert delivery.surfaces, "a successful read produced no surface at all"
        assert delivery.state().get("spec") is not None

    async def test_the_library_still_leaves_the_artifact_half_empty(self) -> None:
        """Pin the assumption the fallback exists for, not our memory of it."""

        _content, artifact = self.text_only_result(_INCIDENTS)

        assert artifact is None


class TestTheKillSwitch(SurfaceDeliveryHarness):
    """``SURFACES_V2=false`` is the rollback, and it must be total.

    Also the reason this file names the flag: a capability whose OFF path is the
    documented rollback and whose ON path is what ships needs both driven, which
    is exactly what ``tools/check_dark_capabilities.py`` demands.
    """

    async def test_the_flag_off_emits_no_surface(self) -> None:
        delivery = await self.deliver(surfaces_v2=False)

        assert delivery.surfaces == ()

    async def test_the_flag_off_writes_no_ledger_events(self) -> None:
        delivery = await self.deliver(surfaces_v2=False)

        emitted = {event.event_type for event in delivery.events}
        assert RuntimeApiEventType.SURFACE_CREATED not in emitted
        assert RuntimeApiEventType.VIEW_DERIVED not in emitted
