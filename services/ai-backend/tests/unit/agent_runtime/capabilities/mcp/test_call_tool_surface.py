"""Surface-emission tests for :class:`CallMcpTool` (generative-UI PRD-02, AC1/AC4).

Asserts a curated tool result carries a top-level ``surface_uri`` + a
``surface`` envelope whose ``state.spec`` matches the builtin; an uncurated tool
still gets a URI + data but no spec; an ``isError`` result gets neither; and
``RUNTIME_SURFACE_EMISSION=false`` restores the byte-compatible pre-surface
payload.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping

import pytest

from agent_runtime.capabilities.mcp import (
    CallMcpTool,
    DynamicMcpRegistry,
    McpLoader,
)
from agent_runtime.capabilities.surfaces import builtin
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


class TestCallMcpToolSurfaceEmission(SurfaceEmissionMixin):
    def test_curated_tool_result_carries_matching_surface(
        self, runtime_context_admin: AgentRuntimeContext
    ) -> None:
        tool = self.make_call_tool(
            runtime_context_admin,
            server="linear",
            tool="get_issue",
            output=_LINEAR_ISSUE_OUTPUT,
        )

        result = self.invoke(tool, server="linear", tool_name="get_issue")

        assert "error" not in result
        assert result["surface_uri"] == "record://linear/get_issue/issue-uuid-1"
        surface = result["surface"]
        assert surface["archetype"] == "record"
        expected_spec = builtin.lookup("linear", "get_issue").model_dump(
            mode="json", exclude_none=True
        )
        assert surface["state"]["spec"] == expected_spec
        assert surface["state"]["data"] == _LINEAR_ISSUE_OUTPUT

    def test_uncurated_tool_result_has_uri_and_data_but_no_spec(
        self, runtime_context_admin: AgentRuntimeContext
    ) -> None:
        tool = self.make_call_tool(
            runtime_context_admin,
            server="customsvc",
            tool="do_thing",
            output=_UNCURATED_OUTPUT,
        )

        result = self.invoke(tool, server="customsvc", tool_name="do_thing")

        assert "error" not in result
        assert result["surface_uri"] == "record://customsvc/do_thing/w-9"
        surface = result["surface"]
        assert "spec" not in surface["state"]
        assert surface["state"]["data"] == _UNCURATED_OUTPUT

    def test_is_error_result_gets_no_surface(
        self, runtime_context_admin: AgentRuntimeContext
    ) -> None:
        tool = self.make_call_tool(
            runtime_context_admin,
            server="linear",
            tool="get_issue",
            output={
                "content": [{"type": "text", "text": "boom"}],
                "isError": True,
            },
        )

        result = self.invoke(tool, server="linear", tool_name="get_issue")

        assert "error" in result
        assert "surface" not in result
        assert "surface_uri" not in result

    def test_emission_disabled_restores_byte_compatible_payload(
        self,
        runtime_context_admin: AgentRuntimeContext,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("RUNTIME_SURFACE_EMISSION", "false")
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


class TestCallMcpToolLedgerEmission(SurfaceEmissionMixin):
    """PRD-A3 Hook 1: ``ainvoke`` records the v2 ledger read path when an
    emitter is bound, and no-ops (result byte-identical) when it is not."""

    def _bind_and_invoke(
        self,
        tool: CallMcpTool,
        *,
        server: str,
        tool_name: str,
    ) -> tuple[dict[str, object], list[dict[str, object]]]:
        recorded: list[dict[str, object]] = []

        async def _emit(event_type_value, payload, summary):  # type: ignore[no-untyped-def]
            recorded.append({"event_type": event_type_value, "payload": dict(payload)})

        token = WorkLedgerEmitter.bind_for_run(WorkLedgerEmitter(emit=_emit))
        try:
            result = self.invoke(tool, server=server, tool_name=tool_name)
        finally:
            WorkLedgerEmitter.unbind(token)
        return result, recorded

    def test_bound_emitter_records_ledger_events(
        self, runtime_context_admin: AgentRuntimeContext
    ) -> None:
        tool = self.make_call_tool(
            runtime_context_admin,
            server="linear",
            tool="get_issue",
            output=_LINEAR_ISSUE_OUTPUT,
        )

        result, recorded = self._bind_and_invoke(
            tool, server="linear", tool_name="get_issue"
        )

        # Result is unchanged (the emitter only reads the attached envelope).
        assert result["surface_uri"] == "record://linear/get_issue/issue-uuid-1"
        assert [row["event_type"] for row in recorded] == [
            LedgerEventType.ACTION_CLASSIFIED.value,
            LedgerEventType.READ_EXECUTED.value,
            LedgerEventType.SURFACE_CREATED.value,
            LedgerEventType.VIEW_DERIVED.value,
        ]
        # payload_ref points back at this tool call's result (D1).
        read = recorded[1]["payload"]
        assert read["payload_ref"].startswith("call:")
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

        _result, recorded = self._bind_and_invoke(
            tool, server="customsvc", tool_name="do_thing"
        )

        assert [row["event_type"] for row in recorded] == [
            LedgerEventType.ACTION_CLASSIFIED.value,
            LedgerEventType.READ_EXECUTED.value,
            LedgerEventType.SURFACE_CREATED.value,
            LedgerEventType.VIEW_DERIVED.value,
        ]
        # No builtin spec ⇒ generic/schema view.
        assert recorded[3]["payload"]["tier"] == "generic"
        assert recorded[3]["payload"]["basis"] == "schema"

    def test_no_emitter_bound_is_no_op_and_byte_identical(
        self, runtime_context_admin: AgentRuntimeContext
    ) -> None:
        # No emitter bound (flag-off posture): active() is None, and the result
        # is exactly the surface-emission shape — no ledger side effects.
        assert WorkLedgerEmitter.active() is None
        tool = self.make_call_tool(
            runtime_context_admin,
            server="linear",
            tool="get_issue",
            output=_LINEAR_ISSUE_OUTPUT,
        )

        result = self.invoke(tool, server="linear", tool_name="get_issue")

        assert result["surface_uri"] == "record://linear/get_issue/issue-uuid-1"
        assert WorkLedgerEmitter.active() is None
