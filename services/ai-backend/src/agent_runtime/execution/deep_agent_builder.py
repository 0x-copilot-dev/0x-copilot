"""Concrete Deep Agents construction for the runtime factory."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from deepagents import HarnessProfile, create_deep_agent, register_harness_profile
from langchain.agents.middleware import AgentMiddleware
from langchain.chat_models import init_chat_model
from langchain.embeddings import init_embeddings
from langchain_core.embeddings import Embeddings
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import SystemMessage

from agent_runtime.execution.contracts import (
    ModelConfig,
    ModelReasoningEffort,
    ModelThinkingMode,
    RuntimeErrorCode,
)
from agent_runtime.execution.errors import AgentRuntimeError
from agent_runtime.execution.fake_model import FakeModelProvider
from agent_runtime.execution.openai_compat import OpenAICompatibleProviders
from agent_runtime.execution.tool_surface import (
    DEEP_AGENT_PROFILE_EXCLUDED_TOOL_NAMES,
)

WEB_EXCLUDED_DEEP_AGENT_TOOLS = DEEP_AGENT_PROFILE_EXCLUDED_TOOL_NAMES
_WEB_HARNESS_PROFILE_KEYS = (
    "anthropic",
    # Hermetic runtime tests use this provider identity. Keeping it on the
    # same profile is what lets integration tests exercise the production
    # middleware topology instead of silently dropping graph-wide controls.
    "deterministicfakechatmodel",
    "gemini",
    "google_genai",
    "openai",
)
# Layered onto every Deep Agents subagent prompt (and the supervisor) to keep
# tool sequences bounded and surface intermediate progress. The earlier wording
# (`pause and emit a checkpoint as a plain-text message before calling another
# tool`) produced an AIMessage with `tool_calls=[]`, which Deep Agents'
# subagent loop treats as the final answer — subagents terminated on the
# checkpoint message and supervisors re-dispatched the same task. The current
# wording requires the checkpoint to ride in the same AIMessage as the next
# tool call so the loop continues, and reserves a tool-call-free message for
# the explicit final answer.
# The per-tool cap the model is TOLD in the suffix below. It must equal the
# enforced seed cap ``DefaultToolBudget.MAX_CALLS_PER_RUN`` (10): telling the
# model a smaller number than ``ToolBudgetMiddleware`` admits is the
# prompt-vs-enforced drift the SSOT gate
# (tests/unit/architecture/test_named_default_ssot.py) exists to catch. It was
# ``5`` against an enforced ``10``; T0.3 closed the gap. A literal, not an
# import, to keep this execution module off the persistence layer — the gate
# keeps the two in step.
#
# ``hyperparameters.json`` states this as ``execution.tool_call_budget`` and the
# mapper pair below as ``model_mapper``, but this module cannot read either:
# ``execution/__init__.py`` reaches it while ``execution/contracts.py`` is still
# mid-import, and the document's contracts import ``delegation.subagents``,
# whose eager package ``__init__`` imports ``execution.contracts`` right back.
# Binding here fails EVERY import of ``agent_runtime``. Blocked until the
# document sources its subagent ceilings without that package import.
_DEFAULT_TOOL_CALL_BUDGET = 10


def format_web_subagent_suffix(
    tool_call_budget: int = _DEFAULT_TOOL_CALL_BUDGET,
) -> str:
    """Build the supervisor / subagent prompt suffix with a dynamic per-tool cap.

    The cap interpolated here mirrors the value ``ToolBudgetMiddleware``
    hard-enforces so the model receives a consistent contract.
    """

    return (
        "When you call multiple tools, every 2 to 3 tool calls include a short "
        "progress checkpoint as the assistant message's `content` while ALSO "
        "calling your next tool in the SAME message. The checkpoint should "
        "briefly state what you have learned so far, what is still missing, and "
        "which tool you are about to call next. Do NOT emit a checkpoint without "
        "an accompanying tool call — a message with no tool call is treated as "
        "your final answer. When you genuinely have no more tools to call, write "
        "your final answer instead of a checkpoint.\n\n"
        "Plan web_search queries before issuing them. Decide which 1–3 distinct "
        "queries you actually need — each targeting a different facet (different "
        "entity, attribute, time period, or source via `site:`). Do NOT "
        "paraphrase a query whose prior result was already usable; the per-tool "
        "cap is for new angles, not retries or double-checks. If two consecutive "
        "searches return the same sources or add nothing beyond what you "
        "already have, stop searching and answer with what you have plus an "
        "honest note on what is still uncertain. The `web-search-discipline` "
        "skill has deeper guidance — load it when planning a search batch or "
        "when consecutive searches stop helping.\n\n"
        f"Bound any single tool to at most {tool_call_budget} invocations within "
        f"one task: after {tool_call_budget} calls of the same tool, stop "
        "calling that tool and return your final answer summarizing what you "
        "found, even if your answer is incomplete or uncertain. A partial "
        "answer with citations beats an exhausted budget. "
        'Open-ended phrasing in the request ("many", "comprehensive", '
        '"thorough") does not lift this cap — pick the most informative queries '
        "and stop.\n\n"
        "You do not have to track this yourself. As a tool nears its cap its "
        "results carry a note of the form `[Tool budget — <tool_name>: X of Y "
        "calls used this turn, Z calls left.]`. Treat that as a planning "
        "signal, not a warning to acknowledge: with a small number left, spend "
        "them on the gaps that matter most rather than on refinements of what "
        "you already know. At zero, do not call that tool again — write the "
        "answer. The count covers the whole turn, including calls your "
        "delegated subagents make, and it resets when the user sends the next "
        "message. If a call is refused because the budget is gone, that is not "
        "an error to report or retry: finalize with what you have and say "
        "plainly what is still uncertain.\n\n"
        "Subagent execution traces from this and prior turns are available "
        "read-only at `/subagents/<task_id>/`. When the user asks about a "
        "delegate's tools, queries, or conversation, run `ls /subagents/` and "
        "`read_file` on the relevant `tool_calls.json` or `conversation.md` "
        "rather than guessing or saying you cannot recall.\n\n"
        # Model-declared citation pointers (subagent path).
        "Cite tool calls inline. Each tool result you read ends with a "
        "pointer of the form `[Tool call #N — <tool_name> — cite as "
        "[[N]] when referencing this result.]`. When you ground any "
        "factual claim — including in a checkpoint, a delegated "
        "summary, or your final answer — append `[[N]]` immediately "
        "after the claim, where N is the matching tool call number. "
        "Use double square brackets with a positive integer (e.g. "
        "`[[3]]`, `[[12]]`); never invent ordinals you were not "
        "shown. If no pointer was provided for the source you used, "
        "omit the marker rather than guessing."
    )


# Back-compat constant. Callers wanting a per-org cap should invoke
# ``format_web_subagent_suffix(cap)`` directly instead.
WEB_SUBAGENT_CHECKPOINT_SUFFIX = format_web_subagent_suffix()

# Appended to the supervisor system prompt ONLY when a read-only ``/workspace/``
# route is composed for the run (the desktop capability broker is configured and
# the user has granted at least one host folder). Off the desktop path the route
# is absent, so the factory omits this block and the prompt is unchanged. Mirrors
# the ``/subagents/`` guidance above: name the virtual root, tell the model to
# list before it reads, and state the hard read-only boundary.
WORKSPACE_ACCESS_GUIDANCE = (
    "The user has granted read-only access to one or more host folders, "
    "mounted under `/workspace/`. Each granted folder is a named mount: run "
    "`ls /workspace/` to see the available mounts, then use `ls`, `read_file`, "
    "`glob`, and `grep` under `/workspace/<mount>/<path>` to inspect their "
    "contents. These are the user's real files — never assume a path exists; "
    "list a directory first, then read. `/workspace/` is strictly READ-ONLY: "
    "you cannot create, edit, move, or delete anything there. When you need to "
    "author or revise content, write it to `/drafts/` instead."
)
# C3 replacement for the legacy write-through guidance. ``write_file`` and
# ``edit_file`` update the run overlay and produce an exact A4 stage. They must
# never imply that the host changed before A5 consumes an approved command.
WORKSPACE_STAGED_WRITE_GUIDANCE = (
    "The user has granted access to one or more host folders, mounted under "
    "`/workspace/`. Each granted folder is a named mount: run `ls /workspace/` "
    "to see the available mounts, then use `ls`, `read_file`, `glob`, and "
    "`grep` under `/workspace/<mount>/<path>` to inspect their contents. These "
    "are the user's real files — never assume a path exists; list a directory "
    "first, then read. On writable mounts, `write_file` and `edit_file` create "
    "a reviewable staged change in the run overlay; they do NOT immediately "
    "modify the host file. Say that the change is staged, not saved. The exact "
    "reviewed revision is applied later by the workspace effect coordinator. "
    "Read-only or unavailable grants refuse mutations without falling through "
    "to another filesystem."
)
# Appended to the supervisor system prompt ONLY when the gated ``run_code_mode``
# tool is present for the run (AC6 Monty, ``RUNTIME_ENABLE_MONTY`` +
# ``single_user_desktop``). Off that path the tool is absent and this block is
# omitted, so the prompt is unchanged. Code mode ships pure-compute for now:
# calculation / transformation only, with NO tool-calling from inside the
# interpreter (external functions are unavailable until the direct-path
# tool-policy engine lands), so the guidance must not promise tool access.
CODE_MODE_GUIDANCE = (
    "You have a `run_code_mode` tool that runs a small program in a sandboxed "
    "Python subset — no filesystem, network, or imports. Use it for exact "
    "calculation, data transformation, parsing, branching, and repeated "
    "arithmetic where doing the math yourself would be error-prone. It is "
    "calculation/transformation ONLY right now: it cannot call other tools, read "
    "files, or reach the network, so do not declare `external_functions`. Pass "
    "your data via `inputs` and return the result as JSON."
)
# Appended to the supervisor system prompt ONLY when the gated ``run_in_sandbox``
# tool is present (AC7, ``RUNTIME_ENABLE_REMOTE_SANDBOX`` + a configured provider
# + ``single_user_desktop``). Off that path the tool is absent and this block is
# omitted. Each call provisions a throwaway remote sandbox and destroys it after,
# so nothing persists between calls and nothing is shared with local files.
SANDBOX_EXECUTE_GUIDANCE = (
    "You have a `run_in_sandbox` tool that runs a single shell command in an "
    "isolated, network-restricted remote sandbox and returns its output and exit "
    "code. Each call gets a fresh sandbox that is destroyed immediately after, so "
    "no state carries between calls and it CANNOT see the user's files. Use it "
    "for one-shot scripts or CLI tools that need a real shell; to read or write "
    "the user's files, use the filesystem tools instead."
)
FILESYSTEM_IS_NOT_SHELL_GUIDANCE = (
    "Capability truth: filesystem tools such as `ls`, `read_file`, `glob`, "
    "`grep`, `write_file`, and `edit_file`, when present, are bounded file APIs. "
    "They are not shell or terminal access. Never describe them as the ability "
    "to run arbitrary commands, bash, or ordinary Python processes."
)
NO_SHELL_EXECUTE_GUIDANCE = (
    "This run has no shell/terminal command tool. If asked, say that directly "
    "and describe only the specific tools that are actually available. Do not "
    "claim you can execute shell commands or ask the user to rely on a command "
    "having run."
)
_web_harness_profiles_registered = False
_runtime_checkpointer: object | None = None
_UNIVERSAL_MIDDLEWARE_FACTORIES: ContextVar[
    tuple[Callable[[], AgentMiddleware], ...]
] = ContextVar(
    "universal_deep_agent_middleware_factories",
    default=(),
)
_UNIVERSAL_CHILD_GRAPHS_REMAINING: ContextVar[int | None] = ContextVar(
    "universal_deep_agent_child_graphs_remaining",
    # ``None`` retains compatibility for direct callers that supply only the
    # historical universal factories. Canonical factory builds set an exact
    # child count and pass the reviewed root sequence explicitly.
    default=None,
)


def _materialize_universal_middleware() -> tuple[AgentMiddleware, ...]:
    """Build a fresh universal stack for one local child graph.

    Pinned Deep Agents materializes harness middleware for declarative children
    before it assembles the supervisor. Canonical builds pass the supervisor
    sequence through ``create_deep_agent(middleware=...)`` and set an exact
    child-materialization count here, preventing duplicate root admission.
    """

    remaining = _UNIVERSAL_CHILD_GRAPHS_REMAINING.get()
    if remaining is not None:
        if remaining <= 0:
            return ()
        _UNIVERSAL_CHILD_GRAPHS_REMAINING.set(remaining - 1)
    return tuple(factory() for factory in _UNIVERSAL_MIDDLEWARE_FACTORIES.get())


def _ensure_web_harness_profiles_registered() -> None:
    """Register per-provider web harness profiles once, excluding unsafe built-in tools.

    Idempotent — subsequent calls return immediately once the registration flag is set.
    """

    global _web_harness_profiles_registered
    if _web_harness_profiles_registered:
        return

    profile = HarnessProfile(
        system_prompt_suffix=WEB_SUBAGENT_CHECKPOINT_SUFFIX,
        excluded_tools=WEB_EXCLUDED_DEEP_AGENT_TOOLS,
        # Deep Agents materializes profile middleware independently for the
        # supervisor, explicit local subagents, and the built-in general-purpose
        # subagent. The ContextVar supplies the current build's reviewed
        # factories without global mutable per-run state.
        extra_middleware=_materialize_universal_middleware,
    )
    for profile_key in _WEB_HARNESS_PROFILE_KEYS:
        register_harness_profile(profile_key, profile)
    _web_harness_profiles_registered = True


@runtime_checkable
class DeepAgentsBackend(Protocol):
    """Backend protocol accepted by Deep Agents filesystem integration."""

    memory_paths: Sequence[str]

    def download_files(self, paths: list[str]) -> dict[str, str]:
        """Download files for synchronous Deep Agents calls."""

    def upload_files(self, files: dict[str, str]) -> None:
        """Upload files for synchronous Deep Agents calls."""

    async def adownload_files(self, paths: list[str]) -> dict[str, str]:
        """Download files for asynchronous Deep Agents calls."""

    async def aupload_files(self, files: dict[str, str]) -> None:
        """Upload files for asynchronous Deep Agents calls."""


@dataclass(frozen=True)
class DeepAgentBuildRequest:
    """Resolved, authorized inputs for a concrete Deep Agents instance."""

    tools: tuple[object, ...]
    model_config: ModelConfig
    system_prompt: str | SystemMessage
    subagents: tuple[object, ...] = ()
    memory_backend: DeepAgentsBackend | None = None
    memory_paths: tuple[str, ...] = ()
    skill_directories: tuple[str, ...] = ()
    interrupt_on: Mapping[str, object] | None = None
    # Deep Agents ``FilesystemPermission`` rules for the built-in file tools.
    # An ``interrupt``-mode rule (e.g. ``/workspace/**`` writes) auto-installs
    # the SAME ``HumanInTheLoopMiddleware`` that gates MCP tools, so a matching
    # ``write_file`` / ``edit_file`` pauses for human approval BEFORE it runs.
    permissions: tuple[object, ...] = ()
    checkpointer: object | None = None
    # Extra ``init_chat_model`` kwargs from workspace + user policy (training
    # opt-out headers, region ``base_url``, BYOK ``api_key``). Derived in
    # ``factory.py`` and threaded here so every chat-model construction site —
    # including subagents — honours policy uniformly. ``repr=False`` because
    # the mapping may carry a plaintext user API key.
    extra_model_kwargs: Mapping[str, object] | None = field(default=None, repr=False)
    # Main-agent-only middleware supplied through Deep Agents' public API.
    middleware: tuple[AgentMiddleware, ...] = ()
    # Factories materialized through the active harness profile for the
    # supervisor and every locally compiled Deep Agents subagent.
    universal_middleware_factories: tuple[
        Callable[[], AgentMiddleware],
        ...,
    ] = ()

    @property
    def model_name(self) -> str:
        """Return the provider-native model name for tests and diagnostics."""

        return self.model_config.model_name


def build_deep_agent(request: DeepAgentBuildRequest) -> object:
    """Build a Deep Agents graph with an explicit, version-pinned API call."""

    _ensure_web_harness_profiles_registered()
    # Wrap each tool's args_schema to carry the optional ``_display_*`` fields;
    # the wrapper strips them before forwarding to the underlying implementation.
    # Idempotent: safe to call on a list that has already been wrapped.
    from agent_runtime.capabilities.middleware import (  # noqa: PLC0415
        wrap_tools_with_display,
    )

    kwargs: dict[str, object] = {
        "model": build_chat_model(
            request.model_config,
            extra_kwargs=request.extra_model_kwargs,
        ),
        "tools": wrap_tools_with_display(request.tools),
        "system_prompt": request.system_prompt,
        "subagents": list(request.subagents) or None,
        "skills": list(request.skill_directories) or None,
        "memory": list(request.memory_paths) or None,
        "backend": request.memory_backend,
    }
    if request.interrupt_on:
        kwargs["interrupt_on"] = dict(request.interrupt_on)
    if request.permissions:
        # ``create_deep_agent`` merges any interrupt-mode rules here into the
        # HITL ``interrupt_on`` (user-supplied ``interrupt_on`` wins per tool),
        # and applies deny/allow at the built-in file tools. This is the single
        # seam host-write approval flows through.
        kwargs["permissions"] = list(request.permissions)
    if request.checkpointer is not None:
        kwargs["checkpointer"] = request.checkpointer
    # Always exercise the reviewed public seam, including an intentionally
    # empty immutable sequence on compatibility/test builds.
    kwargs["middleware"] = list(request.middleware)
    middleware_token = _UNIVERSAL_MIDDLEWARE_FACTORIES.set(
        request.universal_middleware_factories
    )
    child_count_token = _UNIVERSAL_CHILD_GRAPHS_REMAINING.set(
        (_local_subagent_graph_count(request.subagents) if request.middleware else None)
    )
    try:
        return create_deep_agent(**kwargs)
    finally:
        _UNIVERSAL_CHILD_GRAPHS_REMAINING.reset(child_count_token)
        _UNIVERSAL_MIDDLEWARE_FACTORIES.reset(middleware_token)


def _local_subagent_graph_count(subagents: Sequence[object]) -> int:
    """Return pinned Deep Agents' harness-middleware materialization count."""

    declarative = 0
    has_explicit_general_purpose = False
    for spec in subagents:
        if not isinstance(spec, Mapping) or "graph_id" in spec:
            continue
        if str(spec.get("name", "")).strip() == "general-purpose":
            has_explicit_general_purpose = True
        if "runnable" not in spec:
            declarative += 1
    return declarative + (0 if has_explicit_general_purpose else 1)


