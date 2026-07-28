from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any, cast

from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.tools import StructuredTool

from agent_runtime.capabilities.middleware import RuntimeControlMiddleware
from agent_runtime.control_plane.context import RunControlBinding, RunControlContext
from agent_runtime.control_plane.contracts import RunControlSnapshot, RunPolicyRevisions
from agent_runtime.control_plane.feature_modes import FeatureMode, FeatureModeSet
from agent_runtime.control_plane.model_reliability import (
    ModelReliabilityControlSnapshot,
    ModelReliabilityReleaseResolver,
)
from agent_runtime.api.model_invocation_store import EventJournalModelInvocationStore
from agent_runtime.execution.model_invocation.contracts import (
    ModelCapability,
    ModelCredentialAvailability,
    ModelCredentialMode,
    ModelDeploymentCatalog,
    ModelDeploymentDescriptor,
    ModelDispatchState,
    ModelFallbackPolicy,
    ModelAttemptDecisionReason,
    ModelInvocationAuthority,
    ModelInvocationBudget,
    ModelInvocationRequirements,
    ModelInvocationRequirementsSnapshot,
    ModelRouteEntry,
    ModelRoutePlan,
)
from agent_runtime.execution.model_invocation.journal import (
    ModelAttemptAdmissionRecord,
    ModelAttemptFailedRecord,
    ModelAttemptStateRecord,
    ModelAttemptUsageRecord,
    ModelInvocationCompletedRecord,
    ModelInvocationFailedRecord,
    ModelInvocationPlannedRecord,
    ModelInvocationRecoveryRecord,
    ModelInvocationWrite,
    ModelRecoveryKind,
    SequencedModelInvocationRecord,
)
from agent_runtime.execution.model_invocation.runtime import (
    ModelCacheFallbackPosture,
    ModelInvocationMiddleware,
    ModelInvocationReplayConflict,
    ModelInvocationRuntimeBinding,
    canonical_model_request_digest,
)
from agent_runtime.execution.providers.model_failure_adapters import (
    ProviderFailureAdapterRegistry,
)
from agent_runtime.prompts import (
    AnthropicProductPromptCacheAdapter,
    FactoryPromptFragmentProvider,
    PromptAssembler,
    PromptAssemblyContext,
    PromptCacheEligibility,
    PromptCacheFallbackContext,
    PromptFragment,
    PromptFragmentScope,
    PromptFragmentTier,
    PromptRuntimeBinding,
    PromptSensitivity,
    PromptTrustLabel,
    ProviderCacheAdapterRegistry,
    ProviderCacheOwner,
    ProviderCacheRejectionAdapterRegistry,
    ProviderCacheRejectionRule,
)

_SHA = "0" * 64
_NOW = datetime(2026, 7, 28, tzinfo=timezone.utc)


class _GenericAnthropicBadRequest(Exception):
    pass


_GenericAnthropicBadRequest.__module__ = "anthropic"
_GenericAnthropicBadRequest.__qualname__ = "BadRequestError"


class _CacheMetadataRejected(_GenericAnthropicBadRequest):
    pass


_CacheMetadataRejected.__module__ = "reviewed_cache_adapter"
_CacheMetadataRejected.__qualname__ = "CacheMetadataRejectedError"


class _Journal:
    def __init__(self) -> None:
        self.records: list[SequencedModelInvocationRecord] = []
        self.fail_after: int | None = None

    async def append(
        self, write: ModelInvocationWrite
    ) -> SequencedModelInvocationRecord:
        existing = next(
            (
                item
                for item in self.records
                if item.record.record_id == write.record.record_id
            ),
            None,
        )
        if existing is not None:
            return existing
        if self.fail_after is not None and len(self.records) >= self.fail_after:
            raise RuntimeError("journal unavailable")
        EventJournalModelInvocationStore._validate_next(
            run_id=write.record.run_id,
            records=tuple(self.records),
            candidate=write.record,
        )
        item = SequencedModelInvocationRecord(
            sequence_no=len(self.records) + 1, record=write.record
        )
        self.records.append(item)
        return item

    async def list_for_run(self, **kwargs: object):
        del kwargs
        return tuple(self.records)

    async def list_for_invocation(self, *, invocation_id: str, **kwargs: object):
        del kwargs
        return tuple(
            item for item in self.records if item.record.invocation_id == invocation_id
        )


