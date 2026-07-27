"""Deterministic, no-I/O model routing and attempt-recovery policy."""

from __future__ import annotations

from collections.abc import Iterable

from agent_runtime.execution.model_invocation.contracts import (
    ByokPolicy,
    ModelAttemptAdmissionRequest,
    ModelAttemptDecision,
    ModelAttemptDecisionKind,
    ModelAttemptDecisionReason,
    ModelAttemptOutcome,
    ModelCredentialMode,
    ModelDeploymentDescriptor,
    ModelDeploymentHealth,
    ModelDispatchState,
    ModelFailureClass,
    ModelFailureSignal,
    ModelFallbackPolicy,
    ModelInvocationRequirements,
    ModelPrivacyFeature,
    ModelRecoveryScope,
    ModelRouteExclusion,
    ModelRouteExclusionReason,
    ModelRoutePlan,
    ModelStreamState,
    ProviderFailureObservation,
)


class ModelRoutePolicy:
    """Intersect verified requirements with a bounded descriptor catalog in ``O(D)``."""

    def plan(
        self,
        requirements: ModelInvocationRequirements,
        descriptors: Iterable[ModelDeploymentDescriptor],
    ) -> ModelRoutePlan:
        descriptor_list = tuple(descriptors)
        descriptor_ids = tuple(item.deployment_id for item in descriptor_list)
        if len(descriptor_ids) != len(set(descriptor_ids)):
            raise ValueError("deployment_id values must be unique")

        availability = {
            item.provider: item.modes for item in requirements.credential_availability
        }
        eligible: dict[tuple[int, int, int], list[ModelDeploymentDescriptor]] = {}
        exclusions: list[ModelRouteExclusion] = []
        for descriptor in descriptor_list:
            available_modes = availability.get(descriptor.provider, frozenset())
            reasons = self._exclusion_reasons(
                requirements,
                descriptor,
                available_modes=available_modes,
            )
            if reasons:
                exclusions.append(
                    ModelRouteExclusion(
                        deployment_id=descriptor.deployment_id,
                        reasons=tuple(reasons),
                    )
                )
                continue
            route_key = self._route_key(
                requirements,
                descriptor,
                available_modes=available_modes,
            )
            eligible.setdefault(route_key, []).append(descriptor)

        # Deployment catalogs can be assembled from independent sources.  Never
        # let source enumeration order decide a fallback or alter the persisted
        # route plan: rank first, then use the opaque deployment ID only as a
        # stable tie-breaker.
        ordered_eligible = [
            descriptor
            for route_key in sorted(eligible)
            for descriptor in sorted(
                eligible[route_key], key=lambda item: item.deployment_id
            )
        ]
        if (
            requirements.fallback_policy is ModelFallbackPolicy.NONE
            and ordered_eligible
        ):
            kept = ordered_eligible[:1]
            for descriptor in ordered_eligible[1:]:
                exclusions.append(
                    ModelRouteExclusion(
                        deployment_id=descriptor.deployment_id,
                        reasons=(ModelRouteExclusionReason.FALLBACK_NOT_PERMITTED,),
                    )
                )
            ordered_eligible = kept

        return ModelRoutePlan(
            deployment_ids=tuple(
                descriptor.deployment_id for descriptor in ordered_eligible
            ),
            exclusions=tuple(sorted(exclusions, key=lambda item: item.deployment_id)),
            fallback_policy=requirements.fallback_policy,
            budget=requirements.budget,
        )

    @classmethod
    def _exclusion_reasons(
        cls,
        requirements: ModelInvocationRequirements,
        descriptor: ModelDeploymentDescriptor,
        *,
        available_modes: frozenset[ModelCredentialMode],
    ) -> list[ModelRouteExclusionReason]:
        reasons: list[ModelRouteExclusionReason] = []
        if not descriptor.enabled:
            reasons.append(ModelRouteExclusionReason.DISABLED)
        if descriptor.health is ModelDeploymentHealth.UNAVAILABLE:
            reasons.append(ModelRouteExclusionReason.HEALTH_UNAVAILABLE)
        elif descriptor.health is ModelDeploymentHealth.OPEN_CIRCUIT:
            reasons.append(ModelRouteExclusionReason.OPEN_CIRCUIT)

        exact_model = (
            descriptor.provider == requirements.provider
            and descriptor.model_name == requirements.model_name
        )
        if not exact_model:
            if (
                requirements.fallback_policy
                is not ModelFallbackPolicy.QUALIFIED_EQUIVALENT
            ):
                if descriptor.provider != requirements.provider:
                    reasons.append(ModelRouteExclusionReason.PROVIDER_MISMATCH)
                if descriptor.model_name != requirements.model_name:
                    reasons.append(ModelRouteExclusionReason.MODEL_MISMATCH)
            elif requirements.task_family not in descriptor.qualified_task_families:
                reasons.append(ModelRouteExclusionReason.EQUIVALENCE_NOT_QUALIFIED)

        if (
            requirements.primary_deployment_id is not None
            and descriptor.deployment_id != requirements.primary_deployment_id
            and requirements.fallback_policy is ModelFallbackPolicy.NONE
        ):
            reasons.append(ModelRouteExclusionReason.FALLBACK_NOT_PERMITTED)

        if not requirements.required_capabilities.issubset(descriptor.capabilities):
            reasons.append(ModelRouteExclusionReason.CAPABILITY_MISMATCH)
        if descriptor.max_input_tokens < requirements.minimum_context_tokens:
            reasons.append(ModelRouteExclusionReason.CONTEXT_TOO_SMALL)
        if (
            requirements.region is not None
            and requirements.region not in descriptor.regions
        ):
            reasons.append(ModelRouteExclusionReason.REGION_MISMATCH)

        available_modes = descriptor.credential_modes & available_modes
        if not available_modes:
            reasons.append(ModelRouteExclusionReason.CREDENTIAL_UNAVAILABLE)
        if (
            requirements.byok_policy is ByokPolicy.REQUIRED
            and ModelCredentialMode.BYOK not in available_modes
        ):
            reasons.append(ModelRouteExclusionReason.BYOK_REQUIRED)
        if requirements.byok_policy is ByokPolicy.DISALLOWED and available_modes == {
            ModelCredentialMode.BYOK
        }:
            reasons.append(ModelRouteExclusionReason.BYOK_DISALLOWED)
        if (
            requirements.training_opt_out_required
            and ModelPrivacyFeature.TRAINING_OPT_OUT not in descriptor.privacy_features
        ):
            reasons.append(ModelRouteExclusionReason.PRIVACY_INCOMPATIBLE)
        return reasons

    @staticmethod
    def _route_key(
        requirements: ModelInvocationRequirements,
        descriptor: ModelDeploymentDescriptor,
        *,
        available_modes: frozenset[ModelCredentialMode],
    ) -> tuple[int, int, int]:
        if descriptor.deployment_id == requirements.primary_deployment_id:
            tier = 0
        elif (
            descriptor.provider == requirements.provider
            and descriptor.model_name == requirements.model_name
        ):
            tier = 1
        else:
            tier = 2
        credential_rank = (
            0
            if requirements.byok_policy is ByokPolicy.PREFERRED
            and ModelCredentialMode.BYOK in available_modes
            else 1
        )
        health_rank = 0 if descriptor.health is ModelDeploymentHealth.AVAILABLE else 1
        return (tier, credential_rank, health_rank)