def runtime_checkpointer(checkpointer: object | None = None) -> object:
    """Return *checkpointer* if supplied, else the shared lazy singleton.

    The singleton is chosen once, by deployment, in a fixed precedence:

    1. ``single_user_desktop`` file store (``RUNTIME_STORE_BACKEND=file`` with
       ``RUNTIME_FILE_STORE_ROOT`` set) -> a file-backed ``AsyncSqliteSaver`` so
       graph/approval continuation survives a worker restart.
    2. Server Postgres (``RUNTIME_STORE_BACKEND=postgres`` with ``DATABASE_URL``
       set) -> an ``AsyncPostgresSaver`` over a lazily-opened connection pool, so
       graph state and paused approvals survive a worker restart instead of
       dying with the process-local ``InMemorySaver``.
    3. Everything else (in-memory dev/test, web) -> the process-local
       ``InMemorySaver`` exactly as before.

    Each builder returns ``None`` when its env signals are absent, so the
    ``or``-chain falls through to the next candidate; the order is load-bearing.

    The Postgres saver does NOT self-open its pool or create its tables. A
    server startup seam must ``await setup_runtime_checkpointer()`` once (worker
    ``amain`` / in-process worker start) and ``await
    teardown_runtime_checkpointer()`` on shutdown; the desktop SQLite and
    in-memory savers need neither.
    """

    if checkpointer is not None:
        return checkpointer
    global _runtime_checkpointer
    if _runtime_checkpointer is None:
        _runtime_checkpointer = (
            _file_store_checkpointer()  # desktop AsyncSqliteSaver — unchanged
            or _postgres_checkpointer()  # server AsyncPostgresSaver — durable
            or _in_memory_checkpointer()  # last-resort process-local default
        )
    return _runtime_checkpointer