class _AuthorityAdapter:
    def __init__(
        self,
        *,
        routes: tuple[str, ...] = ("primary",),
        budget: ModelInvocationBudget | None = None,
        provider: str = "openai",
        model_name: str = "gpt-5",
    ) -> None:
        self.calls: list[str] = []
        self.catalog = ModelDeploymentCatalog.create(
            tuple(
                _descriptor(
                    deployment,
                    provider=provider,
                    model_name=model_name,
                )
                for deployment in routes
            )
        )
        fallback = (
            ModelFallbackPolicy.SAME_MODEL
            if len(routes) > 1
            else ModelFallbackPolicy.NONE
        )
        requirements = ModelInvocationRequirements(
            task_family="research",
            provider=provider,
            model_name=model_name,
            primary_deployment_id=routes[0] if routes else None,
            required_capabilities=frozenset({ModelCapability.STREAMING}),
            minimum_context_tokens=1,
            credential_availability=(
                ModelCredentialAvailability(
                    provider=provider,
                    modes=frozenset({ModelCredentialMode.DEPLOYMENT}),
                ),
            ),
            fallback_policy=fallback,
            budget=budget or ModelInvocationBudget(),
        )
        self.requirements = ModelInvocationRequirementsSnapshot.create(requirements)
        self.route_plan = ModelRoutePlan.create(
            routes=tuple(
                _route(
                    deployment,
                    provider=provider,
                    model_name=model_name,
                )
                for deployment in routes
            ),
            exclusions=(),
            fallback_policy=fallback,
            budget=requirements.budget,
        )

    def prepare(self, *, authority_input, call_identity, control):
        request_digest = cast(str, authority_input)
        self.calls.append(request_digest)
        authority = ModelInvocationAuthority.create(
            call_identity=call_identity,
            purpose="main",
            request_digest=request_digest,
            run_control_snapshot_digest=control.snapshot.snapshot_digest,
            requirements=self.requirements,
            catalog=self.catalog,
            route_plan=self.route_plan,
        )
        return SimpleNamespace(
            authority=authority,
            requirements=self.requirements,
            catalog=self.catalog,
            route_plan=self.route_plan,
        )


class _Resolver:
    def __init__(self) -> None:
        self.routes: list[str] = []
        self.model = FakeListChatModel(responses=["alternate"])

    def resolve(self, route: ModelRouteEntry):
        self.routes.append(route.deployment_id)
        return self.model


class _PromptBinding:
    """Small F2 protocol double proving outer-to-inner request ownership."""

    model_family = "gpt-5"
    observation_publisher = None

    def prepare(self, **kwargs: object):
        tools = cast(tuple[object, ...], kwargs["tools"])
        return SimpleNamespace(
            system_message=SystemMessage(content="F2 final assembled harness"),
            tools=tools,
            observation=SimpleNamespace(
                sent_assembled_prompt=True,
                provider="openai",
            ),
        )

    async def record_assembled(self, **kwargs: object) -> object:
        del kwargs
        return object()

    async def record_cache(self, **kwargs: object) -> None:
        del kwargs


def _tool() -> StructuredTool:
    def implementation(query: str) -> str:
        return query

    return StructuredTool.from_function(
        implementation,
        name="search",
        description="Search the authorized corpus.",
    )


def _cache_prompt_binding(
    *,
    owner: ProviderCacheOwner = ProviderCacheOwner.PRODUCT,
) -> PromptRuntimeBinding:
    plan = PromptAssembler(
        context=PromptAssemblyContext(
            provider="anthropic",
            model_family="claude-sonnet-4-6",
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
                source_owner="test.cache",
                source_revision="v1",
                tier=PromptFragmentTier.SYSTEM_POLICY,
                source_scope=PromptFragmentScope.INSTALLATION,
                scope=PromptFragmentScope.INSTALLATION,
                sensitivity=PromptSensitivity.INTERNAL,
                trust=PromptTrustLabel.IMMUTABLE_POLICY,
                content="Runtime cache policy.",
                cache_eligibility=PromptCacheEligibility.STABLE_PREFIX,
            ),
        )
    )
    return PromptRuntimeBinding(
        mode=FeatureMode.ENFORCE,
        provider="anthropic",
        model_family="claude-sonnet-4-6",
        harness_revision="harness-v1",
        fragment_provider=FactoryPromptFragmentProvider(
            legacy_plan=plan,
            run_scope_fingerprint="a" * 64,
        ),
        cache_registry=ProviderCacheAdapterRegistry(
            (AnthropicProductPromptCacheAdapter(),)
        ),
        cache_owner=owner,
        framework_cache_installed=owner is ProviderCacheOwner.FRAMEWORK,
        cache_rejection_adapters=ProviderCacheRejectionAdapterRegistry(
            (
                ProviderCacheRejectionRule(
                    provider="anthropic",
                    adapter_ref="anthropic-system-prefix:v1",
                    exception_module=_CacheMetadataRejected.__module__,
                    exception_qualname=_CacheMetadataRejected.__qualname__,
                ),
            )
        ),
    )


def _cache_request(*, child: str | None = None) -> ModelRequest[Any]:
    metadata = {"supervisor_task_call_id": child} if child else {}
    return ModelRequest(
        model=FakeListChatModel(responses=["done"]),
        messages=[HumanMessage(content="private user body")],
        system_message=SystemMessage(content="Runtime cache policy.\n\nSDK harness."),
        tools=[_tool()],
        state={"runtime_control_model_turn": 1},
        runtime=cast(Any, SimpleNamespace(config={"metadata": metadata})),
        model_settings={"temperature": 0},
    )


