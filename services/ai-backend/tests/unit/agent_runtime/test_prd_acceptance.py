from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from agent_runtime.execution.contracts import (
    AgentRuntimeContext,
    FeatureFlag,
    ModelConfig,
    RuntimeDependencies,
)
from agent_runtime.execution.factory import create_agent_runtime
from agent_runtime.capabilities.mcp import (
    DynamicMcpRegistry,
    McpAuthMode,
    McpLoadRequest,
    McpLoader,
    McpResourceAccessPolicy,
    McpResourceDescriptor,
    McpRiskLevel,
    McpServerCard,
    McpServerHealth,
    McpToolDescriptor,
    McpTransport,
)
from agent_runtime.context.memory import (
    ContextCompressionStrategy,
    ContextPayloadManager,
    ContextSummarizationManager,
    MemoryAccessOperation,
    MemoryActorRole,
    MemoryRoutePlan,
    MemoryScopeType,
    ScopedMemoryBackendFactory,
    TokenBudgetPolicy,
)
from agent_runtime.context.memory.policy import MemoryPolicyAuthorizer
from agent_runtime.capabilities.skills.sources import (
    SkillSourceConfig,
    SkillSourceRegistry,
)
from agent_runtime.delegation.subagents import (
    AsyncSubagentLaunch,
    AsyncSubagentLifecycle,
    AsyncTaskStatus,
    DynamicSubagentCatalog,
    SubagentDefinition,
    SubagentHandoffBuilder,
    SubagentResult,
    SubagentTask,
)
from agent_runtime.capabilities.tools import (
    DynamicToolRegistry,
    LoadedToolSpec,
    ToolCard,
    ToolLoadRequest,
    ToolLoader,
    ToolPermissionPolicy,
    ToolRiskLevel,
    ToolSideEffect,
)
from tests.unit.agent_runtime.agent.helpers import CapturingAgentBuilder


@dataclass
class FakeToolSpecProvider:
    cards: Sequence[ToolCard]
    specs: Mapping[str, LoadedToolSpec]
    loaded_names: list[str] = field(default_factory=list)

    def list_tool_cards(self) -> Sequence[ToolCard]:
        return self.cards

    def load_tool_spec(self, name: str) -> LoadedToolSpec:
        self.loaded_names.append(name)
        return self.specs[name]


@dataclass
class FakeMcpClient:
    tools: Sequence[McpToolDescriptor]
    resources: Sequence[McpResourceDescriptor]

    async def connect(self) -> None:
        return None

    async def list_tools(self) -> Sequence[McpToolDescriptor]:
        return self.tools

    async def list_resources(self) -> Sequence[McpResourceDescriptor]:
        return self.resources


@dataclass
class FakeMcpProvider:
    cards: Sequence[McpServerCard]
    client: FakeMcpClient
    created_for: list[str] = field(default_factory=list)

    def list_server_cards(self) -> Sequence[McpServerCard]:
        return self.cards

    def create_client(self, card: McpServerCard) -> FakeMcpClient:
        self.created_for.append(card.name)
        return self.client


@dataclass
class FakeSubagentDefinitionProvider:
    definitions: Sequence[SubagentDefinition]

    def list_subagent_definitions(self) -> Sequence[SubagentDefinition]:
        return self.definitions


@dataclass
class FakeSubagentRunner:
    started_tasks: list[SubagentTask] = field(default_factory=list)

    async def start(
        self,
        definition: SubagentDefinition,
        task: SubagentTask,
    ) -> AsyncSubagentLaunch:
        self.started_tasks.append(task)
        return AsyncSubagentLaunch(
            thread_id="thread_123",
            run_id="run_123",
            status=AsyncTaskStatus.RUNNING,
        )

    async def check(self, _state: object) -> SubagentResult:
        return SubagentResult.ok(
            response="Launch risks are owner gaps and unresolved blockers.",
            execution_summary="Checked delegated research inputs and summarized the findings.",
            plan_summary="Next verify owners for every unresolved launch blocker.",
        )

    async def update(self, _state: object, _task: SubagentTask) -> None:
        return None

    async def cancel(self, _state: object) -> None:
        return None