def _in_memory_checkpointer() -> object:
    """Return a fresh process-local ``InMemorySaver`` (non-desktop default)."""

    try:
        from langgraph.checkpoint.memory import InMemorySaver
    except ImportError:  # pragma: no cover — older langgraph alias
        from langgraph.checkpoint.memory import MemorySaver as InMemorySaver

    return InMemorySaver()


def _file_store_checkpointer() -> object | None:
    """Build a durable SQLite checkpointer for the desktop file store, or ``None``.

    Returns ``None`` (so the caller falls back to ``InMemorySaver``) unless the
    file store is active: ``RUNTIME_STORE_BACKEND=file`` **and**
    ``RUNTIME_FILE_STORE_ROOT`` is set. The checkpoint database lives next to
    the disposable catalog index at ``<root>/index/checkpoints.sqlite3`` — it is
    NOT the disposable index itself, so wiping ``index/catalog.sqlite3`` never
    drops in-flight graph state.

    The async graph is driven via ``ainvoke``/``astream``; the synchronous
    ``SqliteSaver`` rejects async calls, so we use ``AsyncSqliteSaver`` over a
    lazily-connected ``aiosqlite`` connection (it binds to the worker event loop
    on first use and auto-creates its tables). ``check_same_thread=False`` lets
    aiosqlite service the connection from its own worker thread.
    """

    import os

    backend = os.environ.get("RUNTIME_STORE_BACKEND", "").strip().lower()
    root = os.environ.get("RUNTIME_FILE_STORE_ROOT", "").strip()
    if backend != "file" or not root:
        return None

    from pathlib import Path

    import aiosqlite
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

    # ``index/checkpoints.sqlite3`` mirrors ``FileStoreLayout.index_dir``; keep
    # the two in sync if the on-disk layout ever moves. Imported by string here
    # rather than pulling ``runtime_adapters`` into ``agent_runtime`` (adapters
    # depend on the domain, never the reverse).
    db_dir = Path(root).expanduser().resolve() / "index"
    db_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    db_path = db_dir / "checkpoints.sqlite3"
    connection = aiosqlite.connect(str(db_path), check_same_thread=False)
    return AsyncSqliteSaver(connection)


