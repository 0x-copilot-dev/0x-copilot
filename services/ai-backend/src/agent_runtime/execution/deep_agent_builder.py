"""Concrete Deep Agents construction for the runtime factory."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from deepagents import HarnessProfile, create_deep_agent, register_harness_profile
from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel

from agent_runtime.execution.contracts import (
    ModelConfig,
    ModelReasoningEffort,
    ModelThinkingMode,
)

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
    checkpointer: object | None = None
    # Extra ``init_chat_model`` kwargs from workspace policy (e.g. training
    # opt-out headers). Derived in ``factory.py`` and threaded here so every
    # chat-model construction site — including subagents — honours policy
    # uniformly.
    extra_model_kwargs: Mapping[str, object] | None = None

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
    if request.checkpointer is not None:
        kwargs["checkpointer"] = request.checkpointer
    return create_deep_agent(**kwargs)


def runtime_checkpointer(checkpointer: object | None = None) -> object:
    """Return *checkpointer* if supplied, else the shared lazy singleton."""

    if checkpointer is not None:
        return checkpointer
    global _runtime_checkpointer
    if _runtime_checkpointer is None:
        try:
            from langgraph.checkpoint.memory import InMemorySaver
        except ImportError:
            from langgraph.checkpoint.memory import MemorySaver as InMemorySaver

        _runtime_checkpointer = InMemorySaver()
    return _runtime_checkpointer


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
    if model_config.provider == "openai":
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


def _langchain_model_provider(provider: str) -> str:
    """Translate the normalised provider slug to the LangChain ``model_provider`` string."""
    # LangChain uses ``google_genai`` while the runtime normalises to ``gemini``.
    if provider == "gemini":
        return "google_genai"
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