def _descriptor(
    deployment: str,
    *,
    provider: str = "openai",
    model_name: str = "gpt-5",
) -> ModelDeploymentDescriptor:
    return ModelDeploymentDescriptor(
        deployment_id=deployment,
        endpoint_ref="endpoint_" + "1" * 32,
        provider=provider,
        model_name=model_name,
        capabilities=frozenset({ModelCapability.STREAMING}),
        max_input_tokens=10_000,
        max_output_tokens=1_000,
        region="global",
        credential_modes=frozenset({ModelCredentialMode.DEPLOYMENT}),
        price_revision="price-v1",
        descriptor_revision="descriptor-v1",
    )


def _route(
    deployment: str,
    *,
    provider: str = "openai",
    model_name: str = "gpt-5",
) -> ModelRouteEntry:
    return ModelRouteEntry.from_descriptor(
        _descriptor(
            deployment,
            provider=provider,
            model_name=model_name,
        ),
        credential_mode=ModelCredentialMode.DEPLOYMENT,
    )


def _control(run_id: str = "run-1") -> RunControlBinding:
    modes = FeatureModeSet.model_validate(
        {field: FeatureMode.OFF for field in FeatureModeSet.model_fields}
    )
    revisions = {field: "v1" for field in RunPolicyRevisions.model_fields}
    revisions["model_route"] = "model-route-policy.v2"
    snapshot = RunControlSnapshot.create(
        run_id=run_id,
        conversation_id="conversation-1",
        subject_fingerprint=_SHA,
        deployment_profile="single_user_desktop",
        harness_variant_ref="harness-v1",
        task_policy_selection_ref="task-v1",
        policy_revisions=RunPolicyRevisions.model_validate(revisions),
        feature_modes=modes,
        budget_envelope_ref=f"budget://v1/sha256/{_SHA}",
        assignment_revision="assignment-v1",
    )
    return RunControlBinding(snapshot=snapshot, effective_modes=modes, decisions=())


def _request(*, child: str | None = None, turn: int = 1) -> ModelRequest[Any]:
    metadata = {"supervisor_task_call_id": child} if child else {}
    return ModelRequest(
        model=FakeListChatModel(responses=["done"]),
        messages=[HumanMessage(content="private user body")],
        system_message=SystemMessage(content="final assembled harness"),
        tools=[],
        state={"runtime_control_model_turn": turn},
        runtime=cast(Any, SimpleNamespace(config={"metadata": metadata})),
        model_settings={},
    )


def _binding(
    *,
    journal: _Journal,
    authority: _AuthorityAdapter,
    retry: bool = False,
    alternate: bool = False,
    resolver: _Resolver | None = None,
    diagnostics: list[Exception] | None = None,
    cache_posture: ModelCacheFallbackPosture = (
        ModelCacheFallbackPosture.NOT_CONFIGURED
    ),
    external_effect_observed=lambda: False,
) -> ModelInvocationRuntimeBinding:
    release = ModelReliabilityReleaseResolver().resolve(
        run_id="run-1",
        snapshot_id="snapshot-1",
        snapshot_digest=_SHA,
        snapshot=ModelReliabilityControlSnapshot(
            same_deployment_retry=(FeatureMode.ENFORCE if retry else FeatureMode.OFF),
            alternate_route=(FeatureMode.ENFORCE if alternate else FeatureMode.OFF),
        ),
        snapshot_f10_mode=FeatureMode.ENFORCE,
        effective_f10_mode=FeatureMode.ENFORCE,
    )
    return ModelInvocationRuntimeBinding(
        authority_adapter=authority,
        authority_input_factory=lambda digest: digest,
        journal=journal,
        route_model_resolver=resolver,
        release=release,
        org_id="org-1",
        subject_fingerprint=_SHA,
        trace_id="trace-1",
        failure_adapters=ProviderFailureAdapterRegistry.defaults(),
        cache_fallback_posture=cache_posture,
        projected_cost_microusd=0,
        projected_input_tokens=0,
        projected_output_tokens=0,
        post_response_error_observer=(
            diagnostics.append if diagnostics is not None else None
        ),
        external_effect_observed=external_effect_observed,
        now=lambda: _NOW,
    )


async def _invoke(
    binding: ModelInvocationRuntimeBinding,
    request: ModelRequest[Any],
    handler,
    *,
    control: RunControlBinding | None = None,
):
    token = RunControlContext.bind_for_run(control or _control())
    try:
        RunControlContext.install_model_invocation_runtime(binding)
        return await ModelInvocationMiddleware().awrap_model_call(request, handler)
    finally:
        RunControlContext.unbind(token)


async def _invoke_with_f2(
    *,
    prompt_binding: PromptRuntimeBinding,
    model_binding: ModelInvocationRuntimeBinding,
    request: ModelRequest[Any],
    provider_handler,
):
    async def f10_handler(inner: ModelRequest[Any]) -> ModelResponse[Any]:
        return await ModelInvocationMiddleware().awrap_model_call(
            inner, provider_handler
        )

    token = RunControlContext.bind_for_run(_control())
    try:
        RunControlContext.install_prompt_runtime(prompt_binding)
        RunControlContext.install_model_invocation_runtime(model_binding)
        return await RuntimeControlMiddleware().awrap_model_call(request, f10_handler)
    finally:
        RunControlContext.unbind(token)