def _postgres_checkpointer() -> object | None:
    """Build a durable ``AsyncPostgresSaver`` for a server deployment, or ``None``.

    Returns ``None`` (so the caller falls through to the ``InMemorySaver``)
    unless the server Postgres path is active: ``RUNTIME_STORE_BACKEND=postgres``
    **and** ``DATABASE_URL`` set. This is what stops a multi-process server from
    losing in-flight graph state (and paused approvals) to a process-local
    ``InMemorySaver`` on every worker restart.

    The pool is constructed with ``open=False`` so selecting/importing the saver
    never blocks on a live database — ``setup_runtime_checkpointer()`` opens it
    and creates the checkpoint tables once at startup. ``autocommit=True`` +
    ``row_factory=dict_row`` + ``prepare_threshold=0`` are the connection
    settings ``AsyncPostgresSaver`` documents for pooled usage.

    Imports are lazy and AFTER the env gate (mirroring the SQLite builder) so a
    desktop/in-memory process never needs the postgres checkpointer package.
    ``ImportError`` is deliberately NOT swallowed: a server that asked for the
    Postgres backend but is missing the driver must fail loudly, not silently
    degrade to a non-durable saver.
    """

    import os

    backend = os.environ.get("RUNTIME_STORE_BACKEND", "").strip().lower()
    database_url = os.environ.get("DATABASE_URL", "").strip()
    if backend != "postgres" or not database_url:
        return None

    from psycopg.rows import dict_row
    from psycopg_pool import AsyncConnectionPool

    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

    pool = AsyncConnectionPool(
        conninfo=database_url,
        open=False,
        kwargs={
            "autocommit": True,
            "row_factory": dict_row,
            "prepare_threshold": 0,
        },
    )
    return AsyncPostgresSaver(pool)


