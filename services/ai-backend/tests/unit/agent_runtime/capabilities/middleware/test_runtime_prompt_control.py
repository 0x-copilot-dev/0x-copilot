"""Graph-wide F2 model-call seam tests."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import StructuredTool

from agent_runtime.capabilities.middleware import RuntimeControlMiddleware
from agent_runtime.control_plane.context import RunControlBinding, RunControlContext
from agent_runtime.control_plane.contracts import (
    RunControlSnapshot,
    RunPolicyRevisions,
)
from agent_runtime.control_plane.feature_modes import (
    AgentQualityFeature,
    FeatureMode,
    FeatureModeSet,
)
from agent_runtime.delegation.subagents.operation_identity import (
    SUPERVISOR_TASK_CALL_ID_KEY,
)
from agent_runtime.execution.call_identity import (
    RuntimeCallContext,
    RuntimeToolCallIdentity,
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

_SHA = "0" * 64


class _Observer:
    def __init__(self) -> None:
        self.items: list[PromptRuntimeObservation] = []

    def observe(self, observation: PromptRuntimeObservation) -> None:
        self.items.append(observation)


def _control_binding() -> RunControlBinding:
    modes = FeatureModeSet.model_validate(
        {
            feature.value: (
                FeatureMode.ENFORCE
                if feature is AgentQualityFeature.F2_PROMPT_ASSEMBLY
                else FeatureMode.OFF
            )
            for feature in AgentQualityFeature
        }
    )
    revisions = RunPolicyRevisions.model_validate(
        {field: "v1" for field in RunPolicyRevisions.model_fields}
    )
    snapshot = RunControlSnapshot.create(
        run_id="run-1",
        conversation_id="conversation-1",
        subject_fingerprint=_SHA,
        deployment_profile="single_user_desktop",
        harness_variant_ref="harness-v1",
        task_policy_selection_ref="task-policy-v1",
        policy_revisions=revisions,
        feature_modes=modes,
        budget_envelope_ref=f"budget://v1/sha256/{_SHA}",
        assignment_revision="assignment-v1",
    )
    return RunControlBinding(
        snapshot=snapshot,
        effective_modes=modes,
        decisions=(),
    )


def _prompt_binding(observer: _Observer) -> PromptRuntimeBinding:
    plan = PromptAssembler().assemble(
        (
            PromptFragment(
                fragment_id="policy",
                revision="v1",
                tier=PromptFragmentTier.SYSTEM_POLICY,
                scope=PromptFragmentScope.INSTALLATION,
                content="Runtime policy.",
                cache_eligibility=PromptCacheEligibility.STABLE_PREFIX,
            ),
        )
    )
    return PromptRuntimeBinding(
        mode=FeatureMode.ENFORCE,
        provider="openai",
        model_family="gpt-5.4-mini",
        harness_revision="harness-v1",
        fragment_provider=FactoryPromptFragmentProvider(
            legacy_plan=plan,
            run_scope_fingerprint="a" * 64,
        ),
        cache_registry=ProviderCacheAdapterRegistry.default(),
        cache_owner=ProviderCacheOwner.FRAMEWORK,
        framework_cache_installed=True,
        observer=observer,
    )


def _tool(name: str) -> StructuredTool:
    def implementation(value: str) -> str:
        return value

    return StructuredTool.from_function(
        func=implementation,
        name=name,
        description=f"{name} description",
    )


def _request(*, subagent: bool = False) -> ModelRequest[Any]:
    config: dict[str, object] = {}
    if subagent:
        config = {
            "metadata": {
                SUPERVISOR_TASK_CALL_ID_KEY: "task-call-1",
            }
        }
    runtime = SimpleNamespace(config=config)
    return ModelRequest(
        model=FakeListChatModel(responses=["done"]),
        messages=[HumanMessage(content="private user message")],
        system_message=SystemMessage(content="Runtime policy.\n\nSDK harness."),
        tools=[_tool("search"), _tool("task")],
        state={"runtime_control_model_turn": 2},
        runtime=cast(Any, runtime),
        model_settings={"temperature": 0},
    )


async def test_async_seam_overrides_only_system_and_final_tools() -> None:
    middleware = RuntimeControlMiddleware()
    observer = _Observer()
    request = _request(subagent=True)
    request_before = {
        "messages": list(request.messages),
        "system": request.system_message.model_dump_json(),
        "state": dict(request.state),
        "model_settings": dict(request.model_settings),
        "tools": list(request.tools),
    }
    captured: list[ModelRequest[Any]] = []

    async def handler(inner: ModelRequest[Any]) -> ModelResponse[Any]:
        captured.append(inner)
        return ModelResponse(result=[])

    token = RunControlContext.bind_for_run(_control_binding())
    try:
        RunControlContext.install_prompt_runtime(_prompt_binding(observer))
        await middleware.awrap_model_call(request, handler)
    finally:
        RunControlContext.unbind(token)

    assert RunControlContext.prompt_runtime() is None
    assert len(captured) == 1
    outbound = captured[0]
    assert outbound is not request
    assert outbound.messages is request.messages
    assert outbound.state is request.state
    assert outbound.model is request.model
    assert outbound.model_settings is request.model_settings
    assert [tool.name for tool in outbound.tools] == ["search", "task"]
    assert "SDK harness." in str(outbound.system_message.content)
    assert request.messages == request_before["messages"]
    assert request.system_message.model_dump_json() == request_before["system"]
    assert request.state == request_before["state"]
    assert request.model_settings == request_before["model_settings"]
    assert request.tools == request_before["tools"]
    assert observer.items[0].execution_scope == "subagent:task-call-1"
    assert observer.items[0].sent_assembled_prompt
    assert middleware.final_tool_surface is not None
    assert middleware.final_tool_surface.tool_names == ("search", "task")


def test_sync_seam_preserves_runtime_call_context() -> None:
    middleware = RuntimeControlMiddleware()
    observer = _Observer()
    request = _request()
    identity = RuntimeToolCallIdentity(
        run_id="run-1",
        snapshot_id="snapshot-1",
        execution_scope="supervisor",
        model_turn=2,
        model_tool_call_id="call-1",
        operation_id="op_00000000-0000-4000-8000-000000000000",
        control_call_id="runtime-control:" + "a" * 64,
    )

    def handler(inner: ModelRequest[Any]) -> ModelResponse[Any]:
        assert RuntimeCallContext.current() is identity
        assert inner.system_message is not request.system_message
        return ModelResponse(result=[])

    token = RunControlContext.bind_for_run(_control_binding())
    try:
        RunControlContext.install_prompt_runtime(_prompt_binding(observer))
        with RuntimeCallContext.bind(identity):
            middleware.wrap_model_call(request, handler)
            assert RuntimeCallContext.current() is identity
    finally:
        RunControlContext.unbind(token)

    assert RuntimeCallContext.current() is None
    assert observer.items[0].execution_scope == "supervisor"
