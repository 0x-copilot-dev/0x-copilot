"""Worker-owned production composition for the F10 model-call seam.

The domain middleware intentionally knows nothing about settings, the picker
catalog, BYOK hydration, or an SDK model constructor.  This module is the only
place the worker turns those already-verified run facts into a per-run binding.
It retains neither plaintext keys nor endpoint URLs: both remain only in the
hydrated context captured by the ephemeral route-model resolver.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
import logging

from langchain_core.language_models import BaseChatModel

from agent_runtime.api.model_catalog import ModelCatalog
from agent_runtime.api.model_enablement import ModelEnablementResolver
from agent_runtime.api.model_invocation_catalog import (
    ModelEndpointAuthority,
    ModelHealthAuthority,
    ModelInvocationAuthorityAdapter,
    ModelInvocationAuthorityAdapterInput,
    ModelQualificationAuthority,
)
from agent_runtime.api.ports import EventStorePort, PersistencePort
from agent_runtime.control_plane.context import RunControlBinding
from agent_runtime.control_plane.feature_modes import FeatureMode
from agent_runtime.execution.contracts import (
    AgentRuntimeContext,
    ModelConfig,
    RuntimeErrorCode,
)
from agent_runtime.execution.deep_agent_builder import build_chat_model
from agent_runtime.execution.errors import AgentRuntimeError
from agent_runtime.execution.model_invocation.contracts import (
    ModelFallbackPolicy,
    ModelInvocationBudget,
    ModelRouteEntry,
)
from agent_runtime.execution.model_invocation.journal import ModelInvocationStorePort
from agent_runtime.execution.model_invocation.runtime import (
    EphemeralRouteModelResolverPort,
    ModelInvocationRuntimeBinding,
)
from agent_runtime.observability.context_occupancy_binding import (
    ContextOccupancyRuntimeBinding,
)
from agent_runtime.execution.provider_kwargs import (
    user_policy_model_kwargs,
    workspace_model_kwargs,
)
from agent_runtime.execution.providers.model_failure_adapters import (
    ProviderFailureAdapterRegistry,
)
from agent_runtime.settings import RuntimeSettings
from runtime_api.schemas import RunRecord, RuntimeApiEventType
from runtime_api.schemas.runs import ModelCatalogItem
from runtime_api.schemas.workspace_defaults import WorkspaceDefaultsRecord
from runtime_worker.model_invocation_circuit import ProviderCircuitHealthRegistry


_OCCUPANCY_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ModelInvocationCompositionFacts:
    """Immutable, non-secret authority inputs for one worker attempt."""

    catalog_items: tuple[ModelCatalogItem, ...]
    endpoints: tuple[ModelEndpointAuthority, ...] = ()
    qualifications: tuple[ModelQualificationAuthority, ...] = ()
    health: tuple[ModelHealthAuthority, ...] = ()
    deployment_credential_providers: frozenset[str] = frozenset()
    task_family: str = "agent_run"
    budget: ModelInvocationBudget = field(default_factory=ModelInvocationBudget)


FactsFactory = Callable[
    [
        AgentRuntimeContext,
        RunControlBinding,
        WorkspaceDefaultsRecord | None,
    ],
    ModelInvocationCompositionFacts,
]


@dataclass(slots=True)
class ModelInvocationEffectTracker:
    """Monotonic run-local proof that an external effect has completed."""

    observed: bool = False

    def mark_event(self, event_type: RuntimeApiEventType) -> None:
        if event_type in {
            RuntimeApiEventType.EFFECT_APPLIED,
            RuntimeApiEventType.EFFECT_INDETERMINATE,
        }:
            self.observed = True


@dataclass(frozen=True, slots=True)
class ComposedModelInvocationRuntime:
    """The installed binding plus its canonical external-effect observer."""

    binding: ModelInvocationRuntimeBinding
    effect_tracker: ModelInvocationEffectTracker


class _RouteModelResolver(EphemeralRouteModelResolverPort):
    """Build an alternate only from a route already verified by F10 authority."""

    def __init__(self, context: AgentRuntimeContext) -> None:
        # A deep copy keeps each run's BYOK material and endpoint map isolated
        # from subsequent queue claims.  It is never serialized or logged.
        self._context = context.model_copy(deep=True)

    def resolve(self, route: ModelRouteEntry) -> BaseChatModel:
        profile = self._context.model_profile
        config = profile.model_copy(
            update={"provider": route.provider, "model_name": route.model_name}
        )
        if not isinstance(config, ModelConfig):  # defensive Pydantic boundary
            raise TypeError("verified route did not produce a model configuration")
        kwargs = workspace_model_kwargs(
            provider=config.provider,
            workspace_behavior_overrides=self._context.workspace_behavior_overrides
            or None,
        )
        kwargs.update(
            user_policy_model_kwargs(
                provider=config.provider,
                user_policies_json=self._context.user_policies_json or None,
                provider_keys=self._context.provider_keys or None,
                provider_endpoints=self._context.provider_endpoints or None,
            )
        )
        return build_chat_model(config, extra_kwargs=kwargs or None)


class ModelInvocationWorkerComposer:
    """Compose one F10 binding after worker claim and BYOK re-hydration."""

    def __init__(
        self,
        *,
        settings: RuntimeSettings,
        persistence: PersistencePort,
        event_store: EventStorePort,
        journal: ModelInvocationStorePort | None,
        facts_factory: FactsFactory | None = None,
        authority_adapter: ModelInvocationAuthorityAdapter | None = None,
        failure_adapters: ProviderFailureAdapterRegistry | None = None,
        circuit_health: ProviderCircuitHealthRegistry | None = None,
    ) -> None:
        self._settings = settings
        self._persistence = persistence
        self._event_store = event_store
        self._journal = journal
        self._facts_factory = facts_factory or self._default_facts
        self._authority_adapter = authority_adapter or ModelInvocationAuthorityAdapter()
        self._failure_adapters = (
            failure_adapters or ProviderFailureAdapterRegistry.defaults()
        )
        self._circuit_health = circuit_health

    async def compose(
        self,
        *,
        run: RunRecord,
        context: AgentRuntimeContext,
        control: RunControlBinding,
    ) -> ComposedModelInvocationRuntime | None:
        """Return a binding, or cleanly leave F10 OFF.

        Shadow retains the primary dispatch path because the release decision
        gates recovery in the middleware; it still receives a journal binding
        so its observations are durable.  Enforce refuses model construction
        when the required journal or authority facts cannot be composed.
        """

        release = control.model_reliability
        if release.effective_f10_mode is FeatureMode.OFF:
            return None
        if self._journal is None:
            return self._missing("model invocation journal is not composed", release)
        try:
            defaults = await self._workspace_defaults(run)
            facts = self._facts_factory(context, control, defaults)
            # Freeze a per-run deep copy before any SDK construction.  The
            # authority input excludes it from serialization, so BYOK keys and
            # endpoint URLs cannot enter the event journal.
            frozen_context = context.model_copy(deep=True)
            tracker = ModelInvocationEffectTracker(
                observed=await self._external_effect_observed(run)
            )
            binding = ModelInvocationRuntimeBinding(
                authority_adapter=self._authority_adapter,
                authority_input_factory=lambda request_digest: (
                    ModelInvocationAuthorityAdapterInput(
                        runtime_context=frozen_context,
                        catalog_items=facts.catalog_items,
                        endpoints=facts.endpoints,
                        qualifications=facts.qualifications,
                        # Take an immutable health view immediately before each
                        # F10 authority plan. Shadow still observes the same
                        # shared reducer but deliberately preserves the static
                        # catalog, while enforce may exclude an open circuit.
                        health=(
                            self._circuit_health.authority_facts(
                                facts=facts,
                                context=frozen_context,
                            )
                            if self._circuit_health is not None
                            and release.circuit_influence_enabled
                            else facts.health
                        ),
                        deployment_credential_providers=(
                            facts.deployment_credential_providers
                        ),
                        task_family=facts.task_family,
                        fallback_policy=self._fallback_policy(release),
                        budget=facts.budget,
                        purpose="agent_model_call",
                        request_digest=request_digest,
                    )
                ),
                journal=self._journal,
                # Context Occupancy Ledger sink (design §5). The store is the
                # same ``PersistencePort`` this composer already holds, and it
                # already implements ``append_context_occupancy``; leaving the
                # field defaulted meant the *only* production construction of
                # this binding never wired one, so ``_persist_occupancy``
                # returned before ``finalize`` on every model call of every
                # deployment. Every reconciliation field in §3.3 / §4.4 / §6.6 —
                # ``provider_input_tokens``, the signed ``unattributed_delta``,
                # ``cached_input_tokens`` — was therefore never computed on the
                # real path, and the read API §7 ships could only ever answer
                # with an empty series. Wiring it here, in the composer that owns
                # every other durable port, is what makes the ledger a ledger.
                context_occupancy_store=self._persistence,
                route_model_resolver=_RouteModelResolver(frozen_context),
                release=release,
                org_id=run.org_id,
                subject_fingerprint=control.snapshot.subject_fingerprint,
                trace_id=run.trace_id,
                failure_adapters=self._failure_adapters,
                external_effect_observed=lambda: tracker.observed,
                circuit_success_observer=(
                    (
                        lambda route: self._circuit_health.observe_success(
                            route=route,
                            context=frozen_context,
                        )
                    )
                    if self._circuit_health is not None
                    and release.circuit_influence.observes
                    else None
                ),
                circuit_failure_observer=(
                    (
                        lambda route, failure_class: (
                            self._circuit_health.observe_failure(
                                route=route,
                                context=frozen_context,
                                failure_class=failure_class,
                            )
                        )
                    )
                    if self._circuit_health is not None
                    and release.circuit_influence.observes
                    else None
                ),
            )
            return ComposedModelInvocationRuntime(
                binding=binding,
                effect_tracker=tracker,
            )
        except Exception as exc:
            if release.effective_f10_mode is FeatureMode.ENFORCE:
                raise AgentRuntimeError(
                    RuntimeErrorCode.CONFIGURATION_ERROR,
                    "F10 model reliability authority could not be composed.",
                    retryable=False,
                    correlation_id=run.trace_id,
                ) from exc
            return None

    def compose_context_occupancy(
        self,
        *,
        run: RunRecord,
        context: AgentRuntimeContext,
    ) -> ContextOccupancyRuntimeBinding | None:
        """Compose the occupancy sink for one run, independently of F10.

        Deliberately a separate method from :meth:`compose`, taking no
        ``RunControlBinding`` and reading no release decision. The Context
        Occupancy Ledger shipped inert precisely because its sink was a field on
        the F10 binding, and ``compose`` returns ``None`` before it reaches that
        field whenever ``effective_f10_mode`` is ``OFF`` — the default. A method
        that has no feature mode in scope cannot regress that way again.

        It lives on this composer anyway, because this is where every other
        durable port for a run is wired and ``self._persistence`` — which already
        implements ``append_context_occupancy`` — is the sink.

        ``None`` only if the profile cannot describe a model, which the run's own
        validation makes unreachable; it is returned rather than raised because
        an observability lane must not be able to refuse a run.
        """

        try:
            profile = context.model_profile
            return ContextOccupancyRuntimeBinding(
                sink=self._persistence,
                org_id=run.org_id,
                provider=profile.provider,
                model_family=profile.model_name,
                context_window_tokens=profile.max_input_tokens,
            )
        except Exception:  # noqa: BLE001 — measurement never fails a run (§6.4)
            _OCCUPANCY_LOGGER.warning(
                "Could not compose the context occupancy binding for run %s; "
                "this run will dispatch without measuring occupancy.",
                run.run_id,
                exc_info=True,
            )
            return None

    def _missing(
        self, message: str, release: object
    ) -> ComposedModelInvocationRuntime | None:
        if getattr(release, "effective_f10_mode", None) is FeatureMode.ENFORCE:
            raise AgentRuntimeError(
                RuntimeErrorCode.CONFIGURATION_ERROR,
                f"F10 {message}.",
                retryable=False,
            )
        return None

    def _default_facts(
        self,
        context: AgentRuntimeContext,
        _control: RunControlBinding,
        defaults: WorkspaceDefaultsRecord | None,
    ) -> ModelInvocationCompositionFacts:
        providers = frozenset(context.provider_keys)
        enabled_catalog = ModelEnablementResolver.apply(
            ModelCatalog.build(self._settings, user_key_providers=providers),
            enabled_models=defaults.enabled_models if defaults is not None else None,
            default_model=defaults.default_model if defaults is not None else None,
        )
        # The chosen profile was validated at run creation and persisted on the
        # run. A later workspace-picker curation cannot retroactively turn an
        # already queued primary into an un-routable shadow observation. This
        # retention is deliberately exact (provider + model), never an inferred
        # alternate; all other entries remain governed by enablement above.
        selected = (
            context.model_profile.provider,
            context.model_profile.model_name,
        )
        catalog = tuple(
            item.model_copy(update={"enabled": True})
            if (item.provider, item.model_name) == selected
            else item
            for item in enabled_catalog
        )
        deployment_providers = frozenset(
            item.provider
            for item in catalog
            if self._deployment_credential_available(item.provider)
        )
        return ModelInvocationCompositionFacts(
            catalog_items=catalog,
            deployment_credential_providers=deployment_providers,
        )

    async def _workspace_defaults(
        self, run: RunRecord
    ) -> WorkspaceDefaultsRecord | None:
        """Load workspace curation once at the verified worker boundary."""

        return await self._persistence.get_workspace_defaults(org_id=run.org_id)

    def _deployment_credential_available(self, provider: str) -> bool:
        try:
            return self._settings.provider_settings(provider).is_configured
        except ValueError:
            return False

    @staticmethod
    def _fallback_policy(release: object) -> ModelFallbackPolicy:
        # SHADOW plans candidates for observations, while the runtime release
        # gate leaves dispatch primary-only.  No alternate is inferred from the
        # picker: qualification still decides which entries are eligible.
        if getattr(release, "equivalent_route", None) is not None and getattr(
            release.equivalent_route, "observes", False
        ):
            return ModelFallbackPolicy.QUALIFIED_EQUIVALENT
        if getattr(release, "alternate_route", None) is not None and getattr(
            release.alternate_route, "observes", False
        ):
            return ModelFallbackPolicy.SAME_MODEL
        return ModelFallbackPolicy.NONE

    async def _external_effect_observed(self, run: RunRecord) -> bool:
        """Snapshot durable effect completion conservatively before dispatch."""

        try:
            events = await self._event_store.list_events_after(
                org_id=run.org_id, run_id=run.run_id, after_sequence=0
            )
        except Exception:  # noqa: BLE001 - conservative safety-read fallback
            # A failed safety read must narrow recovery, never broaden it.
            return True
        terminal_effects = {
            RuntimeApiEventType.EFFECT_APPLIED,
            RuntimeApiEventType.EFFECT_INDETERMINATE,
        }
        return any(event.event_type in terminal_effects for event in events)


__all__ = (
    "ComposedModelInvocationRuntime",
    "ModelInvocationCompositionFacts",
    "ModelInvocationEffectTracker",
    "ModelInvocationWorkerComposer",
)