async def setup_runtime_checkpointer() -> None:
    """Open the Postgres checkpointer pool and create its tables, once at startup.

    A no-op on every non-Postgres saver: the desktop ``AsyncSqliteSaver`` opens
    its aiosqlite connection lazily on first use, and the in-memory saver needs
    nothing. Duck-typed on the class name so importing this module on a desktop
    build never drags in the postgres checkpointer package.

    Idempotent enough for a startup seam: opening an already-open pool and
    re-running ``AsyncPostgresSaver.setup()`` (``CREATE TABLE IF NOT EXISTS``
    DDL) are both safe. Must be awaited inside the event loop the worker will run
    on — ``AsyncPostgresSaver`` binds to the running loop at construction.
    """

    saver = runtime_checkpointer()
    if type(saver).__name__ != "AsyncPostgresSaver":
        return
    from psycopg_pool import AsyncConnectionPool

    conn = getattr(saver, "conn", None)
    if isinstance(conn, AsyncConnectionPool):
        await conn.open()
        await conn.wait()
    await saver.setup()


async def teardown_runtime_checkpointer() -> None:
    """Close the Postgres checkpointer pool on shutdown; a no-op otherwise.

    Mirror of :func:`setup_runtime_checkpointer`. Reads the module singleton
    directly rather than calling :func:`runtime_checkpointer` so shutdown never
    *constructs* a saver, and does nothing when none was built or when the saver
    is the desktop SQLite / in-memory variant.
    """

    saver = _runtime_checkpointer
    if saver is None or type(saver).__name__ != "AsyncPostgresSaver":
        return
    from psycopg_pool import AsyncConnectionPool

    conn = getattr(saver, "conn", None)
    if isinstance(conn, AsyncConnectionPool):
        await conn.close()


