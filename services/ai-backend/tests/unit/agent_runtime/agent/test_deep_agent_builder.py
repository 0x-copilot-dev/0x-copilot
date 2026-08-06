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
from langchain.agents.middleware import TodoListMiddleware

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
    PromptAssemblyContext,
    PromptAssembler,
    PromptCacheEligibility,
    PromptFragment,
    PromptFragmentScope,
    PromptFragmentTier,
    PromptRuntimeBinding,
    PromptRuntimeObservation,
    PromptSensitivity,
    PromptTrustLabel,
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
    # Exactly one runtime boundary per graph. This is the invariant that
    # matters and it still holds: two controllers on one graph would double
    # every admission decision.
    assert all(len(instances) == 1 for instances in controls)
    assert controls[-1][0] is root_control

    # Instance IDENTITY is deliberately no longer asserted. It was a proxy for
    # "a child cannot spend the supervisor's tool budget", and that property is
    # owned elsewhere: production binds ONE run-scoped `RunControlContext`, and
    # `RuntimeControlMiddleware` reads admission from it — the instance-local
    # `_fallback_serial_admission` fires only when no binding exists, which the
    # worker makes impossible (`loop.py` constructs a `RunControlPlaneBuilder`
    # when the caller supplies none).
    #
    # 0.7.1 also makes per-graph identity unachievable without forking the
    # library: it compiles TWO graphs per subagent and materializes harness
    # middleware once per subagent PROFILE, so both of a child's graphs share
    # one materialization by construction.
    #
    # What remains asserted is the thing that would make sharing dangerous: the
    # middleware must carry no per-graph mutable state beyond the documented
    # fallbacks. `_final_tool_surface` is the one exception and has no
    # production reader — it is a test canary. If that ever changes, this
    # assertion fails and the sharing question has to be reopened.
    shared = {
        name
        for name, value in vars(root_control).items()
        if name not in {"_excluded_tool_names", "_final_tool_surface"}
        and not name.startswith("_fallback_")
    }
    assert shared == set(), (
        f"RuntimeControlMiddleware grew per-graph mutable state {sorted(shared)}; "
        "graphs share instances under 0.7.1, so this is now cross-graph state"
    )