class ProviderFailureClassifier:
    """Classify adapter-attested facts without provider exception heuristics."""

    _DIRECT_CLASSES = {
        ModelFailureSignal.REQUEST_INVALID: ModelFailureClass.REQUEST_INVALID,
        ModelFailureSignal.AUTH_INVALID: ModelFailureClass.AUTH_INVALID,
        ModelFailureSignal.REGION_UNAVAILABLE: ModelFailureClass.REGION_UNAVAILABLE,
        ModelFailureSignal.POLICY_INCOMPATIBLE: ModelFailureClass.POLICY_INCOMPATIBLE,
        ModelFailureSignal.CONTEXT_EXCEEDED: ModelFailureClass.CONTEXT_EXCEEDED,
        ModelFailureSignal.CANCELLED: ModelFailureClass.CANCELLED,
        ModelFailureSignal.DEADLINE_EXCEEDED: ModelFailureClass.DEADLINE_EXCEEDED,
    }

    def classify(self, observation: ProviderFailureObservation) -> ModelFailureClass:
        direct = self._DIRECT_CLASSES.get(observation.signal)
        if direct is not None:
            return direct
        if observation.signal is ModelFailureSignal.STREAM_INTERRUPTED:
            if observation.stream_state is ModelStreamState.VISIBLE_OUTPUT:
                return ModelFailureClass.STREAM_INTERRUPTED_AFTER_CONTENT
            return ModelFailureClass.STREAM_INTERRUPTED_BEFORE_CONTENT
        if observation.signal in (
            ModelFailureSignal.RATE_LIMITED,
            ModelFailureSignal.OVERLOADED,
        ):
            return ModelFailureClass.PROVIDER_OVERLOADED
        if observation.signal is ModelFailureSignal.CONNECTIVITY:
            if (
                observation.dispatch_state
                in (
                    ModelDispatchState.BEFORE_DISPATCH,
                    ModelDispatchState.NOT_ACCEPTED,
                )
                and observation.stream_state is ModelStreamState.NOT_STARTED
            ):
                return ModelFailureClass.PRE_DISPATCH_TRANSIENT
            return ModelFailureClass.AMBIGUOUS_PROVIDER_STATE
        return ModelFailureClass.AMBIGUOUS_PROVIDER_STATE


