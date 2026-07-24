"""E3 cutover keystone (T4): the run ledger is v1-free, yet v2 canvas is complete.

Drives an executed MCP read through :class:`CallMcpTool` with the run handler's
real :class:`WorkLedgerEmitter` bound (built with ``SURFACES_V2`` **unset** — the
E3 default-on posture, no env flag needed), then asserts:

* **No v1 residue** — no event payload emitted for the run carries a ``surface``
  or top-level ``surface_uri`` key (the retired v1 appendage is gone from the
  wire, not just from the result dict);
* **v2 is complete** — ``surface.created`` and ``view.derived`` events still
  appear, and :meth:`SurfaceStoreProjection.fold` yields a canvas surface with a
  non-empty ``title`` / ``kind`` / ``payload_ref`` — i.e. v2 needs *nothing* from
  the v1 pipeline.

This is the cutover proof the retirement rests on: surface data reaches the
client via ledger events + ``payload_ref`` resolution, never ``payload.surface``.
"""

from __future__ import annotations

from collections.abc import Mapping

from agent_runtime.capabilities.mcp import (
    CallMcpTool,
    DynamicMcpRegistry,
    McpLoader,
)
from agent_runtime.execution.contracts import AgentRuntimeContext
from agent_runtime.settings import RuntimeSettings
from agent_runtime.surfaces_v2.emitter import WorkLedgerEmitter
from agent_runtime.surfaces_v2.ledger_models import LedgerEventType
from agent_runtime.surfaces_v2.projection import SurfaceStoreProjection
from runtime_adapters.in_memory import InMemoryRuntimeApiStore
from runtime_api.schemas import RuntimeApiEventType
from runtime_worker.handlers.run import RuntimeRunHandler

from tests.unit.agent_runtime.mcp.helpers import DynamicMcpLoadingMixin
from tests.unit.runtime_worker.test_runtime_worker import _TestHelpers

_LINEAR_ISSUE_OUTPUT: dict[str, object] = {
    "issue": {
        "id": "issue-uuid-1",
        "identifier": "ENG-1421",
        "title": "Fix login redirect loop",
        "state": {"name": "In Progress"},
        "assignee": {"displayName": "Sarah Chen"},
        "priorityLabel": "High",
        "updatedAt": "2026-07-20T10:00:00Z",
        "url": "https://linear.app/acme/issue/ENG-1421",
    }
}

_SURFACE_KEYS = ("surface", "surface_uri")


def _default_on_settings() -> RuntimeSettings:
    # SURFACES_V2 deliberately UNSET — proves E3's default-on flip (surfaces_v2
    # resolves True with no env flag) as well as the v1-free invariant.
    return RuntimeSettings.load(
        environ={
            "OPENAI_API_KEY": "sk-test",
            "RUNTIME_DEFAULT_PROVIDER": "openai",
            "RUNTIME_DEFAULT_MODEL": "gpt-5.4-mini",
        }
    )


