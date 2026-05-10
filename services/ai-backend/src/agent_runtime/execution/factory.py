"""Runtime factory for the Deep Agents harness."""

from __future__ import annotations

import asyncio
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
from agent_runtime.execution.provider_kwargs import workspace_model_kwargs
from agent_runtime.execution.deep_agent_builder import (
    DeepAgentBuildRequest,
    DeepAgentsBackend,
    build_deep_agent,
    runtime_checkpointer,
)
from agent_runtime.api.constants import Values
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
from agent_runtime.capabilities.tools.builtin.ask_a_question import (
    AskAQuestionInput,
    AskAQuestionTool,
)
from agent_runtime.capabilities.tools.builtin.suggest_mcp_connector import (
    SuggestMcpConnectorInput,
    SuggestMcpConnectorTool,
)
from agent_runtime.capabilities.tools.prior_results import (
    LoadPriorToolResultInput,
    LoadPriorToolResultTool,
)
from agent_runtime.prompts.runtime import (
    DEFAULT_INSTRUCTIONS,
    MCP_SERVER_CARDS_INSTRUCTIONS,
    NO_MCP_SERVER_CARDS_INSTRUCTIONS,
    SKILL_CARDS_INSTRUCTIONS,
)
from agent_runtime.execution.atlas_task_tool import install_atlas_task_tool

