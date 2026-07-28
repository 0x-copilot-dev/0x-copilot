from __future__ import annotations

import pytest
from langchain_core.messages import SystemMessage
from langchain_core.tools import StructuredTool

from agent_runtime.capabilities.task_policy import (
    TaskFamily,
    TaskPolicyProfile,
    TaskPolicySelection,
    TaskPolicySelectionReason,
)
from agent_runtime.capabilities.middleware import RuntimeControlMiddleware
from agent_runtime.control_plane.context import (
    RunControlBinding,
    RunControlContext,
    TaskPolicyRuntimeBinding,
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
from agent_runtime.execution.factory import (
    RuntimeHarness,
    _instructions_with_application_context,
    _instructions_with_capability_tools,
    _instructions_with_mcp_cards,
    _instructions_with_skill_cards,
    _instructions_with_suggested_connectors,
    _instructions_with_workspace,
    acreate_agent_runtime,
)
from agent_runtime.prompts.runtime import DEFAULT_INSTRUCTIONS
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


def _control_binding(
    runtime_context: AgentRuntimeContext,
    *,
    harness_revision: str = "harness-f2-v7",
    prompt_revision: str = "prompt-policy-v7",
    capability_revision: str = "capability-bridge-v7",
    task_policy_revision: str = "task-policy-v7",
    f2_mode: FeatureMode = FeatureMode.ENFORCE,
) -> RunControlBinding:
    feature_modes = FeatureModeSet.model_validate(
        {
            feature.value: (
                f2_mode
                if feature is AgentQualityFeature.F2_PROMPT_ASSEMBLY
                else FeatureMode.OFF
            )
            for feature in AgentQualityFeature
        }
    )
    revision_values = {field: "policy-v7" for field in RunPolicyRevisions.model_fields}
    revision_values.update(
        {
            "prompt": prompt_revision,
            "capability": capability_revision,
            "tool_controller": task_policy_revision,
        }
    )
    snapshot = RunControlSnapshot.create(
        run_id=runtime_context.run_id,
        conversation_id="conversation-1",
        subject_fingerprint=_SHA256,
        deployment_profile="single_user_desktop",
        harness_variant_ref=harness_revision,
        task_policy_selection_ref="task-policy-selection-v7",
        policy_revisions=RunPolicyRevisions.model_validate(revision_values),
        feature_modes=feature_modes,
        budget_envelope_ref=f"budget://v7/sha256/{_SHA256}",
        assignment_revision="assignment-v7",
    )
    return RunControlBinding(
        snapshot=snapshot,
        effective_modes=feature_modes,
        decisions=(),
    )


def _task_policy_binding(
    runtime_context: AgentRuntimeContext,
    *,
    revision: str = "task-policy-v7",
    family: TaskFamily = TaskFamily.PUBLIC_RESEARCH,
) -> TaskPolicyRuntimeBinding:
    profile = TaskPolicyProfile(
        profile_id=f"{family.value}.bounded",
        revision=revision,
        task_family=family,
    )
    selection = TaskPolicySelection.create(
        run_id=runtime_context.run_id,
        profile=profile,
        reason=TaskPolicySelectionReason.SERVER_SELECTED_FAMILY,
        bundle_ref=f"task-policy-bundle://{revision}",
    )
    return TaskPolicyRuntimeBinding(
        selection=selection,
        profile=profile,
        controller=object(),  # type: ignore[arg-type]
        fingerprinter=object(),  # type: ignore[arg-type]
        mode=FeatureMode.ENFORCE,
        progress_projector=lambda: {  # type: ignore[arg-type,return-value]
            "profile_id": profile.profile_id,
            "profile_revision": profile.revision,
            "task_family": profile.task_family.value,
        },
    )


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


async def test_factory_binds_verified_snapshot_and_f4_prompt_authority(
    runtime_context_admin: AgentRuntimeContext,
    fake_dependencies: RuntimeDependencies,
) -> None:
    builder = CapturingAgentBuilder()
    control = _control_binding(runtime_context_admin)
    task_policy = _task_policy_binding(runtime_context_admin)

    token = RunControlContext.bind_for_run(control, task_policy=task_policy)
    try:
        harness = await acreate_agent_runtime(
            context=runtime_context_admin,
            dependencies=fake_dependencies,
            agent_builder=builder,
        )
    finally:
        RunControlContext.unbind(token)

    plan = harness.prompt_assembly_plan
    assert plan is not None
    assert plan.harness_revision == control.snapshot.harness_variant_ref
    assert (
        plan.capability_bridge_revision == control.snapshot.policy_revisions.capability
    )
    assert plan.policy_revision == control.snapshot.policy_revisions.prompt
    assert plan.locked_task_profile is not None
    assert plan.locked_task_profile.task_family == (
        task_policy.selection.task_family.value
    )
    assert plan.locked_task_profile.profile_revision == (
        task_policy.selection.profile_revision
    )
    assert plan.locked_task_profile.lock_revision == (
        task_policy.selection.selection_digest
    )
    assert harness.prompt_runtime_binding is not None
    assert harness.prompt_runtime_binding.harness_revision == plan.harness_revision


async def test_factory_verified_feature_off_preserves_the_exact_model_request(
    runtime_context_admin: AgentRuntimeContext,
    fake_dependencies: RuntimeDependencies,
) -> None:
    builder = CapturingAgentBuilder()
    control = _control_binding(runtime_context_admin, f2_mode=FeatureMode.OFF)

    token = RunControlContext.bind_for_run(control)
    try:
        harness = await acreate_agent_runtime(
            context=runtime_context_admin,
            dependencies=fake_dependencies,
            agent_builder=builder,
        )
    finally:
        RunControlContext.unbind(token)

    binding = harness.prompt_runtime_binding
    plan = harness.prompt_assembly_plan
    assert binding is not None
    assert binding.mode is FeatureMode.OFF
    assert plan is not None
    inbound = SystemMessage(
        content=plan.rendered_prompt,
        additional_kwargs={"preserve": True},
    )
    result = binding.prepare(
        system_message=inbound,
        state={"runtime_prompt_approval": "approved"},
        tools=builder.calls[0].tools,
        execution_scope="supervisor",
        task_policy_progress=None,
    )

    assert result.system_message is inbound
    assert result.tools == builder.calls[0].tools
    assert result.plan is None
    assert result.decoration is None
    assert result.observation.cache_reason_code == "feature_off"
    assert builder.calls[0].system_prompt == plan.rendered_prompt


async def test_factory_root_and_subagent_share_authority_and_tool_changes_invalidate(
    runtime_context_admin: AgentRuntimeContext,
    fake_dependencies: RuntimeDependencies,
) -> None:
    builder = CapturingAgentBuilder()
    control = _control_binding(runtime_context_admin)
    task_policy = _task_policy_binding(runtime_context_admin)

    token = RunControlContext.bind_for_run(control, task_policy=task_policy)
    try:
        harness = await acreate_agent_runtime(
            context=runtime_context_admin,
            dependencies=fake_dependencies,
            agent_builder=builder,
        )
    finally:
        RunControlContext.unbind(token)

    binding = harness.prompt_runtime_binding
    legacy_plan = harness.prompt_assembly_plan
    assert binding is not None
    assert legacy_plan is not None
    system = SystemMessage(content=legacy_plan.rendered_prompt)
    root = binding.prepare(
        system_message=system,
        state={},
        tools=builder.calls[0].tools,
        execution_scope="supervisor",
        task_policy_progress=None,
    )
    child = binding.prepare(
        system_message=system,
        state={},
        tools=builder.calls[0].tools,
        execution_scope="subagent:researcher",
        task_policy_progress=None,
    )
    changed_child = binding.prepare(
        system_message=system,
        state={},
        tools=(*builder.calls[0].tools, _category_tool("child_only_tool")),
        execution_scope="subagent:researcher",
        task_policy_progress=None,
    )

    assert root.plan is not None
    assert child.plan is not None
    assert changed_child.plan is not None
    assert root.plan.plan_digest == child.plan.plan_digest
    assert root.plan.stable_prefix_digest == child.plan.stable_prefix_digest
    assert root.plan.rendered_prompt == child.plan.rendered_prompt
    assert root.plan.locked_task_profile == child.plan.locked_task_profile
    assert changed_child.plan.tool_schema_revision != root.plan.tool_schema_revision
    assert changed_child.plan.plan_digest != root.plan.plan_digest
    assert changed_child.plan.stable_prefix_digest != root.plan.stable_prefix_digest
    assert changed_child.plan.rendered_prompt == root.plan.rendered_prompt


async def test_factory_authority_revision_changes_invalidate_without_byte_drift(
    runtime_context_admin: AgentRuntimeContext,
    fake_dependencies: RuntimeDependencies,
) -> None:
    async def plan_for(
        context: AgentRuntimeContext,
        control: RunControlBinding,
        task_policy: TaskPolicyRuntimeBinding,
    ):
        token = RunControlContext.bind_for_run(control, task_policy=task_policy)
        try:
            harness = await acreate_agent_runtime(
                context=context,
                dependencies=fake_dependencies,
                agent_builder=CapturingAgentBuilder(),
            )
        finally:
            RunControlContext.unbind(token)
        assert harness.prompt_assembly_plan is not None
        return harness.prompt_assembly_plan

    baseline = await plan_for(
        runtime_context_admin,
        _control_binding(runtime_context_admin),
        _task_policy_binding(runtime_context_admin),
    )
    changed_plans = (
        await plan_for(
            runtime_context_admin,
            _control_binding(runtime_context_admin, harness_revision="harness-f2-v8"),
            _task_policy_binding(runtime_context_admin),
        ),
        await plan_for(
            runtime_context_admin,
            _control_binding(
                runtime_context_admin,
                capability_revision="capability-bridge-v8",
            ),
            _task_policy_binding(runtime_context_admin),
        ),
        await plan_for(
            runtime_context_admin,
            _control_binding(runtime_context_admin, prompt_revision="prompt-policy-v8"),
            _task_policy_binding(runtime_context_admin),
        ),
        await plan_for(
            runtime_context_admin.model_copy(
                update={
                    "permission_scopes": frozenset(
                        {*runtime_context_admin.permission_scopes, "prompt:test"}
                    )
                }
            ),
            _control_binding(runtime_context_admin),
            _task_policy_binding(runtime_context_admin),
        ),
        await plan_for(
            runtime_context_admin,
            _control_binding(
                runtime_context_admin,
                task_policy_revision="task-policy-v8",
            ),
            _task_policy_binding(runtime_context_admin, revision="task-policy-v8"),
        ),
    )

    for changed in changed_plans:
        assert changed.plan_digest != baseline.plan_digest
        assert changed.stable_prefix_digest != baseline.stable_prefix_digest
        assert changed.rendered_prompt == baseline.rendered_prompt


async def test_factory_typed_plan_is_byte_identical_to_legacy_prompt_order(
    runtime_context_admin: AgentRuntimeContext,
    fake_dependencies: RuntimeDependencies,
) -> None:
    builder = CapturingAgentBuilder()
    harness = await acreate_agent_runtime(
        context=runtime_context_admin,
        dependencies=fake_dependencies,
        agent_builder=builder,
    )

    expected = _instructions_with_application_context(instructions=DEFAULT_INSTRUCTIONS)
    expected = _instructions_with_mcp_cards(
        instructions=expected,
        mcp_servers=harness.mcp_servers,
    )
    expected = _instructions_with_skill_cards(
        instructions=expected,
        skill_cards=harness.skill_cards,
    )
    expected = _instructions_with_suggested_connectors(
        instructions=expected,
        suggestions=runtime_context_admin.suggested_connectors,
    )
    expected = _instructions_with_workspace(
        instructions=expected,
        workspace_active=False,
    )
    expected = _instructions_with_capability_tools(
        instructions=expected,
        code_mode_active=False,
        sandbox_execute_active=False,
    )

    assert harness.prompt_assembly_plan is not None
    assert harness.prompt_assembly_plan.rendered_prompt == expected
    assert builder.calls[0].system_prompt == expected


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
