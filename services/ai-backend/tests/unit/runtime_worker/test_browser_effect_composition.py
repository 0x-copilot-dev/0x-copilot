"""Production A4→A5 browser composition and no-bypass proofs."""

from __future__ import annotations

from dataclasses import dataclass, field

from agent_runtime.capabilities.browser.contracts import (
    BrowserActionKind,
    BrowserActionPlan,
    BrowserApplyOutcome,
    BrowserApplyReceipt,
    BrowserPrecondition,
    BrowserPrepareResult,
)
from agent_runtime.capabilities.browser.effect_adapter import BrowserEffectStageAdapter
from agent_runtime.effects.contracts import EffectActorIdentity, EffectPolicySnapshot
from agent_runtime.effects.coordinator import EffectReconcileCommand
from agent_runtime.execution.contracts import AgentRuntimeContext, ModelConfig
from agent_runtime.settings import RuntimeSettings
from agent_runtime.surfaces_v2.entities import OperationRequest
from agent_runtime.surfaces_v2.ledger_models import (
    EffectActor,
    EffectDecisionKind,
    EffectPolicy,
    Producer,
)
from runtime_adapters.artifact_references import InMemoryArtifactReferenceStore
from runtime_adapters.in_memory.artifact_blob_store import InMemoryArtifactBlobStore
from runtime_adapters.in_memory.artifact_publication import (
    InMemoryArtifactPublicationCoordinator,
)
from runtime_adapters.in_memory.effect_claim_store import InMemoryEffectClaimStore
from runtime_adapters.in_memory.runtime_api_store import InMemoryRuntimeApiStore
from runtime_api.schemas import RunRecord
from runtime_worker.handlers.effect_commit import RuntimeEffectCommitHandler
from runtime_worker.handlers.run import RuntimeRunHandler
from runtime_worker.mcp_operation_storage import RuntimeMcpEffectCoordinatorFactory

_OPERATION_ID = "op_00000000-0000-4000-8000-000000000001"


def _settings() -> RuntimeSettings:
    return RuntimeSettings.load(
        environ={
            "SURFACES_V2": "true",
            "OPERATION_GATEWAY_MODE": "enforce",
        }
    )


def _run() -> RunRecord:
    context = AgentRuntimeContext(
        user_id="user-browser",
        org_id="org-browser",
        roles={"employee"},
        model_profile=ModelConfig(
            provider="openai",
            model_name="gpt-test",
            max_input_tokens=4096,
            timeout_seconds=30,
            temperature=0,
        ),
        run_id="run-browser",
        trace_id="trace-browser",
    )
    return RunRecord(
        run_id="run-browser",
        conversation_id="conv-browser",
        org_id="org-browser",
        user_id="user-browser",
        user_message_id="msg-browser",
        trace_id="trace-browser",
        model_provider="openai",
        model_name="gpt-test",
        runtime_context=context,
    )


def _plan() -> BrowserActionPlan:
    precondition = BrowserPrecondition(
        page_generation=4,
        origin="https://example.com",
        element_fingerprint="a" * 64,
        form_fingerprint="d" * 64,
        form_payload_digest="e" * 64,
    )
    return BrowserActionPlan(
        session_ref="browser-session://ses_exact",
        page_ref="browser-page://pg_exact",
        origin="https://example.com",
        top_level_origin="https://example.com",
        action_kind=BrowserActionKind.SUBMIT,
        element_ref="e4_2",
        element_fingerprint="a" * 64,
        form_fingerprint="d" * 64,
        form_payload_digest="e" * 64,
        form_action_url="https://example.com/send",
        method="POST",
        canonical_fields_ref=f"operation://{_OPERATION_ID}/args",
        fields_digest="b" * 64,
        precondition=precondition,
        precondition_digest=precondition.digest,
        user_visible_summary="Review browser submit on https://example.com.",
    )


def _request() -> OperationRequest:
    return OperationRequest(
        operation_id=_OPERATION_ID,
        run_id="run-browser",
        producer=Producer.MODEL,
        capability="desktop-browser",
        op="browser_submit",
        canonical_args_ref=f"operation://{_OPERATION_ID}/args",
        args_digest="b" * 64,
        requested_at="2026-07-25T00:00:00+00:00",
    )


@dataclass
class _Bridge:
    apply_outcome: BrowserApplyOutcome = BrowserApplyOutcome.APPLIED
    prepare_calls: list[BrowserActionPlan] = field(default_factory=list)
    apply_calls: list[str] = field(default_factory=list)
    reconcile_calls: list[str] = field(default_factory=list)

    async def prepare_action(self, plan: BrowserActionPlan) -> BrowserPrepareResult:
        self.prepare_calls.append(plan)
        return BrowserPrepareResult(
            prepared_ref="browser-prepared://ses_exact/one",
            observed_precondition_digest=plan.precondition_digest,
        )

    async def apply_prepared(self, prepared_ref: str) -> BrowserApplyReceipt:
        self.apply_calls.append(prepared_ref)
        return BrowserApplyReceipt(
            outcome=self.apply_outcome,
            receipt_ref="browser-receipt://ses_exact/one",
            result_digest="c" * 64,
        )

    async def reconcile_action(self, prepared_ref: str) -> BrowserApplyReceipt:
        self.reconcile_calls.append(prepared_ref)
        return BrowserApplyReceipt(
            outcome=BrowserApplyOutcome.INDETERMINATE,
        )


