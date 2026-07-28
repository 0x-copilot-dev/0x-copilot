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
    ModelDeploymentCatalog,
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
    ModelRouteEntry,
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
        *,
        policy_revision: str = "model-route-policy.v2",
    ) -> ModelRoutePlan:
        descriptor_list = tuple(
            sorted(descriptors, key=lambda item: item.deployment_id)
        )
        descriptor_ids = tuple(item.deployment_id for item in descriptor_list)
        if len(descriptor_ids) != len(set(descriptor_ids)):
            raise ValueError("deployment_id values must be unique")
        return self._plan_ordered(
            requirements,
            descriptor_list,
            policy_revision=policy_revision,
        )

    def plan_catalog(
        self,
        requirements: ModelInvocationRequirements,
        catalog: ModelDeploymentCatalog,
        *,
        policy_revision: str = "model-route-policy.v2",
    ) -> ModelRoutePlan:
        """Plan in one ``O(D)`` pass over an already canonical catalog.

        ``ModelDeploymentCatalog`` validates deployment-ID ordering and
        uniqueness once at the authority boundary. Route planning therefore
        avoids another descriptor sort while retaining the legacy ``plan``
        entry point for unordered callers.
        """

        return self._plan_ordered(
            requirements,
            catalog.descriptors,
            policy_revision=policy_revision,
        )

    def _plan_ordered(
        self,
        requirements: ModelInvocationRequirements,
        descriptor_list: tuple[ModelDeploymentDescriptor, ...],
        *,
        policy_revision: str,
    ) -> ModelRoutePlan:
        availability = {
            item.provider: item.modes for item in requirements.credential_availability
        }
        eligible: dict[
            tuple[int, int, int],
            list[tuple[ModelDeploymentDescriptor, ModelCredentialMode]],
        ] = {}
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
            selected_credential_mode = self._select_credential_mode(
                requirements,
                descriptor.credential_modes & available_modes,
            )
            route_key = self._route_key(
                requirements,
                descriptor,
                selected_credential_mode=selected_credential_mode,
            )
            eligible.setdefault(route_key, []).append(
                (descriptor, selected_credential_mode)
            )

        # ``descriptor_list`` is canonical deployment-ID order. The finite
        # route-key set is constant-sized, so bucket concatenation remains O(D)
        # and deployment IDs are already a stable tie-breaker inside each key.
        ordered_eligible = [
            candidate
            for route_key in sorted(eligible)
            for candidate in eligible[route_key]
        ]
        if (
            requirements.fallback_policy is ModelFallbackPolicy.NONE
            and ordered_eligible
        ):
            kept = ordered_eligible[:1]
            for descriptor, _credential_mode in ordered_eligible[1:]:
                exclusions.append(
                    ModelRouteExclusion(
                        deployment_id=descriptor.deployment_id,
                        reasons=(ModelRouteExclusionReason.FALLBACK_NOT_PERMITTED,),
                    )
                )
            ordered_eligible = kept

        return ModelRoutePlan.create(
            routes=tuple(
                ModelRouteEntry.from_descriptor(
                    descriptor,
                    credential_mode=credential_mode,
                )
                for descriptor, credential_mode in ordered_eligible
            ),
            exclusions=tuple(exclusions),
            fallback_policy=requirements.fallback_policy,
            budget=requirements.budget,
            policy_revision=policy_revision,
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
        if requirements.region is not None and requirements.region != descriptor.region:
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
        selected_credential_mode: ModelCredentialMode,
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
            and selected_credential_mode is ModelCredentialMode.BYOK
            else 1
        )
        health_rank = 0 if descriptor.health is ModelDeploymentHealth.AVAILABLE else 1
        return (tier, credential_rank, health_rank)

    @staticmethod
    def _select_credential_mode(
        requirements: ModelInvocationRequirements,
        available_modes: frozenset[ModelCredentialMode],
    ) -> ModelCredentialMode:
        if (
            requirements.byok_policy
            in {
                ByokPolicy.REQUIRED,
                ByokPolicy.PREFERRED,
                ByokPolicy.ALLOWED,
            }
            and ModelCredentialMode.BYOK in available_modes
        ):
            return ModelCredentialMode.BYOK
        for mode in (
            ModelCredentialMode.DEPLOYMENT,
            ModelCredentialMode.KEYLESS,
        ):
            if mode in available_modes:
                return mode
        # Eligibility rejects an empty set and BYOK-only under DISALLOWED.
        raise ValueError("no credential mode survived route eligibility")


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

    def decide_cache_fallback(
        self,
        request: ModelAttemptAdmissionRequest,
    ) -> ModelAttemptDecision:
        """Admit one same-route undecorated retry from an external typed signal."""

        denial = self._preflight_denial(request)
        if denial is not None:
            return self._deny(denial)
        if not request.prior_attempts:
            return self._deny(ModelAttemptDecisionReason.PRIOR_ATTEMPT_NOT_FAILED)
        last_attempt = request.prior_attempts[-1]
        if last_attempt.stream_state is ModelStreamState.VISIBLE_OUTPUT:
            return self._deny(ModelAttemptDecisionReason.VISIBLE_OUTPUT_ALREADY_EMITTED)
        if last_attempt.failure_class is not ModelFailureClass.REQUEST_INVALID:
            return self._deny(ModelAttemptDecisionReason.FAILURE_NOT_RETRYABLE)
        same_route_count = sum(
            attempt.deployment_id == last_attempt.deployment_id
            for attempt in request.prior_attempts
        )
        if same_route_count >= request.route_plan.budget.max_same_deployment_attempts:
            return self._deny(ModelAttemptDecisionReason.SAME_DEPLOYMENT_LIMIT_REACHED)
        return self._admit(
            deployment_id=last_attempt.deployment_id,
            ordinal=len(request.prior_attempts) + 1,
            reason=ModelAttemptDecisionReason.SAFE_CACHE_UNDECORATED_RETRY,
        )

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
