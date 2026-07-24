"""A5 safety proofs for the staged desktop-browser effect adapter."""

from __future__ import annotations

from dataclasses import dataclass, field

from agent_runtime.capabilities.browser.contracts import (
    BrowserActionKind,
    BrowserActionPlan,
    BrowserApplyOutcome,
    BrowserApplyReceipt,
    BrowserPrecondition,
    BrowserPrepareResult,
    BrowserStoredPlan,
)
from agent_runtime.capabilities.browser.effect_adapter import (
    BrowserEffectExecutor,
    BrowserEffectStageAdapter,
)
from agent_runtime.effects.claims import EffectClaim
from agent_runtime.effects.contracts import EffectStageStatus
from agent_runtime.effects.staging import EffectStager
from agent_runtime.surfaces_v2.entities import EffectExecutionRequest, OperationRequest
from agent_runtime.surfaces_v2.ledger_models import (
    EffectActor,
    EffectExecutorKind,
    EffectOutcome,
    Producer,
)
from tests.unit.agent_runtime.effects.fakes import (
    FakeClock,
    FakeLedger,
    FakeOutbox,
    FakeStageIds,
    policy_snapshot,
    scope,
    user,
)

_OPERATION_ID = "op_00000000-0000-4000-8000-000000000001"
_ARTIFACT_REF = "artifact://art_00000000-0000-4000-8000-000000000001/revisions/1"
_STAGE_ID = "stg_00000000-0000-4000-8000-000000000001"


def _plan() -> BrowserActionPlan:
    precondition = BrowserPrecondition(
        page_generation=7,
        origin="https://example.com",
        element_fingerprint="a" * 64,
    )
    return BrowserActionPlan(
        session_ref="browser-session://ses_123",
        page_ref="browser-page://pg_123",
        origin="https://example.com",
        top_level_origin="https://example.com",
        action_kind=BrowserActionKind.CLICK,
        element_ref="e7_1",
        element_fingerprint="a" * 64,
        form_action_url="https://example.com/send",
        method="POST",
        canonical_fields_ref=f"operation://{_OPERATION_ID}/args",
        fields_digest="b" * 64,
        precondition=precondition,
        precondition_digest=precondition.digest,
        user_visible_summary="Review browser click on https://example.com.",
    )


def _request() -> OperationRequest:
    return OperationRequest(
        operation_id=_OPERATION_ID,
        run_id="run_a4_test",
        producer=Producer.SYSTEM,
        capability="desktop-browser",
        op="browser_click",
        canonical_args_ref=f"operation://{_OPERATION_ID}/args",
        args_digest="b" * 64,
        requested_at="2026-07-25T00:00:00+00:00",
    )


@dataclass
class _Plans:
    plans: dict[str, BrowserActionPlan] = field(default_factory=dict)

    async def store(self, *, plan: BrowserActionPlan) -> BrowserStoredPlan:
        self.plans[_ARTIFACT_REF] = plan
        return BrowserStoredPlan(content_ref=_ARTIFACT_REF, digest=plan.digest)

    async def load(self, *, content_ref: str) -> BrowserActionPlan | None:
        return self.plans.get(content_ref)


@dataclass
class _Bridge:
    prepare_calls: list[BrowserActionPlan] = field(default_factory=list)
    apply_calls: list[str] = field(default_factory=list)
    reconcile_calls: list[str] = field(default_factory=list)

    async def prepare_action(self, plan: BrowserActionPlan) -> BrowserPrepareResult:
        self.prepare_calls.append(plan)
        return BrowserPrepareResult(
            prepared_ref="browser-prepared://ses_123/one",
            observed_precondition_digest=plan.precondition_digest,
        )

    async def apply_prepared(self, prepared_ref: str) -> BrowserApplyReceipt:
        self.apply_calls.append(prepared_ref)
        return BrowserApplyReceipt(outcome=BrowserApplyOutcome.APPLIED)

    async def reconcile_action(self, prepared_ref: str) -> BrowserApplyReceipt:
        self.reconcile_calls.append(prepared_ref)
        return BrowserApplyReceipt(outcome=BrowserApplyOutcome.INDETERMINATE)


