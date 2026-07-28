from __future__ import annotations

import pytest
from langchain_core.tools import StructuredTool

from agent_runtime.capabilities.middleware import RuntimeControlMiddleware
from agent_runtime.control_plane.context import (
    RunControlBinding,
    RunControlContext,
)
from agent_runtime.control_plane.contracts import (
    RunControlSnapshot,
    RunPolicyRevisions,
)
from agent_runtime.control_plane.feature_modes import (
    AgentQualityFeature,
    FeatureMode,
    FeatureModeSet,
)
from agent_runtime.execution.contracts import (
    AgentRuntimeContext,
    RuntimeDependencies,
    RuntimeErrorCode,
)
from agent_runtime.execution.errors import AgentRuntimeError
from agent_runtime.execution.factory import RuntimeHarness, acreate_agent_runtime
from agent_runtime.capabilities.tool_budget_guard import ToolBudgetGuardedTool
from agent_runtime.capabilities.mcp.cards import McpAuthState, McpServerCard
from agent_runtime.capabilities.mcp.registry import DynamicMcpRegistry
from tests.unit.agent_runtime.agent.helpers import CapturingAgentBuilder
from tests.unit.fakes import (
    FakeMcpRegistry,
    FakeMemoryBackendFactory,
    FakeSubagentCatalog,
    FakeToolRegistry,
)

_SHA256 = "0" * 64