async def _approved_fixture(
    bridge: _Bridge,
) -> tuple[
    InMemoryRuntimeApiStore,
    InMemoryEffectClaimStore,
    RuntimeMcpEffectCoordinatorFactory,
]:
    store = InMemoryRuntimeApiStore()
    run = _run()
    store.runs[run.run_id] = run
    store.events_by_run[run.run_id] = []
    coordinator = InMemoryArtifactPublicationCoordinator()
    blobs = InMemoryArtifactBlobStore(coordinator)
    references = InMemoryArtifactReferenceStore(coordinator)
    run_handler = RuntimeRunHandler(
        persistence=store,
        event_store=store,
        settings=_settings(),
        queue=store,
        artifact_blob_store=blobs,
        artifact_reference_store=references,
    )
    services = run_handler._build_mcp_operation_gateway_services(run)
    assert services is not None
    assert services.browser_plans is not None
    stage_adapter = BrowserEffectStageAdapter(
        plans=services.browser_plans,  # type: ignore[arg-type]
        stager=services.stager,
        scope=services.stage_scope,
        actor=services.stage_author,
        policy_snapshot=EffectPolicySnapshot(
            snapshot_ref="policy://runs/run-browser/desktop-browser",
            descriptor_known=True,
            capability_policy=EffectPolicy.REQUIRE,
            user_policy=EffectPolicy.REQUIRE,
            sensitive_target=True,
        ),
    )
    proposal = await stage_adapter.stage(request=_request(), plan=_plan())
    state = await services.stager.get_state(
        scope=services.stage_scope,
        stage_id=proposal.stage_id,
    )
    await services.stager.decide(
        scope=services.stage_scope,
        stage_id=state.stage_id,
        revision=state.current_revision.revision,
        decision=EffectDecisionKind.APPROVE,
        proposal_digest=state.current_revision.proposal_digest,
        target_digest=state.target_digest,
        actor=EffectActorIdentity(
            actor=EffectActor.USER,
            principal_ref=services.stage_scope.owner_ref,
        ),
        idempotency_key="browser-approve-exact",
    )
    claims = InMemoryEffectClaimStore()
    factory = RuntimeMcpEffectCoordinatorFactory(
        event_producer=run_handler.event_producer,
        claims=claims,
        blobs=blobs,
        references=references,
        dependencies_factory=object(),
        timeout_seconds=10,
        browser_bridge=bridge,
    )
    return store, claims, factory


async def test_approved_exact_plan_applies_once_and_redelivery_never_replays() -> None:
    bridge = _Bridge()
    store, _claims, factory = await _approved_fixture(bridge)
    assert len(store.effect_commit_commands) == 1
    handler = RuntimeEffectCommitHandler(
        persistence=store,
        coordinator_factory=factory,
    )

    await handler.handle(store.effect_commit_commands[0])
    await handler.handle(store.effect_commit_commands[0])

    # A5's durable claim makes command redelivery inert before the executor:
    # neither prepare nor apply is repeated.
    assert bridge.prepare_calls == [_plan()]
    assert bridge.apply_calls == ["browser-prepared://ses_exact/one"]
    events = store.events_by_run["run-browser"]
    applied = [event for event in events if event.event_type.value == "effect.applied"]
    assert len(applied) == 1
    assert applied[0].payload["outcome"] == "applied"
    assert applied[0].payload["result_digest"] == "c" * 64
    assert "receipt_ref" not in applied[0].payload


async def test_indeterminate_submission_reconciles_observationally_without_apply() -> (
    None
):
    bridge = _Bridge(apply_outcome=BrowserApplyOutcome.INDETERMINATE)
    store, claims, factory = await _approved_fixture(bridge)
    handler = RuntimeEffectCommitHandler(
        persistence=store,
        coordinator_factory=factory,
    )
    await handler.handle(store.effect_commit_commands[0])
    incomplete = await claims.list_incomplete(org_id="org-browser")
    assert len(incomplete) == 1

    coordinator = factory.for_run(run=_run())
    await coordinator.reconcile(
        EffectReconcileCommand(
            org_id="org-browser",
            claim_id=incomplete[0].claim_id,
        )
    )

    assert bridge.apply_calls == ["browser-prepared://ses_exact/one"]
    assert bridge.reconcile_calls == ["browser-prepared://ses_exact/one"]
    assert len(bridge.apply_calls) == 1
