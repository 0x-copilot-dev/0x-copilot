from __future__ import annotations

import pytest

from agent_runtime.execution.contracts import (
    AgentRuntimeContext,
    RuntimeDependencies,
    RuntimeErrorCode,
)
from agent_runtime.execution.errors import AgentRuntimeError
from agent_runtime.execution.factory import RuntimeHarness, create_agent_runtime
from agent_runtime.capabilities.mcp.cards import McpAuthState, McpServerCard
from agent_runtime.capabilities.mcp.registry import DynamicMcpRegistry
from tests.unit.agent_runtime.agent.helpers import CapturingAgentBuilder
from tests.unit.fakes import (
    FakeMcpRegistry,
    FakeMemoryBackendFactory,
    FakeSubagentCatalog,
    FakeToolRegistry,
)


def test_factory_propagates_permissions_to_runtime_ports(
    runtime_context_admin: AgentRuntimeContext,
    fake_dependencies: RuntimeDependencies,
) -> None:
    builder = CapturingAgentBuilder()

    harness = create_agent_runtime(
        context=runtime_context_admin,
        dependencies=fake_dependencies,
        agent_builder=builder,
    )

    assert isinstance(harness, RuntimeHarness)
    assert harness.tools == ("doc_search",)
    assert harness.mcp_servers == ("drive_mcp",)
    assert harness.subagents == ("researcher",)

    tool_registry = fake_dependencies.tool_registry
    mcp_registry = fake_dependencies.mcp_registry
    subagent_catalog = fake_dependencies.subagent_catalog
    memory_factory = fake_dependencies.memory_backend_factory

    assert isinstance(tool_registry, FakeToolRegistry)
    assert isinstance(mcp_registry, FakeMcpRegistry)
    assert isinstance(subagent_catalog, FakeSubagentCatalog)
    assert isinstance(memory_factory, FakeMemoryBackendFactory)
    assert tool_registry.seen_contexts == [runtime_context_admin]
    assert mcp_registry.seen_contexts == [runtime_context_admin]
    assert subagent_catalog.seen_contexts == [runtime_context_admin]
    assert memory_factory.seen_contexts == [runtime_context_admin]

    call = builder.calls[0]
    assert call.model_name == runtime_context_admin.model_profile.model_name
    tool_names = tuple(str(getattr(tool, "name", tool)) for tool in call.tools)
    assert "doc_search" in tool_names
    assert "ask_a_question" in tool_names
    assert call.subagents == ("researcher",)
    assert call.memory_backend is None


class FakeMcpProvider:
    def list_server_cards(self) -> tuple[McpServerCard, ...]:
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


def test_factory_wraps_dynamic_loader_adapters_as_langchain_tools(
    runtime_context_admin: AgentRuntimeContext,
    fake_dependencies: RuntimeDependencies,
) -> None:
    builder = CapturingAgentBuilder()
    dependencies = fake_dependencies.model_copy(
        update={"mcp_registry": DynamicMcpRegistry(providers=(FakeMcpProvider(),))}
    )

    create_agent_runtime(
        context=runtime_context_admin,
        dependencies=dependencies,
        agent_builder=builder,
    )

    tool_names = {getattr(tool, "name", "") for tool in builder.calls[0].tools}
    assert "load_mcp_server" in tool_names
    assert "call_mcp_tool" in tool_names
    assert "drive_search" not in tool_names
    assert "answer directly from these cards" in builder.calls[0].system_prompt


def test_factory_wraps_prior_tool_result_loader_as_langchain_tool(
    runtime_context_admin: AgentRuntimeContext,
    fake_dependencies: RuntimeDependencies,
) -> None:
    builder = CapturingAgentBuilder()
    dependencies = fake_dependencies.model_copy(
        update={"prior_tool_result_loader": object()}
    )

    create_agent_runtime(
        context=runtime_context_admin,
        dependencies=dependencies,
        agent_builder=builder,
    )

    tool_names = {getattr(tool, "name", "") for tool in builder.calls[0].tools}
    assert "load_prior_tool_result" in tool_names


def test_factory_instructs_model_not_to_load_when_no_mcp_cards(
    runtime_context_admin: AgentRuntimeContext,
    fake_dependencies: RuntimeDependencies,
) -> None:
    builder = CapturingAgentBuilder()
    dependencies = fake_dependencies.model_copy(
        update={"mcp_registry": FakeMcpRegistry(servers=())}
    )

    create_agent_runtime(
        context=runtime_context_admin,
        dependencies=dependencies,
        agent_builder=builder,
    )

    system_prompt = builder.calls[0].system_prompt
    assert "No MCP server cards are currently registered or visible" in system_prompt
    assert "Do not call load_mcp_server" in system_prompt


def test_factory_instructs_model_to_return_fenced_code(
    runtime_context_admin: AgentRuntimeContext,
    fake_dependencies: RuntimeDependencies,
) -> None:
    builder = CapturingAgentBuilder()

    create_agent_runtime(
        context=runtime_context_admin,
        dependencies=fake_dependencies,
        agent_builder=builder,
    )

    system_prompt = builder.calls[0].system_prompt
    assert "fenced Markdown code blocks" in system_prompt
    assert "indentation and formatting are preserved" in system_prompt


def test_factory_instructs_model_to_render_links_with_descriptive_labels(
    runtime_context_admin: AgentRuntimeContext,
    fake_dependencies: RuntimeDependencies,
) -> None:
    builder = CapturingAgentBuilder()

    create_agent_runtime(
        context=runtime_context_admin,
        dependencies=fake_dependencies,
        agent_builder=builder,
    )

    system_prompt = builder.calls[0].system_prompt
    assert "Markdown links with concise, descriptive labels" in system_prompt
    assert "use the title as the link label" in system_prompt
    assert "Use only links that came from the user" in system_prompt
    assert "Do not place raw URLs on their own lines" in system_prompt


def test_factory_rejects_invalid_dependency_dict(
    runtime_context_admin: AgentRuntimeContext,
) -> None:
    with pytest.raises(AgentRuntimeError) as exc_info:
        create_agent_runtime(
            context=runtime_context_admin,
            dependencies={
                "tool_registry": object(),
                "mcp_registry": object(),
                "skill_source_config": {},
                "memory_backend_factory": object(),
                "subagent_catalog": object(),
            },
            agent_builder=CapturingAgentBuilder(),
        )

    assert exc_info.value.code == RuntimeErrorCode.DEPENDENCY_ERROR
    assert exc_info.value.safe_message == "Runtime dependencies are invalid."


def test_factory_wraps_builder_failure_without_leaking_secret(
    runtime_context_admin: AgentRuntimeContext,
    fake_dependencies: RuntimeDependencies,
) -> None:
    def failing_builder(**_: object) -> object:
        raise RuntimeError("provider token=super-secret")

    with pytest.raises(AgentRuntimeError) as exc_info:
        create_agent_runtime(
            context=runtime_context_admin,
            dependencies=fake_dependencies,
            agent_builder=failing_builder,
        )

    assert exc_info.value.code == RuntimeErrorCode.RUNTIME_FACTORY_ERROR
    assert "super-secret" not in exc_info.value.safe_message
    assert exc_info.value.correlation_id == runtime_context_admin.trace_id