def test_runtime_capability_stack_wires_together_without_live_llm_calls(
    tmp_path,
    model_config: ModelConfig,
) -> None:
    context = AgentRuntimeContext(
        user_id="user_123",
        org_id="org_456",
        roles={"admin"},
        permission_scopes={"search:read", "docs:read"},
        connector_scopes={"google-drive": {"docs:read"}},
        model_profile=model_config,
        trace_id="trace_123",
        feature_flags=set(FeatureFlag),
    )

    skill_root = tmp_path / "skills"
    skill_dir = skill_root / "research"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        """---
name: research
description: Gather source-backed evidence before answering.
allowed_tools: [doc_search]
---
# Research
Use this only when source-backed research is needed.
""",
        encoding="utf-8",
    )
    skill_config = SkillSourceConfig(roots=(str(skill_root),))

    tool_spec = LoadedToolSpec(
        name="doc_search",
        description="Search indexed enterprise documents by query.",
        args_schema={
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
        return_schema={
            "type": "object",
            "properties": {"answer": {"type": "string"}},
        },
        side_effects={ToolSideEffect.READ},
        timeout_ms=5_000,
        permission_policy=ToolPermissionPolicy(
            connector="google-drive",
            required_scopes={"docs:read"},
            risk_level=ToolRiskLevel.LOW,
        ),
    )
    tool_provider = FakeToolSpecProvider(
        cards=(
            ToolCard(
                name="doc_search",
                display_name="Doc Search",
                short_description="Search indexed Google Drive documents.",
                connector="google-drive",
                tags={"search", "docs"},
                required_scopes={"docs:read"},
                risk_level=ToolRiskLevel.LOW,
                load_cost=10,
            ),
            ToolCard(
                name="slack_search",
                display_name="Slack Search",
                short_description="Search Slack messages.",
                connector="slack",
                tags={"search", "chat"},
                required_scopes={"chat:read"},
                risk_level=ToolRiskLevel.LOW,
                load_cost=10,
            ),
        ),
        specs={"doc_search": tool_spec},
    )
    tool_registry = DynamicToolRegistry(providers=(tool_provider,))

    mcp_tool = McpToolDescriptor(
        name="drive_search",
        description="Search Google Drive through MCP.",
        input_schema={
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
        output_shape={"type": "object", "properties": {"answer": {"type": "string"}}},
        risk_level=McpRiskLevel.LOW,
    )
    mcp_resource = McpResourceDescriptor(
        uri="mcp://drive/root",
        name="Drive Root",
        mime_type="application/json",
        description="Root Drive resource index.",
        access_policy=McpResourceAccessPolicy(required_scopes={"docs:read"}),
    )
    mcp_provider = FakeMcpProvider(
        cards=(
            McpServerCard(
                name="drive_mcp",
                short_description="Search Google Drive through MCP.",
                transport=McpTransport.HTTP,
                auth_mode=McpAuthMode.OAUTH2,
                required_scopes={"docs:read"},
                health=McpServerHealth.HEALTHY,
                load_cost=10,
            ),
            McpServerCard(
                name="offline_mcp",
                short_description="Unavailable MCP server.",
                transport=McpTransport.HTTP,
                auth_mode=McpAuthMode.OAUTH2,
                required_scopes={"docs:read"},
                health=McpServerHealth.UNAVAILABLE,
                load_cost=10,
            ),
        ),
        client=FakeMcpClient(tools=(mcp_tool,), resources=(mcp_resource,)),
    )
    mcp_registry = DynamicMcpRegistry(providers=(mcp_provider,))

    subagent_definition = SubagentDefinition(
        name="researcher",
        description="Investigates enterprise sources and returns concise grounded summaries.",
        graph_id="researcher_graph",
        tools={"doc_search"},
        skills={"research"},
        required_scopes={"docs:read"},
        timeout_seconds=120,
        concurrency_limit=2,
    )
    subagent_catalog = DynamicSubagentCatalog(
        providers=(FakeSubagentDefinitionProvider((subagent_definition,)),)
    )

    builder = CapturingAgentBuilder()
    harness = create_agent_runtime(
        context=context,
        dependencies=RuntimeDependencies(
            tool_registry=tool_registry,
            mcp_registry=mcp_registry,
            skill_source_config=skill_config,
            memory_backend_factory=ScopedMemoryBackendFactory(),
            subagent_catalog=subagent_catalog,
        ),
        agent_builder=builder,
    )

    assert harness.agent == {"agent": "fake"}
    assert builder.calls[0].model_name == context.model_profile.model_name
    assert builder.calls[0].skill_directories == (
        str(skill_root.resolve(strict=False)),
    )
    assert tuple(card.name for card in harness.tools) == ("doc_search",)
    assert "args_schema" not in harness.tools[0].model_dump()
    assert tuple(card.name for card in harness.mcp_servers) == ("drive_mcp",)
    assert tuple(definition.name for definition in harness.subagents) == ("researcher",)

    discovered_skills = SkillSourceRegistry.discover_configured_skills(skill_config)
    assert tuple(skill.manifest.name for skill in discovered_skills) == ("research",)
    assert harness.skill_directories == (str(skill_root.resolve(strict=False)),)

    tool_result = ToolLoader(tool_registry).load_tool(
        ToolLoadRequest(tool_name="doc_search", runtime_context=context)
    )
    assert tool_result.succeeded
    assert tool_result.loaded_spec == tool_spec
    assert tool_provider.loaded_names == ["doc_search"]

    mcp_result = asyncio.run(
        McpLoader(mcp_registry).load_server(
            McpLoadRequest(server_name="drive_mcp", runtime_context=context)
        )
    )
    assert mcp_result.succeeded
    assert mcp_result.loaded_server is not None
    assert tuple(tool.name for tool in mcp_result.loaded_server.tools) == (
        "drive_search",
    )
    assert mcp_provider.created_for == ["drive_mcp"]

    memory_plan = harness.memory_backend
    assert isinstance(memory_plan, MemoryRoutePlan)
    assert (
        memory_plan.route_for_path("/memories/preferences.md").scope.scope_type
        == MemoryScopeType.USER
    )
    assert (
        memory_plan.route_for_path("/policies/security.md").scope.scope_type
        == MemoryScopeType.ORGANIZATION
    )
    MemoryPolicyAuthorizer.ensure_authorized(
        path="/policies/security.md",
        actor_role=MemoryActorRole.APPLICATION,
        operation=MemoryAccessOperation.WRITE,
        content="Policy updated through an approved application workflow.",
        correlation_id=context.trace_id,
    )
    offloaded = ContextPayloadManager.prepare_tool_output(
        content="\n".join(f"row {index}: {'x' * 120}" for index in range(30)),
        policy=TokenBudgetPolicy(
            max_input_tokens=100,
            summary_threshold_ratio=0.8,
            recent_context_ratio=0.1,
        ),
        trace_id=context.trace_id,
        offload_writer=lambda content: "/memories/tool-output.md",
    )
    fallback_summary = ContextSummarizationManager.summarize_or_fallback(
        objective="Prepare the launch readiness brief.",
        decisions=("Use source-backed claims only.",),
        artifacts=("launch-brief.md",),
        next_steps=("Verify unresolved blocker owners.",),
        summarizer=lambda: (_ for _ in ()).throw(RuntimeError("llm unavailable")),
        trace_id=context.trace_id,
        before_tokens=10_000,
    )
    assert offloaded.strategy == ContextCompressionStrategy.OFFLOAD
    assert offloaded.content is None
    assert fallback_summary.fallback_used is True
    assert fallback_summary.summary.objective == "Prepare the launch readiness brief."

    task = SubagentHandoffBuilder().build_task(
        context=context,
        definition=subagent_definition,
        objective="Find launch readiness risks.",
        relevant_summary="Supervisor needs a compact delegated research summary.",
        constraints=("Return only source-backed findings.",),
        requested_tools=("doc_search", "admin_delete"),
        requested_skills=("research", "private_skill"),
        conversation_history=({"role": "user", "content": "full raw chat"},),
    )
    runner = FakeSubagentRunner()
    lifecycle = AsyncSubagentLifecycle(catalog=subagent_catalog, runner=runner)
    started = asyncio.run(
        lifecycle.start(context=context, subagent_name="researcher", task=task)
    )
    checked = asyncio.run(lifecycle.check(started.state.task_id))  # type: ignore[union-attr]
    assert task.allowed_tools == frozenset({"doc_search"})
    assert "full raw chat" not in str(task.model_dump())
    assert started.state is not None
    assert checked.result is not None
    assert checked.result.execution_summary is not None