# Replace deepagents' built-in `task` tool builder with ours so each
# subagent's RunnableConfig metadata carries `supervisor_task_call_id`.
# This makes the (subgraph_task_id → supervisor_call_id) linkage in the
# worker's stream handlers deterministic and removes the FIFO-pop
# heuristic that returned None whenever ≥2 subagents were unlinked
# concurrently (parallel research fleets).
install_atlas_task_tool()

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
    """Create a request-scoped Deep Agents runtime (synchronous).

    The runtime resolves authorized capabilities through injected ports before
    handing anything to the model-facing agent builder.

    Use :func:`acreate_agent_runtime` from async contexts (workers, ``ainvoke``).
    The async variant unblocks the event loop and parallelises the four sync
    listing calls; this sync entrypoint exists for the dev / LangChain Runnable
    ``invoke`` surface in :mod:`agent_runtime.execution.graph`.
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
    skill_directories = SkillSourceRegistry.skill_directories_for_deep_agent(
        runtime_dependencies.skill_source_config
    )

    return _assemble_harness(
        runtime_context=runtime_context,
        runtime_dependencies=runtime_dependencies,
        builder=builder,
        instructions=instructions,
        tools=tools,
        mcp_servers=mcp_servers,
        subagents=subagents,
        skill_directories=skill_directories,
    )


async def acreate_agent_runtime(
    *,
    context: AgentRuntimeContext | dict[str, Any],
    dependencies: RuntimeDependencies | dict[str, Any],
    instructions: str = DEFAULT_INSTRUCTIONS,
    agent_builder: AgentBuilder | None = None,
) -> RuntimeHarness:
    """Async variant of :func:`create_agent_runtime`.

    The four registry-listing calls (tools / mcp / subagents / skill
    directories) are sync and may do blocking I/O — most notably
    ``BackendMcpProvider.list_server_cards`` does a sync ``httpx.get`` to
    backend. Running them via ``asyncio.gather(asyncio.to_thread(...), ...)``
    accomplishes two things on every run-start:

    1. The worker's asyncio loop is no longer blocked for the duration of
       backend HTTP calls; other queued runs can interleave.
    2. The four listings run concurrently rather than sequentially.

    Post-fan-out assembly (prompt build, model kwargs, builder kickoff) stays
    sequential — it is CPU-bound and depends on the resolved values.
    """

    runtime_context = _parse_context(context)
    runtime_dependencies = _parse_dependencies(dependencies, runtime_context.trace_id)
    builder = agent_builder or build_deep_agent

    tools_raw, mcp_servers_raw, subagents_raw, skill_directories = await asyncio.gather(
        asyncio.to_thread(
            runtime_dependencies.tool_registry.list_available_tools, runtime_context
        ),
        asyncio.to_thread(
            runtime_dependencies.mcp_registry.list_available_servers, runtime_context
        ),
        asyncio.to_thread(
            runtime_dependencies.subagent_catalog.list_available_subagents,
            runtime_context,
        ),
        asyncio.to_thread(
            SkillSourceRegistry.skill_directories_for_deep_agent,
            runtime_dependencies.skill_source_config,
        ),
    )

    return _assemble_harness(
        runtime_context=runtime_context,
        runtime_dependencies=runtime_dependencies,
        builder=builder,
        instructions=instructions,
        tools=tuple(tools_raw),
        mcp_servers=tuple(mcp_servers_raw),
        subagents=tuple(subagents_raw),
        skill_directories=skill_directories,
    )


def _assemble_harness(
    *,
    runtime_context: AgentRuntimeContext,
    runtime_dependencies: RuntimeDependencies,
    builder: AgentBuilder,
    instructions: str,
    tools: tuple[object, ...],
    mcp_servers: tuple[object, ...],
    subagents: tuple[object, ...],
    skill_directories: tuple[str, ...],
) -> RuntimeHarness:
    """Shared post-listing assembly used by both sync and async factories.

    Everything in here either:
      * depends on the listed values and is local/cheap (no I/O), or
      * is the deepagents builder kickoff which is CPU-bound.

    Keeping it in one helper means ``create_agent_runtime`` and
    ``acreate_agent_runtime`` cannot diverge silently in their handling
    of the assembly path — they are required by definition to produce the
    same ``RuntimeHarness`` for a given resolved capability set.
    """

    # PR 1.3.5 — translate SubagentDefinition.fs_permissions to deepagents'
    # FilesystemPermission rules so subagents only get write access to
    # ``/drafts/`` (and other privileged prefixes) when their definition
    # explicitly grants it.
    deepagents_subagents = _subagents_with_fs_permissions(subagents)
    memory_backend = runtime_dependencies.memory_backend_factory.create(runtime_context)
    deep_backend = _composed_deep_backend(
        runtime_dependencies.subagent_artifacts_backend,
        drafts_backend=runtime_dependencies.drafts_backend,
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
            prior_tool_result_loader=runtime_dependencies.prior_tool_result_loader,
            runtime_context=runtime_context,
            mcp_discovery_enabled=runtime_dependencies.mcp_discovery_enabled,
        )
        model_instructions = _instructions_with_suggested_connectors(
            instructions=_instructions_with_skill_cards(
                instructions=_instructions_with_mcp_cards(
                    instructions=instructions,
                    mcp_servers=mcp_servers,
                ),
                skill_cards=skill_cards,
            ),
            suggestions=runtime_context.suggested_connectors,
        )
        # PR 4.3 — compute workspace-policy kwargs (e.g. training opt-out
        # provider headers) once per build and thread them through every
        # chat-model construction in the graph. Subagents inherit the
        # same kwargs because they share the runtime context.
        extra_model_kwargs = workspace_model_kwargs(
            provider=runtime_context.model_profile.provider,
            workspace_behavior_overrides=(
                runtime_context.workspace_behavior_overrides or None
            ),
        )
        agent = builder(
            DeepAgentBuildRequest(
                tools=model_tools,
                model_config=runtime_context.model_profile,
                system_prompt=model_instructions,
                subagents=deepagents_subagents,
                memory_backend=deep_backend
                if deep_backend is not None
                else (
                    memory_backend
                    if isinstance(memory_backend, DeepAgentsBackend)
                    else None
                ),
                memory_paths=_deepagents_memory_paths(memory_backend),
                skill_directories=skill_directories,
                interrupt_on=_native_interrupt_config(model_tools),
                checkpointer=runtime_checkpointer(),
                extra_model_kwargs=extra_model_kwargs or None,
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
    prior_tool_result_loader: object | None,
    runtime_context: AgentRuntimeContext,
    mcp_discovery_enabled: bool = False,
) -> tuple[object, ...]:
    model_tools = list(tools)
    auth_session_creator = _auth_session_creator(mcp_registry)
    local_tool_names = _local_tool_names(
        model_tools,
        include_mcp_tools=callable(getattr(mcp_registry, "resolve_server", None)),
        include_auth_mcp=auth_session_creator is not None,
        include_skill_loader=skill_registry is not None
        and callable(getattr(skill_registry, "load_skill_by_name", None)),
        include_mcp_discovery=mcp_discovery_enabled,
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
    if prior_tool_result_loader is not None:
        model_tools.append(
            _structured_tool(
                LoadPriorToolResultTool(
                    loader=prior_tool_result_loader,
                    runtime_context=runtime_context,
                ),
                LoadPriorToolResultInput,
            )
        )
    model_tools.append(
        _structured_tool(
            AskAQuestionTool(runtime_context=runtime_context),
            AskAQuestionInput,
        )
    )
    # PR 3.3 — non-blocking MCP discovery. Registered only when the
    # feature flag is on so the agent never sees the tool in a
    # deployment that hasn't opted in. The tool itself short-circuits
    # to ``discovery_disabled`` when no service is bound on the worker
    # side — defence in depth, never an authoritative gate.
    if mcp_discovery_enabled:
        model_tools.append(
            _structured_tool(
                SuggestMcpConnectorTool(),
                SuggestMcpConnectorInput,
            )
        )
    return tuple(model_tools)


def _native_interrupt_config(
    model_tools: Sequence[object],
) -> dict[str, object]:
    """Return DeepAgents HITL policies for model-visible gated tools."""

    tool_names = {str(getattr(tool, "name", "")).strip() for tool in model_tools}
    if McpValues.ToolName.CALL_MCP_TOOL not in tool_names:
        return {}
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
    include_mcp_discovery: bool = False,
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
    names.add(Values.Tool.ASK_A_QUESTION)
    if include_mcp_discovery:
        names.add(Values.Tool.SUGGEST_MCP_CONNECTOR)
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


def _instructions_with_suggested_connectors(
    *, instructions: str, suggestions: Sequence[object]
) -> str:
    """PR 4.4.7 Phase 2 (Slice B) — render the catalog suggestions
    section.

    Renders only when ``suggestions`` is non-empty so a run with no
    suggestible catalog entries pays no token tax. Each row carries
    just the slug, display name, and a one-line scope/description so
    the agent has enough context to map a user request to a relevant
    suggestion via the existing ``suggest_mcp_connector`` tool.
    """

    if not suggestions:
        return instructions
    lines = []
    for entry in suggestions:
        slug = getattr(entry, "slug", str(entry))
        display_name = getattr(entry, "display_name", slug)
        summary = getattr(entry, "scopes_summary", None) or getattr(
            entry, "description", ""
        )
        if summary:
            lines.append(f"- {slug} ({display_name}): {summary}")
        else:
            lines.append(f"- {slug} ({display_name})")
    return "\n\n".join(
        (
            instructions,
            (
                "## Suggestable integrations the user has not yet connected\n\n"
                "The capabilities below are available in the workspace "
                "catalog but are NOT installed for the current user.\n\n"
                "**When the user's request mentions or implies one of these "
                'slugs (e.g. "check my Linear tasks", "any new Notion '
                'pages?", "connect Asana"), you MUST:**\n'
                "1. Immediately call ``suggest_mcp_connector(slug, reason, "
                "expected_value)`` with the matching slug. This emits a "
                "Connect/Skip card the user can click — no extra "
                "confirmation from you needed.\n"
                "2. Then write a single short line to the user pointing at "
                "the card (e.g. \"Linear isn't connected yet — tap "
                'Connect above to set it up.").\n\n'
                "**Do NOT:**\n"
                "- Ask the user which option they want or list numbered "
                "alternatives. The Connect/Skip card is the one and only "
                "next step.\n"
                "- Call ``auth_mcp`` for these slugs. ``auth_mcp`` only "
                "works for servers the user has already installed; calling "
                "it on a catalog entry will fail.\n"
                "- Pretend you can already access these tools — you "
                "cannot, and saying you can will mislead the user.\n\n"
                "Suggest at most one connector per turn. If the user "
                "skipped a connector earlier in this run, do not re-suggest "
                "the same one.\n\n"
                "Available slugs:"
            ),
            "\n".join(lines),
        )
    )


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


def _composed_deep_backend(
    subagent_artifacts_backend: object | None,
    *,
    drafts_backend: object | None = None,
) -> object | None:
    """Wrap optional Atlas-specific backends in a deepagents ``CompositeBackend``.

    ``CompositeBackend`` routes paths to per-prefix backends and falls back to
    a default. We register two prefixes:

    - ``/subagents/`` → read-only subagent execution trace projection.
    - ``/drafts/`` → versioned, append-only Workspace-pane draft persistence
      (PR 1.3). Catches the agent's existing ``write_file`` / ``edit_file``
      tool calls and turns them into ``runtime_drafts`` rows + ``DRAFT_UPDATED``
      events.

    Other FS paths (``/memories/``, ``/skills/``, …) stay on deepagents'
    ``StateBackend`` default.
    """

    routes: dict[str, object] = {}
    if subagent_artifacts_backend is not None:
        routes["/subagents/"] = subagent_artifacts_backend
    if drafts_backend is not None:
        routes["/drafts/"] = drafts_backend
    if not routes:
        return None
    from deepagents.backends.composite import CompositeBackend
    from deepagents.backends.state import StateBackend

    return CompositeBackend(default=StateBackend(), routes=routes)


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


def _subagents_with_fs_permissions(
    subagents: tuple[object, ...],
) -> tuple[object, ...]:
    """Attach deepagents ``FilesystemPermission`` rules to subagents that need them.

    For each :class:`SubagentDefinition` whose ``fs_permissions`` is non-empty,
    we attach the translated rules onto the object. Subagents whose
    definition has no ``fs_permissions`` are passed through unchanged so the
    deepagents middleware applies the parent agent's permissions to them
    (the existing default).

    The translation is best-effort: if deepagents is unavailable at import
    time, or if the subagent isn't a SubagentDefinition, we pass through
    unchanged. Tests assert the rule list shape, not deepagents internals.
    """

    if not subagents:
        return subagents
    try:
        from deepagents.middleware.filesystem import (  # noqa: PLC0415
            FilesystemPermission,
        )
    except ImportError:  # pragma: no cover — deepagents always present in prod
        return subagents
    from agent_runtime.delegation.subagents.contracts import (  # noqa: PLC0415
        SubagentDefinition,
    )

    translated: list[object] = []
    for subagent in subagents:
        specs = getattr(subagent, "fs_permissions", None) or ()
        if not isinstance(subagent, SubagentDefinition) or not specs:
            translated.append(subagent)
            continue
        rules = [
            FilesystemPermission(
                operations=list(spec.operations),
                paths=list(spec.paths),
                mode=spec.mode,
            )
            for spec in specs
        ]
        # The deepagents subagent contract reads ``permissions`` off the
        # subagent object. We attach the rules as a non-Pydantic attribute
        # using the model's underlying ``__dict__`` so Pydantic's frozen-
        # validation doesn't reject the assignment.
        try:
            object.__setattr__(subagent, "permissions", rules)
        except (AttributeError, TypeError):  # pragma: no cover — defensive
            pass
        translated.append(subagent)
    return tuple(translated)