def test_pinned_framework_cache_middleware_remains_on_root_and_local_children(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Product ownership stays dormant without a supported per-run skip seam."""

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
    monkeypatch.setattr(
        deepagents_subagents,
        "create_agent",
        capture_create_agent,
    )
    monkeypatch.setattr(builder_module, "_web_harness_profiles_registered", False)

    request = DeepAgentBuildRequest(
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
        middleware=(RuntimeControlMiddleware(),),
        universal_middleware_factories=(RuntimeControlMiddleware,),
    )

    build_deep_agent(request)

    assert len(captured_stacks) >= 3
    assert all(
        "AnthropicPromptCachingMiddleware"
        in {type(middleware).__name__ for middleware in stack}
        for stack in captured_stacks
    )


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

    prompt_plan = PromptAssembler(
        context=PromptAssemblyContext(
            provider="openai",
            model_family="gpt-5.4-mini",
            harness_revision="harness-v1",
            capability_bridge_revision="bridge-v1",
            tool_schema_revision="tools-v1",
            policy_revision="policy-v1",
            authorization_revision="authorization-v1",
        )
    ).assemble(
        (
            PromptFragment(
                fragment_id="policy",
                source_owner="test.runtime",
                source_revision="v1",
                tier=PromptFragmentTier.SYSTEM_POLICY,
                source_scope=PromptFragmentScope.INSTALLATION,
                scope=PromptFragmentScope.INSTALLATION,
                sensitivity=PromptSensitivity.INTERNAL,
                trust=PromptTrustLabel.IMMUTABLE_POLICY,
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

    # `write_file` / `edit_file` are model-visible again. They were withheld by
    # `DEEP_AGENT_PROFILE_EXCLUDED_TOOL_NAMES` while host writes were meant to
    # route through the staged lane — a lane that never ran on desktop — so the
    # agent could not write anywhere, including its own scratch. What bounds a
    # write is now the rule set (writable grant only) plus the floor.
    expected_local_tools = frozenset(
        {
            "catalog_read",
            # deepagents 0.7.1 added `delete` to FilesystemMiddleware.
            "delete",
            "edit_file",
            "glob",
            "grep",
            "ls",
            "read_file",
            "write_file",
            # `write_todos` is contributed by `TodoListMiddleware` rather than
            # passed through `tools=`, and under 0.7.1 a middleware-contributed
            # tool is not visible at THIS capture point. It is still bound and
            # callable — `runtime_worker/test_todo_list_real_run.py` drives a
            # real graph with the real middleware and asserts the tool runs — so
            # asserting it here would pin the capture point, not the surface.
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
    # `final_tool_surface` is per-INSTANCE, and 0.7.1 shares one instance across
    # graphs, so every controller now reports the surface of whichever graph
    # observed last — the supervisor's. Asserting it per graph would be pinning
    # the sharing, not the surface. What is still worth holding is that the
    # canary observed a real surface and that the exclusion set was honoured.
    #
    # This is the ONLY reader of `final_tool_surface` anywhere, production
    # included, which is why the sharing is tolerable: see the note on instance
    # identity in `test_universal_middleware_is_materialized_...` above.
    supervisor_tools = max(bound_tool_sets, key=len)
    for stack, _bound_tools in zip(captured_stacks, bound_tool_sets, strict=True):
        controller = next(
            middleware
            for middleware in stack
            if isinstance(middleware, RuntimeControlMiddleware)
        )
        assert controller.final_tool_surface is not None
        assert frozenset(controller.final_tool_surface.tool_names) == supervisor_tools
        assert builder_module.WEB_EXCLUDED_DEEP_AGENT_TOOLS.isdisjoint(
            controller.final_tool_surface.tool_names
        )


async def test_live_final_tool_categories_cross_one_runtime_admission(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Drive factory, bridge, dataflow, task, and injected tools through a graph."""

    category_names = (
        "registry_search",
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
            # `TodoListMiddleware` is declared here for the same reason the
            # factory declares it: 0.7.1 no longer injects `write_todos`, so a
            # graph that wants it in its admission canary has to compose it.
            middleware=(RuntimeControlMiddleware(), TodoListMiddleware()),
            universal_middleware_factories=(
                RuntimeControlMiddleware,
                TodoListMiddleware,
            ),
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


async def test_distinct_supervisor_and_child_instances_share_one_run_ledger() -> None:
    """Independently built graphs share the run's records — not a serial lock.

    Deep Agents materializes a fresh middleware stack for every locally compiled
    subagent, so "one object per run, reachable from every graph" is the property
    that has to hold. It used to be demonstrated with the run-scoped exclusive
    permit, which also meant a supervisor call and a delegated call could never
    overlap. Scheduling is the framework's now, so the two halves are asserted
    separately: they do overlap, and they still land in the same run ledger.
    """

    supervisor = RuntimeControlMiddleware()
    child = RuntimeControlMiddleware()
    assert supervisor is not child
    active = 0
    maximum_active = 0
    both_present = asyncio.Event()

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
        if active >= 2:
            both_present.set()
        try:
            # Times out rather than hangs if something serializes the two, so
            # the failure is a peak of one instead of a stuck suite.
            await asyncio.wait_for(both_present.wait(), timeout=5.0)
        except TimeoutError:
            pass
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
        # The run-scoped object both graphs reached: one ledger, both calls, and
        # neither middleware fell back to its instance-local reducer.
        reducer = RunControlContext.lifecycle_reducer()
        assert reducer is not None
        recorded = {record.operation_id for record in reducer.records()}
    finally:
        RunControlContext.unbind(token)

    assert maximum_active == 2
    assert len(recorded) == 2, recorded


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

    # `display` is now always sent, in BOTH thinking modes. It governs whether
    # the thinking summary comes back at all and defaults to "omitted" on the
    # newest Claude models, so taking the provider default meant paying for
    # every thinking token and discarding the text (measured: 0 reasoning events
    # on claude-sonnet-5, 3 with this field). The tokens are billed identically
    # either way, so requesting the summary costs nothing.
    assert chat_models.calls[0].kwargs["thinking"] == {
        "type": "enabled",
        "budget_tokens": 10_000,
        "display": "summarized",
    }


def test_claude_5_rejects_manual_thinking_so_the_builder_sends_adaptive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A budgeted config must not be passed through verbatim to a 4.7+ model.

    Manual thinking returns a 400 there — `"thinking.type.enabled" is not
    supported for this model` — so a deployment carrying
    ``RUNTIME_DEFAULT_THINKING_MODE=enabled`` (ours does) failed EVERY Anthropic
    run on Sonnet 5, not merely the thinking part of it. The operator's intent
    is honoured in the grammar the model accepts.
    """

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
                model_name="claude-sonnet-5",
                max_input_tokens=200_000,
                timeout_seconds=60,
                temperature=0,
                supports_streaming=True,
                reasoning=ModelReasoningConfig(
                    budget_tokens=4_000,
                    thinking_mode=ModelThinkingMode.ENABLED,
                ),
            ),
            system_prompt="Follow policy.",
        )
    )

    thinking = chat_models.calls[0].kwargs["thinking"]
    assert thinking["type"] == "adaptive"
    # `budget_tokens` is meaningless in adaptive mode and is dropped rather than
    # sent alongside a mode that ignores it.
    assert "budget_tokens" not in thinking
    assert thinking["display"] == "summarized"


def test_older_claude_models_keep_manual_thinking() -> None:
    """The coercion is scoped: guessing adaptive for a manual-only model would
    break it in the opposite direction."""

    assert not builder_module._anthropic_is_adaptive_only("claude-sonnet-4-5")
    assert not builder_module._anthropic_is_adaptive_only("claude-opus-4-5")
    assert builder_module._anthropic_is_adaptive_only("claude-sonnet-5")
    assert builder_module._anthropic_is_adaptive_only("claude-opus-4-7")
    assert builder_module._anthropic_is_adaptive_only("claude-fable-5")
    # Not a Claude model at all — leave the caller's mode alone.
    assert not builder_module._anthropic_is_adaptive_only("gpt-5.6")


def test_thinking_mode_predicates_partition_on_the_generation_boundary() -> None:
    """The two modes are mutually exclusive, and BOTH directions are guarded.

    Measured against the live API, which is why this is stated as a partition
    rather than as two independent rules:

        claude-sonnet-5    adaptive OK   enabled 400
        claude-sonnet-4-5  adaptive 400  enabled OK
        claude-haiku-4-5   adaptive 400  enabled OK

    Asserted together so neither half can be relaxed on its own — the bug this
    pins existed because only the first half was ever written.
    """

    adaptive_only = builder_module._anthropic_is_adaptive_only
    rejects_adaptive = builder_module._anthropic_rejects_adaptive

    for name in ("claude-sonnet-5", "claude-opus-4-7", "claude-fable-5"):
        assert adaptive_only(name), name
        assert not rejects_adaptive(name), name
    for name in ("claude-haiku-4-5", "claude-sonnet-4-5", "claude-opus-4-1"):
        assert not adaptive_only(name), name
        assert rejects_adaptive(name), name

    # An unrecognised name is coerced in NEITHER direction: both predicates are
    # false, so the caller's configured mode survives untouched. Guessing here
    # is what would break a model we have never seen.
    for name in ("gpt-5.6", "claude-experimental", "llama-3"):
        assert not adaptive_only(name), name
        assert not rejects_adaptive(name), name


def test_claude_45_rejects_adaptive_so_the_builder_sends_manual(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The mirror of the Sonnet 5 case, and the one that shipped broken.

    Adaptive is the default when nothing configures a mode, and
    ``ModelSelection`` synthesises a bare ``ModelReasoningConfig()`` for every
    Anthropic thinking model — so a packaged desktop with no reasoning env sent
    ``type: "adaptive"`` to Claude Haiku 4.5 and failed EVERY run on it with
    ``400 adaptive thinking is not supported on this model``.
    """

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
                model_name="claude-haiku-4-5",
                max_input_tokens=200_000,
                timeout_seconds=60,
                temperature=0,
                supports_streaming=True,
                # Exactly what ModelSelection synthesises: no mode, no budget.
                reasoning=ModelReasoningConfig(),
            ),
            system_prompt="Follow policy.",
        )
    )

    thinking = chat_models.calls[0].kwargs["thinking"]
    assert thinking["type"] == "enabled"
    # Manual thinking 400s without a budget, and the adaptive config it was
    # coerced from can never carry one, so the floor is supplied.
    assert thinking["budget_tokens"] == builder_module._ANTHROPIC_MIN_THINKING_BUDGET
    assert thinking["display"] == "summarized"
    # `output_config.effort` is adaptive-only and must not survive the coercion.
    assert "output_config" not in chat_models.calls[0].kwargs