async def test_feature_off_and_sync_legacy_paths_preserve_exact_request() -> None:
    middleware = ModelInvocationMiddleware()
    request = _request()
    seen: list[ModelRequest[Any]] = []

    async def async_handler(inner):
        seen.append(inner)
        return ModelResponse(result=[AIMessage(content="done")])

    def sync_handler(inner):
        seen.append(inner)
        return ModelResponse(result=[AIMessage(content="done")])

    assert await middleware.awrap_model_call(request, async_handler)
    assert middleware.wrap_model_call(request, sync_handler)
    assert seen == [request, request]


async def test_success_journals_complete_lineage_and_plain_digest_projection() -> None:
    journal = _Journal()
    authority = _AuthorityAdapter()
    original = _request()
    captured: list[ModelRequest[Any]] = []

    async def handler(inner):
        captured.append(inner)
        return ModelResponse(
            result=[
                AIMessage(
                    content="done",
                    usage_metadata={
                        "input_tokens": 4,
                        "output_tokens": 2,
                        "total_tokens": 6,
                    },
                )
            ]
        )

    response = await _invoke(
        _binding(journal=journal, authority=authority), original, handler
    )
    assert response.result[0].content == "done"
    kinds = [item.record.record_kind for item in journal.records]
    assert kinds == [
        "invocation_planned",
        "route_eligible",
        "attempt_admission",
        "attempt_state",
        "attempt_state",
        "attempt_state",
        "attempt_state",
        "attempt_usage",
        "invocation_completed",
    ]
    planned = cast(ModelInvocationPlannedRecord, journal.records[0].record)
    assert len(planned.request_digest) == 64
    assert authority.calls == ["sha256:" + planned.request_digest]
    assert captured[0].model is not original.model
    assert getattr(captured[0].model, "callbacks")
    assert any(
        isinstance(item.record, ModelInvocationCompletedRecord)
        for item in journal.records
    )


async def test_f10_hashes_and_dispatches_only_the_final_f2_assembled_request() -> None:
    journal = _Journal()
    authority = _AuthorityAdapter()
    binding = _binding(journal=journal, authority=authority)
    request = _request()
    expected = request.override(
        system_message=SystemMessage(content="F2 final assembled harness")
    )
    captured: list[ModelRequest[Any]] = []

    async def provider_handler(inner: ModelRequest[Any]) -> ModelResponse[Any]:
        captured.append(inner)
        return ModelResponse(result=[AIMessage(content="done")])

    async def f10_handler(inner: ModelRequest[Any]) -> ModelResponse[Any]:
        return await ModelInvocationMiddleware().awrap_model_call(
            inner, provider_handler
        )

    token = RunControlContext.bind_for_run(_control())
    try:
        RunControlContext.install_prompt_runtime(cast(Any, _PromptBinding()))
        RunControlContext.install_model_invocation_runtime(binding)
        await RuntimeControlMiddleware().awrap_model_call(request, f10_handler)
    finally:
        RunControlContext.unbind(token)

    assert authority.calls == [canonical_model_request_digest(expected)]
    assert cast(SystemMessage, captured[0].system_message).content == (
        "F2 final assembled harness"
    )


async def test_supervisor_and_child_have_disjoint_replay_stable_identity() -> None:
    journal = _Journal()
    authority = _AuthorityAdapter()
    binding = _binding(journal=journal, authority=authority)

    async def handler(_inner):
        return ModelResponse(result=[AIMessage(content="done")])

    token = RunControlContext.bind_for_run(_control())
    try:
        RunControlContext.install_model_invocation_runtime(binding)
        middleware = ModelInvocationMiddleware()
        await middleware.awrap_model_call(_request(), handler)
        await middleware.awrap_model_call(_request(child="task-7"), handler)
    finally:
        RunControlContext.unbind(token)
    planned = [
        item.record
        for item in journal.records
        if isinstance(item.record, ModelInvocationPlannedRecord)
    ]
    assert {item.execution_scope for item in planned} == {
        "supervisor",
        "subagent:task-7",
    }
    assert len({item.model_call_id for item in planned}) == 2


async def test_safe_pre_dispatch_failure_retries_same_invocation() -> None:
    journal = _Journal()
    authority = _AuthorityAdapter(
        budget=ModelInvocationBudget(max_attempts=2, max_same_deployment_attempts=2)
    )
    calls = 0
    ConnectionErrorType = type("APIConnectionError", (Exception,), {})
    ConnectionErrorType.__module__ = "openai"

    async def handler(_inner):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ConnectionErrorType("message text is ignored")
        return ModelResponse(result=[AIMessage(content="done")])

    result = await _invoke(
        _binding(journal=journal, authority=authority, retry=True),
        _request(),
        handler,
    )
    assert result.result[0].content == "done"
    assert calls == 2
    assert (
        sum(
            isinstance(item.record, ModelAttemptAdmissionRecord)
            for item in journal.records
        )
        == 2
    )
    assert (
        sum(
            isinstance(item.record, ModelInvocationRecoveryRecord)
            for item in journal.records
        )
        == 1
    )
    assert (
        sum(
            isinstance(item.record, ModelAttemptFailedRecord)
            for item in journal.records
        )
        == 1
    )