class ModelAttemptAdmissionPolicy:
    """Admit bounded provider attempts; never authorize whole-run replay."""

    _SAFE_RETRY_CLASSES = frozenset(
        {
            ModelFailureClass.PRE_DISPATCH_TRANSIENT,
            ModelFailureClass.PROVIDER_OVERLOADED,
            ModelFailureClass.STREAM_INTERRUPTED_BEFORE_CONTENT,
        }
    )

    def decide(
        self,
        request: ModelAttemptAdmissionRequest,
    ) -> ModelAttemptDecision:
        denial = self._preflight_denial(request)
        if denial is not None:
            return self._deny(denial)

        ordinal = len(request.prior_attempts) + 1
        if not request.prior_attempts:
            return self._admit(
                deployment_id=request.route_plan.deployment_ids[0],
                ordinal=ordinal,
                reason=ModelAttemptDecisionReason.FIRST_ATTEMPT,
            )

        last_attempt = request.prior_attempts[-1]
        retry_denial = self._retry_denial(last_attempt)
        if retry_denial is not None:
            return self._deny(retry_denial)

        same_route_count = sum(
            attempt.deployment_id == last_attempt.deployment_id
            for attempt in request.prior_attempts
        )
        if same_route_count < request.route_plan.budget.max_same_deployment_attempts:
            return self._admit(
                deployment_id=last_attempt.deployment_id,
                ordinal=ordinal,
                reason=ModelAttemptDecisionReason.SAFE_SAME_DEPLOYMENT_RETRY,
            )

        attempted_deployments = {
            attempt.deployment_id for attempt in request.prior_attempts
        }
        alternate = next(
            (
                deployment_id
                for deployment_id in request.route_plan.deployment_ids
                if deployment_id not in attempted_deployments
            ),
            None,
        )
        if alternate is not None:
            return self._admit(
                deployment_id=alternate,
                ordinal=ordinal,
                reason=ModelAttemptDecisionReason.SAFE_ALTERNATE_ROUTE,
            )
        if len(request.route_plan.deployment_ids) == 1:
            return self._deny(ModelAttemptDecisionReason.SAME_DEPLOYMENT_LIMIT_REACHED)
        return self._deny(ModelAttemptDecisionReason.ROUTE_SET_EXHAUSTED)

    @classmethod
    def _preflight_denial(
        cls,
        request: ModelAttemptAdmissionRequest,
    ) -> ModelAttemptDecisionReason | None:
        if request.recovery_scope is ModelRecoveryScope.WHOLE_RUN:
            return ModelAttemptDecisionReason.WHOLE_RUN_REPLAY_FORBIDDEN
        if request.external_effect_observed and request.prior_attempts:
            return ModelAttemptDecisionReason.EXTERNAL_EFFECT_OBSERVED
        if not request.route_plan.deployment_ids:
            return ModelAttemptDecisionReason.NO_ELIGIBLE_ROUTE
        if any(
            attempt.deployment_id not in request.route_plan.deployment_ids
            for attempt in request.prior_attempts
        ):
            return ModelAttemptDecisionReason.PRIOR_ROUTE_MISMATCH

        budget = request.route_plan.budget
        if len(request.prior_attempts) >= budget.max_attempts:
            return ModelAttemptDecisionReason.ATTEMPT_LIMIT_REACHED
        if budget.deadline_at is not None and request.now >= budget.deadline_at:
            return ModelAttemptDecisionReason.DEADLINE_EXPIRED

        total_cost = sum(attempt.cost_microusd for attempt in request.prior_attempts)
        if budget.max_cost_microusd is not None:
            if request.projected_cost_microusd is None:
                return ModelAttemptDecisionReason.PROJECTED_COST_UNKNOWN
            if total_cost + request.projected_cost_microusd > budget.max_cost_microusd:
                return ModelAttemptDecisionReason.COST_BUDGET_EXCEEDED

        total_input = sum(attempt.input_tokens for attempt in request.prior_attempts)
        total_output = sum(attempt.output_tokens for attempt in request.prior_attempts)
        if budget.max_input_tokens is not None:
            if request.projected_input_tokens is None:
                return ModelAttemptDecisionReason.PROJECTED_TOKEN_USAGE_UNKNOWN
            if total_input + request.projected_input_tokens > budget.max_input_tokens:
                return ModelAttemptDecisionReason.INPUT_TOKEN_BUDGET_EXCEEDED
        if budget.max_output_tokens is not None:
            if request.projected_output_tokens is None:
                return ModelAttemptDecisionReason.PROJECTED_TOKEN_USAGE_UNKNOWN
            if (
                total_output + request.projected_output_tokens
                > budget.max_output_tokens
            ):
                return ModelAttemptDecisionReason.OUTPUT_TOKEN_BUDGET_EXCEEDED
        return None

    @classmethod
    def _retry_denial(
        cls,
        attempt: ModelAttemptOutcome,
    ) -> ModelAttemptDecisionReason | None:
        if attempt.stream_state is ModelStreamState.VISIBLE_OUTPUT:
            return ModelAttemptDecisionReason.VISIBLE_OUTPUT_ALREADY_EMITTED
        if attempt.failure_class is None:
            return ModelAttemptDecisionReason.PRIOR_ATTEMPT_NOT_FAILED
        if attempt.failure_class is ModelFailureClass.AMBIGUOUS_PROVIDER_STATE:
            return ModelAttemptDecisionReason.AMBIGUOUS_PROVIDER_STATE
        if attempt.failure_class is ModelFailureClass.CONTEXT_EXCEEDED:
            return ModelAttemptDecisionReason.CONTEXT_REPLAN_REQUIRED
        if attempt.failure_class not in cls._SAFE_RETRY_CLASSES:
            return ModelAttemptDecisionReason.FAILURE_NOT_RETRYABLE
        return None

    @staticmethod
    def _admit(
        *,
        deployment_id: str,
        ordinal: int,
        reason: ModelAttemptDecisionReason,
    ) -> ModelAttemptDecision:
        return ModelAttemptDecision(
            kind=ModelAttemptDecisionKind.ADMIT,
            deployment_id=deployment_id,
            ordinal=ordinal,
            reason=reason,
        )

    @staticmethod
    def _deny(reason: ModelAttemptDecisionReason) -> ModelAttemptDecision:
        return ModelAttemptDecision(
            kind=ModelAttemptDecisionKind.DENY,
            reason=reason,
        )


__all__ = (
    "ModelAttemptAdmissionPolicy",
    "ModelRoutePolicy",
    "ProviderFailureClassifier",
)
