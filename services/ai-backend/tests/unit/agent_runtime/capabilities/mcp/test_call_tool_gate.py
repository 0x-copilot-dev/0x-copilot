"""PRD-C2 gate-interception tests for :class:`CallMcpTool`.

The adversarial core of C2: a cancelled gate must fail closed BEFORE any client
is created (the dependent connector call never dispatches), a resumed
authenticated card must dispatch without a second interrupt (no gate loop), a
mid-run ``McpAuthError`` re-enters the gate only with the flag on, and the flag
being off is byte-identical to pre-C2. Pure fakes — no LangGraph, no network.
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
from agent_runtime.capabilities.mcp.cards import (
    McpAuthMode,
    McpAuthState,
    McpServerCard,
    McpServerHealth,
    McpTransport,
)
from agent_runtime.capabilities.mcp.client import McpAuthError
from agent_runtime.capabilities.mcp.middleware.auth_mcp import McpAuthSession
from agent_runtime.execution.contracts import AgentRuntimeContext, ModelConfig
from agent_runtime.surfaces_v2.gate import ToolAccessGate
from tests.unit.agent_runtime.mcp.helpers import DynamicMcpLoadingMixin
from datetime import datetime, timezone

_SERVER = "linear"
_TOOL = "search_issues"


def _context() -> AgentRuntimeContext:
    return AgentRuntimeContext(
        user_id="user_123",
        org_id="org_456",
        roles={"employee"},
        permission_scopes={"docs:read", "docs:write"},
        model_profile=ModelConfig(
            provider="openai",
            model_name="gpt-4o-mini",
            max_input_tokens=4096,
            timeout_seconds=30,
            temperature=0.0,
        ),
        run_id="run_abcdef",
        trace_id="trace_gate",
    )


class _FakeAuthSessionCreator:
    async def create_auth_session(
        self, *, server_id: str, runtime_context: AgentRuntimeContext
    ) -> McpAuthSession:
        return McpAuthSession(
            server_id=server_id,
            server_name=_SERVER,
            display_name="Linear",
            auth_url="https://vendor.example/oauth",
            expires_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )


class _Interrupt:
    """Records calls; returns a canned resume value (never raises)."""

    def __init__(self, resume: object) -> None:
        self.resume = resume
        self.calls = 0

    def __call__(self, payload: dict) -> object:
        self.calls += 1
        return self.resume


class _RaisingAuthClient:
    """A fake MCP client whose ``call_tool`` raises ``McpAuthError`` (revocation)."""

    async def call_tool(self, *, tool_name: str, arguments: Mapping[str, object]):
        raise McpAuthError("vendor rejected")


class GateFixture(DynamicMcpLoadingMixin):
    def _card(self, auth_state: McpAuthState) -> McpServerCard:
        return McpServerCard(
            name=_SERVER,
            server_id="seed:linear",
            short_description="Linear MCP.",
            transport=McpTransport.HTTP,
            auth_mode=McpAuthMode.OAUTH2,
            auth_state=auth_state,
            required_scopes=("docs:read",),
            health=McpServerHealth.HEALTHY,
            load_cost=10,
        )

    def make_tool_and_provider(
        self, *, auth_state: McpAuthState, raising: bool = False
    ):
        client = (
            _RaisingAuthClient()
            if raising
            else self.FakeMcpClient(
                tools=(self.make_tool(name=_TOOL),),
                resources=(),
                tool_outputs={_TOOL: {"answer": "ok"}},
            )
        )
        provider = self.FakeMcpProvider(
            cards=(self._card(auth_state),),
            clients={_SERVER: client},
        )
        return provider

    def make_call_tool(self, provider, *, gate: ToolAccessGate | None) -> CallMcpTool:
        registry = DynamicMcpRegistry(providers=(provider,))
        return CallMcpTool(
            registry=registry,
            loader=McpLoader(registry),
            runtime_context=_context(),
            gate=gate,
        )

    def gate(self, interrupt: _Interrupt) -> ToolAccessGate:
        return ToolAccessGate(
            auth_session_creator=_FakeAuthSessionCreator(),
            runtime_context=_context(),
            interrupt_handler=interrupt,
            classifier=None,
        )

    def invoke(self, tool: CallMcpTool) -> dict:
        return asyncio.run(
            tool.ainvoke({"server_name": _SERVER, "tool_name": _TOOL, "arguments": {}})
        )


@pytest.fixture
def fx() -> GateFixture:
    return GateFixture()


# --------------------------------------------------------------------------- #
# flag off — byte-identical
# --------------------------------------------------------------------------- #


def test_flag_off_call_tool_bytes_identical(fx: GateFixture, monkeypatch) -> None:
    monkeypatch.delenv("SURFACES_V2", raising=False)
    # Same authenticated card; one tool with a gate wired, one without.
    interrupt = _Interrupt({"decision": "rejected"})
    with_gate = fx.make_call_tool(
        fx.make_tool_and_provider(auth_state=McpAuthState.UNAUTHENTICATED),
        gate=fx.gate(interrupt),
    )
    without_gate = fx.make_call_tool(
        fx.make_tool_and_provider(auth_state=McpAuthState.UNAUTHENTICATED),
        gate=None,
    )
    result_with = fx.invoke(with_gate)
    result_without = fx.invoke(without_gate)
    # Flag off: the gate is inert even on an UNAUTHENTICATED card — dispatch
    # proceeds identically and the interrupt is never reached.
    assert interrupt.calls == 0
    assert result_with == result_without
    assert "output" in result_with and "error" not in result_with


# --------------------------------------------------------------------------- #
# flag on — interception
# --------------------------------------------------------------------------- #


def test_gate_blocks_before_client_creation(fx: GateFixture, monkeypatch) -> None:
    """Cancelled gate ⇒ dependent branch does not execute (create_client never runs)."""

    monkeypatch.setenv("SURFACES_V2", "true")
    provider = fx.make_tool_and_provider(auth_state=McpAuthState.UNAUTHENTICATED)
    interrupt = _Interrupt({"decision": "rejected"})
    tool = fx.make_call_tool(provider, gate=fx.gate(interrupt))
    result = fx.invoke(tool)
    assert interrupt.calls == 1
    assert provider.created_clients == []  # dispatch never happened
    assert result["error"]["code"] == "auth_failure"


def test_cancelled_gate_returns_typed_auth_failure_no_dispatch(
    fx: GateFixture, monkeypatch
) -> None:
    monkeypatch.setenv("SURFACES_V2", "true")
    provider = fx.make_tool_and_provider(auth_state=McpAuthState.AUTH_FAILED)
    tool = fx.make_call_tool(
        provider, gate=fx.gate(_Interrupt({"decision": "rejected"}))
    )
    result = fx.invoke(tool)
    assert result["error"]["code"] == "auth_failure"
    assert provider.created_clients == []


def test_resumed_authenticated_card_dispatches_without_second_interrupt(
    fx: GateFixture, monkeypatch
) -> None:
    """An already-authenticated card never gates — dispatch, no interrupt."""

    monkeypatch.setenv("SURFACES_V2", "true")
    provider = fx.make_tool_and_provider(auth_state=McpAuthState.AUTHENTICATED)
    interrupt = _Interrupt({"decision": "approved"})
    tool = fx.make_call_tool(provider, gate=fx.gate(interrupt))
    result = fx.invoke(tool)
    assert interrupt.calls == 0
    assert provider.created_clients == [_SERVER]
    assert "output" in result and "error" not in result


def test_still_unauthenticated_after_approve_fails_closed_no_loop(
    fx: GateFixture, monkeypatch
) -> None:
    """An approve resume with a still-unusable card dispatches (approve ⇒ fall through).

    The gate is called at most once; on approve it falls through to dispatch —
    there is no second interrupt and no gate loop.
    """

    monkeypatch.setenv("SURFACES_V2", "true")
    provider = fx.make_tool_and_provider(auth_state=McpAuthState.UNAUTHENTICATED)
    interrupt = _Interrupt({"decision": "approved"})
    tool = fx.make_call_tool(provider, gate=fx.gate(interrupt))
    result = fx.invoke(tool)
    assert interrupt.calls == 1  # exactly one interrupt per invocation
    # Approve ⇒ fall through to dispatch (the OAuth completed while parked).
    assert provider.created_clients == [_SERVER]
    assert "output" in result and "error" not in result


def test_mcp_auth_error_regates_when_flag_on_terminal_failure_when_off(
    fx: GateFixture, monkeypatch
) -> None:
    # Flag ON: an authenticated card whose dispatch raises McpAuthError re-enters
    # the gate (interrupt called), then fails closed when park returns rejected.
    monkeypatch.setenv("SURFACES_V2", "true")
    provider_on = fx.make_tool_and_provider(
        auth_state=McpAuthState.AUTHENTICATED, raising=True
    )
    interrupt_on = _Interrupt({"decision": "rejected"})
    tool_on = fx.make_call_tool(provider_on, gate=fx.gate(interrupt_on))
    result_on = fx.invoke(tool_on)
    assert interrupt_on.calls == 1  # re-gated
    assert result_on["error"]["code"] == "auth_failure"

    # Flag OFF: the same McpAuthError is the terminal failure — no re-gate.
    monkeypatch.delenv("SURFACES_V2", raising=False)
    provider_off = fx.make_tool_and_provider(
        auth_state=McpAuthState.AUTHENTICATED, raising=True
    )
    interrupt_off = _Interrupt({"decision": "rejected"})
    tool_off = fx.make_call_tool(provider_off, gate=fx.gate(interrupt_off))
    result_off = fx.invoke(tool_off)
    assert interrupt_off.calls == 0
    assert result_off["error"]["code"] == "auth_failure"
