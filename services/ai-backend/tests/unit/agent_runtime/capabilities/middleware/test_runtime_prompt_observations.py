"""Durable F2 observations at the supported LangChain model-call seam."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

import pytest
from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

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
from agent_runtime.prompts import (
    FactoryPromptFragmentProvider,
    PromptAssemblyContext,
    PromptAssembler,
    PromptCacheEligibility,
    PromptFragment,
    PromptFragmentScope,
    PromptFragmentTier,
    PromptRuntimeBinding,
    PromptSensitivity,
    PromptTrustLabel,
    ProviderCacheAdapterRegistry,
    ProviderCacheOwner,
)
from agent_runtime.prompts.observation import (
    PromptAssemblyObserver,
    PromptCacheOutcome,
    PromptObservationConflict,
    PromptObservationWrite,
    SequencedPromptObservationRecord,
)

_SHA = "0" * 64


class _IdempotentObservationStore:
    def __init__(self) -> None:
        self.items: list[SequencedPromptObservationRecord] = []

    async def append(
        self,
        write: PromptObservationWrite,
    ) -> SequencedPromptObservationRecord:
        existing = next(
            (
                item
                for item in self.items
                if item.record.record_id == write.record.record_id
            ),
            None,
        )
        if existing is not None:
            if existing.record.record_digest != write.record.record_digest:
                raise PromptObservationConflict(
                    run_id=write.record.run_id,
                    record_id=write.record.record_id,
                )
            return existing
        item = SequencedPromptObservationRecord(
            sequence_no=len(self.items) + 1,
            record=write.record,
        )
        self.items.append(item)
        return item

    async def list_for_run(
        self,
        *,
        org_id: str,
        run_id: str,
        subject_fingerprint: str,
        after_sequence: int = 0,
    ) -> tuple[SequencedPromptObservationRecord, ...]:
        del org_id, run_id, subject_fingerprint
        return tuple(item for item in self.items if item.sequence_no > after_sequence)


class _FailingObservationStore(_IdempotentObservationStore):
    async def append(
        self,
        write: PromptObservationWrite,
    ) -> SequencedPromptObservationRecord:
        del write
        raise RuntimeError("observation store unavailable")


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
    snapshot = RunControlSnapshot.create(
        run_id="run-observed",
        conversation_id="conversation-observed",
        subject_fingerprint=_SHA,
        deployment_profile="single_user_desktop",
        harness_variant_ref="harness-v1",
        task_policy_selection_ref="task-policy-v1",
        policy_revisions=RunPolicyRevisions.model_validate(
            {field: "v1" for field in RunPolicyRevisions.model_fields}
        ),
        feature_modes=modes,
        budget_envelope_ref=f"budget://v1/sha256/{_SHA}",
        assignment_revision="assignment-v1",
    )
    return RunControlBinding(
        snapshot=snapshot,
        effective_modes=modes,
        decisions=(),
    )


def _prompt_binding(
    *,
    control: RunControlBinding,
    store: _IdempotentObservationStore,
    provider: str,
    model_family: str,
    mode: FeatureMode = FeatureMode.ENFORCE,
    cache_owner: ProviderCacheOwner = ProviderCacheOwner.FRAMEWORK,
) -> PromptRuntimeBinding:
    plan = PromptAssembler(
        context=PromptAssemblyContext(
            provider=provider,
            model_family=model_family,
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
                content="Runtime policy.",
                cache_eligibility=PromptCacheEligibility.STABLE_PREFIX,
            ),
        )
    )
    return PromptRuntimeBinding(
        mode=mode,
        provider=provider,
        model_family=model_family,
        harness_revision="harness-v1",
        fragment_provider=FactoryPromptFragmentProvider(
            legacy_plan=plan,
            run_scope_fingerprint="a" * 64,
        ),
        cache_registry=ProviderCacheAdapterRegistry.default(),
        cache_owner=cache_owner,
        framework_cache_installed=True,
        observation_publisher=PromptAssemblyObserver(
            store=store,
            binding=control,
            org_id="org-observed",
            subject_fingerprint=_SHA,
            trace_id="trace-observed",
        ),
    )


def _request(
    *,
    task_call_id: str | None = None,
) -> ModelRequest[Any]:
    metadata = (
        {SUPERVISOR_TASK_CALL_ID_KEY: task_call_id} if task_call_id is not None else {}
    )
    return ModelRequest(
        model=FakeListChatModel(responses=["done"]),
        messages=[HumanMessage(content="private user message")],
        system_message=SystemMessage(content="Runtime policy.\n\nSDK harness."),
        tools=[],
        state={"runtime_control_model_turn": 2},
        runtime=cast(Any, SimpleNamespace(config={"metadata": metadata})),
        model_settings={},
    )


@pytest.mark.parametrize(
    ("provider", "model_family", "response_metadata", "expected"),
    (
        (
            "openai",
            "gpt-5.4-mini",
            {
                "token_usage": {
                    "prompt_tokens": 100,
                    "completion_tokens": 10,
                    "prompt_tokens_details": {"cached_tokens": 80},
                }
            },
            PromptCacheOutcome.READ,
        ),
        (
            "anthropic",
            "claude-sonnet-4",
            {
                "usage": {
                    "input_tokens": 20,
                    "output_tokens": 10,
                    "cache_creation_input_tokens": 80,
                    "cache_read_input_tokens": 0,
                }
            },
            PromptCacheOutcome.WRITE,
        ),
        (
            "openai",
            "gpt-5.4-mini",
            {
                "token_usage": {
                    "prompt_tokens": 100,
                    "completion_tokens": 10,
                    "prompt_tokens_details": {"cached_tokens": 0},
                }
            },
            PromptCacheOutcome.MISS,
        ),
        (
            "gemini",
            "gemini-2.5-pro",
            {
                "usage": {
                    "prompt_token_count": 100,
                    "candidates_token_count": 10,
                }
            },
            PromptCacheOutcome.UNSUPPORTED,
        ),
    ),
)
async def test_provider_usage_is_recorded_after_dispatch(
    provider: str,
    model_family: str,
    response_metadata: dict[str, object],
    expected: PromptCacheOutcome,
) -> None:
    control = _control_binding()
    store = _IdempotentObservationStore()
    middleware = RuntimeControlMiddleware()

    async def handler(request: ModelRequest[Any]) -> ModelResponse[Any]:
        assert len(store.items) == 1
        assert store.items[0].record.record_kind == "assembled"
        return ModelResponse(
            result=[
                AIMessage(content="done", response_metadata=response_metadata),
            ]
        )

    token = RunControlContext.bind_for_run(control)
    try:
        RunControlContext.install_prompt_runtime(
            _prompt_binding(
                control=control,
                store=store,
                provider=provider,
                model_family=model_family,
            )
        )
        await middleware.awrap_model_call(
            _request(),
            handler,
        )
    finally:
        RunControlContext.unbind(token)

    assert [item.record.record_kind for item in store.items] == [
        "assembled",
        "cache_observed",
    ]
    assert store.items[1].record.outcome is expected  # type: ignore[union-attr]


async def test_root_child_identity_is_distinct_and_replay_is_idempotent() -> None:
    control = _control_binding()
    store = _IdempotentObservationStore()
    middleware = RuntimeControlMiddleware()

    async def handler(_request: ModelRequest[Any]) -> ModelResponse[Any]:
        return ModelResponse(
            result=[
                AIMessage(
                    content="done",
                    response_metadata={
                        "token_usage": {
                            "prompt_tokens": 100,
                            "completion_tokens": 10,
                            "prompt_tokens_details": {"cached_tokens": 0},
                        }
                    },
                )
            ]
        )

    token = RunControlContext.bind_for_run(control)
    try:
        RunControlContext.install_prompt_runtime(
            _prompt_binding(
                control=control,
                store=store,
                provider="openai",
                model_family="gpt-5.4-mini",
            )
        )
        await middleware.awrap_model_call(
            _request(),
            handler,
        )
        child = _request(
            task_call_id="task-call-1",
        )
        await middleware.awrap_model_call(child, handler)
        await middleware.awrap_model_call(child, handler)
    finally:
        RunControlContext.unbind(token)

    assert len(store.items) == 4
    model_call_ids = {
        item.record.model_call_id
        for item in store.items
        if item.record.record_kind == "assembled"
    }
    assert len(model_call_ids) == 2


async def test_provider_reported_outcome_wins_over_local_decoration_state() -> None:
    control = _control_binding()
    store = _IdempotentObservationStore()
    middleware = RuntimeControlMiddleware()

    async def handler(_request: ModelRequest[Any]) -> ModelResponse[Any]:
        return ModelResponse(
            result=[
                AIMessage(
                    content="done",
                    response_metadata={
                        "token_usage": {
                            "prompt_tokens": 100,
                            "completion_tokens": 10,
                            "prompt_tokens_details": {"cached_tokens": 80},
                        }
                    },
                )
            ]
        )

    token = RunControlContext.bind_for_run(control)
    try:
        RunControlContext.install_prompt_runtime(
            _prompt_binding(
                control=control,
                store=store,
                provider="openai",
                model_family="gpt-5.4-mini",
                cache_owner=ProviderCacheOwner.NONE,
            )
        )
        await middleware.awrap_model_call(_request(), handler)
    finally:
        RunControlContext.unbind(token)

    cache = store.items[1].record
    assert cache.outcome is PromptCacheOutcome.READ  # type: ignore[union-attr]
    assert cache.reason_code == "provider_reported_read"  # type: ignore[union-attr]


async def test_provider_failure_leaves_assembly_without_fabricated_cache_usage() -> (
    None
):
    control = _control_binding()
    store = _IdempotentObservationStore()
    middleware = RuntimeControlMiddleware()

    async def handler(_request: ModelRequest[Any]) -> ModelResponse[Any]:
        raise TimeoutError("provider timed out")

    token = RunControlContext.bind_for_run(control)
    try:
        RunControlContext.install_prompt_runtime(
            _prompt_binding(
                control=control,
                store=store,
                provider="openai",
                model_family="gpt-5.4-mini",
            )
        )
        with pytest.raises(TimeoutError, match="provider timed out"):
            await middleware.awrap_model_call(_request(), handler)
    finally:
        RunControlContext.unbind(token)

    assert [item.record.record_kind for item in store.items] == ["assembled"]


async def test_shadow_observation_failure_preserves_legacy_provider_request() -> None:
    control = _control_binding()
    store = _FailingObservationStore()
    middleware = RuntimeControlMiddleware()
    request = _request()
    original_system = request.system_message
    dispatched = False

    async def handler(inner: ModelRequest[Any]) -> ModelResponse[Any]:
        nonlocal dispatched
        dispatched = True
        assert inner is request
        assert inner.system_message is original_system
        return ModelResponse(result=[AIMessage(content="done")])

    token = RunControlContext.bind_for_run(control)
    try:
        RunControlContext.install_prompt_runtime(
            _prompt_binding(
                control=control,
                store=store,
                provider="openai",
                model_family="gpt-5.4-mini",
                mode=FeatureMode.SHADOW,
            )
        )
        await middleware.awrap_model_call(request, handler)
    finally:
        RunControlContext.unbind(token)

    assert dispatched


async def test_enforce_observation_failure_blocks_provider_dispatch() -> None:
    control = _control_binding()
    store = _FailingObservationStore()
    middleware = RuntimeControlMiddleware()
    dispatched = False

    async def handler(_request: ModelRequest[Any]) -> ModelResponse[Any]:
        nonlocal dispatched
        dispatched = True
        return ModelResponse(result=[])

    token = RunControlContext.bind_for_run(control)
    try:
        RunControlContext.install_prompt_runtime(
            _prompt_binding(
                control=control,
                store=store,
                provider="openai",
                model_family="gpt-5.4-mini",
            )
        )
        with pytest.raises(RuntimeError, match="observation store unavailable"):
            await middleware.awrap_model_call(_request(), handler)
    finally:
        RunControlContext.unbind(token)

    assert dispatched is False


def test_sync_dispatch_fails_before_provider_when_durable_observer_is_bound() -> None:
    control = _control_binding()
    store = _IdempotentObservationStore()
    middleware = RuntimeControlMiddleware()
    dispatched = False

    def handler(_request: ModelRequest[Any]) -> ModelResponse[Any]:
        nonlocal dispatched
        dispatched = True
        return ModelResponse(result=[])

    token = RunControlContext.bind_for_run(control)
    try:
        RunControlContext.install_prompt_runtime(
            _prompt_binding(
                control=control,
                store=store,
                provider="openai",
                model_family="gpt-5.4-mini",
            )
        )
        with pytest.raises(RuntimeError, match="async model-call seam"):
            middleware.wrap_model_call(
                _request(),
                handler,
            )
    finally:
        RunControlContext.unbind(token)

    assert dispatched is False
    assert store.items == []
