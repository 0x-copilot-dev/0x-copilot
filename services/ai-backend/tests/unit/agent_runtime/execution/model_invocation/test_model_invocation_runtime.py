from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any, cast

from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from agent_runtime.capabilities.middleware import RuntimeControlMiddleware
from agent_runtime.control_plane.context import RunControlBinding, RunControlContext
from agent_runtime.control_plane.contracts import RunControlSnapshot, RunPolicyRevisions
from agent_runtime.control_plane.feature_modes import FeatureMode, FeatureModeSet
from agent_runtime.api.model_invocation_store import EventJournalModelInvocationStore
from agent_runtime.execution.model_invocation.contracts import (
    ModelCapability,
    ModelCredentialAvailability,
    ModelCredentialMode,
    ModelDeploymentCatalog,
    ModelDeploymentDescriptor,
    ModelFallbackPolicy,
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
    ModelInvocationCompletedRecord,
    ModelInvocationFailedRecord,
    ModelInvocationPlannedRecord,
    ModelInvocationRecoveryRecord,
    ModelInvocationWrite,
    SequencedModelInvocationRecord,
)
from agent_runtime.execution.model_invocation.release_controls import (
    ModelReliabilityReleaseControls,
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

_SHA = "0" * 64
_NOW = datetime(2026, 7, 28, tzinfo=timezone.utc)


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
    ) -> None:
        self.calls: list[str] = []
        self.catalog = ModelDeploymentCatalog.create(
            tuple(_descriptor(deployment) for deployment in routes)
        )
        fallback = (
            ModelFallbackPolicy.SAME_MODEL
            if len(routes) > 1
            else ModelFallbackPolicy.NONE
        )
        requirements = ModelInvocationRequirements(
            task_family="research",
            provider="openai",
            model_name="gpt-5",
            primary_deployment_id=routes[0] if routes else None,
            required_capabilities=frozenset({ModelCapability.STREAMING}),
            minimum_context_tokens=1,
            credential_availability=(
                ModelCredentialAvailability(
                    provider="openai",
                    modes=frozenset({ModelCredentialMode.DEPLOYMENT}),
                ),
            ),
            fallback_policy=fallback,
            budget=budget or ModelInvocationBudget(),
        )
        self.requirements = ModelInvocationRequirementsSnapshot.create(requirements)
        self.route_plan = ModelRoutePlan.create(
            routes=tuple(_route(deployment) for deployment in routes),
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


def _descriptor(deployment: str) -> ModelDeploymentDescriptor:
    return ModelDeploymentDescriptor(
        deployment_id=deployment,
        endpoint_ref="endpoint_" + "1" * 32,
        provider="openai",
        model_name="gpt-5",
        capabilities=frozenset({ModelCapability.STREAMING}),
        max_input_tokens=10_000,
        max_output_tokens=1_000,
        region="global",
        credential_modes=frozenset({ModelCredentialMode.DEPLOYMENT}),
        price_revision="price-v1",
        descriptor_revision="descriptor-v1",
    )


def _route(deployment: str) -> ModelRouteEntry:
    return ModelRouteEntry.from_descriptor(
        _descriptor(deployment), credential_mode=ModelCredentialMode.DEPLOYMENT
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
) -> ModelInvocationRuntimeBinding:
    controls = ModelReliabilityReleaseControls(
        retry_mode=FeatureMode.ENFORCE if retry else FeatureMode.OFF,
        alternate_route_mode=(FeatureMode.ENFORCE if alternate else FeatureMode.OFF),
    )
    return ModelInvocationRuntimeBinding(
        authority_adapter=authority,
        authority_input_factory=lambda digest: digest,
        journal=journal,
        route_model_resolver=resolver,
        release=controls.resolve(),
        org_id="org-1",
        subject_fingerprint=_SHA,
        trace_id="trace-1",
        failure_adapters=ProviderFailureAdapterRegistry.defaults(),
        projected_cost_microusd=0,
        projected_input_tokens=0,
        projected_output_tokens=0,
        post_response_error_observer=(
            diagnostics.append if diagnostics is not None else None
        ),
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