class TestV1FreeLedger(DynamicMcpLoadingMixin):
    def _call_tool(
        self,
        runtime_context: AgentRuntimeContext,
        *,
        server: str,
        tool: str,
        output: Mapping[str, object],
    ) -> CallMcpTool:
        provider = self.FakeMcpProvider(
            cards=(self.make_card(name=server),),
            clients={
                server: self.FakeMcpClient(
                    tools=(self.make_tool(name=tool),),
                    resources=(),
                    tool_outputs={tool: output},
                )
            },
        )
        registry = DynamicMcpRegistry(providers=(provider,))
        return CallMcpTool(
            registry=registry,
            loader=McpLoader(registry),
            runtime_context=runtime_context,
        )

    async def test_executed_read_leaves_no_v1_surface_but_full_v2_canvas(
        self, runtime_context_admin: AgentRuntimeContext
    ) -> None:
        store = InMemoryRuntimeApiStore()
        settings = _default_on_settings()

        # Default-on proof: the master flag resolves True with SURFACES_V2 unset.
        assert settings.execution.surfaces_v2 is True

        run_id = await _TestHelpers.create_queued_run(store, settings)
        run = await store.get_run(org_id="org_123", run_id=run_id)
        assert run is not None

        handler = RuntimeRunHandler(
            persistence=store,
            event_store=store,
            settings=settings,
        )
        emitter = handler._build_work_ledger_emitter(run)
        assert emitter is not None, (
            "default-on: emitter must bind with SURFACES_V2 unset"
        )

        tool = self._call_tool(
            runtime_context_admin,
            server="linear",
            tool="get_issue",
            output=_LINEAR_ISSUE_OUTPUT,
        )

        token = WorkLedgerEmitter.bind_for_run(emitter)
        try:
            result = await tool.ainvoke(
                {
                    "server_name": "linear",
                    "tool_name": "get_issue",
                    "arguments": {"query": "ENG-1421"},
                }
            )
        finally:
            WorkLedgerEmitter.unbind(token)

        # The tool result itself is v1-free.
        for key in _SURFACE_KEYS:
            assert key not in result

        events = list(
            await store.list_events_after(
                org_id="org_123", run_id=run_id, after_sequence=0
            )
        )

        # (1) No v1 residue anywhere on the run's event stream.
        for event in events:
            for key in _SURFACE_KEYS:
                assert key not in event.payload, (
                    f"v1 residue {key!r} on {event.event_type}"
                )

        # (2) v2 surface events still appear.
        types = [event.event_type for event in events]
        assert RuntimeApiEventType.SURFACE_CREATED in types
        assert RuntimeApiEventType.VIEW_DERIVED in types

        # The surface.created payload keys off surface_id + payload_ref, NOT a
        # v1 surface envelope.
        created = next(
            e.payload
            for e in events
            if e.event_type is RuntimeApiEventType.SURFACE_CREATED
        )
        assert created["surface_id"] == "record://linear/get_issue/issue-uuid-1"
        assert created["payload_ref"].startswith("call:")

        # (3) The fold yields a complete canvas surface from ledger events alone.
        state = SurfaceStoreProjection.fold(run_id, events)
        assert len(state.surfaces) == 1
        surface = state.surfaces[0]
        assert surface.surface_id == "record://linear/get_issue/issue-uuid-1"
        assert surface.kind
        assert surface.title
        assert surface.payload_ref.startswith("call:")
        assert surface.view is not None
        assert surface.view.tier == "shaped"

    async def test_read_executed_event_is_present_for_the_call(
        self, runtime_context_admin: AgentRuntimeContext
    ) -> None:
        # Sanity: the read path itself is recorded (so a canvas title/source can
        # resolve the payload_ref), independent of the surface events.
        store = InMemoryRuntimeApiStore()
        settings = _default_on_settings()
        run_id = await _TestHelpers.create_queued_run(store, settings)
        run = await store.get_run(org_id="org_123", run_id=run_id)
        handler = RuntimeRunHandler(
            persistence=store, event_store=store, settings=settings
        )
        emitter = handler._build_work_ledger_emitter(run)
        assert emitter is not None

        tool = self._call_tool(
            runtime_context_admin,
            server="linear",
            tool="get_issue",
            output=_LINEAR_ISSUE_OUTPUT,
        )
        token = WorkLedgerEmitter.bind_for_run(emitter)
        try:
            await tool.ainvoke(
                {
                    "server_name": "linear",
                    "tool_name": "get_issue",
                    "arguments": {"query": "ENG-1421"},
                }
            )
        finally:
            WorkLedgerEmitter.unbind(token)

        events = list(
            await store.list_events_after(
                org_id="org_123", run_id=run_id, after_sequence=0
            )
        )
        read_values = {
            LedgerEventType.ACTION_CLASSIFIED.value,
            LedgerEventType.READ_EXECUTED.value,
        }
        seen = {e.event_type.value for e in events if e.event_type.value in read_values}
        assert seen == read_values
