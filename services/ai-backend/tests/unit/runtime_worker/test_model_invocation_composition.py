"""Worker composition coverage for the F10 production binding seam."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from agent_runtime.api.model_invocation_catalog import ModelEndpointAuthority
from agent_runtime.control_plane.context import RunControlBinding
from agent_runtime.control_plane.contracts import RunControlSnapshot, RunPolicyRevisions
from agent_runtime.control_plane.feature_modes import FeatureMode, FeatureModeSet
from agent_runtime.execution.contracts import AgentRuntimeContext, ModelConfig
from agent_runtime.execution.model_invocation.contracts import (
    ModelCredentialMode,
    ModelFailureClass,
    ModelFallbackPolicy,
    ModelInvocationBudget,
    ModelRouteEntry,
    ModelRoutePlan,
)
from agent_runtime.execution.model_invocation.runtime import ModelInvocationMiddleware
from agent_runtime.observability.context_occupancy_recorder import ContextOccupancySink
from agent_runtime.settings import RuntimeSettings
from runtime_adapters.in_memory import InMemoryRuntimeApiStore
from runtime_api.schemas import AgentRunStatus, RunRecord, RuntimeApiEventType
from runtime_api.schemas.runs import ModelCatalogItem
from runtime_worker.model_invocation_composition import (
    ModelInvocationCompositionFacts,
    ModelInvocationWorkerComposer,
)

_SUBJECT = "a" * 64


def _settings() -> RuntimeSettings:
    return RuntimeSettings.load(
        environ={
            "OPENAI_API_KEY": "test-key",
            "RUNTIME_DEFAULT_PROVIDER": "openai",
            "RUNTIME_DEFAULT_MODEL": "gpt-5-mini",
        }
    )


def _context() -> AgentRuntimeContext:
    return AgentRuntimeContext(
        user_id="user-1",
        org_id="org-1",
        roles=frozenset({"member"}),
        model_profile=ModelConfig(
            provider="openai",
            model_name="gpt-5-mini",
            max_input_tokens=16_000,
            max_output_tokens=1_000,
            timeout_seconds=30,
            temperature=0,
            supports_streaming=True,
        ),
        request_id="request-1",
        run_id="run-1",
        trace_id="trace-1",
        provider_keys={"openai": "byok-key-never-persisted"},
        provider_endpoints={"openai": "https://private.example/v1"},
    )


def _run(context: AgentRuntimeContext) -> RunRecord:
    return RunRecord(
        run_id="run-1",
        conversation_id="conversation-1",
        org_id="org-1",
        user_id="user-1",
        user_message_id="message-1",
        trace_id="trace-1",
        status=AgentRunStatus.RUNNING,
        model_provider="openai",
        model_name="gpt-5-mini",
        runtime_context=context,
        created_at=datetime(2026, 7, 28, tzinfo=UTC),
    )


def _control(mode: FeatureMode) -> RunControlBinding:
    revisions = {field: "revision-1" for field in RunPolicyRevisions.model_fields}
    snapshot = RunControlSnapshot.create(
        run_id="run-1",
        conversation_id="conversation-1",
        subject_fingerprint=_SUBJECT,
        deployment_profile="single_user_desktop",
        harness_variant_ref="harness-1",
        task_policy_selection_ref="task-policy-1",
        policy_revisions=RunPolicyRevisions.model_validate(revisions),
        feature_modes=FeatureModeSet(f10=mode),
        budget_envelope_ref=f"budget://test/sha256/{_SUBJECT}",
        assignment_revision="assignment-1",
        snapshot_id="snapshot-1",
        created_at=datetime(2026, 7, 28, tzinfo=UTC),
    )
    return RunControlBinding(
        snapshot=snapshot,
        effective_modes=FeatureModeSet(f10=mode),
        decisions=(),
    )


def _facts(*_args: object) -> ModelInvocationCompositionFacts:
    item = ModelCatalogItem(
        id="openai:gpt-5-mini",
        provider="openai",
        model_name="gpt-5-mini",
        name="GPT 5 mini",
        configured=True,
        enabled=True,
        supports_streaming=True,
        context_window=16_000,
        max_output_tokens=1_000,
    )
    return ModelInvocationCompositionFacts(
        catalog_items=(item,),
        endpoints=(
            ModelEndpointAuthority.from_revision(
                provider="openai",
                endpoint_identity_revision="test-endpoint-v1",
                credential_modes=frozenset({ModelCredentialMode.BYOK}),
            ),
        ),
    )


@pytest.mark.asyncio
async def test_shadow_authority_failure_keeps_primary_dispatch_unbound() -> None:
    store = InMemoryRuntimeApiStore()
    composer = ModelInvocationWorkerComposer(
        settings=_settings(),
        persistence=store,
        event_store=store,
        journal=None,
        facts_factory=_facts,
    )

    assert (
        await composer.compose(
            run=_run(_context()),
            context=_context(),
            control=_control(FeatureMode.SHADOW),
        )
        is None
    )


@pytest.mark.asyncio
async def test_enforce_missing_journal_fails_before_dispatch() -> None:
    store = InMemoryRuntimeApiStore()
    composer = ModelInvocationWorkerComposer(
        settings=_settings(),
        persistence=store,
        event_store=store,
        journal=None,
        facts_factory=_facts,
    )

    with pytest.raises(Exception, match="journal"):
        await composer.compose(
            run=_run(_context()),
            context=_context(),
            control=_control(FeatureMode.ENFORCE),
        )


@pytest.mark.asyncio
async def test_the_context_occupancy_sink_is_wired_to_the_persistence_store() -> None:
    """The Context Occupancy Ledger's only writer, on the only production path.

    ``ModelInvocationRuntimeBinding.context_occupancy_store`` defaults to
    ``None`` and this composer is the sole place the binding is constructed
    outside tests. With the field left defaulted, ``_persist_occupancy``
    returned before ``finalize`` on every model call of every deployment: no
    ``provider_input_tokens``, no ``cached_input_tokens``, no signed
    ``unattributed_delta``, no row — so the whole reconciliation half of the
    design (§3.3, §4.4, §6.6) never ran on real traffic and the shipped read API
    could only ever answer with an empty series. The seam's own tests inject a
    sink directly into the binding, so only an assertion here can catch it.
    """

    store = InMemoryRuntimeApiStore()
    composer = ModelInvocationWorkerComposer(
        settings=_settings(),
        persistence=store,
        event_store=store,
        journal=object(),  # type: ignore[arg-type]
        facts_factory=_facts,
    )

    composed = await composer.compose(
        run=_run(_context()), context=_context(), control=_control(FeatureMode.SHADOW)
    )

    assert composed is not None
    assert composed.binding.context_occupancy_store is store
    # Structural, not nominal: the seam types the field as a one-method protocol,
    # so the store has to actually satisfy it rather than merely be passed.
    assert isinstance(composed.binding.context_occupancy_store, ContextOccupancySink)


@pytest.mark.asyncio
async def test_effect_tracker_blocks_later_retry_without_event_polling() -> None:
    store = InMemoryRuntimeApiStore()
    journal = object()
    composer = ModelInvocationWorkerComposer(
        settings=_settings(),
        persistence=store,
        event_store=store,
        journal=journal,  # type: ignore[arg-type]
        facts_factory=_facts,
    )
    composed = await composer.compose(
        run=_run(_context()), context=_context(), control=_control(FeatureMode.SHADOW)
    )
    assert composed is not None
    assert not composed.binding.external_effect_observed()

    # This is called by the canonical operation/effect event append closure
    # after its immutable event has persisted.  No model-call-time polling is
    # needed to narrow a later retry.
    composed.effect_tracker.mark_event(RuntimeApiEventType.EFFECT_INDETERMINATE)
    assert composed.binding.external_effect_observed()
    route = ModelRouteEntry(
        deployment_id="model-deployment:test",
        deployment_revision="deployment-v1",
        descriptor_revision="descriptor-v1",
        endpoint_ref="endpoint_0123456789abcdef0123456789abcdef",
        endpoint_revision="endpoint-v1",
        provider="openai",
        model_name="gpt-5-mini",
        region="default",
        credential_mode=ModelCredentialMode.BYOK,
        price_revision="price-v1",
        max_input_tokens=16_000,
        max_output_tokens=1_000,
    )
    assert not ModelInvocationMiddleware._can_retry(
        ModelFailureClass.PRE_DISPATCH_TRANSIENT,
        type(
            "State",
            (),
            {"visible_output_observed": False},
        )(),
        composed.binding,
        route,
        ModelRoutePlan.create(
            routes=(route,),
            exclusions=(),
            fallback_policy=ModelFallbackPolicy.NONE,
            budget=ModelInvocationBudget(),
        ),
    )
