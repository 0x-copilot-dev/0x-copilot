"""Runtime factory for the Deep Agents harness."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from langchain_core.tools import StructuredTool
from pydantic import ValidationError

from agent_runtime.execution.contracts import (
    AgentRuntimeContext,
    RuntimeDependencies,
    RuntimeErrorCode,
)
from agent_runtime.execution.errors import AgentRuntimeError
from agent_runtime.execution.deep_agent_builder import (
    DeepAgentBuildRequest,
    DeepAgentsBackend,
    build_deep_agent,
    runtime_checkpointer,
)
from agent_runtime.capabilities.mcp.loader import McpLoader
from agent_runtime.capabilities.mcp.cards import McpToolCallRequest
from agent_runtime.capabilities.mcp.constants import Values as McpValues
from agent_runtime.capabilities.mcp.middleware.auth_mcp import AuthMcpInput, AuthMcpTool
from agent_runtime.capabilities.mcp.middleware.call_tool import CallMcpTool
from agent_runtime.capabilities.mcp.middleware.dynamic_loader import (
    LoadMcpServerInput,
    LoadMcpServerTool,
)
from agent_runtime.capabilities.skills.middleware import LoadSkillInput, LoadSkillTool
from agent_runtime.capabilities.skills.sources import SkillSourceRegistry
from agent_runtime.prompts.runtime import (
    DEFAULT_INSTRUCTIONS,
    MCP_SERVER_CARDS_INSTRUCTIONS,
    NO_MCP_SERVER_CARDS_INSTRUCTIONS,
    SKILL_CARDS_INSTRUCTIONS,
)

AgentBuilder = Callable[[DeepAgentBuildRequest], object]


@dataclass(frozen=True)
class RuntimeHarness:
    """Fully wired runtime surface for a single request context."""

    agent: object
    context: AgentRuntimeContext
    dependencies: RuntimeDependencies
    tools: tuple[object, ...]
    mcp_servers: tuple[object, ...]
    subagents: tuple[object, ...]
    memory_backend: object
    skill_directories: tuple[str, ...]
    skill_cards: tuple[object, ...] = ()


def create_agent_runtime(
    *,
    context: AgentRuntimeContext | dict[str, Any],
    dependencies: RuntimeDependencies | dict[str, Any],
    instructions: str = DEFAULT_INSTRUCTIONS,
    agent_builder: AgentBuilder | None = None,
) -> RuntimeHarness:
    """Create a request-scoped Deep Agents runtime.

    The runtime resolves authorized capabilities through injected ports before
    handing anything to the model-facing agent builder.
    """

    runtime_context = _parse_context(context)
    runtime_dependencies = _parse_dependencies(dependencies, runtime_context.trace_id)
    builder = agent_builder or build_deep_agent

    tools = tuple(
        runtime_dependencies.tool_registry.list_available_tools(runtime_context)
    )
    mcp_servers = tuple(
        runtime_dependencies.mcp_registry.list_available_servers(runtime_context)
    )
    subagents = tuple(
        runtime_dependencies.subagent_catalog.list_available_subagents(runtime_context)
    )
    memory_backend = runtime_dependencies.memory_backend_factory.create(runtime_context)
    skill_directories = SkillSourceRegistry.skill_directories_for_deep_agent(
        runtime_dependencies.skill_source_config
    )
    skill_cards = _skill_cards(
        skill_registry=runtime_dependencies.skill_registry,
        runtime_context=runtime_context,
    )

    try:
        model_tools = _model_visible_tools(
            tools=tools,
            mcp_registry=runtime_dependencies.mcp_registry,
            skill_registry=runtime_dependencies.skill_registry,
            runtime_context=runtime_context,
        )
        model_instructions = _instructions_with_skill_cards(
            instructions=_instructions_with_mcp_cards(
                instructions=instructions,
                mcp_servers=mcp_servers,
            ),
            skill_cards=skill_cards,
        )
        agent = builder(
            DeepAgentBuildRequest(
                tools=model_tools,
                model_config=runtime_context.model_profile,
                system_prompt=model_instructions,
                subagents=subagents,
                memory_backend=(
                    memory_backend
                    if isinstance(memory_backend, DeepAgentsBackend)
                    else None
                ),
                memory_paths=_deepagents_memory_paths(memory_backend),
                skill_directories=skill_directories,
                interrupt_on=_native_interrupt_config(runtime_context),
                checkpointer=runtime_checkpointer(),
            )
        )
    except AgentRuntimeError:
        raise
    except Exception as exc:
        raise AgentRuntimeError(
            RuntimeErrorCode.RUNTIME_FACTORY_ERROR,
            "The agent runtime could not be constructed.",
            retryable=False,
            correlation_id=runtime_context.trace_id,
        ) from exc

    return RuntimeHarness(
        agent=agent,
        context=runtime_context,
        dependencies=runtime_dependencies,
        tools=tools,
        mcp_servers=mcp_servers,
        subagents=subagents,
        memory_backend=memory_backend,
        skill_directories=skill_directories,
        skill_cards=skill_cards,
    )


def _model_visible_tools(
    *,
    tools: Sequence[object],
    mcp_registry: object,
    skill_registry: object | None,
    runtime_context: AgentRuntimeContext,
) -> tuple[object, ...]:
    model_tools = list(tools)
    auth_session_creator = _auth_session_creator(mcp_registry)
    local_tool_names = _local_tool_names(
        model_tools,
        include_mcp_tools=callable(getattr(mcp_registry, "resolve_server", None)),
        include_auth_mcp=auth_session_creator is not None,
        include_skill_loader=skill_registry is not None
        and callable(getattr(skill_registry, "load_skill_by_name", None)),
    )
    if callable(getattr(mcp_registry, "resolve_server", None)):
        loader = McpLoader(mcp_registry)  # type: ignore[arg-type]
        model_tools.append(
            _structured_tool(
                LoadMcpServerTool(
                    loader=loader,
                    runtime_context=runtime_context,
                    local_tool_names=local_tool_names,
                ),
                LoadMcpServerInput,
            )
        )
        model_tools.append(
            _structured_tool(
                CallMcpTool(
                    registry=mcp_registry,  # type: ignore[arg-type]
                    loader=loader,
                    runtime_context=runtime_context,
                ),
                McpToolCallRequest,
            )
        )
    if auth_session_creator is not None:
        model_tools.append(
            _structured_tool(
                AuthMcpTool(
                    auth_session_creator=auth_session_creator,
                    runtime_context=runtime_context,
                ),
                AuthMcpInput,
            )
        )
    if skill_registry is not None and callable(
        getattr(skill_registry, "load_skill_by_name", None)
    ):
        model_tools.append(
            _structured_tool(LoadSkillTool(registry=skill_registry), LoadSkillInput)
        )  # type: ignore[arg-type]
    return tuple(model_tools)


def _native_interrupt_config(
    _runtime_context: AgentRuntimeContext,
) -> dict[str, object]:
    """Return DeepAgents HITL policies for model-visible gated tools."""

    return {
        McpValues.ToolName.CALL_MCP_TOOL: {
            "allowed_decisions": ["approve", "edit", "reject"],
        },
    }


def _local_tool_names(
    tools: Sequence[object],
    *,
    include_mcp_tools: bool,
    include_auth_mcp: bool,
    include_skill_loader: bool,
) -> frozenset[str]:
    """Return trusted names already exposed to the model for collision checks."""

    names = {name for tool in tools if (name := str(getattr(tool, "name", "")).strip())}
    if include_mcp_tools:
        names.update(
            {
                McpValues.ToolName.LOAD_MCP_SERVER,
                McpValues.ToolName.CALL_MCP_TOOL,
            }
        )
    if include_auth_mcp:
        names.add(McpValues.ToolName.AUTH_MCP)
    if include_skill_loader:
        names.add("load_skill")
    return frozenset(names)


def _structured_tool(tool_adapter: object, args_schema: type[object]) -> StructuredTool:
    async def invoke_adapter(**kwargs: Any) -> object:
        return await tool_adapter.ainvoke(kwargs)  # type: ignore[attr-defined]

    return StructuredTool.from_function(
        coroutine=invoke_adapter,
        name=str(getattr(tool_adapter, "name")),
        description=str(getattr(tool_adapter, "description")),
        args_schema=args_schema,
    )


def _auth_session_creator(mcp_registry: object) -> object | None:
    providers = getattr(mcp_registry, "providers", ())
    for provider in providers:
        if callable(getattr(provider, "create_auth_session", None)):
            return provider
    return None


def _instructions_with_mcp_cards(
    *, instructions: str, mcp_servers: Sequence[object]
) -> str:
    if not mcp_servers:
        return "\n\n".join(
            (
                instructions,
                NO_MCP_SERVER_CARDS_INSTRUCTIONS,
            )
        )
    card_lines = []
    for server in mcp_servers:
        name = getattr(server, "name", str(server))
        description = getattr(server, "short_description", "")
        auth_state = getattr(server, "auth_state", None)
        auth_value = getattr(auth_state, "value", auth_state) or "unknown"
        server_id = getattr(server, "server_id", None) or name
        display_name = getattr(server, "display_name", None) or name
        card_lines.append(
            f"- {name} ({display_name}, id={server_id}, auth_state={auth_value}): {description}"
        )
    return "\n\n".join(
        (
            instructions,
            MCP_SERVER_CARDS_INSTRUCTIONS,
            "\n".join(card_lines),
        )
    )


def _skill_cards(
    *, skill_registry: object | None, runtime_context: AgentRuntimeContext
) -> tuple[object, ...]:
    if skill_registry is None:
        return ()
    list_available = getattr(skill_registry, "list_available_skills", None)
    if not callable(list_available):
        return ()
    return tuple(list_available(runtime_context))


def _instructions_with_skill_cards(
    *, instructions: str, skill_cards: Sequence[object]
) -> str:
    if not skill_cards:
        return instructions
    card_lines = []
    for skill in skill_cards:
        name = getattr(skill, "name", str(skill))
        description = getattr(skill, "description", "")
        virtual_path = getattr(skill, "virtual_path", "")
        display_name = getattr(skill, "display_name", None) or name
        allowed_tools = tuple(getattr(skill, "allowed_tools", ()) or ())
        allowed = f", allowed_tools={','.join(allowed_tools)}" if allowed_tools else ""
        card_lines.append(
            f"- {name} ({display_name}, path={virtual_path}{allowed}): {description}"
        )
    return "\n\n".join(
        (
            instructions,
            SKILL_CARDS_INSTRUCTIONS,
            "\n".join(card_lines),
        )
    )


def _deepagents_memory_paths(memory_backend: object | None) -> tuple[str, ...]:
    """Return configured Deep Agents memory paths for compatible backends."""

    if not isinstance(memory_backend, DeepAgentsBackend):
        return ()
    return tuple(str(path) for path in memory_backend.memory_paths)


def _parse_context(
    context: AgentRuntimeContext | dict[str, Any],
) -> AgentRuntimeContext:
    if isinstance(context, AgentRuntimeContext):
        return context
    try:
        return AgentRuntimeContext.model_validate(context)
    except ValidationError as exc:
        raise AgentRuntimeError(
            RuntimeErrorCode.VALIDATION_ERROR,
            "Runtime context is invalid.",
            retryable=False,
        ) from exc


def _parse_dependencies(
    dependencies: RuntimeDependencies | dict[str, Any],
    correlation_id: str,
) -> RuntimeDependencies:
    if isinstance(dependencies, RuntimeDependencies):
        return dependencies
    try:
        return RuntimeDependencies.model_validate(dependencies)
    except ValidationError as exc:
        raise AgentRuntimeError(
            RuntimeErrorCode.DEPENDENCY_ERROR,
            "Runtime dependencies are invalid.",
            retryable=False,
            correlation_id=correlation_id,
        ) from exc