def build_chat_model(
    model_config: ModelConfig,
    *,
    extra_kwargs: Mapping[str, object] | None = None,
) -> BaseChatModel:
    """Create the LangChain chat model configured for a runtime model profile.

    ``extra_kwargs`` is merged after provider-specific kwargs so workspace policy
    (e.g. training opt-out headers) wins on any conflict. Pass ``None`` for
    callers without a workspace context (e.g. the presentation layer's projection
    factory).
    """

    # Hermetic-test affordance: an env-gated deterministic fake substitutes the
    # concrete model at this single funnel, leaving the real graph + streaming
    # executor untouched. Never active in a shipped deployment (see fake_model).
    if FakeModelProvider.is_enabled():
        return FakeModelProvider.build(model_config)

    kwargs: dict[str, object] = {"timeout": model_config.timeout_seconds}
    if model_config.reasoning is None or not model_config.reasoning.enabled:
        kwargs["temperature"] = model_config.temperature
    # ``max_tokens`` is the LangChain-canonical key for the output cap and is
    # honoured by every supported provider (OpenAI, Anthropic, Gemini). We
    # only emit it when the resolved config carries an explicit number so
    # deployments without a baseline keep relying on provider defaults.
    # The number is already depth-scaled by ``DepthBudgetTable.apply`` —
    # this is the single read site.
    if model_config.max_output_tokens is not None:
        kwargs["max_tokens"] = model_config.max_output_tokens
    compat = OpenAICompatibleProviders.get(model_config.provider)
    is_custom_compat = OpenAICompatibleProviders.is_custom(model_config.provider)
    if compat is not None:
        # OpenAI-wire-compatible gateway (OpenRouter today): a fixed
        # base_url and CHAT-COMPLETIONS ONLY. ``use_responses_api`` MUST be
        # False, and we must NOT apply ``_openai_model_kwargs`` — its
        # ``reasoning`` / ``output_version`` / ``include`` payload silently
        # re-routes ChatOpenAI onto the OpenAI ``/responses`` endpoint,
        # which these gateways do not implement.
        kwargs["base_url"] = compat.resolve_base_url()
        kwargs["use_responses_api"] = False
        headers = compat.default_headers()
        if headers:
            kwargs["default_headers"] = headers
        # Deployment-level fallback key. A per-user BYOK key arrives via
        # ``extra_kwargs`` (from ``user_policy_model_kwargs``) and overrides
        # this on the merge below — BYOK always wins over the env key.
        env_key = compat.api_key_from_env()
        if env_key is not None:
            kwargs["api_key"] = env_key
        elif not compat.requires_api_key:
            # Keyless local runtime (Ollama). ChatOpenAI rejects an empty
            # api_key, so pass a sentinel the endpoint ignores.
            kwargs["api_key"] = "ollama"
    elif is_custom_compat:
        # User-supplied custom OpenAI-compatible endpoint (BYOK decision D-2).
        # Same Chat-Completions-only posture as the registry gateways, but the
        # ``base_url`` + ``api_key`` are per-run and arrive via ``extra_kwargs``
        # (from ``user_policy_model_kwargs`` — the endpoint map + BYOK key).
        # We must NOT apply ``_openai_model_kwargs`` for the same /responses
        # reason as above. The fail-closed guard after the merge ensures we
        # never silently fall through to api.openai.com without a base_url.
        kwargs["use_responses_api"] = False
    elif model_config.provider == "openai":
        kwargs.update(_openai_model_kwargs(model_config))
    elif model_config.provider == "anthropic":
        kwargs.update(_anthropic_model_kwargs(model_config))
    if extra_kwargs:
        # Deep-merge known nested kwarg keys (``model_kwargs``,
        # ``extra_headers``) so workspace policy adds fields rather
        # than wiping the provider-specific ones we set above.
        for key, value in extra_kwargs.items():
            if (
                isinstance(value, dict)
                and key in kwargs
                and isinstance(kwargs[key], dict)
            ):
                merged = dict(kwargs[key])  # type: ignore[arg-type]
                merged.update(value)
                kwargs[key] = merged
            else:
                kwargs[key] = value

    # Fail closed: a custom endpoint with no resolved ``base_url`` must ERROR,
    # never silently construct a client pointed at api.openai.com with the
    # user's key (the internal-lane resolver can degrade the endpoint map to
    # empty on partial config — that must surface, not mis-route).
    if is_custom_compat and not kwargs.get("base_url"):
        raise AgentRuntimeError(
            RuntimeErrorCode.CONFIGURATION_ERROR,
            "Custom OpenAI-compatible endpoint is missing its base URL. "
            "Re-add it in Settings -> Provider keys.",
            retryable=False,
        )

    return init_chat_model(
        model_config.model_name,
        model_provider=_langchain_model_provider(model_config.provider),
        **kwargs,
    )


