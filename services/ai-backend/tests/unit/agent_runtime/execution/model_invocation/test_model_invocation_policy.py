from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from agent_runtime.execution.contracts import ModelConfig
from agent_runtime.execution.model_invocation import (
    ByokPolicy,
    ModelAttemptAdmissionPolicy,
    ModelAttemptAdmissionRequest,
    ModelAttemptDecisionKind,
    ModelAttemptDecisionReason,
    ModelAttemptOutcome,
    ModelCapability,
    ModelCredentialAvailability,
    ModelCredentialMode,
    ModelDeploymentDescriptor,
    ModelDeploymentHealth,
    ModelDispatchState,
    ModelFailureClass,
    ModelFailureSignal,
    ModelFallbackPolicy,
    ModelInvocationBudget,
    ModelInvocationRequirements,
    ModelPrivacyFeature,
    ModelRecoveryScope,
    ModelRouteExclusionReason,
    ModelRoutePlan,
    ModelRoutePolicy,
    ModelStreamState,
    ProviderFailureClassifier,
    ProviderFailureObservation,
)

NOW = datetime(2026, 7, 27, tzinfo=timezone.utc)


def _descriptor(
    deployment_id: str,
    *,
    provider: str = "openai",
    model_name: str = "gpt-5",
    capabilities: frozenset[ModelCapability] = frozenset(
        {ModelCapability.STREAMING, ModelCapability.TOOLS}
    ),
    max_input_tokens: int = 200_000,
    regions: frozenset[str] = frozenset({"us-east"}),
    credential_modes: frozenset[ModelCredentialMode] = frozenset(
        {ModelCredentialMode.DEPLOYMENT}
    ),
    privacy_features: frozenset[ModelPrivacyFeature] = frozenset(
        {ModelPrivacyFeature.TRAINING_OPT_OUT}
    ),
    qualified_task_families: frozenset[str] = frozenset(),
    health: ModelDeploymentHealth = ModelDeploymentHealth.AVAILABLE,
    enabled: bool = True,
) -> ModelDeploymentDescriptor:
    return ModelDeploymentDescriptor(
        deployment_id=deployment_id,
        provider=provider,
        model_name=model_name,
        capabilities=capabilities,
        max_input_tokens=max_input_tokens,
        regions=regions,
        credential_modes=credential_modes,
        privacy_features=privacy_features,
        qualified_task_families=qualified_task_families,
        health=health,
        enabled=enabled,
        descriptor_revision="catalog-7",
    )


def _requirements(
    *,
    primary_deployment_id: str | None = "primary",
    fallback_policy: ModelFallbackPolicy = ModelFallbackPolicy.NONE,
    available_credential_modes: frozenset[ModelCredentialMode] = frozenset(
        {ModelCredentialMode.DEPLOYMENT}
    ),
    credential_availability: tuple[ModelCredentialAvailability, ...] | None = None,
    byok_policy: ByokPolicy = ByokPolicy.ALLOWED,
    budget: ModelInvocationBudget | None = None,
) -> ModelInvocationRequirements:
    return ModelInvocationRequirements(
        task_family="research",
        provider="openai",
        model_name="gpt-5",
        primary_deployment_id=primary_deployment_id,
        required_capabilities=frozenset(
            {ModelCapability.STREAMING, ModelCapability.TOOLS}
        ),
        minimum_context_tokens=100_000,
        region="us-east",
        credential_availability=credential_availability
        or (
            ModelCredentialAvailability(
                provider="openai",
                modes=available_credential_modes,
            ),
        ),
        byok_policy=byok_policy,
        training_opt_out_required=True,
        fallback_policy=fallback_policy,
        budget=budget or ModelInvocationBudget(),
    )


