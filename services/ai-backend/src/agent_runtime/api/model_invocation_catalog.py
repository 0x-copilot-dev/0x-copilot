"""Trusted adapters from the current model/runtime surfaces into F10 facts.

The adapters consume already-resolved runtime context, curated catalog items,
workspace enablement, and bounded deployment facts. Plaintext credentials and
endpoint URLs are used only to derive availability/revision identities and are
never retained in any returned contract.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import math

from pydantic import Field, field_validator

from agent_runtime.control_plane.context import RunControlBinding
from agent_runtime.execution.call_identity import RuntimeModelCallIdentity
from agent_runtime.execution.contracts import AgentRuntimeContext, RuntimeContract
from agent_runtime.execution.model_invocation.authority import (
    ModelInvocationAuthorityBinder,
)
from agent_runtime.execution.model_invocation.contracts import (
    ByokPolicy,
    ModelCapability,
    ModelCredentialAvailability,
    ModelCredentialMode,
    ModelDeploymentCatalog,
    ModelDeploymentDescriptor,
    ModelDeploymentHealth,
    ModelFallbackPolicy,
    ModelInvocationAuthority,
    ModelInvocationBudget,
    ModelInvocationRequirements,
    ModelInvocationRequirementsSnapshot,
    ModelPrivacyFeature,
    ModelRoutePlan,
)
from agent_runtime.execution.model_invocation.policy import ModelRoutePolicy
from agent_runtime.execution.models import ModelConfigResolver
from agent_runtime.execution.openai_compat import (
    CUSTOM_OPENAI_COMPATIBLE_PROVIDER,
)
from agent_runtime.surfaces_v2.canonical_json import canonical_json_sha256
from agent_runtime.validation import ValueNormalizer
from runtime_api.schemas.runs import ModelCatalogItem

_DEFAULT_REGION = "default"
_MAX_CATALOG_ITEMS = 512
_DEFAULT_CREDENTIAL_MODES: Mapping[str, frozenset[ModelCredentialMode]] = {
    "openai": frozenset({ModelCredentialMode.DEPLOYMENT, ModelCredentialMode.BYOK}),
    "anthropic": frozenset({ModelCredentialMode.DEPLOYMENT, ModelCredentialMode.BYOK}),
    "gemini": frozenset({ModelCredentialMode.DEPLOYMENT, ModelCredentialMode.BYOK}),
    "openrouter": frozenset({ModelCredentialMode.BYOK}),
    # Virtuals declares BOTH because both are wired: a per-user BYOK key is the
    # product path, and ``VIRTUALS_ACP_KEY`` is the deployment fallback that
    # lets `make dev` and the test suites reach the gateway without a keychain.
    # Availability is the INTERSECTION of what is actually present with what is
    # declared here, so omitting DEPLOYMENT would not make the env key
    # unsupported — it would make it silently unusable, excluding every Virtuals
    # model as CREDENTIAL_UNAVAILABLE on a machine that has the key set.
    "virtuals": frozenset({ModelCredentialMode.DEPLOYMENT, ModelCredentialMode.BYOK}),
    "ollama": frozenset({ModelCredentialMode.KEYLESS}),
    CUSTOM_OPENAI_COMPATIBLE_PROVIDER: frozenset({ModelCredentialMode.BYOK}),
}
_DEFAULT_PRIVACY_FEATURES: Mapping[
    str,
    frozenset[ModelPrivacyFeature],
] = {
    "openai": frozenset({ModelPrivacyFeature.TRAINING_OPT_OUT}),
    "anthropic": frozenset({ModelPrivacyFeature.TRAINING_OPT_OUT}),
    # Local inference does not send prompts to a remote training system.
    "ollama": frozenset({ModelPrivacyFeature.TRAINING_OPT_OUT}),
}


def _provider(value: str) -> str:
    normalized = ModelConfigResolver.canonical_provider(value)
    if normalized is None:
        raise ValueError(f"unsupported model provider {value!r}")
    return normalized


class ModelEndpointAuthority(RuntimeContract):
    """Non-secret identity and supported credential modes for one endpoint."""

    provider: str = Field(min_length=1, max_length=64)
    region: str = Field(min_length=1, max_length=64)
    endpoint_ref: str = Field(pattern=r"^endpoint_[0-9a-f]{32}$")
    endpoint_revision: str = Field(min_length=1, max_length=255)
    credential_modes: frozenset[ModelCredentialMode] = Field(min_length=1)
    privacy_features: frozenset[ModelPrivacyFeature] = Field(default_factory=frozenset)

    @field_validator("provider")
    @classmethod
    def _normalize_provider(cls, value: str) -> str:
        return _provider(value)

    @field_validator("region")
    @classmethod
    def _normalize_region(cls, value: str) -> str:
        return ValueNormalizer.normalize_slug(value, "region")

    @classmethod
    def from_revision(
        cls,
        *,
        provider: str,
        endpoint_identity_revision: str,
        region: str = _DEFAULT_REGION,
        credential_modes: frozenset[ModelCredentialMode],
        privacy_features: frozenset[ModelPrivacyFeature] = frozenset(),
    ) -> "ModelEndpointAuthority":
        """Create an opaque endpoint ref without retaining endpoint material."""

        canonical_provider = _provider(provider)
        normalized_region = ValueNormalizer.normalize_slug(region, "region")
        identity_digest = canonical_json_sha256(
            {
                "schema_revision": "model-endpoint-authority.v1",
                "provider": canonical_provider,
                "region": normalized_region,
                "endpoint_identity_revision": endpoint_identity_revision,
            }
        )
        return cls(
            provider=canonical_provider,
            region=normalized_region,
            endpoint_ref=f"endpoint_{identity_digest[:32]}",
            endpoint_revision=f"model-endpoint.v1:sha256:{identity_digest}",
            credential_modes=credential_modes,
            privacy_features=privacy_features,
        )

    @classmethod
    def from_endpoint_url(
        cls,
        *,
        provider: str,
        endpoint_url: str,
        region: str = _DEFAULT_REGION,
        credential_modes: frozenset[ModelCredentialMode],
        privacy_features: frozenset[ModelPrivacyFeature] = frozenset(),
    ) -> "ModelEndpointAuthority":
        """Consume an ephemeral URL and retain only its domain-separated hash."""

        normalized_url = endpoint_url.strip()
        if not normalized_url:
            raise ValueError("endpoint_url must be non-empty")
        return cls.from_revision(
            provider=provider,
            region=region,
            endpoint_identity_revision=(
                "endpoint-url:sha256:"
                + canonical_json_sha256(
                    {
                        "purpose": "model-endpoint-url-identity.v1",
                        "url": normalized_url,
                    }
                )
            ),
            credential_modes=credential_modes,
            privacy_features=privacy_features,
        )


class ModelQualificationAuthority(RuntimeContract):
    """F1-approved task-family equivalence facts for one model."""

    provider: str = Field(min_length=1, max_length=64)
    model_name: str = Field(min_length=1, max_length=200)
    task_families: frozenset[str] = Field(default_factory=frozenset)
    qualification_revision: str = Field(min_length=1, max_length=255)

    @field_validator("provider")
    @classmethod
    def _normalize_provider(cls, value: str) -> str:
        return _provider(value)

    @field_validator("task_families", mode="before")
    @classmethod
    def _normalize_families(cls, value: object) -> frozenset[str]:
        return ValueNormalizer.normalize_slug_set(value, "task_families")


class ModelHealthAuthority(RuntimeContract):
    """Bounded health fact for one provider/model/region deployment."""

    provider: str = Field(min_length=1, max_length=64)
    model_name: str = Field(min_length=1, max_length=200)
    region: str = Field(min_length=1, max_length=64)
    health: ModelDeploymentHealth
    health_revision: str = Field(min_length=1, max_length=255)

    @field_validator("provider")
    @classmethod
    def _normalize_provider(cls, value: str) -> str:
        return _provider(value)

    @field_validator("region")
    @classmethod
    def _normalize_region(cls, value: str) -> str:
        return ValueNormalizer.normalize_slug(value, "region")


class ModelInvocationAuthorityAdapterInput(RuntimeContract):
    """Complete trusted input envelope for one model-call authority decision.

    The runtime context is deliberately excluded from serialization because it
    can contain ephemeral BYOK material and custom endpoint URLs. Requiring one
    envelope prevents worker integration from deriving the catalog,
    requirements, and route authority from different fact sets.
    """

    runtime_context: AgentRuntimeContext = Field(exclude=True, repr=False)
    catalog_items: tuple[ModelCatalogItem, ...] = Field(max_length=_MAX_CATALOG_ITEMS)
    endpoints: tuple[ModelEndpointAuthority, ...] = Field(
        default=(),
        max_length=_MAX_CATALOG_ITEMS,
    )
    qualifications: tuple[ModelQualificationAuthority, ...] = Field(
        default=(),
        max_length=_MAX_CATALOG_ITEMS,
    )
    health: tuple[ModelHealthAuthority, ...] = Field(
        default=(),
        max_length=_MAX_CATALOG_ITEMS,
    )
    deployment_credential_providers: frozenset[str] = Field(
        default_factory=frozenset,
        max_length=32,
    )
    task_family: str = Field(min_length=1, max_length=80)
    required_capabilities: frozenset[ModelCapability] = Field(default_factory=frozenset)
    byok_policy: ByokPolicy = ByokPolicy.ALLOWED
    fallback_policy: ModelFallbackPolicy = ModelFallbackPolicy.NONE
    budget: ModelInvocationBudget = Field(default_factory=ModelInvocationBudget)
    purpose: str = Field(min_length=1, max_length=80)
    request_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")

    @field_validator("runtime_context", mode="before")
    @classmethod
    def _snapshot_runtime_context(cls, value: object) -> object:
        if isinstance(value, AgentRuntimeContext):
            return value.model_copy(deep=True)
        return value

    @field_validator("deployment_credential_providers", mode="before")
    @classmethod
    def _normalize_deployment_providers(cls, value: object) -> frozenset[str]:
        return frozenset(_provider(provider) for provider in value)  # type: ignore[union-attr]

    @field_validator("task_family", "purpose")
    @classmethod
    def _normalize_slugs(cls, value: str, info) -> str:  # type: ignore[no-untyped-def]
        return ValueNormalizer.normalize_slug(value, info.field_name)


class PreparedModelInvocationAuthority(RuntimeContract):
    """Replay-safe result of the atomic model authority adapter."""

    catalog: ModelDeploymentCatalog
    requirements: ModelInvocationRequirementsSnapshot
    route_plan: ModelRoutePlan
    authority: ModelInvocationAuthority


class ModelDeploymentCatalogAdapter:
    """Build deterministic descriptors from the existing curated catalog."""

    def build(
        self,
        authority_input: ModelInvocationAuthorityAdapterInput,
    ) -> ModelDeploymentCatalog:
        runtime_context = authority_input.runtime_context
        catalog_items = authority_input.catalog_items
        if len(catalog_items) > _MAX_CATALOG_ITEMS:
            raise ValueError("model catalog exceeds the bounded descriptor limit")

        items = self._canonical_items(
            runtime_context=runtime_context,
            catalog_items=catalog_items,
        )
        endpoints_by_provider = self._endpoints(
            runtime_context=runtime_context,
            model_keys=frozenset(items),
            endpoints=authority_input.endpoints,
        )
        qualifications_by_model = self._qualifications(authority_input.qualifications)
        health_by_deployment = self._health(authority_input.health)

        descriptors: list[ModelDeploymentDescriptor] = []
        for (provider, model_name), item in items.items():
            for endpoint in endpoints_by_provider.get((provider, model_name), ()):
                descriptors.append(
                    self._descriptor(
                        runtime_context=runtime_context,
                        item=item,
                        provider=provider,
                        model_name=model_name,
                        endpoint=endpoint,
                        qualification=qualifications_by_model.get(
                            (provider, model_name)
                        ),
                        health=health_by_deployment.get(
                            (provider, model_name, endpoint.region)
                        ),
                    )
                )
                if len(descriptors) > _MAX_CATALOG_ITEMS:
                    raise ValueError(
                        "expanded model deployment catalog exceeds its bound"
                    )
        return ModelDeploymentCatalog.create(tuple(descriptors))

    @staticmethod
    def _canonical_items(
        *,
        runtime_context: AgentRuntimeContext,
        catalog_items: Sequence[ModelCatalogItem],
    ) -> dict[tuple[str, str], ModelCatalogItem]:
        by_model: dict[tuple[str, str], ModelCatalogItem] = {}
        for item in catalog_items:
            if item.kind != "chat":
                continue
            provider = _provider(item.provider)
            key = (provider, item.model_name)
            if key in by_model:
                raise ValueError("model catalog contains duplicate provider/model")
            by_model[key] = item.model_copy(update={"provider": provider})

        config = runtime_context.model_profile
        selected_key = (_provider(config.provider), config.model_name)
        if selected_key not in by_model:
            by_model[selected_key] = ModelCatalogItem(
                id=config.model_name,
                provider=selected_key[0],
                model_name=config.model_name,
                name=config.model_name,
                description="Resolved runtime model",
                configured=True,
                enabled=True,
                supports_streaming=config.supports_streaming,
                supports_reasoning=config.reasoning is not None,
                context_window=config.max_input_tokens,
                max_output_tokens=config.max_output_tokens,
            )
        return dict(sorted(by_model.items()))

    @classmethod
    def _endpoints(
        cls,
        *,
        runtime_context: AgentRuntimeContext,
        model_keys: frozenset[tuple[str, str]],
        endpoints: Sequence[ModelEndpointAuthority],
    ) -> dict[tuple[str, str], tuple[ModelEndpointAuthority, ...]]:
        by_model: dict[tuple[str, str], list[ModelEndpointAuthority]] = {
            model_key: [] for model_key in model_keys
        }
        seen_refs: set[str] = set()
        for endpoint in endpoints:
            if endpoint.endpoint_ref in seen_refs:
                raise ValueError("model endpoint authority contains duplicate ref")
            seen_refs.add(endpoint.endpoint_ref)
            for model_key in model_keys:
                if model_key[0] == endpoint.provider:
                    by_model[model_key].append(endpoint)

        selected_key = (
            _provider(runtime_context.model_profile.provider),
            runtime_context.model_profile.model_name,
        )
        if selected_key not in by_model:
            raise ValueError("resolved runtime model is absent from the model catalog")

        # Never infer alternates from picker metadata. In the absence of
        # verified endpoint records, only the exact model already selected by
        # the current run receives an endpoint authority.
        if not by_model[selected_key]:
            provider = selected_key[0]
            modes = _DEFAULT_CREDENTIAL_MODES.get(provider)
            if modes is None:
                raise ValueError("model provider has no endpoint credential policy")
            endpoint_url = runtime_context.provider_endpoints.get(provider)
            selected_endpoint = (
                ModelEndpointAuthority.from_endpoint_url(
                    provider=provider,
                    endpoint_url=endpoint_url,
                    credential_modes=modes,
                    privacy_features=_DEFAULT_PRIVACY_FEATURES.get(
                        provider,
                        frozenset(),
                    ),
                )
                if endpoint_url is not None
                else ModelEndpointAuthority.from_revision(
                    provider=provider,
                    endpoint_identity_revision=f"default-endpoint:{provider}:v1",
                    credential_modes=modes,
                    privacy_features=_DEFAULT_PRIVACY_FEATURES.get(
                        provider,
                        frozenset(),
                    ),
                )
            )
            by_model[selected_key].append(selected_endpoint)

        for values in by_model.values():
            values.sort(key=lambda item: (item.region, item.endpoint_ref))
        return {
            model_key: tuple(values) for model_key, values in sorted(by_model.items())
        }

    @staticmethod
    def _qualifications(
        values: Sequence[ModelQualificationAuthority],
    ) -> dict[tuple[str, str], ModelQualificationAuthority]:
        result: dict[tuple[str, str], ModelQualificationAuthority] = {}
        for value in values:
            key = (value.provider, value.model_name)
            if key in result:
                raise ValueError("duplicate model qualification authority")
            result[key] = value
        return result

    @staticmethod
    def _health(
        values: Sequence[ModelHealthAuthority],
    ) -> dict[tuple[str, str, str], ModelHealthAuthority]:
        result: dict[tuple[str, str, str], ModelHealthAuthority] = {}
        for value in values:
            key = (value.provider, value.model_name, value.region)
            if key in result:
                raise ValueError("duplicate model health authority")
            result[key] = value
        return result

    @classmethod
    def _descriptor(
        cls,
        *,
        runtime_context: AgentRuntimeContext,
        item: ModelCatalogItem,
        provider: str,
        model_name: str,
        endpoint: ModelEndpointAuthority,
        qualification: ModelQualificationAuthority | None,
        health: ModelHealthAuthority | None,
    ) -> ModelDeploymentDescriptor:
        selected = (
            provider == runtime_context.model_profile.provider
            and model_name == runtime_context.model_profile.model_name
        )
        max_input_tokens = item.context_window or (
            runtime_context.model_profile.max_input_tokens if selected else 1
        )
        max_output_tokens = item.max_output_tokens or (
            runtime_context.model_profile.max_output_tokens if selected else None
        )
        # Unknown metadata stays ineligible for ordinary calls instead of
        # inheriting another model's limit.
        max_output_tokens = max_output_tokens or 1
        capabilities = cls._capabilities(
            item=item,
            runtime_context=runtime_context if selected else None,
        )
        price_revision = cls._price_revision(item)
        qualification_revision = (
            qualification.qualification_revision
            if qualification is not None
            else "model-qualification.none.v1"
        )
        qualified_families = (
            qualification.task_families if qualification is not None else frozenset()
        )
        health_state = (
            health.health if health is not None else ModelDeploymentHealth.AVAILABLE
        )
        health_revision = (
            health.health_revision if health is not None else "model-health.default.v1"
        )
        deployment_digest = canonical_json_sha256(
            {
                "schema_revision": "model-deployment-identity.v1",
                "provider": provider,
                "model_name": model_name,
                "endpoint_ref": endpoint.endpoint_ref,
            }
        )
        deployment_id = f"model-deployment:{deployment_digest}"
        deployment_revision = "model-deployment.v1:sha256:" + canonical_json_sha256(
            {
                "deployment_id": deployment_id,
                "provider": provider,
                "model_name": model_name,
                "endpoint_revision": endpoint.endpoint_revision,
            }
        )
        descriptor_body = {
            "deployment_id": deployment_id,
            "deployment_revision": deployment_revision,
            "endpoint_ref": endpoint.endpoint_ref,
            "endpoint_revision": endpoint.endpoint_revision,
            "provider": provider,
            "model_name": model_name,
            "capabilities": sorted(capability.value for capability in capabilities),
            "max_input_tokens": max_input_tokens,
            "max_output_tokens": max_output_tokens,
            "region": endpoint.region,
            "credential_modes": sorted(
                mode.value for mode in endpoint.credential_modes
            ),
            "privacy_features": sorted(
                feature.value for feature in endpoint.privacy_features
            ),
            "qualified_task_families": sorted(qualified_families),
            "health": health_state.value,
            "health_revision": health_revision,
            "enabled": item.enabled,
            "price_revision": price_revision,
            "qualification_revision": qualification_revision,
        }
        descriptor_revision = "model-descriptor.v1:sha256:" + canonical_json_sha256(
            descriptor_body
        )
        return ModelDeploymentDescriptor(
            deployment_id=deployment_id,
            deployment_revision=deployment_revision,
            endpoint_ref=endpoint.endpoint_ref,
            endpoint_revision=endpoint.endpoint_revision,
            provider=provider,
            model_name=model_name,
            capabilities=capabilities,
            max_input_tokens=max_input_tokens,
            max_output_tokens=max_output_tokens,
            region=endpoint.region,
            credential_modes=endpoint.credential_modes,
            privacy_features=endpoint.privacy_features,
            qualified_task_families=qualified_families,
            health=health_state,
            enabled=item.enabled,
            price_revision=price_revision,
            qualification_revision=qualification_revision,
            descriptor_revision=descriptor_revision,
        )

    @staticmethod
    def _capabilities(
        *,
        item: ModelCatalogItem,
        runtime_context: AgentRuntimeContext | None,
    ) -> frozenset[ModelCapability]:
        values: set[ModelCapability] = set()
        if item.supports_streaming:
            values.add(ModelCapability.STREAMING)
        if item.supports_tools is True:
            values.add(ModelCapability.TOOLS)
        if item.supports_reasoning:
            values.add(ModelCapability.REASONING)
        if item.supports_attachments:
            values.add(ModelCapability.VISION)
        if runtime_context is not None:
            if runtime_context.model_profile.supports_streaming:
                values.add(ModelCapability.STREAMING)
            if runtime_context.model_profile.reasoning is not None:
                values.add(ModelCapability.REASONING)
        return frozenset(values)

    @staticmethod
    def _price_revision(item: ModelCatalogItem) -> str:
        def finite(value: float | None) -> float | None:
            return value if value is not None and math.isfinite(value) else None

        digest = canonical_json_sha256(
            {
                "schema_revision": "model-price.v1",
                "input_cost_per_mtok": finite(item.input_cost_per_mtok),
                "output_cost_per_mtok": finite(item.output_cost_per_mtok),
            }
        )
        return f"model-price.v1:sha256:{digest}"


class ModelInvocationRequirementsAdapter:
    """Project verified per-run model, privacy, credential, and budget facts."""

    def build(
        self,
        *,
        authority_input: ModelInvocationAuthorityAdapterInput,
        catalog: ModelDeploymentCatalog,
    ) -> ModelInvocationRequirementsSnapshot:
        runtime_context = authority_input.runtime_context
        config = runtime_context.model_profile
        region, training_opt_out = self._privacy(runtime_context)
        providers = tuple(
            sorted({descriptor.provider for descriptor in catalog.descriptors})
        )
        supported_modes: dict[str, set[ModelCredentialMode]] = {
            provider: set() for provider in providers
        }
        for descriptor in catalog.descriptors:
            supported_modes[descriptor.provider].update(descriptor.credential_modes)

        deployment_providers = frozenset(
            _provider(provider)
            for provider in authority_input.deployment_credential_providers
        )
        availability: list[ModelCredentialAvailability] = []
        for provider in providers:
            modes: set[ModelCredentialMode] = set()
            if provider in deployment_providers:
                modes.add(ModelCredentialMode.DEPLOYMENT)
            key = runtime_context.provider_keys.get(provider)
            if isinstance(key, str) and key.strip():
                modes.add(ModelCredentialMode.BYOK)
            if ModelCredentialMode.KEYLESS in supported_modes[provider]:
                modes.add(ModelCredentialMode.KEYLESS)
            availability.append(
                ModelCredentialAvailability(
                    provider=provider,
                    modes=frozenset(modes).intersection(supported_modes[provider]),
                )
            )

        capabilities = set(authority_input.required_capabilities)
        if config.supports_streaming:
            capabilities.add(ModelCapability.STREAMING)
        if config.reasoning is not None:
            capabilities.add(ModelCapability.REASONING)
        primary_deployment_id = self._primary_deployment_id(
            catalog=catalog,
            provider=config.provider,
            model_name=config.model_name,
            region=region,
        )
        requirements = ModelInvocationRequirements(
            task_family=authority_input.task_family,
            provider=config.provider,
            model_name=config.model_name,
            primary_deployment_id=primary_deployment_id,
            required_capabilities=frozenset(capabilities),
            minimum_context_tokens=config.max_input_tokens,
            region=region,
            credential_availability=tuple(availability),
            byok_policy=authority_input.byok_policy,
            training_opt_out_required=training_opt_out,
            # Cross-model expansion is never inferred from key/catalog
            # availability. The caller must explicitly authorize it.
            fallback_policy=authority_input.fallback_policy,
            budget=authority_input.budget,
        )
        return ModelInvocationRequirementsSnapshot.create(requirements)

    @staticmethod
    def _privacy(runtime_context: AgentRuntimeContext) -> tuple[str | None, bool]:
        workspace_opt_out = (
            runtime_context.workspace_behavior_overrides.get("training_data_opt_out")
            is True
        )
        privacy = runtime_context.user_policies_json.get("privacy")
        if not isinstance(privacy, Mapping):
            return None, workspace_opt_out
        user_opt_out = privacy.get("training_opt_out") is True
        raw_region = privacy.get("region")
        region = (
            raw_region.strip()
            if isinstance(raw_region, str) and raw_region.strip()
            else None
        )
        return region, workspace_opt_out or user_opt_out

    @staticmethod
    def _primary_deployment_id(
        *,
        catalog: ModelDeploymentCatalog,
        provider: str,
        model_name: str,
        region: str | None,
    ) -> str | None:
        candidates = tuple(
            descriptor
            for descriptor in catalog.descriptors
            if descriptor.provider == provider
            and descriptor.model_name == model_name
            and (region is None or descriptor.region == region)
        )
        if not candidates:
            return None
        if region is None:
            default = next(
                (
                    descriptor
                    for descriptor in candidates
                    if descriptor.region == _DEFAULT_REGION
                ),
                None,
            )
            if default is not None:
                return default.deployment_id
        return candidates[0].deployment_id


class ModelInvocationAuthorityAdapter:
    """Atomically derive and bind all trusted authority for one model call."""

    def __init__(
        self,
        *,
        catalog_adapter: ModelDeploymentCatalogAdapter | None = None,
        requirements_adapter: ModelInvocationRequirementsAdapter | None = None,
        route_policy: ModelRoutePolicy | None = None,
        authority_binder: ModelInvocationAuthorityBinder | None = None,
    ) -> None:
        self._catalog_adapter = catalog_adapter or ModelDeploymentCatalogAdapter()
        self._requirements_adapter = (
            requirements_adapter or ModelInvocationRequirementsAdapter()
        )
        self._route_policy = route_policy or ModelRoutePolicy()
        self._authority_binder = authority_binder or ModelInvocationAuthorityBinder(
            route_policy=self._route_policy
        )

    def prepare(
        self,
        *,
        authority_input: ModelInvocationAuthorityAdapterInput,
        call_identity: RuntimeModelCallIdentity,
        control: RunControlBinding,
    ) -> PreparedModelInvocationAuthority:
        catalog = self._catalog_adapter.build(authority_input)
        requirements = self._requirements_adapter.build(
            authority_input=authority_input,
            catalog=catalog,
        )
        route_plan = self._route_policy.plan_catalog(
            requirements.requirements,
            catalog,
            policy_revision=control.snapshot.policy_revisions.model_route,
        )
        authority = self._authority_binder.bind(
            call_identity=call_identity,
            control=control,
            purpose=authority_input.purpose,
            request_digest=authority_input.request_digest,
            requirements=requirements,
            catalog=catalog,
            route_plan=route_plan,
        )
        return PreparedModelInvocationAuthority(
            catalog=catalog,
            requirements=requirements,
            route_plan=route_plan,
            authority=authority,
        )


__all__ = (
    "ModelDeploymentCatalogAdapter",
    "ModelEndpointAuthority",
    "ModelHealthAuthority",
    "ModelInvocationAuthorityAdapter",
    "ModelInvocationAuthorityAdapterInput",
    "ModelInvocationRequirementsAdapter",
    "ModelQualificationAuthority",
    "PreparedModelInvocationAuthority",
)
