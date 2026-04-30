"""Runtime factory for the Deep Agents harness."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
import inspect
from importlib import import_module
from typing import Any

from pydantic import ValidationError

from agent_runtime.execution.contracts import (
    AgentRuntimeContext,
    RuntimeDependencies,
    RuntimeErrorCode,
)
from agent_runtime.execution.errors import AgentRuntimeError
from agent_runtime.capabilities.mcp.loader import McpLoader
from agent_runtime.capabilities.mcp.middleware.auth_mcp import AuthMcpTool
from agent_runtime.capabilities.mcp.middleware.dynamic_loader import LoadMcpServerTool
from agent_runtime.capabilities.skills.constants import Keys as SkillKeys
from agent_runtime.capabilities.skills.sources import SkillSourceRegistry

AgentBuilder = Callable[..., object]

DEFAULT_INSTRUCTIONS = (
    "You are the agent runtime. Respect the provided runtime "
    "context, expose only authorized capabilities, and return grounded answers."
)


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
    builder = agent_builder or _build_deep_agent

    tools = tuple(runtime_dependencies.tool_registry.list_available_tools(runtime_context))
    mcp_servers = tuple(runtime_dependencies.mcp_registry.list_available_servers(runtime_context))
    subagents = tuple(
        runtime_dependencies.subagent_catalog.list_available_subagents(runtime_context)
    )
    memory_backend = runtime_dependencies.memory_backend_factory.create(runtime_context)
    skill_directories = SkillSourceRegistry.skill_directories_for_deep_agent(
        runtime_dependencies.skill_source_config
    )

    try:
        model_tools = _model_visible_tools(
            tools=tools,
            mcp_registry=runtime_dependencies.mcp_registry,
            runtime_context=runtime_context,
        )
        model_instructions = _instructions_with_mcp_cards(
            instructions=instructions,
            mcp_servers=mcp_servers,
        )
        agent = builder(
            tools=model_tools,
            model_config=runtime_context.model_profile,
            instructions=model_instructions,
            runtime_context=runtime_context,
            mcp_servers=mcp_servers,
            subagents=subagents,
            memory_backend=memory_backend,
            stream_normalizer=runtime_dependencies.stream_normalizer,
            **{SkillKeys.DeepAgents.SKILLS: skill_directories},
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
    )


def _build_deep_agent(
    *,
    tools: Sequence[object],
    model_config: object,
    instructions: str,
    memory_backend: object | None = None,
    skills: Sequence[str] = (),
    **_: object,
) -> object:
    """Build the concrete Deep Agents graph without importing it at module load."""

    try:
        deepagents = import_module("deepagents")
        create_deep_agent = getattr(deepagents, "create_deep_agent")
    except Exception as exc:
        raise AgentRuntimeError(
            RuntimeErrorCode.CONFIGURATION_ERROR,
            "Deep Agents is not installed or is not importable.",
            retryable=False,
        ) from exc

    model_name = getattr(model_config, "model_name")
    create_kwargs: dict[str, object] = {
        "tools": list(tools),
        "model": model_name,
    }
    parameters = inspect.signature(create_deep_agent).parameters
    if "instructions" in parameters:
        create_kwargs["instructions"] = instructions
    else:
        create_kwargs["system_prompt"] = instructions
    if skills:
        create_kwargs[SkillKeys.DeepAgents.SKILLS] = list(skills)
    if _is_deepagents_backend(memory_backend):
        create_kwargs["backend"] = memory_backend
        memory_paths = tuple(getattr(memory_backend, "memory_paths", ()))
        if memory_paths:
            create_kwargs["memory"] = list(memory_paths)
    return create_deep_agent(**create_kwargs)


def _model_visible_tools(
    *,
    tools: Sequence[object],
    mcp_registry: object,
    runtime_context: AgentRuntimeContext,
) -> tuple[object, ...]:
    model_tools = list(tools)
    if callable(getattr(mcp_registry, "resolve_server", None)):
        loader = McpLoader(mcp_registry)  # type: ignore[arg-type]
        model_tools.append(LoadMcpServerTool(loader=loader, runtime_context=runtime_context))
    auth_session_creator = _auth_session_creator(mcp_registry)
    if auth_session_creator is not None:
        model_tools.append(
            AuthMcpTool(
                auth_session_creator=auth_session_creator,
                runtime_context=runtime_context,
            )
        )
    return tuple(model_tools)


def _auth_session_creator(mcp_registry: object) -> object | None:
    providers = getattr(mcp_registry, "providers", ())
    for provider in providers:
        if callable(getattr(provider, "create_auth_session", None)):
            return provider
    return None


def _instructions_with_mcp_cards(*, instructions: str, mcp_servers: Sequence[object]) -> str:
    if not mcp_servers:
        return instructions
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
            "Available MCP servers are compact cards. If a needed MCP server is not "
            "authenticated, call auth_mcp before trying to load its tools. If it is "
            "authenticated, call load_mcp_server by stable server name before using its tools.",
            "\n".join(card_lines),
        )
    )


def _is_deepagents_backend(memory_backend: object | None) -> bool:
    """Return whether the object implements the DeepAgents backend protocol."""

    if memory_backend is None:
        return False
    return all(
        hasattr(memory_backend, method)
        for method in ("download_files", "upload_files", "adownload_files", "aupload_files")
    )


def _parse_context(context: AgentRuntimeContext | dict[str, Any]) -> AgentRuntimeContext:
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