def _plan(
    *,
    deployments: tuple[str, ...] = ("primary",),
    budget: ModelInvocationBudget | None = None,
) -> ModelRoutePlan:
    return ModelRoutePlan(
        deployment_ids=deployments,
        fallback_policy=(
            ModelFallbackPolicy.SAME_MODEL
            if len(deployments) > 1
            else ModelFallbackPolicy.NONE
        ),
        budget=budget or ModelInvocationBudget(),
    )


def _attempt(
    *,
    ordinal: int = 1,
    deployment_id: str = "primary",
    failure_class: ModelFailureClass | None = (
        ModelFailureClass.PRE_DISPATCH_TRANSIENT
    ),
    stream_state: ModelStreamState = ModelStreamState.NOT_STARTED,
    cost_microusd: int = 0,
    input_tokens: int = 0,
    output_tokens: int = 0,
) -> ModelAttemptOutcome:
    return ModelAttemptOutcome(
        attempt_id=f"attempt-{ordinal}",
        ordinal=ordinal,
        deployment_id=deployment_id,
        failure_class=failure_class,
        stream_state=stream_state,
        cost_microusd=cost_microusd,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )


def test_existing_model_config_adapts_to_closed_requirements() -> None:
    config = ModelConfig(
        provider="openai",
        model_name="gpt-5",
        max_input_tokens=120_000,
        timeout_seconds=90,
        temperature=0,
        supports_streaming=True,
    )

    requirements = ModelInvocationRequirements.from_model_config(
        config,
        task_family="Research",
        available_credential_modes=frozenset({ModelCredentialMode.BYOK}),
        required_capabilities=frozenset({ModelCapability.TOOLS}),
        byok_policy=ByokPolicy.REQUIRED,
    )

    assert requirements.provider == "openai"
    assert requirements.task_family == "research"
    assert requirements.minimum_context_tokens == 120_000
    assert requirements.required_capabilities == {
        ModelCapability.STREAMING,
        ModelCapability.TOOLS,
    }
    assert requirements.fallback_policy is ModelFallbackPolicy.NONE


def test_route_plan_is_deterministic_and_exact_only_keeps_primary() -> None:
    descriptors = (
        _descriptor("alternate"),
        _descriptor("primary"),
        _descriptor(
            "equivalent",
            provider="anthropic",
            model_name="claude-sonnet",
            qualified_task_families=frozenset({"research"}),
        ),
    )

    first = ModelRoutePolicy().plan(_requirements(), descriptors)
    second = ModelRoutePolicy().plan(_requirements(), descriptors)

    assert first == second
    assert first.deployment_ids == ("primary",)
    exclusions = {item.deployment_id: item.reasons for item in first.exclusions}
    assert exclusions["alternate"] == (
        ModelRouteExclusionReason.FALLBACK_NOT_PERMITTED,
    )
    assert exclusions["equivalent"] == (
        ModelRouteExclusionReason.PROVIDER_MISMATCH,
        ModelRouteExclusionReason.MODEL_MISMATCH,
        ModelRouteExclusionReason.FALLBACK_NOT_PERMITTED,
        ModelRouteExclusionReason.CREDENTIAL_UNAVAILABLE,
    )


def test_route_plan_is_invariant_to_descriptor_enumeration_order() -> None:
    descriptors = (
        _descriptor("same-model-b"),
        _descriptor("primary"),
        _descriptor("same-model-a"),
    )
    requirements = _requirements(
        fallback_policy=ModelFallbackPolicy.SAME_MODEL,
    )

    first = ModelRoutePolicy().plan(requirements, descriptors)
    second = ModelRoutePolicy().plan(requirements, tuple(reversed(descriptors)))

    assert first == second
    assert first.deployment_ids == ("primary", "same-model-a", "same-model-b")