async def test_factory_propagates_permissions_to_runtime_ports(
    runtime_context_admin: AgentRuntimeContext,
    fake_dependencies: RuntimeDependencies,
) -> None:
    builder = CapturingAgentBuilder()

    harness = await acreate_agent_runtime(
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
    assert len(call.middleware) == 1
    assert isinstance(call.middleware[0], RuntimeControlMiddleware)
    assert call.universal_middleware_factories == (RuntimeControlMiddleware,)
    assert not any(isinstance(tool, ToolBudgetGuardedTool) for tool in call.tools)


async def test_factory_installs_per_call_prompt_binding_for_verified_run(
    runtime_context_admin: AgentRuntimeContext,
    fake_dependencies: RuntimeDependencies,
) -> None:
    builder = CapturingAgentBuilder()
    feature_modes = FeatureModeSet.model_validate(
        {
            feature.value: (
                FeatureMode.ENFORCE
                if feature is AgentQualityFeature.F2_PROMPT_ASSEMBLY
                else FeatureMode.OFF
            )
            for feature in AgentQualityFeature
        }
    )
    snapshot = RunControlSnapshot.create(
        run_id=runtime_context_admin.run_id,
        conversation_id="conversation-1",
        subject_fingerprint=_SHA256,
        deployment_profile="single_user_desktop",
        harness_variant_ref="harness-f2-v1",
        task_policy_selection_ref="task-policy-v1",
        policy_revisions=RunPolicyRevisions.model_validate(
            {field: "v1" for field in RunPolicyRevisions.model_fields}
        ),
        feature_modes=feature_modes,
        budget_envelope_ref=f"budget://v1/sha256/{_SHA256}",
        assignment_revision="assignment-v1",
    )
    control = RunControlBinding(
        snapshot=snapshot,
        effective_modes=feature_modes,
        decisions=(),
    )

    token = RunControlContext.bind_for_run(control)
    try:
        harness = await acreate_agent_runtime(
            context=runtime_context_admin,
            dependencies=fake_dependencies,
            agent_builder=builder,
        )
        installed = RunControlContext.prompt_runtime()
    finally:
        RunControlContext.unbind(token)

    assert harness.prompt_runtime_binding is installed
    assert installed is not None
    assert installed.mode is FeatureMode.ENFORCE
    assert installed.framework_cache_installed
    assert builder.calls[0].system_prompt == (
        harness.prompt_assembly_plan.rendered_prompt
    )


class FakeMcpProvider:
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


class _FullyEnabledMcpRegistry(FakeMcpRegistry):
    async def resolve_server(self, _name: str) -> object:
        return object()


class _FullyEnabledSkillRegistry:
    async def list_available_skills(self, _context: object) -> tuple[object, ...]:
        return ()

    async def load_skill_by_name(self, _name: str) -> object:
        return object()


def _category_tool(name: str) -> StructuredTool:
    async def invoke(value: str = "") -> str:
        return value

    return StructuredTool.from_function(
        coroutine=invoke,
        name=name,
        description=f"Exercise the {name} runtime category.",
    )


async def test_factory_composes_all_runtime_tool_categories_behind_one_stack(
    runtime_context_admin: AgentRuntimeContext,
    fake_dependencies: RuntimeDependencies,
) -> None:
    """The final factory request has one root stack for every owned category."""

    builder = CapturingAgentBuilder()
    dependencies = fake_dependencies.model_copy(
        update={
            "tool_registry": FakeToolRegistry(
                tools=(_category_tool("registry_search"),)
            ),
            "mcp_registry": _FullyEnabledMcpRegistry(),
            "skill_registry": _FullyEnabledSkillRegistry(),
            "prior_tool_result_loader": object(),
            "code_mode_tool": _category_tool("invoke_capability"),
            "sandbox_execute_tool": _category_tool("execute_dataflow"),
        }
    )

    await acreate_agent_runtime(
        context=runtime_context_admin,
        dependencies=dependencies,
        agent_builder=builder,
    )

    call = builder.calls[0]
    tool_names = tuple(str(getattr(tool, "name", "")) for tool in call.tools)
    assert {
        "registry_search",
        "load_mcp_server",
        "call_mcp_tool",
        "load_skill",
        "load_prior_tool_result",
        "ask_a_question",
        "suggest_mcp_connector",
        "invoke_capability",
        "execute_dataflow",
    }.issubset(tool_names)
    assert len(call.middleware) == 1
    assert isinstance(call.middleware[0], RuntimeControlMiddleware)
    assert call.universal_middleware_factories == (RuntimeControlMiddleware,)


async def test_factory_wraps_dynamic_loader_adapters_as_langchain_tools(
    runtime_context_admin: AgentRuntimeContext,
    fake_dependencies: RuntimeDependencies,
) -> None:
    builder = CapturingAgentBuilder()
    dependencies = fake_dependencies.model_copy(
        update={"mcp_registry": DynamicMcpRegistry(providers=(FakeMcpProvider(),))}
    )

    await acreate_agent_runtime(
        context=runtime_context_admin,
        dependencies=dependencies,
        agent_builder=builder,
    )

    tool_names = {getattr(tool, "name", "") for tool in builder.calls[0].tools}
    assert "load_mcp_server" in tool_names
    assert "call_mcp_tool" in tool_names
    assert "drive_search" not in tool_names
    assert "answer directly from these cards" in builder.calls[0].system_prompt


async def test_factory_wraps_prior_tool_result_loader_as_langchain_tool(
    runtime_context_admin: AgentRuntimeContext,
    fake_dependencies: RuntimeDependencies,
) -> None:
    builder = CapturingAgentBuilder()
    dependencies = fake_dependencies.model_copy(
        update={"prior_tool_result_loader": object()}
    )

    await acreate_agent_runtime(
        context=runtime_context_admin,
        dependencies=dependencies,
        agent_builder=builder,
    )

    tool_names = {getattr(tool, "name", "") for tool in builder.calls[0].tools}
    assert "load_prior_tool_result" in tool_names


async def test_factory_instructs_model_not_to_load_when_no_mcp_cards(
    runtime_context_admin: AgentRuntimeContext,
    fake_dependencies: RuntimeDependencies,
) -> None:
    builder = CapturingAgentBuilder()
    dependencies = fake_dependencies.model_copy(
        update={"mcp_registry": FakeMcpRegistry(servers=())}
    )

    await acreate_agent_runtime(
        context=runtime_context_admin,
        dependencies=dependencies,
        agent_builder=builder,
    )

    system_prompt = builder.calls[0].system_prompt
    assert "No MCP server cards are currently registered or visible" in system_prompt
    assert "Do not call load_mcp_server" in system_prompt


async def test_factory_instructs_model_to_return_fenced_code(
    runtime_context_admin: AgentRuntimeContext,
    fake_dependencies: RuntimeDependencies,
) -> None:
    builder = CapturingAgentBuilder()

    await acreate_agent_runtime(
        context=runtime_context_admin,
        dependencies=fake_dependencies,
        agent_builder=builder,
    )

    system_prompt = builder.calls[0].system_prompt
    assert "fenced Markdown code blocks" in system_prompt
    assert "indentation and formatting are preserved" in system_prompt


async def test_factory_instructs_model_to_render_links_with_descriptive_labels(
    runtime_context_admin: AgentRuntimeContext,
    fake_dependencies: RuntimeDependencies,
) -> None:
    builder = CapturingAgentBuilder()

    await acreate_agent_runtime(
        context=runtime_context_admin,
        dependencies=fake_dependencies,
        agent_builder=builder,
    )

    system_prompt = builder.calls[0].system_prompt
    assert "Markdown links with concise, descriptive labels" in system_prompt
    assert "use the title as the link label" in system_prompt
    assert "Use only links that came from the user" in system_prompt
    assert "Do not place raw URLs on their own lines" in system_prompt


async def test_factory_rejects_invalid_dependency_dict(
    runtime_context_admin: AgentRuntimeContext,
) -> None:
    with pytest.raises(AgentRuntimeError) as exc_info:
        await acreate_agent_runtime(
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


async def test_factory_wraps_builder_failure_without_leaking_secret(
    runtime_context_admin: AgentRuntimeContext,
    fake_dependencies: RuntimeDependencies,
) -> None:
    def failing_builder(**_: object) -> object:
        raise RuntimeError("provider token=super-secret")

    with pytest.raises(AgentRuntimeError) as exc_info:
        await acreate_agent_runtime(
            context=runtime_context_admin,
            dependencies=fake_dependencies,
            agent_builder=failing_builder,
        )

    assert exc_info.value.code == RuntimeErrorCode.RUNTIME_FACTORY_ERROR
    assert "super-secret" not in exc_info.value.safe_message
    assert exc_info.value.correlation_id == runtime_context_admin.trace_id
