from __future__ import annotations

from dataclasses import dataclass, field

from deepagents import HarnessProfile
from langchain_core.messages import HumanMessage
from langchain_core.tools import StructuredTool
import pytest

from agent_runtime.execution import deep_agent_builder as builder_module
from agent_runtime.execution.fake_model import DeterministicFakeChatModel
from agent_runtime.execution.contracts import (
    ModelConfig,
    ModelReasoningConfig,
    ModelReasoningDisplay,
    ModelReasoningEffort,
    ModelReasoningSummary,
    ModelThinkingMode,
)
from agent_runtime.execution.deep_agent_builder import (
    DeepAgentBuildRequest,
    build_deep_agent,
)
from agent_runtime.capabilities.middleware import RuntimeToolControlMiddleware
from tests.unit.agent_runtime.agent.helpers import FakeDeepAgentsModule


@dataclass
class CapturedChatModel:
    model: str
    model_provider: str | None
    kwargs: dict[str, object]


@dataclass
class CapturingChatModelFactory:
    calls: list[CapturedChatModel] = field(default_factory=list)

    def __call__(
        self,
        model: str,
        *,
        model_provider: str | None = None,
        **kwargs: object,
    ) -> CapturedChatModel:
        call = CapturedChatModel(
            model=model,
            model_provider=model_provider,
            kwargs=kwargs,
        )
        self.calls.append(call)
        return call


def test_web_harness_profile_excludes_write_and_execute_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, frozenset[str]]] = []

    def capture_profile(profile_key: str, profile: HarnessProfile) -> None:
        calls.append((profile_key, profile.excluded_tools))

    monkeypatch.setattr(builder_module, "register_harness_profile", capture_profile)
    monkeypatch.setattr(builder_module, "_web_harness_profiles_registered", False)

    builder_module._ensure_web_harness_profiles_registered()
    builder_module._ensure_web_harness_profiles_registered()

    assert calls == [
        ("anthropic", builder_module.WEB_EXCLUDED_DEEP_AGENT_TOOLS),
        (
            "deterministicfakechatmodel",
            builder_module.WEB_EXCLUDED_DEEP_AGENT_TOOLS,
        ),
        ("gemini", builder_module.WEB_EXCLUDED_DEEP_AGENT_TOOLS),
        ("google_genai", builder_module.WEB_EXCLUDED_DEEP_AGENT_TOOLS),
        ("openai", builder_module.WEB_EXCLUDED_DEEP_AGENT_TOOLS),
    ]