def test_route_plan_intersects_capability_region_credential_privacy_and_health() -> (
    None
):
    descriptors = (
        _descriptor(
            "unsafe",
            capabilities=frozenset({ModelCapability.STREAMING}),
            max_input_tokens=50_000,
            regions=frozenset({"eu-west"}),
            credential_modes=frozenset({ModelCredentialMode.DEPLOYMENT}),
            privacy_features=frozenset(),
            health=ModelDeploymentHealth.OPEN_CIRCUIT,
        ),
    )
    requirements = _requirements(
        primary_deployment_id=None,
        available_credential_modes=frozenset({ModelCredentialMode.BYOK}),
        byok_policy=ByokPolicy.REQUIRED,
    )

    plan = ModelRoutePolicy().plan(requirements, descriptors)

    assert plan.deployment_ids == ()
    assert plan.exclusions[0].reasons == (
        ModelRouteExclusionReason.OPEN_CIRCUIT,
        ModelRouteExclusionReason.CAPABILITY_MISMATCH,
        ModelRouteExclusionReason.CONTEXT_TOO_SMALL,
        ModelRouteExclusionReason.REGION_MISMATCH,
        ModelRouteExclusionReason.CREDENTIAL_UNAVAILABLE,
        ModelRouteExclusionReason.BYOK_REQUIRED,
        ModelRouteExclusionReason.PRIVACY_INCOMPATIBLE,
    )


def test_qualified_equivalent_routes_only_for_attested_task_family() -> None:
    descriptors = (
        _descriptor("primary"),
        _descriptor(
            "qualified",
            provider="anthropic",
            model_name="claude-sonnet",
            qualified_task_families=frozenset({"research"}),
        ),
        _descriptor(
            "unqualified",
            provider="gemini",
            model_name="gemini-pro",
            qualified_task_families=frozenset({"coding"}),
        ),
    )
    plan = ModelRoutePolicy().plan(
        _requirements(
            fallback_policy=ModelFallbackPolicy.QUALIFIED_EQUIVALENT,
            credential_availability=(
                ModelCredentialAvailability(
                    provider="openai",
                    modes=frozenset({ModelCredentialMode.DEPLOYMENT}),
                ),
                ModelCredentialAvailability(
                    provider="anthropic",
                    modes=frozenset({ModelCredentialMode.DEPLOYMENT}),
                ),
                ModelCredentialAvailability(
                    provider="gemini",
                    modes=frozenset({ModelCredentialMode.DEPLOYMENT}),
                ),
            ),
        ),
        descriptors,
    )

    assert plan.deployment_ids == ("primary", "qualified")
    assert plan.exclusions[0].deployment_id == "unqualified"
    assert plan.exclusions[0].reasons == (
        ModelRouteExclusionReason.EQUIVALENCE_NOT_QUALIFIED,
    )


def test_byok_availability_is_provider_scoped_across_equivalent_routes() -> None:
    requirements = _requirements(
        fallback_policy=ModelFallbackPolicy.QUALIFIED_EQUIVALENT,
        available_credential_modes=frozenset({ModelCredentialMode.BYOK}),
        byok_policy=ByokPolicy.REQUIRED,
    )
    descriptors = (
        _descriptor(
            "primary",
            credential_modes=frozenset({ModelCredentialMode.BYOK}),
        ),
        _descriptor(
            "other-provider",
            provider="anthropic",
            model_name="claude-sonnet",
            credential_modes=frozenset({ModelCredentialMode.BYOK}),
            qualified_task_families=frozenset({"research"}),
        ),
    )

    plan = ModelRoutePolicy().plan(requirements, descriptors)

    assert plan.deployment_ids == ("primary",)
    assert plan.exclusions[0].deployment_id == "other-provider"
    assert plan.exclusions[0].reasons == (
        ModelRouteExclusionReason.CREDENTIAL_UNAVAILABLE,
        ModelRouteExclusionReason.BYOK_REQUIRED,
    )