async def test_visible_partial_and_unknown_failures_never_retry() -> None:
    for visible in (True, False):
        journal = _Journal()
        authority = _AuthorityAdapter(
            budget=ModelInvocationBudget(max_attempts=2, max_same_deployment_attempts=2)
        )
        calls = 0

        async def handler(inner):
            nonlocal calls
            calls += 1
            if visible:
                inner.model.callbacks[-1].on_llm_new_token("partial")
            raise RuntimeError("HTTP 429 text must not classify")

        try:
            await _invoke(
                _binding(journal=journal, authority=authority, retry=True),
                _request(),
                handler,
            )
        except RuntimeError:
            pass
        assert calls == 1
        assert any(
            isinstance(item.record, ModelInvocationFailedRecord)
            for item in journal.records
        )
        assert not any(
            isinstance(item.record, ModelInvocationRecoveryRecord)
            for item in journal.records
        )


async def test_alternate_route_uses_ephemeral_resolver_without_graph_rebuild() -> None:
    journal = _Journal()
    authority = _AuthorityAdapter(
        routes=("primary", "alternate"),
        # The alternate release is independent: disabled same-route retry must
        # not block the already-authorized alternate.
        budget=ModelInvocationBudget(max_attempts=2, max_same_deployment_attempts=2),
    )
    resolver = _Resolver()
    models: list[object] = []
    calls = 0
    ConnectionErrorType = type("APIConnectionError", (Exception,), {})
    ConnectionErrorType.__module__ = "openai"

    async def handler(inner):
        nonlocal calls
        calls += 1
        models.append(inner.model)
        if calls == 1:
            raise ConnectionErrorType()
        return ModelResponse(result=[AIMessage(content="done")])

    await _invoke(
        _binding(
            journal=journal,
            authority=authority,
            alternate=True,
            resolver=resolver,
        ),
        _request(),
        handler,
    )
    assert resolver.routes == ["alternate"]
    assert models[0] is not models[1]


async def test_route_resolution_failure_never_creates_a_provider_attempt() -> None:
    journal = _Journal()
    authority = _AuthorityAdapter(
        routes=("primary", "alternate"),
        budget=ModelInvocationBudget(max_attempts=2, max_same_deployment_attempts=1),
    )
    ConnectionErrorType = type("APIConnectionError", (Exception,), {})
    ConnectionErrorType.__module__ = "openai"
    calls = 0

    class FailingResolver:
        def resolve(self, route: ModelRouteEntry):
            assert route.deployment_id == "alternate"
            raise RuntimeError("ephemeral model configuration is unavailable")

    async def handler(_inner):
        nonlocal calls
        calls += 1
        raise ConnectionErrorType()

    binding = _binding(
        journal=journal,
        authority=authority,
        alternate=True,
        resolver=cast(Any, FailingResolver()),
    )
    try:
        await _invoke(binding, _request(), handler)
    except RuntimeError as error:
        assert str(error) == "ephemeral model configuration is unavailable"
    else:  # pragma: no cover - assertion guard
        raise AssertionError("route resolver failure was not propagated")

    # The first provider attempt is fully finalized. No recovery or second
    # admission exists because route resolution did not cross the SDK boundary.
    assert calls == 1
    assert (
        sum(
            isinstance(item.record, ModelAttemptAdmissionRecord)
            for item in journal.records
        )
        == 1
    )
    assert not any(
        isinstance(item.record, ModelInvocationRecoveryRecord)
        for item in journal.records
    )


async def test_deadline_denies_before_handler_and_replay_mismatch_fails_closed() -> (
    None
):
    expired = ModelInvocationBudget(deadline_at=_NOW - timedelta(seconds=1))
    journal = _Journal()
    authority = _AuthorityAdapter(budget=expired)
    calls = 0
    control = _control()

    async def handler(_inner):
        nonlocal calls
        calls += 1
        return ModelResponse(result=[AIMessage(content="done")])

    try:
        await _invoke(
            _binding(journal=journal, authority=authority),
            _request(),
            handler,
            control=control,
        )
    except RuntimeError:
        pass
    assert calls == 0
    assert any(
        isinstance(item.record, ModelInvocationFailedRecord) for item in journal.records
    )

    # A terminal model call cannot be repeated merely because the graph re-enters.
    try:
        await _invoke(
            _binding(journal=journal, authority=authority),
            _request(),
            handler,
            control=control,
        )
    except ModelInvocationReplayConflict:
        pass
    assert calls == 0