def build_chat_model_from_id(
    model_id: str,
    *,
    extra_kwargs: Mapping[str, object] | None = None,
) -> BaseChatModel:
    """Build a chat model from a ``[provider:]model_name`` id string.

    A convenience over :func:`build_chat_model` for infrastructure callers that
    carry a single model-id string rather than a full :class:`ModelConfig` — the
    surface-spec generator (PRD-07) routes its nano/mini model through here, so it
    inherits the same provider handling (OpenAI / Anthropic / Gemini native, plus
    OpenRouter / Ollama compat) and the hermetic fake-model funnel for free.

    Accepted forms: ``"anthropic:claude-haiku-4-5"`` (explicit provider) or a bare
    ``"gpt-5-mini"`` / ``"claude-haiku-4-5"`` / ``"gemini-2.5-flash"`` whose
    provider is inferred from the name.
    """

    config = SurfaceModelConfigFactory.from_id(model_id)
    return build_chat_model(config, extra_kwargs=extra_kwargs)


class SurfaceModelConfigFactory:
    """Parse a ``[provider:]model`` id into a minimal :class:`ModelConfig`.

    The config is deliberately small: temperature 0 (a mapping is a deterministic
    classification, not a creative task), a tight output cap (specs are tiny), and
    no streaming. Provider routing reuses the same aliases as the run-model
    resolver so BYOK / OpenRouter / Ollama keep working.
    """

    _PROVIDER_ALIASES = {
        "anthropic": "anthropic",
        "claude": "anthropic",
        "openai": "openai",
        "gemini": "gemini",
        "google": "gemini",
        "google-genai": "gemini",
        "openrouter": "openrouter",
        "ollama": "ollama",
    }
    _MAX_INPUT_TOKENS = 200_000
    #: ``hyperparameters.json``'s ``model_mapper`` section owns this pair, but
    #: this module cannot import the document — see ``_DEFAULT_TOOL_CALL_BUDGET``
    #: above for the cycle. Hoisted out of the ``ModelConfig`` literal anyway so
    #: the binding is a one-line change once that is unblocked.
    #: ``_MAX_INPUT_TOKENS`` / ``_TIMEOUT_SECONDS`` are deliberately not in the
    #: document: it states only the pair the plan lists.
    _MAX_OUTPUT_TOKENS = 1_024
    _TEMPERATURE = 0.0
    _TIMEOUT_SECONDS = 60.0

    @classmethod
    def from_id(cls, model_id: str) -> ModelConfig:
        provider, model_name = cls._split(model_id.strip())
        return ModelConfig(
            provider=provider,
            model_name=model_name,
            max_input_tokens=cls._MAX_INPUT_TOKENS,
            max_output_tokens=cls._MAX_OUTPUT_TOKENS,
            timeout_seconds=cls._TIMEOUT_SECONDS,
            temperature=cls._TEMPERATURE,
            supports_streaming=False,
        )

    @classmethod
    def _split(cls, model_id: str) -> tuple[str, str]:
        if not model_id:
            raise ValueError("model id must be a non-empty string")
        if ":" in model_id:
            prefix, rest = model_id.split(":", 1)
            alias = prefix.strip().lower().replace("_", "-")
            if alias in cls._PROVIDER_ALIASES and rest.strip():
                return (cls._PROVIDER_ALIASES[alias], rest.strip())
        inferred = cls._infer_provider(model_id)
        if inferred is None:
            raise ValueError(
                f"cannot infer model provider from '{model_id}'; use 'provider:model'"
            )
        return (inferred, model_id)

    @staticmethod
    def _infer_provider(model_name: str) -> str | None:
        normalized = model_name.lower().replace(" ", "-").replace("_", "-")
        if normalized.startswith(("gpt-", "o1", "o3", "o4")):
            return "openai"
        if normalized.startswith("claude"):
            return "anthropic"
        if normalized.startswith("gemini"):
            return "gemini"
        if "/" in model_name:
            return "openrouter"
        return None


