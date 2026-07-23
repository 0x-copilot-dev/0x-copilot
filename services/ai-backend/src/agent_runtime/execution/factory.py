"""Runtime factory for the Deep Agents harness."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

from langchain_core.tools import StructuredTool
from pydantic import ValidationError

from agent_runtime.execution.contracts import (
    AgentRuntimeContext,
    RuntimeDependencies,
    RuntimeErrorCode,
)
from agent_runtime.execution.errors import AgentRuntimeError
from agent_runtime.execution.provider_kwargs import (
    RegionUnavailableError,
    user_policy_model_kwargs,
    workspace_model_kwargs,
)
from agent_runtime.execution.deep_agent_builder import (
    CODE_MODE_GUIDANCE,
    SANDBOX_EXECUTE_GUIDANCE,
    WORKSPACE_ACCESS_GUIDANCE,
    WORKSPACE_WRITE_GUIDANCE,
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
from agent_runtime.capabilities.tools.builtin.stage_rowset_write import (
    StageRowsetWriteInput,
)
from agent_runtime.capabilities.tools.builtin.suggest_mcp_connector import (
    SuggestMcpConnectorInput,
    SuggestMcpConnectorTool,
)
from agent_runtime.capabilities.tools.prior_results import (
    LoadPriorToolResultInput,
    LoadPriorToolResultTool,
)
from agent_runtime.capabilities.tools.tool_use_enforcement import (
    ToolUsePolicyEnforcer,
    ToolUsePolicyResolver,
)
from agent_runtime.prompts.runtime import (
    DEFAULT_INSTRUCTIONS,
    MCP_SERVER_CARDS_INSTRUCTIONS,
    NO_MCP_SERVER_CARDS_INSTRUCTIONS,
    SKILL_CARDS_INSTRUCTIONS,
)
from agent_runtime.delegation.subagents.atlas_task_tool import install_atlas_task_tool

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


async def acreate_agent_runtime(
    *,
    context: AgentRuntimeContext | dict[str, Any],
    dependencies: RuntimeDependencies | dict[str, Any],
    instructions: str = DEFAULT_INSTRUCTIONS,
    agent_builder: AgentBuilder | None = None,
) -> RuntimeHarness:
    """Create a request-scoped Deep Agents runtime (async-native).

    The five registry-listing calls (tools / mcp / subagents / skill
    directories / skill cards) are run concurrently via ``asyncio.gather``.
    The MCP registry, skill-card registry, and skill-directory resolver are
    async-native end-to-end (their backend HTTP calls use
    ``httpx.AsyncClient``), so they ``await`` directly. The tool registry
    and subagent catalog are CPU-bound in-memory listings; we still wrap
    them in ``asyncio.to_thread`` to keep the event loop responsive even if
    a custom registry implementation happens to do blocking work.

    Post-fan-out assembly (prompt build, model kwargs, builder kickoff) stays
    sequential — it is CPU-bound and depends on the resolved values.

    Adding a new registry to this fan-out? It must be independent of the
    other branches' outputs — see ``docs/refactor/03-parallel-bootstrap.md``.
    """

    runtime_context = _parse_context(context)
    runtime_dependencies = _parse_dependencies(dependencies, runtime_context.trace_id)
    builder = agent_builder or build_deep_agent

    (
        tools_raw,
        mcp_servers_raw,
        subagents_raw,
        skill_directories,
        skill_cards,
    ) = await asyncio.gather(
        asyncio.to_thread(
            runtime_dependencies.tool_registry.list_available_tools, runtime_context
        ),
        runtime_dependencies.mcp_registry.list_available_servers(runtime_context),
        asyncio.to_thread(
            runtime_dependencies.subagent_catalog.list_available_subagents,
            runtime_context,
        ),
        asyncio.to_thread(
            SkillSourceRegistry.skill_directories_for_deep_agent,
            runtime_dependencies.skill_source_config,
        ),
        _skill_cards(
            skill_registry=runtime_dependencies.skill_registry,
            runtime_context=runtime_context,
        ),
    )

    return await _assemble_harness(
        runtime_context=runtime_context,
        runtime_dependencies=runtime_dependencies,
        builder=builder,
        instructions=instructions,
        tools=tuple(tools_raw),
        mcp_servers=tuple(mcp_servers_raw),
        subagents=tuple(subagents_raw),
        skill_directories=skill_directories,
        skill_cards=skill_cards,
    )


async def _assemble_harness(
    *,
    runtime_context: AgentRuntimeContext,
    runtime_dependencies: RuntimeDependencies,
    builder: AgentBuilder,
    instructions: str,
    tools: tuple[object, ...],
    mcp_servers: tuple[object, ...],
    subagents: tuple[object, ...],
    skill_directories: tuple[str, ...],
    skill_cards: tuple[object, ...],
) -> RuntimeHarness:
    """Shared post-listing assembly used by both sync and async factories.

    Everything in here either:
      * depends on the listed values and is local/cheap (no I/O), or
      * is the deepagents builder kickoff which is CPU-bound.

    Keeping it in one helper means ``create_agent_runtime`` and
    ``acreate_agent_runtime`` cannot diverge silently in their handling
    of the assembly path — they are required by definition to produce the
    same ``RuntimeHarness`` for a given resolved capability set.

    ``skill_cards`` is resolved upstream as the 5th branch of the
    ``acreate_agent_runtime`` gather, removing the last sequential await
    between the listing pass and the builder kickoff.
    """

    # Translate SubagentDefinition.fs_permissions to deepagents'
    # FilesystemPermission rules so subagents only get write access to
    # ``/drafts/`` (and other privileged prefixes) when their definition
    # explicitly grants it.
    deepagents_subagents = _subagents_with_fs_permissions(subagents)
    memory_backend = runtime_dependencies.memory_backend_factory.create(runtime_context)
    workspace_backend = runtime_dependencies.workspace_backend
    # Host writes are live only when the workspace backend reports write
    # authority (a writable grant + a per-run capability context + a snapshot
    # store). This one signal gates BOTH the approval permission and the
    # writable prompt guidance.
    workspace_writable = bool(getattr(workspace_backend, "supports_writes", False))
    deep_backend = _composed_deep_backend(
        runtime_dependencies.subagent_artifacts_backend,
        drafts_backend=runtime_dependencies.drafts_backend,
        large_tool_results_backend=runtime_dependencies.large_tool_results_backend,
        workspace_backend=workspace_backend,
        memory_routes=_file_memory_routes(memory_backend),
    )

    try:
        model_tools = _model_visible_tools(
            tools=tools,
            mcp_registry=runtime_dependencies.mcp_registry,
            skill_registry=runtime_dependencies.skill_registry,
            prior_tool_result_loader=runtime_dependencies.prior_tool_result_loader,
            mcp_discovery_cache=runtime_dependencies.mcp_discovery_cache,
            code_mode_tool=runtime_dependencies.code_mode_tool,
            sandbox_execute_tool=runtime_dependencies.sandbox_execute_tool,
            stage_rowset_write_tool=runtime_dependencies.stage_rowset_write_tool,
            runtime_context=runtime_context,
        )
        # Enforce the per-(org, user) tool-use policy on the model tool surface.
        # ``call_mcp_tool`` (and any future gated umbrella tool) is routed to
        # the SAME human-approval interrupt for ask/require, blocked with a safe
        # result for block, or left untouched for auto. Fails open to the
        # deployment default snapshot (write=ask → the existing MCP approval)
        # when no policy is configured, so an unconfigured run is unchanged.
        enforced_tools = ToolUsePolicyEnforcer.enforce(
            model_tools=model_tools,
            snapshot=ToolUsePolicyResolver.resolve(runtime_context),
        )
        model_tools = enforced_tools.tools
        model_instructions = _instructions_with_capability_tools(
            instructions=_instructions_with_workspace(
                instructions=_instructions_with_suggested_connectors(
                    instructions=_instructions_with_skill_cards(
                        instructions=_instructions_with_mcp_cards(
                            instructions=instructions,
                            mcp_servers=mcp_servers,
                        ),
                        skill_cards=skill_cards,
                    ),
                    suggestions=runtime_context.suggested_connectors,
                ),
                workspace_active=workspace_backend is not None,
                workspace_writable=workspace_writable,
            ),
            code_mode_active=runtime_dependencies.code_mode_tool is not None,
            sandbox_execute_active=runtime_dependencies.sandbox_execute_tool
            is not None,
        )
        # Compute workspace-policy kwargs (e.g. training opt-out provider
        # headers) once per build and thread them through every
        # chat-model construction in the graph. Subagents inherit the
        # same kwargs because they share the runtime context.
        extra_model_kwargs = workspace_model_kwargs(
            provider=runtime_context.model_profile.provider,
            workspace_behavior_overrides=(
                runtime_context.workspace_behavior_overrides or None
            ),
        )
        # Per-user policy + BYOK kwargs are merged AFTER workspace kwargs so
        # the user's opt-out ratchet, region pin, and ``api_key`` win on any
        # conflict. ``provider_keys`` is the in-memory (never persisted)
        # context field — the resulting kwargs must not be logged.
        try:
            extra_model_kwargs.update(
                user_policy_model_kwargs(
                    provider=runtime_context.model_profile.provider,
                    user_policies_json=runtime_context.user_policies_json or None,
                    provider_keys=runtime_context.provider_keys or None,
                    provider_endpoints=runtime_context.provider_endpoints or None,
                )
            )
        except RegionUnavailableError as exc:
            raise AgentRuntimeError(
                RuntimeErrorCode.CONFIGURATION_ERROR,
                f"Data residency region '{exc.region}' is not configured "
                f"for model provider '{exc.provider}'.",
                retryable=False,
                correlation_id=runtime_context.trace_id,
            ) from exc
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
                interrupt_on=enforced_tools.interrupt_on,
                permissions=_workspace_write_permissions(workspace_writable),
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
    mcp_discovery_cache: object | None,
    code_mode_tool: object | None = None,
    sandbox_execute_tool: object | None = None,
    stage_rowset_write_tool: object | None = None,
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
        include_mcp_discovery=True,
    )
    # The cache is constructed at lifespan startup (API) or worker dependency
    # wiring (worker) and threaded through via ``RuntimeDependencies``. We
    # accept ``object | None`` here so test fakes can pass ``None`` without
    # importing the cache type, but the loader and auth tool require the
    # concrete ``McpDiscoveryCache`` to opt in.
    from agent_runtime.capabilities.mcp.discovery_cache import McpDiscoveryCache

    typed_discovery_cache: McpDiscoveryCache | None = (
        mcp_discovery_cache
        if isinstance(mcp_discovery_cache, McpDiscoveryCache)
        else None
    )
    if callable(getattr(mcp_registry, "resolve_server", None)):
        loader = McpLoader(mcp_registry, cache=typed_discovery_cache)  # type: ignore[arg-type]
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
                    gate=_tool_access_gate(
                        auth_session_creator=auth_session_creator,
                        runtime_context=runtime_context,
                    ),
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
                    cache=typed_discovery_cache,
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
    model_tools.append(
        _structured_tool(
            SuggestMcpConnectorTool(),
            SuggestMcpConnectorInput,
        )
    )
    # Gated Wave-1 capability tools. Each is a fully-built ``StructuredTool``
    # (constructed per run by the worker) or ``None`` when its flag+desktop gate
    # is off. Appended last so they receive the SAME tool-policy / approval /
    # budget middleware every other model tool does — they are not privileged.
    if code_mode_tool is not None:
        model_tools.append(code_mode_tool)
    if sandbox_execute_tool is not None:
        model_tools.append(sandbox_execute_tool)
    # PRD-D3 — the gated bulk row-set staging tool. Injected as a domain adapter
    # (the worker builds it per run when SURFACES_V2 is on) and wrapped here with
    # its typed schema, like the other builtin tools. Flag off ⇒ `None` ⇒ absent.
    if stage_rowset_write_tool is not None:
        model_tools.append(
            _structured_tool(stage_rowset_write_tool, StageRowsetWriteInput)
        )
    return tuple(model_tools)


#: Virtual prefix every host-folder mount lives under. A single ``interrupt``
#: rule on ``/workspace/**`` writes gates ALL host mutations for approval,
#: regardless of which mount/grant is addressed; the broker's per-grant
#: mode-gate remains the final authority.
_WORKSPACE_WRITE_GLOB: Final = "/workspace/**"


def _workspace_write_permissions(workspace_writable: bool) -> tuple[object, ...]:  # noqa: FBT001
    """Return the host-write approval permission when the run can write to host folders.

    A single Deep Agents ``FilesystemPermission`` with ``mode="interrupt"`` over
    ``/workspace/**`` writes routes every host ``write_file`` / ``edit_file``
    through the SAME ``HumanInTheLoopMiddleware`` that gates MCP tools: the tool
    call pauses for human approval BEFORE the backend mutation runs. Returns an
    empty tuple when the run has no writable host grant, so the read-only /
    non-desktop path installs no permission and stays byte-identical.
    """
    if not workspace_writable:
        return ()
    # Imported lazily so the deepagents permission type is referenced in exactly
    # one place and non-workspace runs never touch it.
    from deepagents.middleware.filesystem import (  # noqa: PLC0415
        FilesystemPermission,
    )

    return (
        FilesystemPermission(
            operations=["write"],
            paths=[_WORKSPACE_WRITE_GLOB],
            mode="interrupt",
        ),
    )


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
    """Wrap a domain tool adapter as a LangChain ``StructuredTool`` with a typed schema."""

    async def invoke_adapter(**kwargs: Any) -> object:
        return await tool_adapter.ainvoke(kwargs)  # type: ignore[attr-defined]

    return StructuredTool.from_function(
        coroutine=invoke_adapter,
        name=str(getattr(tool_adapter, "name")),
        description=str(getattr(tool_adapter, "description")),
        args_schema=args_schema,
    )


def _auth_session_creator(mcp_registry: object) -> object | None:
    """Return the first MCP registry provider that supports OAuth session creation, or None."""
    providers = getattr(mcp_registry, "providers", ())
    for provider in providers:
        if callable(getattr(provider, "create_auth_session", None)):
            return provider
    return None


def _tool_access_gate(
    *,
    auth_session_creator: object | None,
    runtime_context: AgentRuntimeContext,
) -> object | None:
    """Build the PRD-C2 ToolAccessGate, or ``None`` when no OAuth provider exists.

    Reuses the SAME OAuth-capable provider ``AuthMcpTool`` gets (the
    ``create_auth_session`` duck-probe) so a gate can only park a run when the
    runtime can actually start the connect flow. Wired with C1's
    ``ActionClassifier`` (over the module-level catalog) so the gate card's
    read-only pledge / write-policy choice is honest — an absent classifier fails
    closed to ``write`` inside the gate. Returned as ``object | None`` so the
    ``CallMcpTool`` construction site stays type-agnostic; the whole gate path is
    additionally guarded by ``SurfacesV2Flag`` at call time (flag off ⇒ inert).
    """

    if auth_session_creator is None:
        return None
    from agent_runtime.capabilities.actions.classifier import (  # noqa: PLC0415
        ACTION_CLASSIFIER,
    )
    from agent_runtime.surfaces_v2.gate import ToolAccessGate  # noqa: PLC0415

    return ToolAccessGate(
        auth_session_creator=auth_session_creator,  # type: ignore[arg-type]
        runtime_context=runtime_context,
        classifier=ACTION_CLASSIFIER,
    )


def _instructions_with_mcp_cards(
    *, instructions: str, mcp_servers: Sequence[object]
) -> str:
    """Append the MCP server card block (or the no-servers block) to the base instructions."""
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


async def _skill_cards(
    *, skill_registry: object | None, runtime_context: AgentRuntimeContext
) -> tuple[object, ...]:
    """Fetch skill cards from the registry, or return an empty tuple when absent."""
    if skill_registry is None:
        return ()
    list_available = getattr(skill_registry, "list_available_skills", None)
    if not callable(list_available):
        return ()
    return tuple(await list_available(runtime_context))  # type: ignore[arg-type]


def _instructions_with_suggested_connectors(
    *, instructions: str, suggestions: Sequence[object]
) -> str:
    """Append the catalog suggestions block to the base instructions.

    Renders only when ``suggestions`` is non-empty so a run with no
    suggestible catalog entries pays no token tax. Each row carries
    just the slug, display name, and a one-line scope/description so
    the agent can map a user request to a relevant suggestion via the
    ``suggest_mcp_connector`` tool.
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
    """Append the skill card block to the base instructions when skills are available."""
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
    large_tool_results_backend: object | None = None,
    workspace_backend: object | None = None,
    memory_routes: Mapping[str, object] | None = None,
) -> object | None:
    """Wrap optional Atlas-specific backends in a deepagents ``CompositeBackend``.

    ``CompositeBackend`` routes paths to per-prefix backends and falls back to
    a default. We register up to four Atlas prefixes plus the file-native
    memory routes:

    - ``/subagents/`` → read-only subagent execution trace. On the desktop file
      store this is a file-native reader over the canonical per-subagent JSONL;
      elsewhere it is the on-demand event-store projection.
    - ``/drafts/`` → versioned, append-only Workspace-pane draft persistence.
      Catches the agent's existing ``write_file`` / ``edit_file``
      tool calls and turns them into ``runtime_drafts`` rows + ``DRAFT_UPDATED``
      events.
    - ``/large_tool_results/`` → read-only resolver for offloaded oversized tool
      results from the desktop file store's object store. ``None`` (unrouted)
      on every other backend, so those paths stay on the ``StateBackend``
      default exactly as before.
    - ``/workspace/`` → read-only view of user-granted host folders, backed by
      the desktop capability broker. Present only on the desktop path when the
      broker is configured and the run has at least one active grant; ``None``
      (unrouted) everywhere else, so those paths stay on the ``StateBackend``
      default exactly as before.
    - ``memory_routes`` → the file-native memory prefixes
      (``/memories/`` · ``/policies/`` · ``/skills/``) produced by
      :class:`~runtime_adapters.file.FileMemoryBackendFactory` when the desktop
      file store is active. Mounting them here makes the agent's built-in
      ``read_file`` / ``write_file`` / ``edit_file`` on those paths persist as
      inspectable ``memory/<scope>/<key>.json`` (+ human ``.md``) files instead
      of the ephemeral ``StateBackend``. ``None`` off the file store, so those
      paths stay on the ``StateBackend`` default exactly as before.

    Any FS path not routed above (and, off the file store, ``/memories/`` &c.)
    stays on deepagents' ``StateBackend`` default.
    """

    routes: dict[str, object] = {}
    if subagent_artifacts_backend is not None:
        routes["/subagents/"] = subagent_artifacts_backend
    if drafts_backend is not None:
        routes["/drafts/"] = drafts_backend
    if large_tool_results_backend is not None:
        routes["/large_tool_results/"] = large_tool_results_backend
    if workspace_backend is not None:
        # Single source of truth for the prefix lives with the backend, so
        # wiring and its own path handling cannot drift.
        from agent_runtime.capabilities.desktop import ROUTE_PREFIX  # noqa: PLC0415

        routes[ROUTE_PREFIX] = workspace_backend
    if memory_routes:
        # The FileMemoryBackendFactory owns which memory prefixes exist for the
        # run, so we mount exactly what it produced rather than hard-coding the
        # prefix list here — wiring and route planning cannot drift.
        routes.update(memory_routes)
    if not routes:
        return None
    from deepagents.backends.composite import CompositeBackend
    from deepagents.backends.state import StateBackend

    return CompositeBackend(default=StateBackend(), routes=routes)


def _file_memory_routes(memory_backend: object) -> Mapping[str, object] | None:
    """Return the ``{path_prefix: FileMemoryBackend}`` map when the file store is active.

    ``ScopedMemoryBackendFactory.create`` returns a
    :class:`~agent_runtime.context.memory.backends.MemoryRoutePlan` off the file
    store (no injected ``backend_builder``) and a ``{path_prefix: FileMemoryBackend}``
    mapping on it (its ``backend_builder`` is
    :class:`~runtime_adapters.file.FileMemoryBackendFactory`). Only the mapping
    form is mountable into the composite backend; every other shape — the route
    plan, a test fake's sentinel, ``None`` — yields ``None`` here so memory keeps
    routing to the deepagents ``StateBackend`` default exactly as before.
    """

    if not isinstance(memory_backend, Mapping) or not memory_backend:
        return None
    routes = {
        prefix: backend
        for prefix, backend in memory_backend.items()
        if isinstance(prefix, str) and prefix and backend is not None
    }
    return routes or None


def _instructions_with_workspace(
    *,
    instructions: str,
    workspace_active: bool,
    workspace_writable: bool = False,
) -> str:
    """Append the ``/workspace/`` guidance block when the route is active.

    Gated on the composed ``/workspace/`` route existing for this run: off the
    desktop path (or with no granted folders) ``workspace_active`` is ``False``
    and the prompt is returned unchanged, so non-desktop runs pay no token tax
    and never advertise a route they do not have. When at least one mount is
    writable, the writable guidance (host writes allowed but approval-gated)
    replaces the strictly-read-only block.
    """

    if not workspace_active:
        return instructions
    guidance = (
        WORKSPACE_WRITE_GUIDANCE if workspace_writable else WORKSPACE_ACCESS_GUIDANCE
    )
    return "\n\n".join((instructions, guidance))


def _instructions_with_capability_tools(
    *,
    instructions: str,
    code_mode_active: bool,
    sandbox_execute_active: bool,
) -> str:
    """Append gated Wave-1 capability-tool guidance blocks when their tools exist.

    Each block is gated on the tool actually being present for the run (the
    worker built it because its flag+desktop gate held). Off those paths the
    tools are absent, both flags are ``False``, and the prompt is returned
    unchanged so non-desktop / disabled runs pay no token tax.
    """

    blocks = [instructions]
    if code_mode_active:
        blocks.append(CODE_MODE_GUIDANCE)
    if sandbox_execute_active:
        blocks.append(SANDBOX_EXECUTE_GUIDANCE)
    if len(blocks) == 1:
        return instructions
    return "\n\n".join(blocks)


def _parse_context(
    context: AgentRuntimeContext | dict[str, Any],
) -> AgentRuntimeContext:
    """Coerce a raw dict or typed context into a validated ``AgentRuntimeContext``."""
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
    """Coerce a raw dict or typed object into a validated ``RuntimeDependencies``."""
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