async def test_post_response_journal_failure_returns_provider_output() -> None:
    journal = _Journal()
    authority = _AuthorityAdapter()
    diagnostics: list[Exception] = []
    # plan + route + admission + dispatch succeed; completion telemetry fails.
    journal.fail_after = 4

    async def handler(_inner):
        return ModelResponse(result=[AIMessage(content="valuable answer")])

    result = await _invoke(
        _binding(
            journal=journal,
            authority=authority,
            diagnostics=diagnostics,
        ),
        _request(),
        handler,
    )
    assert result.result[0].content == "valuable answer"
    assert len(diagnostics) == 1


async def test_open_attempt_replay_is_terminalized_ambiguous_before_failing() -> None:
    journal = _Journal()
    authority = _AuthorityAdapter()
    diagnostics: list[Exception] = []
    control = _control()
    journal.fail_after = 4

    async def handler(_inner):
        return ModelResponse(result=[AIMessage(content="answer already delivered")])

    await _invoke(
        _binding(
            journal=journal,
            authority=authority,
            diagnostics=diagnostics,
        ),
        _request(),
        handler,
        control=control,
    )
    journal.fail_after = None
    try:
        await _invoke(
            _binding(journal=journal, authority=authority),
            _request(),
            handler,
            control=control,
        )
    except ModelInvocationReplayConflict:
        pass
    assert isinstance(journal.records[-1].record, ModelInvocationFailedRecord)
    assert cast(
        ModelInvocationFailedRecord, journal.records[-1].record
    ).reason.value == ("ambiguous_recovery")
    assert (
        sum(
            isinstance(item.record, ModelAttemptAdmissionRecord)
            for item in journal.records
        )
        == 1
    )


async def test_callback_end_and_handler_response_terminalize_once() -> None:
    journal = _Journal()
    authority = _AuthorityAdapter()

    async def handler(inner):
        callback = inner.model.callbacks[-1]
        callback.on_llm_end(SimpleNamespace(generations=[]))
        return ModelResponse(result=[AIMessage(content="done")])

    await _invoke(_binding(journal=journal, authority=authority), _request(), handler)
    completed_states = [
        item.record
        for item in journal.records
        if isinstance(item.record, ModelAttemptStateRecord)
        and item.record.state.value == "completed"
    ]
    assert len(completed_states) == 1


async def test_generic_request_invalid_never_triggers_cache_fallback_retry() -> None:
    journal = _Journal()
    authority = _AuthorityAdapter(
        budget=ModelInvocationBudget(max_attempts=2, max_same_deployment_attempts=2)
    )
    calls = 0
    BadRequestType = type("BadRequestError", (Exception,), {})
    BadRequestType.__module__ = "openai"

    async def handler(_inner):
        nonlocal calls
        calls += 1
        raise BadRequestType("cache metadata rejected")

    binding = _binding(journal=journal, authority=authority, retry=True)
    assert binding.cache_fallback_posture is ModelCacheFallbackPosture.NOT_CONFIGURED
    try:
        await _invoke(binding, _request(), handler)
    except BadRequestType:
        pass
    assert calls == 1
    assert not any(
        isinstance(item.record, ModelInvocationRecoveryRecord)
        for item in journal.records
    )


async def test_exact_cache_rejection_retries_once_for_root_and_local_child() -> None:
    for child in (None, "task-cache-1"):
        journal = _Journal()
        authority = _AuthorityAdapter(
            provider="anthropic",
            model_name="claude-sonnet-4-6",
            budget=ModelInvocationBudget(
                max_attempts=2,
                max_same_deployment_attempts=2,
            ),
        )
        binding = _binding(
            journal=journal,
            authority=authority,
            cache_posture=ModelCacheFallbackPosture.ENABLED,
        )
        request = _cache_request(child=child)
        captured: list[ModelRequest[Any]] = []

        async def handler(inner: ModelRequest[Any]) -> ModelResponse[Any]:
            captured.append(inner)
            if len(captured) == 1:
                error = _CacheMetadataRejected("body is never inspected")
                inner.model.callbacks[-1].on_llm_error(error)
                raise error
            return ModelResponse(result=[AIMessage(content="done")])

        response = await _invoke_with_f2(
            prompt_binding=_cache_prompt_binding(),
            model_binding=binding,
            request=request,
            provider_handler=handler,
        )

        assert response.result[0].content == "done"
        assert len(captured) == 2
        first_system = cast(SystemMessage, captured[0].system_message)
        second_system = cast(SystemMessage, captured[1].system_message)
        assert isinstance(first_system.content, list)
        assert any(
            isinstance(block, dict) and "cache_control" in block
            for block in first_system.content
        )
        assert second_system.content == "Runtime cache policy.\n\nSDK harness."
        assert captured[0].messages == captured[1].messages == request.messages
        assert [id(tool) for tool in captured[0].tools] == [
            id(tool) for tool in captured[1].tools
        ]
        assert captured[0].model_settings == captured[1].model_settings
        assert captured[0].tool_choice == captured[1].tool_choice
        assert captured[0].response_format == captured[1].response_format
        semantic = request.override(system_message=second_system)
        assert authority.calls == [canonical_model_request_digest(semantic)]
        recovery = next(
            cast(ModelInvocationRecoveryRecord, item.record)
            for item in journal.records
            if isinstance(item.record, ModelInvocationRecoveryRecord)
        )
        assert recovery.kind is ModelRecoveryKind.CACHE_UNDECORATED_RETRY
        assert (
            recovery.decision_reason
            is ModelAttemptDecisionReason.SAFE_CACHE_UNDECORATED_RETRY
        )
        assert (
            sum(
                isinstance(item.record, ModelAttemptUsageRecord)
                for item in journal.records
            )
            == 2
        )
        first_failure = next(
            cast(ModelAttemptFailedRecord, item.record)
            for item in journal.records
            if isinstance(item.record, ModelAttemptFailedRecord)
        )
        assert first_failure.dispatch_state is ModelDispatchState.NOT_ACCEPTED