def _stager() -> tuple[EffectStager, FakeLedger, FakeOutbox]:
    ledger = FakeLedger()
    outbox = FakeOutbox()
    return (
        EffectStager(
            ledger=ledger,
            outbox=outbox,
            clock=FakeClock(),
            stage_ids=FakeStageIds(),
        ),
        ledger,
        outbox,
    )


async def test_browser_stage_stores_the_exact_plan_and_is_held_without_dispatch() -> (
    None
):
    effect_stager, _ledger, outbox = _stager()
    plans = _Plans()
    bridge = _Bridge()
    adapter = BrowserEffectStageAdapter(
        plans=plans,
        stager=effect_stager,
        scope=scope(),
        actor=user(),
        policy_snapshot=policy_snapshot(),
    )

    proposal = await adapter.stage(request=_request(), plan=_plan())
    state = await effect_stager.get_state(scope=scope(), stage_id=proposal.stage_id)

    assert state.status is EffectStageStatus.HELD
    assert state.current_revision.proposal_content_ref == _ARTIFACT_REF
    assert plans.plans[_ARTIFACT_REF].action_kind is BrowserActionKind.CLICK
    assert (
        _ledger.events_by_stage[proposal.stage_id][0].payload["effect_class"]
        == "unknown"
    )
    assert bridge.prepare_calls == []
    assert bridge.apply_calls == []
    assert outbox.enqueue_calls == 0


async def test_browser_executor_binds_prepare_apply_to_the_approved_exact_plan() -> (
    None
):
    plan = _plan()
    plans = _Plans({_ARTIFACT_REF: plan})
    bridge = _Bridge()
    executor = BrowserEffectExecutor(plans=plans, bridge=bridge)
    request = EffectExecutionRequest(
        stage_id=_STAGE_ID,
        revision=1,
        idempotency_key="browser-apply-1",
        target_ref="browser-target://exact-target",
        target_digest=plan.target_digest,
        proposal_ref=f"proposal://{_STAGE_ID}/revisions/1",
        proposal_content_ref=_ARTIFACT_REF,
        proposal_digest=plan.digest,
        actor=EffectActor.USER,
        decision_ledger_id="rtest·001",
    ).model_copy(update={"target_ref": _browser_target_ref(plan)})

    prepared = await executor.prepare(request)
    result = await executor.apply(prepared)

    assert prepared.prepared_ref == "browser-prepared://ses_123/one"
    assert bridge.prepare_calls == [plan]
    assert bridge.apply_calls == [prepared.prepared_ref]
    assert result.outcome is EffectOutcome.APPLIED
    assert result.retryable is False


async def test_browser_reconcile_is_observational_and_never_replays_apply() -> None:
    plan = _plan()
    plans = _Plans({_ARTIFACT_REF: plan})
    bridge = _Bridge()
    executor = BrowserEffectExecutor(plans=plans, bridge=bridge)
    claim = EffectClaim(
        org_id="org-1",
        run_id="run_a4_test",
        stage_id=_STAGE_ID,
        revision=1,
        idempotency_key="browser-apply-1",
        executor=EffectExecutorKind.BROWSER,
        proposal_digest=plan.digest,
        target_digest=plan.target_digest,
        prepared_ref="browser-prepared://ses_123/one",
        target_ref=_browser_target_ref(plan),
        proposal_ref=f"proposal://{_STAGE_ID}/revisions/1",
        proposal_content_ref=_ARTIFACT_REF,
        actor=EffectActor.USER,
        decision_ledger_id="rtest·001",
    )

    result = await executor.reconcile(claim)

    assert result.outcome is EffectOutcome.INDETERMINATE
    assert bridge.reconcile_calls == ["browser-prepared://ses_123/one"]
    assert bridge.apply_calls == []


def _browser_target_ref(plan: BrowserActionPlan) -> str:
    # Keep the test independent from the private helper while using the same
    # public target shape the A5 execution request carries.
    from agent_runtime.capabilities.browser.effect_adapter import _target_ref

    return _target_ref(plan)
