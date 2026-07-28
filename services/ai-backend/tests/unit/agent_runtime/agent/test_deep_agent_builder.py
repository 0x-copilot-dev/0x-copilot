from __future__ import annotations

import asyncio
from collections import Counter
from collections.abc import Sequence
from dataclasses import FrozenInstanceError, dataclass, field
from typing import Any, ClassVar, cast

from deepagents import HarnessProfile
from langchain.agents.middleware.types import ToolCallRequest
from langchain_core.callbacks import (
    AsyncCallbackManagerForLLMRun,
    CallbackManagerForLLMRun,
)
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    ToolMessage,
)
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables import Runnable
from langchain_core.tools import StructuredTool
import pytest

from agent_runtime.capabilities.middleware import RuntimeControlMiddleware
from agent_runtime.control_plane.context import (
    RunControlBinding,
    RunControlContext,
)
from agent_runtime.control_plane.contracts import (
    RunControlSnapshot,
    RunPolicyRevisions,
)
from agent_runtime.control_plane.feature_modes import FeatureModeSet
from agent_runtime.control_plane.feature_modes import FeatureMode
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
from agent_runtime.prompts import (
    FactoryPromptFragmentProvider,
    PromptAssembler,
    PromptCacheEligibility,
    PromptFragment,
    PromptFragmentScope,
    PromptFragmentTier,
    PromptRuntimeBinding,
    PromptRuntimeObservation,
    ProviderCacheAdapterRegistry,
    ProviderCacheOwner,
)
from tests.unit.agent_runtime.agent.helpers import FakeDeepAgentsModule

_SHA256 = "0" * 64
_POLICY_REVISIONS = RunPolicyRevisions(
    prompt="prompt-v1",
    capability="capability-v1",
    context="context-v1",
    tool_controller="tool-controller-v1",
    concurrency="concurrency-v1",
    dataflow="dataflow-v1",
    mcp_freshness="mcp-freshness-v1",
    delegation="delegation-v1",
    model_route="model-route-v1",
    workspace_edit="workspace-edit-v1",
    answer_verification="answer-verification-v1",
)


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


class _CategoryFanoutModel(BaseChatModel):
    """Call every reviewed supervisor category once, then finish.

    Local child graphs do not expose ``task``. That distinction lets the same
    model return immediately inside the delegated child instead of recursively
    reproducing the supervisor fanout.
    """

    bound_tool_names: tuple[str, ...] = ()

    _TOOL_ARGUMENTS: ClassVar[dict[str, dict[str, object]]] = {
        "registry_search": {"value": "registry"},
        "call_mcp_tool": {"value": "mcp"},
        "load_skill": {"value": "skill"},
        "ask_a_question": {"value": "ask"},
        "load_prior_tool_result": {"value": "prior-result"},
        "workspace_read": {"value": "workspace"},
        "invoke_capability": {"value": "bridge"},
        "execute_dataflow": {"value": "dataflow"},
        "write_todos": {
            "todos": [{"content": "verify middleware", "status": "completed"}]
        },
        "ls": {"path": "/"},
        "read_file": {"file_path": "/missing.txt"},
        "glob": {"pattern": "*", "path": "/"},
        "grep": {"pattern": "missing", "path": "/"},
        "task": {
            "description": "Return a short final answer without tools.",
            "subagent_type": "general-purpose",
        },
    }

    @property
    def _llm_type(self) -> str:
        return "runtime-control-category-fanout"

    def _reply(self, messages: Sequence[BaseMessage]) -> AIMessage:
        if "task" not in self.bound_tool_names or any(
            isinstance(message, ToolMessage) for message in messages
        ):
            return AIMessage(content="done")
        return AIMessage(
            content="",
            tool_calls=[
                {
                    "name": name,
                    "args": dict(self._TOOL_ARGUMENTS[name]),
                    "id": f"call-{name}",
                }
                for name in self._TOOL_ARGUMENTS
                if name in self.bound_tool_names
            ],
        )

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        del stop, run_manager, kwargs
        return ChatResult(generations=[ChatGeneration(message=self._reply(messages))])

    async def _agenerate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        del stop, run_manager, kwargs
        return ChatResult(generations=[ChatGeneration(message=self._reply(messages))])

    def bind_tools(self, tools: Sequence[Any], **kwargs: Any) -> Runnable:
        del kwargs
        return self.model_copy(
            update={
                "bound_tool_names": tuple(
                    str(getattr(tool, "name", "")) for tool in tools
                )
            }
        )


def _model_config() -> ModelConfig:
    return ModelConfig(
        provider="openai",
        model_name="gpt-5.4-mini",
        max_input_tokens=128_000,
        timeout_seconds=45,
        temperature=0,
        supports_streaming=True,
    )