async def test_generic_bad_request_text_never_matches_typed_cache_adapter() -> None:
    journal = _Journal()
    authority = _AuthorityAdapter(
        provider="anthropic",
        model_name="claude-sonnet-4-6",
        budget=ModelInvocationBudget(
            max_attempts=2,
            max_same_deployment_attempts=2,
        ),
    )
    calls = 0

    async def handler(_inner: ModelRequest[Any]) -> ModelResponse[Any]:
        nonlocal calls
        calls += 1
        raise _GenericAnthropicBadRequest("cache metadata rejected")

    try:
        await _invoke_with_f2(
            prompt_binding=_cache_prompt_binding(),
            model_binding=_binding(
                journal=journal,
                authority=authority,
                cache_posture=ModelCacheFallbackPosture.ENABLED,
            ),
            request=_cache_request(),
            provider_handler=handler,
        )
    except _GenericAnthropicBadRequest:
        pass
    else:  # pragma: no cover - assertion guard
        raise AssertionError("generic bad request was not propagated")
    assert calls == 1
    assert not any(
        isinstance(item.record, ModelInvocationRecoveryRecord)
        for item in journal.records
    )
    assert PromptCacheFallbackContext.current() is None


async def test_unconfigured_cache_fallback_preserves_decorated_request() -> None:
    journal = _Journal()
    authority = _AuthorityAdapter(
        provider="anthropic",
        model_name="claude-sonnet-4-6",
        budget=ModelInvocationBudget(
            max_attempts=2,
            max_same_deployment_attempts=2,
        ),
    )
    captured: list[ModelRequest[Any]] = []
    request = _cache_request()
    prompt_binding = _cache_prompt_binding()
    expected = prompt_binding.prepare(
        system_message=request.system_message,
        state=cast(dict[str, object], request.state),
        tools=request.tools or (),
        execution_scope="supervisor",
        task_policy_progress=None,
    )

    async def handler(inner: ModelRequest[Any]) -> ModelResponse[Any]:
        captured.append(inner)
        raise _CacheMetadataRejected("typed but feature-unconfigured")

    try:
        await _invoke_with_f2(
            prompt_binding=prompt_binding,
            model_binding=_binding(journal=journal, authority=authority),
            request=request,
            provider_handler=handler,
        )
    except _CacheMetadataRejected:
        pass
    else:  # pragma: no cover - assertion guard
        raise AssertionError("unconfigured cache rejection was not propagated")
    assert len(captured) == 1
    assert cast(SystemMessage, captured[0].system_message).model_dump(
        mode="json"
    ) == cast(SystemMessage, expected.system_message).model_dump(mode="json")
    assert not any(
        isinstance(item.record, ModelInvocationRecoveryRecord)
        for item in journal.records
    )


async def test_cache_fallback_denies_after_any_unsafe_progress() -> None:
    for progress in ("ack", "content", "tool", "usage", "effect"):
        journal = _Journal()
        authority = _AuthorityAdapter(
            provider="anthropic",
            model_name="claude-sonnet-4-6",
            budget=ModelInvocationBudget(
                max_attempts=2,
                max_same_deployment_attempts=2,
            ),
        )
        calls = 0

        async def handler(inner: ModelRequest[Any]) -> ModelResponse[Any]:
            nonlocal calls
            calls += 1
            callback = inner.model.callbacks[-1]
            if progress == "ack":
                callback.on_llm_new_token("")
            elif progress == "content":
                callback.on_llm_new_token("partial")
            elif progress == "tool":
                callback.on_llm_new_token(
                    "",
                    chunk=AIMessage(
                        content="",
                        tool_calls=[
                            {
                                "name": "search",
                                "args": {"query": "x"},
                                "id": "call-1",
                                "type": "tool_call",
                            }
                        ],
                    ),
                )
            elif progress == "usage":
                callback.on_llm_new_token(
                    "",
                    chunk=AIMessage(
                        content="",
                        usage_metadata={
                            "input_tokens": 1,
                            "output_tokens": 0,
                            "total_tokens": 1,
                        },
                    ),
                )
            raise _CacheMetadataRejected("typed rejection")

        try:
            await _invoke_with_f2(
                prompt_binding=_cache_prompt_binding(),
                model_binding=_binding(
                    journal=journal,
                    authority=authority,
                    cache_posture=ModelCacheFallbackPosture.ENABLED,
                    external_effect_observed=(
                        (lambda: True) if progress == "effect" else (lambda: False)
                    ),
                ),
                request=_cache_request(),
                provider_handler=handler,
            )
        except _CacheMetadataRejected:
            pass
        else:  # pragma: no cover - assertion guard
            raise AssertionError(f"{progress} cache rejection was not propagated")
        assert calls == 1, progress
        assert not any(
            isinstance(item.record, ModelInvocationRecoveryRecord)
            for item in journal.records
        ), progress