def build_embeddings_model(
    *,
    provider: str,
    model_name: str,
    extra_kwargs: Mapping[str, object] | None = None,
) -> Embeddings:
    """Create the LangChain embeddings model for Library retrieval/indexing.

    Companion to :func:`build_chat_model` — keeps the TU-1 invariant that
    every LLM provider client funnels through this single bootstrap file.
    Callers (e.g. ``/internal/v1/llm/embed``, the indexer worker) must
    construct the resulting handle here so the CI guard
    (``tools/check_llm_provider_imports.py``) does not flag a direct
    provider SDK import elsewhere.

    The function deliberately takes ``provider`` and ``model_name`` as
    bare values rather than a :class:`ModelConfig` because the
    embedding model contract is much narrower than the chat-model one
    (no temperature, no reasoning, no streaming, no thinking budget).
    Provider-specific kwargs (e.g. ``api_key``, ``dimensions``) flow
    through ``extra_kwargs``.
    """

    kwargs: dict[str, object] = {}
    if extra_kwargs:
        kwargs.update(extra_kwargs)
    return init_embeddings(
        model=model_name,
        provider=_langchain_model_provider(provider),
        **kwargs,
    )


def _langchain_model_provider(provider: str) -> str:
    """Translate the normalised provider slug to the LangChain ``model_provider`` string."""
    # LangChain uses ``google_genai`` while the runtime normalises to ``gemini``.
    if provider == "gemini":
        return "google_genai"
    # OpenAI-wire-compatible gateways (OpenRouter) run through ChatOpenAI;
    # the endpoint difference is carried by ``base_url`` in build_chat_model.
    if OpenAICompatibleProviders.is_compatible(provider):
        return "openai"
    return provider


def _openai_model_kwargs(model_config: ModelConfig) -> dict[str, object]:
    """Return OpenAI Responses API kwargs derived from a model reasoning config."""
    kwargs: dict[str, object] = {"use_responses_api": True}
    reasoning = model_config.reasoning
    if reasoning is None:
        return kwargs
    if not reasoning.enabled or reasoning.effort is ModelReasoningEffort.NONE:
        kwargs["reasoning"] = None
        return kwargs

    reasoning_payload: dict[str, object] = {}
    if reasoning.effort is not None:
        reasoning_payload["effort"] = reasoning.effort.value
    if reasoning.summary is not None:
        reasoning_payload["summary"] = reasoning.summary.value
        kwargs["output_version"] = "responses/v1"
    kwargs["reasoning"] = reasoning_payload
    if reasoning.include_encrypted_content:
        kwargs["include"] = ["reasoning.encrypted_content"]
        kwargs["output_version"] = "responses/v1"
    return kwargs


def _anthropic_model_kwargs(model_config: ModelConfig) -> dict[str, object]:
    """Return Anthropic extended-thinking kwargs derived from a model reasoning config."""
    reasoning = model_config.reasoning
    if reasoning is None or not reasoning.enabled:
        return {}

    mode = reasoning.thinking_mode
    if mode is None:
        mode = (
            ModelThinkingMode.ENABLED
            if reasoning.budget_tokens is not None
            else ModelThinkingMode.ADAPTIVE
        )
    thinking: dict[str, object] = {"type": mode.value}
    if mode is ModelThinkingMode.ENABLED and reasoning.budget_tokens is not None:
        thinking["budget_tokens"] = reasoning.budget_tokens
    if mode is ModelThinkingMode.ADAPTIVE and reasoning.display is not None:
        thinking["display"] = reasoning.display.value

    kwargs: dict[str, object] = {"thinking": thinking}
    if (
        mode is ModelThinkingMode.ADAPTIVE
        and reasoning.effort is not None
        and reasoning.effort is not ModelReasoningEffort.NONE
    ):
        kwargs["output_config"] = {"effort": reasoning.effort.value}
    return kwargs
