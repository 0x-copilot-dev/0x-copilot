"""D2 — the runtime factory threads the tool-use policy into the built graph.

End-to-end proof that the stored policy reaches the Deep Agents build request:
the write axis controls whether ``call_mcp_tool`` is added to ``interrupt_on``
(the existing HITL approval seam) or replaced by a blocked-result wrapper, and an
unconfigured run stays byte-identical to today (write=ask → approval).
"""

from __future__ import annotations

from agent_runtime.capabilities.mcp.cards import McpAuthState, McpServerCard
from agent_runtime.capabilities.mcp.registry import DynamicMcpRegistry
from agent_runtime.capabilities.tools.tool_use_enforcement import PolicyBlockedTool
from agent_runtime.execution.contracts import AgentRuntimeContext, RuntimeDependencies
from agent_runtime.execution.factory import acreate_agent_runtime
from tests.unit.agent_runtime.agent.helpers import CapturingAgentBuilder

_CALL_MCP_TOOL = "call_mcp_tool"


class _FakeMcpProvider:
    async def list_server_cards(self) -> tuple[McpServerCard, ...]:
        return (
            McpServerCard(
                name="drive_mcp",
                display_name="Drive MCP",
                short_description="Search Drive.",
                transport="http",
                auth_mode="oauth2",
                auth_state=McpAuthState.AUTH_SKIPPED,
                required_scopes=("docs:read",),
                health="healthy",
                load_cost=1,
            ),
        )

    def create_client(self, _name: str) -> object:
        return object()


def _dependencies_with_mcp(
    fake_dependencies: RuntimeDependencies,
) -> RuntimeDependencies:
    return fake_dependencies.model_copy(
        update={"mcp_registry": DynamicMcpRegistry(providers=(_FakeMcpProvider(),))}
    )


def _tool_use(policy: dict[str, str]) -> dict[str, object]:
    return {"tool_use": {"workspace": policy, "user": {}}}


async def test_unconfigured_run_gates_call_mcp_tool_by_default(
    runtime_context_admin: AgentRuntimeContext,
    fake_dependencies: RuntimeDependencies,
) -> None:
    builder = CapturingAgentBuilder()
    await acreate_agent_runtime(
        context=runtime_context_admin,  # user_policies_json defaults to {}
        dependencies=_dependencies_with_mcp(fake_dependencies),
        agent_builder=builder,
    )
    request = builder.calls[0]
    # Fail-open lane: deployment default write=ask → HITL approval, unchanged.
    assert request.interrupt_on == {
        _CALL_MCP_TOOL: {"allowed_decisions": ["approve", "edit", "reject"]}
    }
    assert not any(isinstance(tool, PolicyBlockedTool) for tool in request.tools)


async def test_write_auto_removes_the_interrupt(
    runtime_context_admin: AgentRuntimeContext,
    fake_dependencies: RuntimeDependencies,
) -> None:
    builder = CapturingAgentBuilder()
    context = runtime_context_admin.model_copy(
        update={"user_policies_json": _tool_use({"write": "auto"})}
    )
    await acreate_agent_runtime(
        context=context,
        dependencies=_dependencies_with_mcp(fake_dependencies),
        agent_builder=builder,
    )
    request = builder.calls[0]
    assert request.interrupt_on == {}
    tool_names = {str(getattr(tool, "name", "")) for tool in request.tools}
    assert _CALL_MCP_TOOL in tool_names
    assert not any(isinstance(tool, PolicyBlockedTool) for tool in request.tools)


async def test_write_require_gates_call_mcp_tool(
    runtime_context_admin: AgentRuntimeContext,
    fake_dependencies: RuntimeDependencies,
) -> None:
    builder = CapturingAgentBuilder()
    context = runtime_context_admin.model_copy(
        update={"user_policies_json": _tool_use({"write": "require"})}
    )
    await acreate_agent_runtime(
        context=context,
        dependencies=_dependencies_with_mcp(fake_dependencies),
        agent_builder=builder,
    )
    assert _CALL_MCP_TOOL in builder.calls[0].interrupt_on


async def test_write_block_wraps_call_mcp_tool(
    runtime_context_admin: AgentRuntimeContext,
    fake_dependencies: RuntimeDependencies,
) -> None:
    builder = CapturingAgentBuilder()
    context = runtime_context_admin.model_copy(
        update={"user_policies_json": _tool_use({"write": "block"})}
    )
    await acreate_agent_runtime(
        context=context,
        dependencies=_dependencies_with_mcp(fake_dependencies),
        agent_builder=builder,
    )
    request = builder.calls[0]
    # Blocked: no approval interrupt; the umbrella tool is a blocked wrapper.
    assert request.interrupt_on == {}
    blocked = [tool for tool in request.tools if isinstance(tool, PolicyBlockedTool)]
    assert len(blocked) == 1
    assert blocked[0].name == _CALL_MCP_TOOL