def test_universal_middleware_is_materialized_for_supervisor_and_local_subagents(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Deep Agents must add one fresh runtime boundary to every compiled graph."""

    import deepagents.graph as deepagents_graph
    import deepagents.middleware.subagents as deepagents_subagents

    captured_stacks: list[list[object]] = []

    class _Compiled:
        def with_config(self, _config: object) -> "_Compiled":
            return self

    def capture_create_agent(
        _model: object,
        *,
        middleware: list[object],
        **_kwargs: object,
    ) -> _Compiled:
        captured_stacks.append(middleware)
        return _Compiled()

    monkeypatch.setenv("RUNTIME_FAKE_MODEL", "1")
    monkeypatch.setattr(deepagents_graph, "create_agent", capture_create_agent)
    monkeypatch.setattr(deepagents_subagents, "create_agent", capture_create_agent)
    monkeypatch.setattr(builder_module, "_web_harness_profiles_registered", False)

    build_deep_agent(
        DeepAgentBuildRequest(
            tools=(),
            model_config=ModelConfig(
                provider="openai",
                model_name="gpt-5.4-mini",
                max_input_tokens=128_000,
                timeout_seconds=45,
                temperature=0,
                supports_streaming=True,
            ),
            system_prompt="Follow policy.",
            universal_middleware_factories=(RuntimeToolControlMiddleware,),
        )
    )

    # The auto general-purpose subagent compiles before the supervisor. The
    # task-tool setter recompiles that same reviewed spec after private state is
    # known; both compilations intentionally retain its one middleware instance.
    assert len(captured_stacks) >= 2
    controls = [
        [
            middleware
            for middleware in stack
            if isinstance(middleware, RuntimeToolControlMiddleware)
        ]
        for stack in captured_stacks
    ]
    assert all(len(instances) == 1 for instances in controls)
    main_control = controls[-1][0]
    subagent_controls = {id(instances[0]) for instances in controls[:-1]}
    assert len(subagent_controls) == 1
    assert id(main_control) not in subagent_controls


def test_final_model_visible_tools_have_one_universal_controller(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Capture live final tool bindings, not only the caller-supplied tools."""

    import deepagents.graph as deepagents_graph
    import deepagents.middleware.subagents as deepagents_subagents

    captured_graphs: list[object] = []
    captured_stacks: list[list[object]] = []
    bound_tool_sets: list[frozenset[str]] = []
    graph_create_agent = deepagents_graph.create_agent
    subagent_create_agent = deepagents_subagents.create_agent
    original_bind_tools = DeterministicFakeChatModel.bind_tools

    def capture_graph(
        original: object,
    ) -> object:
        def create_agent(
            model: object,
            *args: object,
            middleware: list[object],
            **kwargs: object,
        ) -> object:
            graph = original(  # type: ignore[operator]
                model,
                *args,
                middleware=middleware,
                **kwargs,
            )
            captured_stacks.append(middleware)
            captured_graphs.append(graph)
            return graph

        return create_agent

    def capture_bind_tools(
        model: DeterministicFakeChatModel,
        tools: list[object],
        **kwargs: object,
    ) -> object:
        bound_tool_sets.append(
            frozenset(str(getattr(tool, "name", "")) for tool in tools)
        )
        return original_bind_tools(model, tools, **kwargs)

    def catalog_read(query: str) -> str:
        return query

    factory_tool = StructuredTool.from_function(
        func=catalog_read,
        name="catalog_read",
        description="Read one reviewed catalog entry.",
    )
    monkeypatch.setenv("RUNTIME_FAKE_MODEL", "1")
    monkeypatch.setattr(
        deepagents_graph,
        "create_agent",
        capture_graph(graph_create_agent),
    )
    monkeypatch.setattr(
        deepagents_subagents,
        "create_agent",
        capture_graph(subagent_create_agent),
    )
    monkeypatch.setattr(
        DeterministicFakeChatModel,
        "bind_tools",
        capture_bind_tools,
    )
    monkeypatch.setattr(builder_module, "_web_harness_profiles_registered", False)

    build_deep_agent(
        DeepAgentBuildRequest(
            tools=(factory_tool,),
            model_config=ModelConfig(
                provider="openai",
                model_name="gpt-5.4-mini",
                max_input_tokens=128_000,
                timeout_seconds=45,
                temperature=0,
                supports_streaming=True,
            ),
            system_prompt="Follow policy.",
            universal_middleware_factories=(RuntimeToolControlMiddleware,),
        )
    )

    for graph in captured_graphs:
        graph.invoke({"messages": [HumanMessage(content="Inspect tools.")]})  # type: ignore[attr-defined]

    expected_local_tools = frozenset(
        {
            "catalog_read",
            "glob",
            "grep",
            "ls",
            "read_file",
            "write_todos",
        }
    )
    expected_supervisor_tools = expected_local_tools | {"task"}
    assert len(captured_graphs) == len(captured_stacks) == len(bound_tool_sets)
    assert bound_tool_sets.count(expected_local_tools) == 2
    assert bound_tool_sets.count(expected_supervisor_tools) == 1
    assert all(
        sum(
            isinstance(middleware, RuntimeToolControlMiddleware) for middleware in stack
        )
        == 1
        for stack in captured_stacks
    )


def test_deep_agent_builder_configures_openai_responses_reasoning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_deepagents = FakeDeepAgentsModule()
    chat_models = CapturingChatModelFactory()
    monkeypatch.setattr(
        builder_module, "create_deep_agent", fake_deepagents.create_deep_agent
    )
    monkeypatch.setattr(builder_module, "init_chat_model", chat_models)

    agent = build_deep_agent(
        DeepAgentBuildRequest(
            tools=("doc_search",),
            model_config=ModelConfig(
                provider="openai",
                model_name="gpt-5.4-mini",
                max_input_tokens=128_000,
                timeout_seconds=45,
                temperature=0,
                supports_streaming=True,
                reasoning=ModelReasoningConfig(
                    effort=ModelReasoningEffort.MEDIUM,
                    summary=ModelReasoningSummary.AUTO,
                    include_encrypted_content=True,
                ),
            ),
            system_prompt="Follow policy.",
        )
    )

    assert agent == {"agent": "fake"}
    call = chat_models.calls[0]
    assert call.model == "gpt-5.4-mini"
    assert call.model_provider == "openai"
    assert call.kwargs["use_responses_api"] is True
    assert call.kwargs["reasoning"] == {"effort": "medium", "summary": "auto"}
    assert call.kwargs["include"] == ["reasoning.encrypted_content"]
    assert call.kwargs["output_version"] == "responses/v1"
    assert "temperature" not in call.kwargs
    assert fake_deepagents.calls[0]["model"] == call


def test_deep_agent_builder_requests_summary_only_reasoning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The resolver synthesizes a summary-only config for a native OpenAI
    # reasoning model that had no explicit reasoning selection. The builder must
    # turn that into ``reasoning={"summary": "auto"}`` on the Responses API so
    # OpenAI emits ``reasoning_summary_text_delta`` (the thinking block); effort
    # is left to OpenAI's default and temperature must be dropped.
    fake_deepagents = FakeDeepAgentsModule()
    chat_models = CapturingChatModelFactory()
    monkeypatch.setattr(
        builder_module, "create_deep_agent", fake_deepagents.create_deep_agent
    )
    monkeypatch.setattr(builder_module, "init_chat_model", chat_models)

    build_deep_agent(
        DeepAgentBuildRequest(
            tools=("doc_search",),
            model_config=ModelConfig(
                provider="openai",
                model_name="gpt-5.4-mini",
                max_input_tokens=128_000,
                timeout_seconds=45,
                temperature=0,
                supports_streaming=True,
                reasoning=ModelReasoningConfig(summary=ModelReasoningSummary.AUTO),
            ),
            system_prompt="Follow policy.",
        )
    )

    call = chat_models.calls[0]
    assert call.kwargs["use_responses_api"] is True
    assert call.kwargs["reasoning"] == {"summary": "auto"}
    assert call.kwargs["output_version"] == "responses/v1"
    assert "temperature" not in call.kwargs
    assert "include" not in call.kwargs


def test_deep_agent_builder_routes_openrouter_to_chat_completions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # OpenRouter is OpenAI-wire-compatible but chat-completions only. Even
    # with a reasoning config present, the builder must route through the
    # OpenAI client with a fixed base_url, use_responses_api=False, and NONE
    # of the Responses-API kwargs (which would 404 against /responses).
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-test000000000000000000009876")
    fake_deepagents = FakeDeepAgentsModule()
    chat_models = CapturingChatModelFactory()
    monkeypatch.setattr(
        builder_module, "create_deep_agent", fake_deepagents.create_deep_agent
    )
    monkeypatch.setattr(builder_module, "init_chat_model", chat_models)

    build_deep_agent(
        DeepAgentBuildRequest(
            tools=("doc_search",),
            model_config=ModelConfig(
                provider="openrouter",
                model_name="anthropic/claude-3.7-sonnet",
                max_input_tokens=128_000,
                timeout_seconds=45,
                temperature=0,
                supports_streaming=True,
                reasoning=ModelReasoningConfig(
                    effort=ModelReasoningEffort.MEDIUM,
                    summary=ModelReasoningSummary.AUTO,
                    include_encrypted_content=True,
                ),
            ),
            system_prompt="Follow policy.",
        )
    )

    call = chat_models.calls[0]
    assert call.model == "anthropic/claude-3.7-sonnet"
    # Resolves to the OpenAI LangChain client — the endpoint difference is
    # carried by base_url, not a distinct provider slug.
    assert call.model_provider == "openai"
    assert call.kwargs["use_responses_api"] is False
    assert call.kwargs["base_url"] == "https://openrouter.ai/api/v1"
    assert call.kwargs["default_headers"] == {
        "HTTP-Referer": "https://0xcopilot.tech",
        "X-Title": "0xCopilot",
    }
    # Deployment env-fallback key is injected explicitly (base_url is
    # openrouter.ai, so the client must NOT read OPENAI_API_KEY).
    assert call.kwargs["api_key"] == "sk-or-v1-test000000000000000000009876"
    # None of the Responses-API-only kwargs may leak through.
    for forbidden in ("reasoning", "include", "output_version"):
        assert forbidden not in call.kwargs


def test_deep_agent_builder_routes_ollama_keyless_to_localhost(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A local Ollama model is keyless: no OPENROUTER/OPENAI key involved. The
    # builder must point at the local base_url, disable the Responses API, and
    # inject a sentinel api_key (ChatOpenAI rejects an empty one).
    monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)
    fake_deepagents = FakeDeepAgentsModule()
    chat_models = CapturingChatModelFactory()
    monkeypatch.setattr(
        builder_module, "create_deep_agent", fake_deepagents.create_deep_agent
    )
    monkeypatch.setattr(builder_module, "init_chat_model", chat_models)

    build_deep_agent(
        DeepAgentBuildRequest(
            tools=("doc_search",),
            model_config=ModelConfig(
                provider="ollama",
                model_name="llama3.2:1b",
                max_input_tokens=8192,
                timeout_seconds=45,
                temperature=0,
                supports_streaming=True,
                reasoning=None,
            ),
            system_prompt="Follow policy.",
        )
    )

    call = chat_models.calls[0]
    assert call.model == "llama3.2:1b"
    assert call.model_provider == "openai"
    assert call.kwargs["base_url"] == "http://localhost:11434/v1"
    assert call.kwargs["use_responses_api"] is False
    assert call.kwargs["api_key"] == "ollama"
    for forbidden in ("reasoning", "include", "output_version"):
        assert forbidden not in call.kwargs


def test_deep_agent_builder_honours_ollama_base_url_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://host.docker.internal:11434/v1")
    fake_deepagents = FakeDeepAgentsModule()
    chat_models = CapturingChatModelFactory()
    monkeypatch.setattr(
        builder_module, "create_deep_agent", fake_deepagents.create_deep_agent
    )
    monkeypatch.setattr(builder_module, "init_chat_model", chat_models)

    build_deep_agent(
        DeepAgentBuildRequest(
            tools=("doc_search",),
            model_config=ModelConfig(
                provider="ollama",
                model_name="qwen2.5:3b",
                max_input_tokens=8192,
                timeout_seconds=45,
                temperature=0,
                supports_streaming=True,
                reasoning=None,
            ),
            system_prompt="Follow policy.",
        )
    )

    assert (
        chat_models.calls[0].kwargs["base_url"]
        == "http://host.docker.internal:11434/v1"
    )


def test_deep_agent_builder_configures_claude_opus_47_adaptive_thinking(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_deepagents = FakeDeepAgentsModule()
    chat_models = CapturingChatModelFactory()
    monkeypatch.setattr(
        builder_module, "create_deep_agent", fake_deepagents.create_deep_agent
    )
    monkeypatch.setattr(builder_module, "init_chat_model", chat_models)

    build_deep_agent(
        DeepAgentBuildRequest(
            tools=("doc_search",),
            model_config=ModelConfig(
                provider="anthropic",
                model_name="claude-opus-4-7",
                max_input_tokens=200_000,
                timeout_seconds=60,
                temperature=0,
                supports_streaming=True,
                reasoning=ModelReasoningConfig(
                    effort=ModelReasoningEffort.MEDIUM,
                    display=ModelReasoningDisplay.SUMMARIZED,
                ),
            ),
            system_prompt="Follow policy.",
        )
    )

    call = chat_models.calls[0]
    assert call.model == "claude-opus-4-7"
    assert call.model_provider == "anthropic"
    assert call.kwargs["thinking"] == {
        "type": "adaptive",
        "display": "summarized",
    }
    assert call.kwargs["output_config"] == {"effort": "medium"}
    assert "temperature" not in call.kwargs


def test_deep_agent_builder_configures_claude_budgeted_thinking(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_deepagents = FakeDeepAgentsModule()
    chat_models = CapturingChatModelFactory()
    monkeypatch.setattr(
        builder_module, "create_deep_agent", fake_deepagents.create_deep_agent
    )
    monkeypatch.setattr(builder_module, "init_chat_model", chat_models)

    build_deep_agent(
        DeepAgentBuildRequest(
            tools=("doc_search",),
            model_config=ModelConfig(
                provider="anthropic",
                model_name="claude-opus-4-6",
                max_input_tokens=200_000,
                timeout_seconds=60,
                temperature=0,
                supports_streaming=True,
                reasoning=ModelReasoningConfig(
                    budget_tokens=10_000,
                    thinking_mode=ModelThinkingMode.ENABLED,
                ),
            ),
            system_prompt="Follow policy.",
        )
    )

    assert chat_models.calls[0].kwargs["thinking"] == {
        "type": "enabled",
        "budget_tokens": 10_000,
    }


def test_subagent_checkpoint_suffix_keeps_tool_calls_in_continuing_messages() -> None:
    """The suffix must instruct the model to package checkpoint text inside the
    SAME assistant message as the next tool call. A tool-call-free message is
    treated by Deep Agents' subagent loop as the final answer; if the
    checkpoint goes out alone the subagent terminates prematurely and the
    supervisor re-dispatches the same task (regression observed in
    run 013e966edcc34634895c9068dc8cc697)."""

    suffix = builder_module.WEB_SUBAGENT_CHECKPOINT_SUFFIX
    assert "include a short progress checkpoint" in suffix
    assert "ALSO calling your next tool in the SAME message" in suffix
    assert "Do NOT emit a checkpoint without an accompanying tool call" in suffix
    assert "treated as your final answer" in suffix
    assert "/subagents/<task_id>/" in suffix


def test_web_search_planning_rule_present_in_suffix() -> None:
    """The suffix must teach query planning so the per-tool budget is spent on
    new angles rather than near-duplicate paraphrases. Pin the load-bearing
    phrases so future edits cannot silently drop the rule."""

    suffix = builder_module.WEB_SUBAGENT_CHECKPOINT_SUFFIX
    assert "Plan web_search queries before issuing them" in suffix
    assert "1–3 distinct queries" in suffix
    assert "Do NOT paraphrase a query whose prior result was already usable" in suffix
    assert "stop searching and answer with what you have" in suffix
    assert "`web-search-discipline` skill" in suffix
