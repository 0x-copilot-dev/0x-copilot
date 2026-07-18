"""Concrete Deep Agents construction for the runtime factory."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from deepagents import HarnessProfile, create_deep_agent, register_harness_profile
from langchain.chat_models import init_chat_model
from langchain.embeddings import init_embeddings
from langchain_core.embeddings import Embeddings
from langchain_core.language_models import BaseChatModel

from agent_runtime.execution.contracts import (
    ModelConfig,
    ModelReasoningEffort,
    ModelThinkingMode,
)
from agent_runtime.execution.openai_compat import OpenAICompatibleProviders

WEB_EXCLUDED_DEEP_AGENT_TOOLS = frozenset(
    {
        "edit_file",
        "execute",
        "write_file",
    }
)
_WEB_HARNESS_PROFILE_KEYS = (
    "anthropic",
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
_DEFAULT_TOOL_CALL_BUDGET = 5  # Aligns with the historical literal "5" used here.


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
# Replaces the read-only line above when the run has at least one WRITABLE host
# grant (``read_write*``). Host writes remain gated: every ``write_file`` /
# ``edit_file`` on ``/workspace/`` pauses for explicit user approval before it
# runs, and the prior contents are snapshotted first so a change can be undone.
WORKSPACE_WRITE_GUIDANCE = (
    "The user has granted access to one or more host folders, mounted under "
    "`/workspace/`. Each granted folder is a named mount: run `ls /workspace/` "
    "to see the available mounts, then use `ls`, `read_file`, `glob`, and "
    "`grep` under `/workspace/<mount>/<path>` to inspect their contents. These "
    "are the user's real files — never assume a path exists; list a directory "
    "first, then read. Some mounts are WRITABLE: you may use `write_file` and "
    "`edit_file` under `/workspace/<mount>/<path>`, but EVERY such change pauses "
    "for the user's explicit approval before it is applied, and the file's "
    "prior contents are snapshotted first. Read a file before editing it, keep "
    "edits minimal, and prefer `/drafts/` for brand-new authored content. "
    "Read-only mounts refuse writes."
)
_web_harness_profiles_registered = False
_runtime_checkpointer: object | None = None


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
    system_prompt: str
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
    return create_deep_agent(**kwargs)


def runtime_checkpointer(checkpointer: object | None = None) -> object:
    """Return *checkpointer* if supplied, else the shared lazy singleton.

    On the ``single_user_desktop`` file-store path (``RUNTIME_STORE_BACKEND=file``
    with ``RUNTIME_FILE_STORE_ROOT`` set) the singleton is a file-backed
    ``AsyncSqliteSaver`` so graph/approval continuation survives a worker
    restart. Every other deployment (postgres, in-memory, web) keeps the
    process-local ``InMemorySaver`` exactly as before — the SQLite path is
    reached only when both env signals hold, so non-desktop behavior is
    untouched.
    """

    if checkpointer is not None:
        return checkpointer
    global _runtime_checkpointer
    if _runtime_checkpointer is None:
        file_saver = _file_store_checkpointer()
        _runtime_checkpointer = (
            file_saver if file_saver is not None else _in_memory_checkpointer()
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

    return init_chat_model(
        model_config.model_name,
        model_provider=_langchain_model_provider(model_config.provider),
        **kwargs,
    )


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