@pytest.mark.parametrize(
    ("observation", "expected"),
    [
        (
            ProviderFailureObservation(
                signal=ModelFailureSignal.CONNECTIVITY,
                dispatch_state=ModelDispatchState.BEFORE_DISPATCH,
            ),
            ModelFailureClass.PRE_DISPATCH_TRANSIENT,
        ),
        (
            ProviderFailureObservation(
                signal=ModelFailureSignal.CONNECTIVITY,
                dispatch_state=ModelDispatchState.UNKNOWN,
            ),
            ModelFailureClass.AMBIGUOUS_PROVIDER_STATE,
        ),
        (
            ProviderFailureObservation(
                signal=ModelFailureSignal.STREAM_INTERRUPTED,
                dispatch_state=ModelDispatchState.ACCEPTED,
                stream_state=ModelStreamState.STARTED_NO_VISIBLE_OUTPUT,
            ),
            ModelFailureClass.STREAM_INTERRUPTED_BEFORE_CONTENT,
        ),
        (
            ProviderFailureObservation(
                signal=ModelFailureSignal.STREAM_INTERRUPTED,
                dispatch_state=ModelDispatchState.ACCEPTED,
                stream_state=ModelStreamState.VISIBLE_OUTPUT,
            ),
            ModelFailureClass.STREAM_INTERRUPTED_AFTER_CONTENT,
        ),
        (
            ProviderFailureObservation(signal=ModelFailureSignal.UNKNOWN),
            ModelFailureClass.AMBIGUOUS_PROVIDER_STATE,
        ),
    ],
)
def test_failure_classifier_is_conservative(
    observation: ProviderFailureObservation,
    expected: ModelFailureClass,
) -> None:
    assert ProviderFailureClassifier().classify(observation) is expected


def test_first_attempt_and_safe_same_route_retry_are_bounded() -> None:
    budget = ModelInvocationBudget(
        max_attempts=2,
        max_same_deployment_attempts=2,
        deadline_at=NOW + timedelta(seconds=30),
    )
    policy = ModelAttemptAdmissionPolicy()

    first = policy.decide(
        ModelAttemptAdmissionRequest(route_plan=_plan(budget=budget), now=NOW)
    )
    retry = policy.decide(
        ModelAttemptAdmissionRequest(
            route_plan=_plan(budget=budget),
            now=NOW,
            prior_attempts=(_attempt(),),
        )
    )

    assert first.kind is ModelAttemptDecisionKind.ADMIT
    assert first.reason is ModelAttemptDecisionReason.FIRST_ATTEMPT
    assert (first.deployment_id, first.ordinal) == ("primary", 1)
    assert retry.kind is ModelAttemptDecisionKind.ADMIT
    assert retry.reason is ModelAttemptDecisionReason.SAFE_SAME_DEPLOYMENT_RETRY
    assert (retry.deployment_id, retry.ordinal) == ("primary", 2)


def test_safe_failure_uses_next_route_after_same_deployment_limit() -> None:
    plan = _plan(
        deployments=("primary", "alternate"),
        budget=ModelInvocationBudget(
            max_attempts=2,
            max_same_deployment_attempts=1,
        ),
    )

    decision = ModelAttemptAdmissionPolicy().decide(
        ModelAttemptAdmissionRequest(
            route_plan=plan,
            now=NOW,
            prior_attempts=(_attempt(),),
        )
    )

    assert decision.kind is ModelAttemptDecisionKind.ADMIT
    assert decision.reason is ModelAttemptDecisionReason.SAFE_ALTERNATE_ROUTE
    assert decision.deployment_id == "alternate"