def _run_control_binding() -> RunControlBinding:
    snapshot = RunControlSnapshot.create(
        run_id="run-1",
        conversation_id="conversation-1",
        subject_fingerprint=_SHA256,
        deployment_profile="single-user-desktop",
        harness_variant_ref="harness://baseline-v1",
        task_policy_selection_ref="task-policy://bounded-v1",
        policy_revisions=_POLICY_REVISIONS,
        feature_modes=FeatureModeSet(),
        budget_envelope_ref=f"budget://bounded-v1/sha256/{_SHA256}",
        assignment_revision="assignment-v1",
        snapshot_id="snapshot-1",
    )
    return RunControlBinding(
        snapshot=snapshot,
        effective_modes=snapshot.feature_modes,
        decisions=(),
    )


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

    root_control = RuntimeControlMiddleware()
    build_deep_agent(
        DeepAgentBuildRequest(
            tools=(),
            model_config=_model_config(),
            system_prompt="Follow policy.",
            subagents=(
                {
                    "name": "researcher",
                    "description": "Research reviewed sources.",
                    "system_prompt": "Return a concise source review.",
                },
            ),
            middleware=(root_control,),
            universal_middleware_factories=(RuntimeControlMiddleware,),
        )
    )

    # The explicit researcher and auto general-purpose child each receive a
    # fresh materialization. The final supervisor receives only its explicitly
    # reviewed root instance.
    assert len(captured_stacks) >= 3
    controls = [
        [
            middleware
            for middleware in stack
            if isinstance(middleware, RuntimeControlMiddleware)
        ]
        for stack in captured_stacks
    ]
    assert all(len(instances) == 1 for instances in controls)
    main_control = controls[-1][0]
    subagent_controls = {id(instances[0]) for instances in controls[:-1]}
    assert len(subagent_controls) == 2
    assert main_control is root_control
    assert id(root_control) not in subagent_controls