def test_manual_thinking_budget_stays_below_the_output_cap() -> None:
    """`max_tokens` must be STRICTLY greater than `thinking.budget_tokens`.

    Equal is ``400 `max_tokens` must be greater than `thinking.budget_tokens```,
    so a deployment whose output cap matches its thinking budget would fail
    every run. With no cap on the request there is nothing to collide with.
    """

    budget_for = builder_module._anthropic_manual_budget
    floor = builder_module._ANTHROPIC_MIN_THINKING_BUDGET

    # No cap: the configured budget stands.
    assert budget_for(ModelReasoningConfig(budget_tokens=4_000), None) == 4_000
    # Unconfigured: the API floor.
    assert budget_for(ModelReasoningConfig(), None) == floor
    # Cap above the budget: untouched.
    assert budget_for(ModelReasoningConfig(budget_tokens=4_000), 8_000) == 4_000
    # Cap equal to the budget: clamped strictly below it.
    assert budget_for(ModelReasoningConfig(budget_tokens=4_000), 4_000) == 3_999
    # Cap with no room to both think and answer: thinking is dropped rather
    # than sent as a request the API will reject.
    assert budget_for(ModelReasoningConfig(budget_tokens=4_000), floor) is None


def test_no_room_for_thinking_drops_the_kwarg_entirely(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A cap too small to think under yields no `thinking` at all — not a 400."""

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
                model_name="claude-haiku-4-5",
                max_input_tokens=200_000,
                max_output_tokens=512,
                timeout_seconds=60,
                temperature=0,
                supports_streaming=True,
                reasoning=ModelReasoningConfig(),
            ),
            system_prompt="Follow policy.",
        )
    )

    assert "thinking" not in chat_models.calls[0].kwargs


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