@pytest.mark.parametrize(
    ("request_updates", "attempt", "reason"),
    [
        (
            {"recovery_scope": ModelRecoveryScope.WHOLE_RUN},
            None,
            ModelAttemptDecisionReason.WHOLE_RUN_REPLAY_FORBIDDEN,
        ),
        (
            {"external_effect_observed": True},
            _attempt(),
            ModelAttemptDecisionReason.EXTERNAL_EFFECT_OBSERVED,
        ),
        (
            {},
            _attempt(
                failure_class=ModelFailureClass.STREAM_INTERRUPTED_AFTER_CONTENT,
                stream_state=ModelStreamState.VISIBLE_OUTPUT,
            ),
            ModelAttemptDecisionReason.VISIBLE_OUTPUT_ALREADY_EMITTED,
        ),
        (
            {},
            _attempt(failure_class=ModelFailureClass.AMBIGUOUS_PROVIDER_STATE),
            ModelAttemptDecisionReason.AMBIGUOUS_PROVIDER_STATE,
        ),
        (
            {},
            _attempt(failure_class=ModelFailureClass.CONTEXT_EXCEEDED),
            ModelAttemptDecisionReason.CONTEXT_REPLAN_REQUIRED,
        ),
        (
            {},
            _attempt(failure_class=ModelFailureClass.AUTH_INVALID),
            ModelAttemptDecisionReason.FAILURE_NOT_RETRYABLE,
        ),
        (
            {},
            _attempt(deployment_id="not-in-route"),
            ModelAttemptDecisionReason.PRIOR_ROUTE_MISMATCH,
        ),
    ],
)
def test_unsafe_recovery_is_never_admitted(
    request_updates: dict[str, object],
    attempt: ModelAttemptOutcome | None,
    reason: ModelAttemptDecisionReason,
) -> None:
    request = ModelAttemptAdmissionRequest(
        route_plan=_plan(
            budget=ModelInvocationBudget(
                max_attempts=2,
                max_same_deployment_attempts=2,
            )
        ),
        now=NOW,
        prior_attempts=() if attempt is None else (attempt,),
        **request_updates,
    )

    decision = ModelAttemptAdmissionPolicy().decide(request)

    assert decision.kind is ModelAttemptDecisionKind.DENY
    assert decision.reason is reason
    assert decision.deployment_id is None


@pytest.mark.parametrize(
    ("budget", "request_updates", "reason"),
    [
        (
            ModelInvocationBudget(deadline_at=NOW),
            {},
            ModelAttemptDecisionReason.DEADLINE_EXPIRED,
        ),
        (
            ModelInvocationBudget(max_cost_microusd=100),
            {},
            ModelAttemptDecisionReason.PROJECTED_COST_UNKNOWN,
        ),
        (
            ModelInvocationBudget(max_cost_microusd=100),
            {"projected_cost_microusd": 101},
            ModelAttemptDecisionReason.COST_BUDGET_EXCEEDED,
        ),
        (
            ModelInvocationBudget(max_input_tokens=100),
            {"projected_input_tokens": 101},
            ModelAttemptDecisionReason.INPUT_TOKEN_BUDGET_EXCEEDED,
        ),
        (
            ModelInvocationBudget(max_output_tokens=100),
            {"projected_output_tokens": 101},
            ModelAttemptDecisionReason.OUTPUT_TOKEN_BUDGET_EXCEEDED,
        ),
    ],
)
def test_attempt_admission_fails_closed_on_deadline_and_unknown_or_exceeded_budget(
    budget: ModelInvocationBudget,
    request_updates: dict[str, object],
    reason: ModelAttemptDecisionReason,
) -> None:
    decision = ModelAttemptAdmissionPolicy().decide(
        ModelAttemptAdmissionRequest(
            route_plan=_plan(budget=budget),
            now=NOW,
            **request_updates,
        )
    )

    assert decision.kind is ModelAttemptDecisionKind.DENY
    assert decision.reason is reason


def test_invalid_budget_and_stream_facts_fail_validation() -> None:
    with pytest.raises(ValidationError, match="cannot exceed max_attempts"):
        ModelInvocationBudget(
            max_attempts=1,
            max_same_deployment_attempts=2,
        )
    with pytest.raises(ValidationError, match="stream progress"):
        ProviderFailureObservation(
            signal=ModelFailureSignal.STREAM_INTERRUPTED,
            dispatch_state=ModelDispatchState.NOT_ACCEPTED,
            stream_state=ModelStreamState.VISIBLE_OUTPUT,
        )