def test_immutable_middleware_order_is_forwarded_even_when_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The public Deep Agents seam receives the exact reviewed tuple order."""

    fake_deepagents = FakeDeepAgentsModule()
    monkeypatch.setattr(
        builder_module, "create_deep_agent", fake_deepagents.create_deep_agent
    )
    monkeypatch.setenv("RUNTIME_FAKE_MODEL", "1")

    first = RuntimeControlMiddleware()
    second = RuntimeControlMiddleware()
    request = DeepAgentBuildRequest(
        tools=(),
        model_config=_model_config(),
        system_prompt="Follow policy.",
        middleware=(first, second),
    )

    with pytest.raises(FrozenInstanceError):
        request.middleware = ()  # type: ignore[misc]

    build_deep_agent(request)
    build_deep_agent(
        DeepAgentBuildRequest(
            tools=(),
            model_config=_model_config(),
            system_prompt="Follow policy.",
        )
    )

    assert fake_deepagents.calls[0]["middleware"] == [first, second]
    assert fake_deepagents.calls[1]["middleware"] == []


async def test_final_model_visible_tools_have_one_universal_controller(
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
            model_config=_model_config(),
            system_prompt="Follow policy.",
            middleware=(RuntimeControlMiddleware(),),
            universal_middleware_factories=(RuntimeControlMiddleware,),
        )
    )

    prompt_plan = PromptAssembler().assemble(
        (
            PromptFragment(
                fragment_id="policy",
                revision="v1",
                tier=PromptFragmentTier.SYSTEM_POLICY,
                scope=PromptFragmentScope.INSTALLATION,
                content="Follow policy.",
                cache_eligibility=PromptCacheEligibility.STABLE_PREFIX,
            ),
        )
    )
    observations: list[PromptRuntimeObservation] = []

    class _Observer:
        def observe(self, observation: PromptRuntimeObservation) -> None:
            observations.append(observation)

    prompt_binding = PromptRuntimeBinding(
        mode=FeatureMode.ENFORCE,
        provider="openai",
        model_family="fake-model",
        harness_revision="harness-v1",
        fragment_provider=FactoryPromptFragmentProvider(
            legacy_plan=prompt_plan,
            run_scope_fingerprint="a" * 64,
        ),
        cache_registry=ProviderCacheAdapterRegistry.default(),
        cache_owner=ProviderCacheOwner.FRAMEWORK,
        framework_cache_installed=True,
        observer=_Observer(),
    )
    run_token = RunControlContext.bind_for_run(_run_control_binding())
    try:
        RunControlContext.install_prompt_runtime(prompt_binding)
        for graph in captured_graphs:
            await graph.ainvoke(  # type: ignore[attr-defined]
                {"messages": [HumanMessage(content="Inspect tools.")]},
            )
    finally:
        RunControlContext.unbind(run_token)

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
    # Each of the two independently compiled child graphs and the supervisor
    # assembled at its own provider call. Direct child-graph canaries have no
    # parent task metadata, so their isolated execution scope is supervisor;
    # the middleware-level test covers task-linked subagent scope propagation.
    assert len(observations) == 3
    assert len({item.tool_schema_revision for item in observations}) == 2
    assert all(item.sent_assembled_prompt for item in observations)
    assert all(
        sum(isinstance(middleware, RuntimeControlMiddleware) for middleware in stack)
        == 1
        for stack in captured_stacks
    )
    for stack, bound_tools in zip(captured_stacks, bound_tool_sets, strict=True):
        controller = next(
            middleware
            for middleware in stack
            if isinstance(middleware, RuntimeControlMiddleware)
        )
        assert controller.final_tool_surface is not None
        assert frozenset(controller.final_tool_surface.tool_names) == bound_tools
        assert builder_module.WEB_EXCLUDED_DEEP_AGENT_TOOLS.isdisjoint(
            controller.final_tool_surface.tool_names
        )


async def test_live_final_tool_categories_cross_one_runtime_admission(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Drive factory, bridge, dataflow, task, and injected tools through a graph."""

    category_names = (
        "registry_search",
        "call_mcp_tool",
        "load_skill",
        "ask_a_question",
        "load_prior_tool_result",
        "workspace_read",
        "invoke_capability",
        "execute_dataflow",
    )

    def category_tool(name: str) -> StructuredTool:
        async def invoke(value: str = "") -> str:
            return f"{name}:{value}"

        return StructuredTool.from_function(
            coroutine=invoke,
            name=name,
            description=f"Exercise the {name} admission category.",
        )

    admissions: list[tuple[int, str, str]] = []
    original_admission = RuntimeControlMiddleware.awrap_tool_call

    async def capture_admission(
        middleware: RuntimeControlMiddleware,
        request: ToolCallRequest,
        handler: Any,
    ) -> object:
        admissions.append(
            (
                id(middleware),
                str(request.tool_call["name"]),
                str(request.tool_call["id"]),
            )
        )
        return await original_admission(middleware, request, handler)

    monkeypatch.setattr(
        RuntimeControlMiddleware,
        "awrap_tool_call",
        capture_admission,
    )
    monkeypatch.setattr(
        builder_module,
        "build_chat_model",
        lambda *_args, **_kwargs: _CategoryFanoutModel(),
    )
    monkeypatch.setattr(builder_module, "_web_harness_profiles_registered", False)

    graph = build_deep_agent(
        DeepAgentBuildRequest(
            tools=tuple(category_tool(name) for name in category_names),
            model_config=_model_config(),
            system_prompt="Exercise every final tool category exactly once.",
            middleware=(RuntimeControlMiddleware(),),
            universal_middleware_factories=(RuntimeControlMiddleware,),
        )
    )

    result = await graph.ainvoke(
        {"messages": [HumanMessage(content="Run the admission canary.")]}
    )

    injected_names = ("write_todos", "ls", "read_file", "glob", "grep", "task")
    expected_names = (*category_names, *injected_names)
    counts = Counter(name for _, name, _ in admissions)
    assert counts == Counter({name: 1 for name in expected_names})
    assert {call_id for _, _, call_id in admissions} == {
        f"call-{name}" for name in expected_names
    }
    # All supervisor calls crossed the one explicit root instance. The task
    # child returned without tools, while the construction test above proves
    # that it received its own fresh reviewed middleware instance.
    assert len({middleware_id for middleware_id, _, _ in admissions}) == 1
    assert result["messages"][-1].content == "done"


async def test_distinct_supervisor_and_child_instances_share_run_serial_permit() -> (
    None
):
    """One verified run permit serializes calls across independently built graphs."""

    supervisor = RuntimeControlMiddleware()
    child = RuntimeControlMiddleware()
    assert supervisor is not child
    active = 0
    maximum_active = 0

    def request(call_id: str) -> ToolCallRequest:
        return ToolCallRequest(
            tool_call={
                "name": "registry_search",
                "args": {"value": call_id},
                "id": call_id,
                "type": "tool_call",
            },
            tool=None,
            state={"runtime_control_model_turn": 1},
            runtime=cast(Any, object()),
        )

    async def handler(inner_request: ToolCallRequest) -> ToolMessage:
        nonlocal active, maximum_active
        active += 1
        maximum_active = max(maximum_active, active)
        await asyncio.sleep(0)
        active -= 1
        return ToolMessage(
            content="done",
            tool_call_id=str(inner_request.tool_call["id"]),
        )

    token = RunControlContext.bind_for_run(_run_control_binding())
    try:
        await asyncio.gather(
            supervisor.awrap_tool_call(request("supervisor-call"), handler),
            child.awrap_tool_call(request("child-call"), handler),
        )
    finally:
        RunControlContext.unbind(token)

    assert maximum_active == 1


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
