"""Pure domain contracts for model-route and attempt-recovery decisions.

These contracts deliberately contain no provider SDK objects, credentials, prompt
content, or persistence concerns.  Provider adapters translate their local errors
into :class:`ProviderFailureObservation`; the deterministic policy layer can then
reason about retry safety without inspecting exception strings.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
import hashlib
import json
from typing import Self

from pydantic import (
    Field,
    NonNegativeInt,
    PositiveInt,
    field_validator,
    model_validator,
)

from agent_runtime.execution.contracts import ModelConfig, RuntimeContract
from agent_runtime.execution.call_identity import RuntimeModelCallIdentity
from agent_runtime.surfaces_v2.canonical_json import canonical_json_sha256
from agent_runtime.validation import ValueNormalizer

_SHA256_REF_PATTERN = r"^sha256:[0-9a-f]{64}$"
_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_MAX_DEPLOYMENTS = 512


class ModelCapability(StrEnum):
    """Closed capability vocabulary used during route eligibility."""

    STREAMING = "streaming"
    TOOLS = "tools"
    STRUCTURED_OUTPUT = "structured_output"
    REASONING = "reasoning"
    VISION = "vision"
    AUDIO = "audio"


class ModelCredentialMode(StrEnum):
    """Credential source a deployment can use."""

    DEPLOYMENT = "deployment"
    BYOK = "byok"
    KEYLESS = "keyless"


class ByokPolicy(StrEnum):
    """Whether a route must, may, or must not use a user-owned key."""

    REQUIRED = "required"
    PREFERRED = "preferred"
    ALLOWED = "allowed"
    DISALLOWED = "disallowed"


class ModelPrivacyFeature(StrEnum):
    """Provider/deployment privacy guarantees relevant to routing."""

    TRAINING_OPT_OUT = "training_opt_out"


class ModelDeploymentHealth(StrEnum):
    """Bounded health facts consumed by the pure route planner."""

    AVAILABLE = "available"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"
    OPEN_CIRCUIT = "open_circuit"


class ModelFallbackPolicy(StrEnum):
    """Maximum route expansion authorized by the product contract."""

    NONE = "none"
    SAME_MODEL = "same_model"
    QUALIFIED_EQUIVALENT = "qualified_equivalent"


class ModelRouteExclusionReason(StrEnum):
    """Stable, content-free reason codes for route exclusion."""

    DISABLED = "disabled"
    HEALTH_UNAVAILABLE = "health_unavailable"
    OPEN_CIRCUIT = "open_circuit"
    PROVIDER_MISMATCH = "provider_mismatch"
    MODEL_MISMATCH = "model_mismatch"
    FALLBACK_NOT_PERMITTED = "fallback_not_permitted"
    EQUIVALENCE_NOT_QUALIFIED = "equivalence_not_qualified"
    CAPABILITY_MISMATCH = "capability_mismatch"
    CONTEXT_TOO_SMALL = "context_too_small"
    REGION_MISMATCH = "region_mismatch"
    CREDENTIAL_UNAVAILABLE = "credential_unavailable"
    BYOK_REQUIRED = "byok_required"
    BYOK_DISALLOWED = "byok_disallowed"
    PRIVACY_INCOMPATIBLE = "privacy_incompatible"


class ModelFailureSignal(StrEnum):
    """Provider-adapter signal; unknown values must be translated to ``UNKNOWN``."""

    CONNECTIVITY = "connectivity"
    RATE_LIMITED = "rate_limited"
    OVERLOADED = "overloaded"
    REQUEST_INVALID = "request_invalid"
    AUTH_INVALID = "auth_invalid"
    REGION_UNAVAILABLE = "region_unavailable"
    POLICY_INCOMPATIBLE = "policy_incompatible"
    CONTEXT_EXCEEDED = "context_exceeded"
    STREAM_INTERRUPTED = "stream_interrupted"
    CANCELLED = "cancelled"
    DEADLINE_EXCEEDED = "deadline_exceeded"
    UNKNOWN = "unknown"


class ModelDispatchState(StrEnum):
    """How far the provider adapter can prove a request progressed."""

    BEFORE_DISPATCH = "before_dispatch"
    NOT_ACCEPTED = "not_accepted"
    ACCEPTED = "accepted"
    UNKNOWN = "unknown"


class ModelStreamState(StrEnum):
    """User-visible progress of one provider attempt."""

    NOT_STARTED = "not_started"
    STARTED_NO_VISIBLE_OUTPUT = "started_no_visible_output"
    VISIBLE_OUTPUT = "visible_output"


class ModelFailureClass(StrEnum):
    """Closed, provider-neutral failure taxonomy."""

    PRE_DISPATCH_TRANSIENT = "pre_dispatch_transient"
    PROVIDER_OVERLOADED = "provider_overloaded"
    REQUEST_INVALID = "request_invalid"
    AUTH_INVALID = "auth_invalid"
    REGION_UNAVAILABLE = "region_unavailable"
    POLICY_INCOMPATIBLE = "policy_incompatible"
    CONTEXT_EXCEEDED = "context_exceeded"
    STREAM_INTERRUPTED_BEFORE_CONTENT = "stream_interrupted_before_content"
    STREAM_INTERRUPTED_AFTER_CONTENT = "stream_interrupted_after_content"
    AMBIGUOUS_PROVIDER_STATE = "ambiguous_provider_state"
    CANCELLED = "cancelled"
    DEADLINE_EXCEEDED = "deadline_exceeded"


class ModelRecoveryScope(StrEnum):
    """Scope a caller is asking this policy to recover."""

    MODEL_INVOCATION = "model_invocation"
    WHOLE_RUN = "whole_run"


class ModelAttemptDecisionKind(StrEnum):
    """Whether another provider attempt may start."""

    ADMIT = "admit"
    DENY = "deny"


class ModelAttemptDecisionReason(StrEnum):
    """Stable, content-free explanation of an attempt admission decision."""

    FIRST_ATTEMPT = "first_attempt"
    SAFE_SAME_DEPLOYMENT_RETRY = "safe_same_deployment_retry"
    SAFE_ALTERNATE_ROUTE = "safe_alternate_route"
    WHOLE_RUN_REPLAY_FORBIDDEN = "whole_run_replay_forbidden"
    EXTERNAL_EFFECT_OBSERVED = "external_effect_observed"
    NO_ELIGIBLE_ROUTE = "no_eligible_route"
    ATTEMPT_LIMIT_REACHED = "attempt_limit_reached"
    SAME_DEPLOYMENT_LIMIT_REACHED = "same_deployment_limit_reached"
    ROUTE_SET_EXHAUSTED = "route_set_exhausted"
    DEADLINE_EXPIRED = "deadline_expired"
    PROJECTED_COST_UNKNOWN = "projected_cost_unknown"
    COST_BUDGET_EXCEEDED = "cost_budget_exceeded"
    PROJECTED_TOKEN_USAGE_UNKNOWN = "projected_token_usage_unknown"
    INPUT_TOKEN_BUDGET_EXCEEDED = "input_token_budget_exceeded"
    OUTPUT_TOKEN_BUDGET_EXCEEDED = "output_token_budget_exceeded"
    VISIBLE_OUTPUT_ALREADY_EMITTED = "visible_output_already_emitted"
    AMBIGUOUS_PROVIDER_STATE = "ambiguous_provider_state"
    FAILURE_NOT_RETRYABLE = "failure_not_retryable"
    CONTEXT_REPLAN_REQUIRED = "context_replan_required"
    PRIOR_ATTEMPT_NOT_FAILED = "prior_attempt_not_failed"
    PRIOR_ROUTE_MISMATCH = "prior_route_mismatch"


def _slug(value: str, field_name: str) -> str:
    return ValueNormalizer.normalize_slug(value, field_name)


def _nonempty(value: str, field_name: str) -> str:
    return ValueNormalizer.normalize_nonempty_string(value, field_name)


def _aware(value: datetime | None, field_name: str) -> datetime | None:
    if value is not None and (value.tzinfo is None or value.utcoffset() is None):
        raise ValueError(f"{field_name} must be timezone-aware")
    return value


class ModelDeploymentDescriptor(RuntimeContract):
    """Versioned, non-secret facts about one callable model deployment."""

    deployment_id: str = Field(min_length=1, max_length=255)
    deployment_revision: str = Field(
        default="model-deployment.v1",
        min_length=1,
        max_length=255,
    )
    endpoint_ref: str = Field(pattern=r"^endpoint_[0-9a-f]{32}$")
    endpoint_revision: str = Field(
        default="model-endpoint.v1",
        min_length=1,
        max_length=255,
    )
    provider: str = Field(min_length=1, max_length=64)
    model_name: str = Field(min_length=1, max_length=200)
    capabilities: frozenset[ModelCapability] = Field(default_factory=frozenset)
    max_input_tokens: PositiveInt = Field(le=2_000_000)
    max_output_tokens: PositiveInt = Field(le=2_000_000)
    region: str = Field(min_length=1, max_length=64)
    credential_modes: frozenset[ModelCredentialMode] = Field(min_length=1)
    privacy_features: frozenset[ModelPrivacyFeature] = Field(default_factory=frozenset)
    qualified_task_families: frozenset[str] = Field(default_factory=frozenset)
    health: ModelDeploymentHealth = ModelDeploymentHealth.AVAILABLE
    enabled: bool = True
    price_revision: str = Field(min_length=1, max_length=255)
    qualification_revision: str = Field(
        default="model-qualification.none.v1",
        min_length=1,
        max_length=255,
    )
    descriptor_revision: str = Field(min_length=1, max_length=255)

    @field_validator(
        "deployment_id",
        "deployment_revision",
        "endpoint_revision",
        "model_name",
        "price_revision",
        "qualification_revision",
        "descriptor_revision",
    )
    @classmethod
    def _normalize_identifier(cls, value: str, info) -> str:  # type: ignore[no-untyped-def]
        return _nonempty(value, info.field_name)

    @field_validator("provider")
    @classmethod
    def _normalize_provider(cls, value: str) -> str:
        return _slug(value, "provider")

    @field_validator("qualified_task_families", mode="before")
    @classmethod
    def _normalize_slug_sets(cls, value: object, info) -> frozenset[str]:  # type: ignore[no-untyped-def]
        return ValueNormalizer.normalize_slug_set(value, info.field_name)

    @field_validator("region")
    @classmethod
    def _normalize_region(cls, value: str) -> str:
        return _slug(value, "region")


class ModelDeploymentCatalog(RuntimeContract):
    """Replay-stable, bounded descriptor set consumed by the route policy."""

    catalog_revision: str = Field(min_length=1, max_length=255)
    descriptors: tuple[ModelDeploymentDescriptor, ...] = Field(
        max_length=_MAX_DEPLOYMENTS
    )
    descriptor_set_digest: str = Field(pattern=_SHA256_REF_PATTERN)

    @classmethod
    def create(
        cls,
        descriptors: tuple[ModelDeploymentDescriptor, ...],
        *,
        schema_revision: str = "model-deployment-catalog.v1",
    ) -> "ModelDeploymentCatalog":
        ordered = tuple(sorted(descriptors, key=lambda item: item.deployment_id))
        digest = cls._digest(ordered)
        return cls(
            catalog_revision=f"{schema_revision}:{digest}",
            descriptors=ordered,
            descriptor_set_digest=digest,
        )

    @model_validator(mode="after")
    def _catalog_is_canonical(self) -> Self:
        deployment_ids = tuple(item.deployment_id for item in self.descriptors)
        if deployment_ids != tuple(sorted(deployment_ids)):
            raise ValueError("deployment catalog must be ordered by deployment_id")
        if len(deployment_ids) != len(set(deployment_ids)):
            raise ValueError("deployment catalog contains duplicate deployment_id")
        expected = self._digest(self.descriptors)
        if self.descriptor_set_digest != expected:
            raise ValueError("descriptor_set_digest does not match descriptors")
        if not self.catalog_revision.endswith(expected):
            raise ValueError("catalog_revision does not bind descriptor_set_digest")
        return self

    @staticmethod
    def _digest(descriptors: tuple[ModelDeploymentDescriptor, ...]) -> str:
        return "sha256:" + canonical_json_sha256(
            {
                "schema_revision": "model-deployment-descriptor.v1",
                "descriptors": [
                    descriptor.model_dump(mode="json") for descriptor in descriptors
                ],
            }
        )


class ModelInvocationBudget(RuntimeContract):
    """Aggregate bounds shared by every attempt in one model invocation."""

    max_attempts: PositiveInt = Field(default=1, le=3)
    max_same_deployment_attempts: PositiveInt = Field(default=1, le=3)
    max_cost_microusd: NonNegativeInt | None = None
    max_input_tokens: PositiveInt | None = Field(default=None, le=10_000_000)
    max_output_tokens: PositiveInt | None = Field(default=None, le=10_000_000)
    deadline_at: datetime | None = None

    @field_validator("deadline_at")
    @classmethod
    def _require_aware_deadline(cls, value: datetime | None) -> datetime | None:
        return _aware(value, "deadline_at")

    @model_validator(mode="after")
    def _same_deployment_limit_fits_total(self) -> Self:
        if self.max_same_deployment_attempts > self.max_attempts:
            raise ValueError("max_same_deployment_attempts cannot exceed max_attempts")
        return self


class ModelCredentialAvailability(RuntimeContract):
    """Credential modes currently usable for one canonical provider."""

    provider: str = Field(min_length=1, max_length=64)
    modes: frozenset[ModelCredentialMode] = Field(default_factory=frozenset)

    @field_validator("provider")
    @classmethod
    def _normalize_provider(cls, value: str) -> str:
        return _slug(value, "provider")


class ModelInvocationRequirements(RuntimeContract):
    """Verified constraints for one model call.

    ``credential_availability`` carries availability only and is provider-scoped,
    so an OpenAI BYOK key can never authorize an Anthropic fallback. Plaintext keys
    and endpoint URLs remain in the existing ephemeral runtime context.
    """

    task_family: str = Field(min_length=1, max_length=100)
    provider: str = Field(min_length=1, max_length=64)
    model_name: str = Field(min_length=1, max_length=200)
    primary_deployment_id: str | None = Field(
        default=None, min_length=1, max_length=255
    )
    required_capabilities: frozenset[ModelCapability] = Field(default_factory=frozenset)
    minimum_context_tokens: PositiveInt = Field(le=2_000_000)
    region: str | None = Field(default=None, min_length=1, max_length=64)
    credential_availability: tuple[ModelCredentialAvailability, ...] = ()
    byok_policy: ByokPolicy = ByokPolicy.ALLOWED
    training_opt_out_required: bool = False
    fallback_policy: ModelFallbackPolicy = ModelFallbackPolicy.NONE
    budget: ModelInvocationBudget = Field(default_factory=ModelInvocationBudget)

    @field_validator("task_family", "provider")
    @classmethod
    def _normalize_slugs(cls, value: str, info) -> str:  # type: ignore[no-untyped-def]
        return _slug(value, info.field_name)

    @field_validator("model_name", "primary_deployment_id")
    @classmethod
    def _normalize_names(
        cls,
        value: str | None,
        info,  # type: ignore[no-untyped-def]
    ) -> str | None:
        if value is None:
            return None
        return _nonempty(value, info.field_name)

    @field_validator("region")
    @classmethod
    def _normalize_region(cls, value: str | None) -> str | None:
        return None if value is None else _slug(value, "region")

    @model_validator(mode="after")
    def _validate_byok_availability(self) -> Self:
        providers = tuple(item.provider for item in self.credential_availability)
        if len(providers) != len(set(providers)):
            raise ValueError("credential availability providers must be unique")
        selected_modes = next(
            (
                item.modes
                for item in self.credential_availability
                if item.provider == self.provider
            ),
            frozenset(),
        )
        if (
            self.byok_policy is ByokPolicy.REQUIRED
            and ModelCredentialMode.BYOK not in selected_modes
        ):
            raise ValueError("BYOK is required but no BYOK credential is available")
        return self

    @classmethod
    def from_model_config(
        cls,
        config: ModelConfig,
        *,
        task_family: str,
        available_credential_modes: frozenset[ModelCredentialMode],
        primary_deployment_id: str | None = None,
        required_capabilities: frozenset[ModelCapability] = frozenset(),
        region: str | None = None,
        byok_policy: ByokPolicy = ByokPolicy.ALLOWED,
        training_opt_out_required: bool = False,
        fallback_policy: ModelFallbackPolicy = ModelFallbackPolicy.NONE,
        budget: ModelInvocationBudget | None = None,
    ) -> ModelInvocationRequirements:
        """Adapt the existing resolved ``ModelConfig`` into routing facts."""

        capabilities = set(required_capabilities)
        if config.supports_streaming:
            capabilities.add(ModelCapability.STREAMING)
        return cls(
            task_family=task_family,
            provider=config.provider,
            model_name=config.model_name,
            primary_deployment_id=primary_deployment_id,
            required_capabilities=frozenset(capabilities),
            minimum_context_tokens=config.max_input_tokens,
            region=region,
            credential_availability=(
                ModelCredentialAvailability(
                    provider=config.provider,
                    modes=available_credential_modes,
                ),
            ),
            byok_policy=byok_policy,
            training_opt_out_required=training_opt_out_required,
            fallback_policy=fallback_policy,
            budget=budget or ModelInvocationBudget(),
        )


class ModelInvocationRequirementsSnapshot(RuntimeContract):
    """Immutable requirements with an explicit schema revision and digest."""

    requirements_revision: str = Field(min_length=1, max_length=255)
    requirements: ModelInvocationRequirements
    requirements_digest: str = Field(pattern=_SHA256_REF_PATTERN)

    @classmethod
    def create(
        cls,
        requirements: ModelInvocationRequirements,
        *,
        requirements_revision: str = "model-invocation-requirements.v1",
    ) -> "ModelInvocationRequirementsSnapshot":
        digest = cls._digest(
            requirements_revision=requirements_revision,
            requirements=requirements,
        )
        return cls(
            requirements_revision=requirements_revision,
            requirements=requirements,
            requirements_digest=digest,
        )

    @model_validator(mode="after")
    def _digest_matches(self) -> Self:
        expected = self._digest(
            requirements_revision=self.requirements_revision,
            requirements=self.requirements,
        )
        if self.requirements_digest != expected:
            raise ValueError("requirements_digest does not match requirements")
        return self

    @staticmethod
    def _digest(
        *,
        requirements_revision: str,
        requirements: ModelInvocationRequirements,
    ) -> str:
        return "sha256:" + canonical_json_sha256(
            {
                "requirements_revision": requirements_revision,
                "requirements": requirements.model_dump(mode="json"),
            }
        )


class ModelRouteExclusion(RuntimeContract):
    """Why one descriptor could not be used."""

    deployment_id: str = Field(min_length=1, max_length=255)
    reasons: tuple[ModelRouteExclusionReason, ...] = Field(min_length=1)


class ModelRouteEntry(RuntimeContract):
    """One fully bound, non-secret provider attempt route."""

    deployment_id: str = Field(min_length=1, max_length=255)
    deployment_revision: str = Field(
        default="model-deployment.v1",
        min_length=1,
        max_length=255,
    )
    descriptor_revision: str = Field(min_length=1, max_length=255)
    endpoint_ref: str = Field(pattern=r"^endpoint_[0-9a-f]{32}$")
    endpoint_revision: str = Field(
        default="model-endpoint.v1",
        min_length=1,
        max_length=255,
    )
    provider: str = Field(min_length=1, max_length=64)
    model_name: str = Field(min_length=1, max_length=200)
    region: str = Field(min_length=1, max_length=64)
    credential_mode: ModelCredentialMode
    price_revision: str = Field(min_length=1, max_length=255)
    qualification_revision: str = Field(
        default="model-qualification.none.v1",
        min_length=1,
        max_length=255,
    )
    max_input_tokens: PositiveInt = Field(le=2_000_000)
    max_output_tokens: PositiveInt = Field(le=2_000_000)

    @field_validator(
        "deployment_id",
        "deployment_revision",
        "descriptor_revision",
        "endpoint_revision",
        "model_name",
        "price_revision",
        "qualification_revision",
    )
    @classmethod
    def _normalize_identifiers(cls, value: str, info) -> str:  # type: ignore[no-untyped-def]
        return _nonempty(value, info.field_name)

    @field_validator("provider", "region")
    @classmethod
    def _normalize_slugs(cls, value: str, info) -> str:  # type: ignore[no-untyped-def]
        return _slug(value, info.field_name)

    @classmethod
    def from_descriptor(
        cls,
        descriptor: ModelDeploymentDescriptor,
        *,
        credential_mode: ModelCredentialMode,
    ) -> "ModelRouteEntry":
        """Project a trusted descriptor and selected credential class."""

        if credential_mode not in descriptor.credential_modes:
            raise ValueError("selected credential mode is not supported by deployment")
        return cls(
            deployment_id=descriptor.deployment_id,
            deployment_revision=descriptor.deployment_revision,
            descriptor_revision=descriptor.descriptor_revision,
            endpoint_ref=descriptor.endpoint_ref,
            endpoint_revision=descriptor.endpoint_revision,
            provider=descriptor.provider,
            model_name=descriptor.model_name,
            region=descriptor.region,
            credential_mode=credential_mode,
            price_revision=descriptor.price_revision,
            qualification_revision=descriptor.qualification_revision,
            max_input_tokens=descriptor.max_input_tokens,
            max_output_tokens=descriptor.max_output_tokens,
        )


class ModelRoutePlan(RuntimeContract):
    """Ordered, fully bound routes and every rejected descriptor."""

    policy_revision: str = Field(default="model-route-policy.v2", min_length=1)
    routes: tuple[ModelRouteEntry, ...]
    exclusions: tuple[ModelRouteExclusion, ...] = ()
    fallback_policy: ModelFallbackPolicy
    budget: ModelInvocationBudget
    route_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")

    @property
    def deployment_ids(self) -> tuple[str, ...]:
        """Compatibility projection used by attempt admission."""

        return tuple(route.deployment_id for route in self.routes)

    @classmethod
    def create(
        cls,
        *,
        routes: tuple[ModelRouteEntry, ...],
        exclusions: tuple[ModelRouteExclusion, ...],
        fallback_policy: ModelFallbackPolicy,
        budget: ModelInvocationBudget,
        policy_revision: str = "model-route-policy.v2",
    ) -> "ModelRoutePlan":
        digest = cls._digest(
            policy_revision=policy_revision,
            routes=routes,
            exclusions=exclusions,
            fallback_policy=fallback_policy,
            budget=budget,
        )
        return cls(
            policy_revision=policy_revision,
            routes=routes,
            exclusions=exclusions,
            fallback_policy=fallback_policy,
            budget=budget,
            route_digest=digest,
        )

    @model_validator(mode="after")
    def _descriptor_sets_are_well_formed(self) -> Self:
        if len(self.deployment_ids) != len(set(self.deployment_ids)):
            raise ValueError("route deployment_ids must be unique")
        excluded_ids = tuple(item.deployment_id for item in self.exclusions)
        if len(excluded_ids) != len(set(excluded_ids)):
            raise ValueError("route exclusion deployment_ids must be unique")
        if set(self.deployment_ids).intersection(excluded_ids):
            raise ValueError("eligible and excluded deployment_ids must be disjoint")
        expected_digest = self._digest(
            policy_revision=self.policy_revision,
            routes=self.routes,
            exclusions=self.exclusions,
            fallback_policy=self.fallback_policy,
            budget=self.budget,
        )
        if self.route_digest != expected_digest:
            raise ValueError("route_digest does not match the bound route plan")
        return self

    @staticmethod
    def _digest(
        *,
        policy_revision: str,
        routes: tuple[ModelRouteEntry, ...],
        exclusions: tuple[ModelRouteExclusion, ...],
        fallback_policy: ModelFallbackPolicy,
        budget: ModelInvocationBudget,
    ) -> str:
        canonical = json.dumps(
            {
                "policy_revision": policy_revision,
                "routes": [route.model_dump(mode="json") for route in routes],
                "exclusions": [
                    exclusion.model_dump(mode="json") for exclusion in exclusions
                ],
                "fallback_policy": fallback_policy.value,
                "budget": budget.model_dump(mode="json"),
            },
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return f"sha256:{hashlib.sha256(canonical).hexdigest()}"


class ModelInvocationAuthority(RuntimeContract):
    """Replay-stable authority binding for one checkpoint-derived model call."""

    invocation_id: str = Field(min_length=1, max_length=160)
    call_identity: RuntimeModelCallIdentity
    purpose: str = Field(min_length=1, max_length=80)
    request_digest: str = Field(pattern=_SHA256_REF_PATTERN)
    run_control_snapshot_digest: str = Field(pattern=_SHA256_PATTERN)
    requirements_revision: str = Field(min_length=1, max_length=255)
    requirements_digest: str = Field(pattern=_SHA256_REF_PATTERN)
    descriptor_set_revision: str = Field(min_length=1, max_length=255)
    descriptor_set_digest: str = Field(pattern=_SHA256_REF_PATTERN)
    route_policy_revision: str = Field(min_length=1, max_length=255)
    route_plan_digest: str = Field(pattern=_SHA256_REF_PATTERN)
    authority_digest: str = Field(pattern=_SHA256_REF_PATTERN)

    @field_validator("purpose")
    @classmethod
    def _normalize_purpose(cls, value: str) -> str:
        return _slug(value, "purpose")

    @classmethod
    def create(
        cls,
        *,
        call_identity: RuntimeModelCallIdentity,
        purpose: str,
        request_digest: str,
        run_control_snapshot_digest: str,
        requirements: ModelInvocationRequirementsSnapshot,
        catalog: ModelDeploymentCatalog,
        route_plan: ModelRoutePlan,
    ) -> "ModelInvocationAuthority":
        payload = {
            "call_identity": call_identity,
            "purpose": purpose,
            "request_digest": request_digest,
            "run_control_snapshot_digest": run_control_snapshot_digest,
            "requirements_revision": requirements.requirements_revision,
            "requirements_digest": requirements.requirements_digest,
            "descriptor_set_revision": catalog.catalog_revision,
            "descriptor_set_digest": catalog.descriptor_set_digest,
            "route_policy_revision": route_plan.policy_revision,
            "route_plan_digest": route_plan.route_digest,
        }
        authority_digest = cls._digest(payload)
        return cls(
            invocation_id=f"model-invocation:{authority_digest.removeprefix('sha256:')}",
            **payload,
            authority_digest=authority_digest,
        )

    @model_validator(mode="after")
    def _authority_matches(self) -> Self:
        expected = self._digest(
            self.model_dump(
                mode="json",
                exclude={"invocation_id", "authority_digest"},
            )
        )
        if self.authority_digest != expected:
            raise ValueError("authority_digest does not match invocation authority")
        expected_id = f"model-invocation:{expected.removeprefix('sha256:')}"
        if self.invocation_id != expected_id:
            raise ValueError("invocation_id does not match invocation authority")
        return self

    @staticmethod
    def _digest(payload: object) -> str:
        if isinstance(payload, dict):
            normalized = {
                key: (
                    value.model_dump(mode="json")
                    if isinstance(value, RuntimeContract)
                    else value
                )
                for key, value in payload.items()
            }
        else:
            normalized = payload
        return "sha256:" + canonical_json_sha256(normalized)


class ProviderFailureObservation(RuntimeContract):
    """Sanitized failure facts attested by a provider adapter."""

    signal: ModelFailureSignal
    dispatch_state: ModelDispatchState = ModelDispatchState.UNKNOWN
    stream_state: ModelStreamState = ModelStreamState.NOT_STARTED

    @model_validator(mode="after")
    def _validate_stream_progress(self) -> Self:
        if (
            self.stream_state is not ModelStreamState.NOT_STARTED
            and self.dispatch_state
            in (ModelDispatchState.BEFORE_DISPATCH, ModelDispatchState.NOT_ACCEPTED)
        ):
            raise ValueError("stream progress requires an accepted or unknown dispatch")
        return self


class ModelAttemptOutcome(RuntimeContract):
    """Content-free terminal outcome for a persisted provider attempt."""

    attempt_id: str = Field(min_length=1, max_length=255)
    ordinal: PositiveInt
    deployment_id: str = Field(min_length=1, max_length=255)
    failure_class: ModelFailureClass | None = None
    stream_state: ModelStreamState = ModelStreamState.NOT_STARTED
    cost_microusd: NonNegativeInt = 0
    input_tokens: NonNegativeInt = 0
    output_tokens: NonNegativeInt = 0


class ModelAttemptAdmissionRequest(RuntimeContract):
    """Current invocation state used to decide whether another attempt may start."""

    route_plan: ModelRoutePlan
    now: datetime
    prior_attempts: tuple[ModelAttemptOutcome, ...] = ()
    recovery_scope: ModelRecoveryScope = ModelRecoveryScope.MODEL_INVOCATION
    external_effect_observed: bool = False
    projected_cost_microusd: NonNegativeInt | None = None
    projected_input_tokens: NonNegativeInt | None = None
    projected_output_tokens: NonNegativeInt | None = None

    @field_validator("now")
    @classmethod
    def _require_aware_now(cls, value: datetime) -> datetime:
        normalized = _aware(value, "now")
        assert normalized is not None
        return normalized

    @model_validator(mode="after")
    def _attempt_ordinals_are_contiguous(self) -> Self:
        ordinals = tuple(attempt.ordinal for attempt in self.prior_attempts)
        if ordinals != tuple(range(1, len(ordinals) + 1)):
            raise ValueError("prior attempt ordinals must be contiguous and one-based")
        return self


class ModelAttemptDecision(RuntimeContract):
    """Pure attempt-admission result; never an instruction to replay a run."""

    kind: ModelAttemptDecisionKind
    reason: ModelAttemptDecisionReason
    deployment_id: str | None = None
    ordinal: PositiveInt | None = None

    @model_validator(mode="after")
    def _admitted_attempt_has_identity(self) -> Self:
        if self.kind is ModelAttemptDecisionKind.ADMIT:
            if self.deployment_id is None or self.ordinal is None:
                raise ValueError("admitted attempts require deployment_id and ordinal")
        elif self.deployment_id is not None or self.ordinal is not None:
            raise ValueError("denied attempts cannot carry attempt identity")
        return self


__all__ = (
    "ByokPolicy",
    "ModelAttemptAdmissionRequest",
    "ModelAttemptDecision",
    "ModelAttemptDecisionKind",
    "ModelAttemptDecisionReason",
    "ModelAttemptOutcome",
    "ModelCapability",
    "ModelCredentialAvailability",
    "ModelCredentialMode",
    "ModelDeploymentCatalog",
    "ModelDeploymentDescriptor",
    "ModelDeploymentHealth",
    "ModelDispatchState",
    "ModelFailureClass",
    "ModelFailureSignal",
    "ModelFallbackPolicy",
    "ModelInvocationBudget",
    "ModelInvocationAuthority",
    "ModelInvocationRequirements",
    "ModelInvocationRequirementsSnapshot",
    "ModelPrivacyFeature",
    "ModelRecoveryScope",
    "ModelRouteExclusion",
    "ModelRouteExclusionReason",
    "ModelRouteEntry",
    "ModelRoutePlan",
    "ModelStreamState",
    "ProviderFailureObservation",
)
