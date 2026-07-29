"""Worker-scoped circuit health composed into F10 authority and attempt events."""

from __future__ import annotations

from typing import TYPE_CHECKING

from agent_runtime.api.model_invocation_catalog import (
    ModelDeploymentCatalogAdapter,
    ModelHealthAuthority,
    ModelInvocationAuthorityAdapterInput,
)
from agent_runtime.execution.contracts import AgentRuntimeContext
from agent_runtime.execution.model_invocation.circuit_health import (
    ProcessLocalProviderCircuitHealth,
    ProviderCircuitKey,
)
from agent_runtime.execution.model_invocation.contracts import (
    ModelCredentialMode,
    ModelDeploymentHealth,
    ModelFailureClass,
    ModelRouteEntry,
)
from agent_runtime.surfaces_v2.canonical_json import canonical_json_sha256

if TYPE_CHECKING:
    from runtime_worker.model_invocation_composition import (
        ModelInvocationCompositionFacts,
    )


class ProviderCircuitHealthRegistry:
    """The sole process-local circuit reducer used by F10 worker composition.

    The reducer never receives prompt content.  BYOK circuit keys use a
    domain-separated credential fingerprint, which lets one customer's bad key
    cool down independently from another customer's route.
    """

    def __init__(self, health: ProcessLocalProviderCircuitHealth | None = None) -> None:
        self.health = health or ProcessLocalProviderCircuitHealth()
        self._catalog = ModelDeploymentCatalogAdapter()

    def authority_facts(
        self,
        *,
        facts: ModelInvocationCompositionFacts,
        context: AgentRuntimeContext,
    ) -> tuple[ModelHealthAuthority, ...]:
        """Overlay bounded current health on the catalog's trusted facts."""

        catalog = self._catalog.build(
            ModelInvocationAuthorityAdapterInput(
                runtime_context=context,
                catalog_items=facts.catalog_items,
                endpoints=facts.endpoints,
                qualifications=facts.qualifications,
                health=facts.health,
                deployment_credential_providers=facts.deployment_credential_providers,
                task_family=facts.task_family,
                budget=facts.budget,
                purpose="agent_model_call",
                request_digest="sha256:" + "0" * 64,
            )
        )
        merged = {
            (item.provider, item.model_name, item.region): item for item in facts.health
        }
        for descriptor in catalog.descriptors:
            key = self._key_for(
                provider=descriptor.provider,
                deployment_id=descriptor.deployment_id,
                region=descriptor.region,
                credential_mode=self._credential_mode(
                    provider=descriptor.provider,
                    supported=descriptor.credential_modes,
                    context=context,
                ),
                context=context,
            )
            observed = self.health.health(key)
            identity = (descriptor.provider, descriptor.model_name, descriptor.region)
            existing = merged.get(identity)
            effective = self._stricter(
                existing.health
                if existing is not None
                else ModelDeploymentHealth.AVAILABLE,
                observed,
            )
            merged[identity] = ModelHealthAuthority(
                provider=descriptor.provider,
                model_name=descriptor.model_name,
                region=descriptor.region,
                health=effective,
                health_revision=(
                    "model-health.circuit.v1:sha256:"
                    + canonical_json_sha256(
                        {
                            "provider": descriptor.provider,
                            "model_name": descriptor.model_name,
                            "region": descriptor.region,
                            "health": effective.value,
                            "circuit_key": key.stable_key,
                        }
                    ).removeprefix("sha256:")
                ),
            )
        return tuple(item for _, item in sorted(merged.items()))

    def observe_success(
        self, *, route: ModelRouteEntry, context: AgentRuntimeContext
    ) -> None:
        self.health.observe_success(self._route_key(route=route, context=context))

    def observe_failure(
        self,
        *,
        route: ModelRouteEntry,
        context: AgentRuntimeContext,
        failure_class: ModelFailureClass,
    ) -> None:
        self.health.observe_failure(
            self._route_key(route=route, context=context), failure_class
        )

    def _route_key(
        self, *, route: ModelRouteEntry, context: AgentRuntimeContext
    ) -> ProviderCircuitKey:
        return self._key_for(
            provider=route.provider,
            deployment_id=route.deployment_id,
            region=route.region,
            credential_mode=route.credential_mode,
            context=context,
        )

    @staticmethod
    def _credential_mode(
        *,
        provider: str,
        supported: frozenset[ModelCredentialMode],
        context: AgentRuntimeContext,
    ) -> ModelCredentialMode:
        if provider in context.provider_keys and ModelCredentialMode.BYOK in supported:
            return ModelCredentialMode.BYOK
        if ModelCredentialMode.DEPLOYMENT in supported:
            return ModelCredentialMode.DEPLOYMENT
        return min(supported, key=lambda item: item.value)

    @staticmethod
    def _key_for(
        *,
        provider: str,
        deployment_id: str,
        region: str,
        credential_mode: ModelCredentialMode,
        context: AgentRuntimeContext,
    ) -> ProviderCircuitKey:
        fingerprint = None
        if credential_mode is ModelCredentialMode.BYOK:
            credential = context.provider_keys.get(provider)
            if not credential:
                # F10 authority will independently reject an unavailable BYOK
                # route; retain a non-secret impossible partition meanwhile.
                credential = "unavailable"
            fingerprint = "sha256:" + canonical_json_sha256(
                {
                    "schema_revision": "provider-circuit-byok.v1",
                    "provider": provider,
                    "credential": credential,
                }
            )
        return ProviderCircuitKey(
            provider=provider,
            deployment_id=deployment_id,
            region=region,
            credential_mode=credential_mode,
            credential_fingerprint=fingerprint,
        )

    @staticmethod
    def _stricter(
        first: ModelDeploymentHealth, second: ModelDeploymentHealth
    ) -> ModelDeploymentHealth:
        rank = {
            ModelDeploymentHealth.AVAILABLE: 0,
            ModelDeploymentHealth.DEGRADED: 1,
            ModelDeploymentHealth.UNAVAILABLE: 2,
            ModelDeploymentHealth.OPEN_CIRCUIT: 3,
        }
        return first if rank[first] >= rank[second] else second


__all__ = ("ProviderCircuitHealthRegistry",)
