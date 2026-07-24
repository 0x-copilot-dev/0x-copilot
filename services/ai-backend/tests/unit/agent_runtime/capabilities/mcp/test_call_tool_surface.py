"""Ledger-emission tests for :class:`CallMcpTool` after the v1 surface retirement
(PRD-E3 D4 / T5).

The v1 ``result["surface"]`` / ``result["surface_uri"]`` appendage is GONE. A
tool result now carries exactly the ``McpToolCallResult.ok`` fields
(``server_name`` / ``tool_name`` / ``output``) — the surface envelope is computed
on-demand ONLY when a :class:`WorkLedgerEmitter` is bound (``SURFACES_V2`` on) and
handed straight to the ledger, never mutated onto the result. These tests pin:

* the result dict is **never** decorated with a ``surface`` / ``surface_uri`` key;
* a bound emitter records ``action.classified`` → ``read.executed`` →
  ``surface.created`` → ``view.derived`` with the curated/uncurated tier the
  surviving ``SurfaceProjector`` ladder computes (the invariant ported from the
  old ``TestCallMcpToolSurfaceEmission``: curated ⇒ shaped, uncurated ⇒ generic,
  ``isError`` ⇒ no surface);
* no emitter bound (flag-off posture) ⇒ **no envelope computation, no generation
  scheduling**, no ledger side effects, byte-identical result.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping

from agent_runtime.capabilities.mcp import (
    CallMcpTool,
    DynamicMcpRegistry,
    McpLoader,
)
from agent_runtime.execution.contracts import AgentRuntimeContext
from agent_runtime.surfaces_v2.emitter import WorkLedgerEmitter
from agent_runtime.surfaces_v2.ledger_models import LedgerEventType
from tests.unit.agent_runtime.mcp.helpers import DynamicMcpLoadingMixin


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

_UNCURATED_OUTPUT: dict[str, object] = {"widget": {"id": "w-9", "label": "Ready"}}


class SurfaceEmissionMixin(DynamicMcpLoadingMixin):
    """Builds a CallMcpTool over a fake server returning a fixed tool output."""

    def make_call_tool(
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

    def invoke(
        self, tool: CallMcpTool, *, server: str, tool_name: str
    ) -> dict[str, object]:
        return asyncio.run(
            tool.ainvoke(
                {
                    "server_name": server,
                    "tool_name": tool_name,
                    "arguments": {"query": "x"},
                }
            )
        )

    def bind_and_invoke(
        self,
        tool: CallMcpTool,
        *,
        server: str,
        tool_name: str,
    ) -> tuple[dict[str, object], list[dict[str, object]]]:
        """Invoke with a captured WorkLedgerEmitter bound; return (result, events)."""
        recorded: list[dict[str, object]] = []

        async def _emit(event_type_value, payload, summary):  # type: ignore[no-untyped-def]
            recorded.append({"event_type": event_type_value, "payload": dict(payload)})

        token = WorkLedgerEmitter.bind_for_run(WorkLedgerEmitter(emit=_emit))
        try:
            result = self.invoke(tool, server=server, tool_name=tool_name)
        finally:
            WorkLedgerEmitter.unbind(token)
        return result, recorded


class TestResultNeverCarriesSurface(SurfaceEmissionMixin):
    """The v1 appendage is retired: the result dict is a bare
    ``McpToolCallResult.ok`` shape, with or without an emitter bound."""

    def test_no_emitter_result_is_bare_ok_shape(
        self, runtime_context_admin: AgentRuntimeContext
    ) -> None:
        tool = self.make_call_tool(
            runtime_context_admin,
            server="linear",
            tool="get_issue",
            output=_LINEAR_ISSUE_OUTPUT,
        )

        result = self.invoke(tool, server="linear", tool_name="get_issue")

        assert "surface" not in result
        assert "surface_uri" not in result
        # Pre-surface shape: exactly the McpToolCallResult.ok(...) fields.
        assert set(result.keys()) == {"server_name", "tool_name", "output"}
        assert result["output"] == _LINEAR_ISSUE_OUTPUT

    def test_bound_emitter_result_still_bare_ok_shape(
        self, runtime_context_admin: AgentRuntimeContext
    ) -> None:
        tool = self.make_call_tool(
            runtime_context_admin,
            server="linear",
            tool="get_issue",
            output=_LINEAR_ISSUE_OUTPUT,
        )

        result, _recorded = self.bind_and_invoke(
            tool, server="linear", tool_name="get_issue"
        )

        # Emitting to the ledger does NOT mutate the tool result.
        assert "surface" not in result
        assert "surface_uri" not in result
        assert set(result.keys()) == {"server_name", "tool_name", "output"}


class TestCallMcpToolLedgerEmission(SurfaceEmissionMixin):
    """PRD-A3 Hook 1 (reworked for E3): ``ainvoke`` records the v2 ledger read
    path when an emitter is bound, computing the surface envelope on-demand from
    the surviving ``SurfaceProjector`` ladder; no-ops when unbound."""

    def test_curated_tool_records_shaped_surface(
        self, runtime_context_admin: AgentRuntimeContext
    ) -> None:
        tool = self.make_call_tool(
            runtime_context_admin,
            server="linear",
            tool="get_issue",
            output=_LINEAR_ISSUE_OUTPUT,
        )

        _result, recorded = self.bind_and_invoke(
            tool, server="linear", tool_name="get_issue"
        )

        assert [row["event_type"] for row in recorded] == [
            LedgerEventType.ACTION_CLASSIFIED.value,
            LedgerEventType.READ_EXECUTED.value,
            LedgerEventType.SURFACE_CREATED.value,
            LedgerEventType.VIEW_DERIVED.value,
        ]
        # payload_ref points back at this tool call's result (D1).
        read = recorded[1]["payload"]
        assert read["payload_ref"].startswith("call:")
        # surface.created carries the projector-computed surface id (ported from
        # the old ``result["surface_uri"] == "record://linear/get_issue/..."``).
        assert (
            recorded[2]["payload"]["surface_id"]
            == "record://linear/get_issue/issue-uuid-1"
        )
        # Curated tool ⇒ shaped/registry view.
        assert recorded[3]["payload"]["tier"] == "shaped"

    def test_uncurated_tool_records_read_and_generic_view(
        self, runtime_context_admin: AgentRuntimeContext
    ) -> None:
        tool = self.make_call_tool(
            runtime_context_admin,
            server="customsvc",
            tool="do_thing",
            output=_UNCURATED_OUTPUT,
        )

        _result, recorded = self.bind_and_invoke(
            tool, server="customsvc", tool_name="do_thing"
        )

        assert [row["event_type"] for row in recorded] == [
            LedgerEventType.ACTION_CLASSIFIED.value,
            LedgerEventType.READ_EXECUTED.value,
            LedgerEventType.SURFACE_CREATED.value,
            LedgerEventType.VIEW_DERIVED.value,
        ]
        assert recorded[2]["payload"]["surface_id"] == "record://customsvc/do_thing/w-9"
        # No builtin spec ⇒ generic/schema view.
        assert recorded[3]["payload"]["tier"] == "generic"
        assert recorded[3]["payload"]["basis"] == "schema"

    def test_is_error_result_emits_no_surface_events(
        self, runtime_context_admin: AgentRuntimeContext
    ) -> None:
        # An isError output returns a fail result BEFORE the ledger hook, so no
        # ledger events at all (ported from the old "isError gets no surface").
        tool = self.make_call_tool(
            runtime_context_admin,
            server="linear",
            tool="get_issue",
            output={
                "content": [{"type": "text", "text": "boom"}],
                "isError": True,
            },
        )

        result, recorded = self.bind_and_invoke(
            tool, server="linear", tool_name="get_issue"
        )

        assert "error" in result
        assert "surface" not in result
        assert "surface_uri" not in result
        assert recorded == []

    def test_no_emitter_bound_is_no_op_and_byte_identical(
        self, runtime_context_admin: AgentRuntimeContext
    ) -> None:
        # No emitter bound (flag-off posture): active() is None ⇒ no envelope
        # computation, no generation scheduling, no ledger side effects.
        assert WorkLedgerEmitter.active() is None
        tool = self.make_call_tool(
            runtime_context_admin,
            server="linear",
            tool="get_issue",
            output=_LINEAR_ISSUE_OUTPUT,
        )

        result = self.invoke(tool, server="linear", tool_name="get_issue")

        assert set(result.keys()) == {"server_name", "tool_name", "output"}
        assert WorkLedgerEmitter.active() is None