async def test_cache_fallback_cannot_be_consumed_twice() -> None:
    journal = _Journal()
    authority = _AuthorityAdapter(
        provider="anthropic",
        model_name="claude-sonnet-4-6",
        budget=ModelInvocationBudget(
            max_attempts=3,
            max_same_deployment_attempts=3,
        ),
    )
    calls = 0

    async def handler(_inner: ModelRequest[Any]) -> ModelResponse[Any]:
        nonlocal calls
        calls += 1
        raise _CacheMetadataRejected("typed rejection")

    try:
        await _invoke_with_f2(
            prompt_binding=_cache_prompt_binding(),
            model_binding=_binding(
                journal=journal,
                authority=authority,
                cache_posture=ModelCacheFallbackPosture.ENABLED,
            ),
            request=_cache_request(),
            provider_handler=handler,
        )
    except _CacheMetadataRejected:
        pass
    else:  # pragma: no cover - assertion guard
        raise AssertionError("second cache rejection was not propagated")
    assert calls == 2
    assert (
        sum(
            isinstance(item.record, ModelInvocationRecoveryRecord)
            for item in journal.records
        )
        == 1
    )


async def test_framework_owned_cache_fallback_is_explicitly_unsupported() -> None:
    journal = _Journal()
    authority = _AuthorityAdapter(
        provider="anthropic",
        model_name="claude-sonnet-4-6",
        budget=ModelInvocationBudget(
            max_attempts=2,
            max_same_deployment_attempts=2,
        ),
    )
    calls = 0

    async def handler(_inner: ModelRequest[Any]) -> ModelResponse[Any]:
        nonlocal calls
        calls += 1
        raise _CacheMetadataRejected("typed rejection")

    try:
        await _invoke_with_f2(
            prompt_binding=_cache_prompt_binding(owner=ProviderCacheOwner.FRAMEWORK),
            model_binding=_binding(
                journal=journal,
                authority=authority,
                cache_posture=ModelCacheFallbackPosture.ENABLED,
            ),
            request=_cache_request(),
            provider_handler=handler,
        )
    except _CacheMetadataRejected:
        pass
    else:  # pragma: no cover - assertion guard
        raise AssertionError("framework-owned rejection was not propagated")
    assert calls == 1
    assert not any(
        isinstance(item.record, ModelInvocationRecoveryRecord)
        for item in journal.records
    )


async def test_f2_cache_handoff_is_exception_safe_and_task_local() -> None:
    prompt_binding = _cache_prompt_binding()
    middleware = RuntimeControlMiddleware()
    started = 0
    both_started = asyncio.Event()
    handoffs: dict[str, object] = {}

    async def invoke(label: str, request: ModelRequest[Any]) -> object:
        async def handler(_inner: ModelRequest[Any]) -> ModelResponse[Any]:
            nonlocal started
            current = PromptCacheFallbackContext.current()
            assert current is not None
            handoffs[label] = current
            started += 1
            if started == 2:
                both_started.set()
            await both_started.wait()
            assert PromptCacheFallbackContext.current() is current
            raise RuntimeError(label)

        try:
            return await middleware.awrap_model_call(request, handler)
        except RuntimeError as error:
            return error

    token = RunControlContext.bind_for_run(_control())
    try:
        RunControlContext.install_prompt_runtime(prompt_binding)
        results = await asyncio.gather(
            invoke("root", _cache_request()),
            invoke("child", _cache_request(child="task-cache-2")),
        )
        assert PromptCacheFallbackContext.current() is None
    finally:
        RunControlContext.unbind(token)

    assert {str(item) for item in results} == {"root", "child"}
    assert handoffs["root"] is not handoffs["child"]
    assert PromptCacheFallbackContext.current() is None


def test_request_digest_is_body_sensitive_but_body_free() -> None:
    first = _request()
    second = first.override(
        system_message=SystemMessage(content="different final F2 assembly")
    )
    digest = canonical_model_request_digest(first)
    assert digest.startswith("sha256:")
    assert len(digest) == 71
    assert digest != canonical_model_request_digest(second)
    assert "private user body" not in digest
