from __future__ import annotations

from dataclasses import dataclass

import pytest

from agent_runtime.api.rowset_effect_review import RowSetEffectReviewService
from agent_runtime.capabilities.tools.builtin.stage_rowset_write import (
    RowSetEffectProposal,
)
from agent_runtime.effects.contracts import EffectProposalKind
from agent_runtime.effects.errors import EffectStageInvalidTransition
from agent_runtime.effects.fold import EffectStageFold
from agent_runtime.effects.ports import StructuralEvent
from agent_runtime.effects.staging import EffectStager
from agent_runtime.surfaces_v2.ledger_models import (
    EffectDecisionKind,
    EffectExecutorKind,
)
from agent_runtime.surfaces_v2.rowset import AgentHold, RowFieldChange, StagedRow
from tests.unit.agent_runtime.effects.fakes import (
    FakeClock,
    FakeLedger,
    FakeOutbox,
    FakeStageIds,
    policy_snapshot,
    proposal,
    scope,
    user,
)

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


_PROPOSAL = RowSetEffectProposal(
    target_connector="linear",
    target_op="update_issue",
    title="Reprioritize launch issues",
    rows=(
        StagedRow(
            row_key="row-a",
            title="Acme renewal",
            target_args={"id": "row-a", "priority": 2},
            changes=(RowFieldChange(field="priority", old=1, new=2),),
        ),
        StagedRow(
            row_key="row-b",
            title="Beta onboarding",
            target_args={"id": "row-b", "priority": 3},
            changes=(RowFieldChange(field="priority", old=1, new=3),),
        ),
    ),
    agent_holds=(AgentHold(row_key="row-b", reason="Recent customer reply"),),
)


@dataclass
class _Material:
    proposal: RowSetEffectProposal = _PROPOSAL

    async def resolve(self, **_kwargs: object) -> RowSetEffectProposal:
        return self.proposal


@dataclass
class _Decisions:
    stager: EffectStager
    ledger: FakeLedger
    retried: tuple[str, ...] | None = None
    retry_basis: str | None = None

    async def stage_history(
        self,
        *,
        stage_id: str,
        **_kwargs: object,
    ) -> tuple[object, tuple[StructuralEvent, ...]]:
        events = tuple(self.ledger.events_by_stage[stage_id])
        return EffectStageFold.fold(events), events

    async def record_row_decisions(
        self,
        *,
        stage_id: str,
        revision: int,
        decisions: dict[str, str],
        proposal_digest: str,
        target_digest: str,
        idempotency_key: str,
        **_kwargs: object,
    ) -> object:
        return await self.stager.record_row_decisions(
            scope=scope(),
            stage_id=stage_id,
            revision=revision,
            decisions=decisions,
            proposal_digest=proposal_digest,
            target_digest=target_digest,
            actor=user(),
            idempotency_key=idempotency_key,
        )

    async def record_decision(
        self,
        *,
        stage_id: str,
        revision: int,
        decision: EffectDecisionKind,
        proposal_digest: str,
        target_digest: str,
        row_keys: tuple[str, ...],
        idempotency_namespace: str,
        **_kwargs: object,
    ) -> object:
        return await self.stager.decide(
            scope=scope(),
            stage_id=stage_id,
            revision=revision,
            decision=decision,
            proposal_digest=proposal_digest,
            target_digest=target_digest,
            row_keys=row_keys,
            actor=user(),
            idempotency_key=idempotency_namespace,
        )

    async def enqueue_rowset_retry(
        self,
        *,
        row_keys: tuple[str, ...],
        basis_ledger_id: str,
        **_kwargs: object,
    ) -> object:
        self.retried = row_keys
        self.retry_basis = basis_ledger_id
        return object()


async def _service_bundle() -> tuple[
    RowSetEffectReviewService,
    _Decisions,
    FakeLedger,
    FakeOutbox,
    str,
]:
    ledger = FakeLedger()
    outbox = FakeOutbox()
    stager = EffectStager(
        ledger=ledger,
        outbox=outbox,
        clock=FakeClock(),
        stage_ids=FakeStageIds(),
    )
    proposed = proposal(
        kind=EffectProposalKind.ROW_SET,
        executor=EffectExecutorKind.BUILTIN,
        agent_hold=True,
    )
    staged = await stager.stage(
        scope=scope(),
        proposed_effect=proposed,
        policy_snapshot=policy_snapshot(),
        actor=user(),
        idempotency_key="review-stage",
    )
    decisions = _Decisions(stager=stager, ledger=ledger)
    service = RowSetEffectReviewService(
        decisions=decisions,  # type: ignore[arg-type]
        material=_Material(),
    )
    return service, decisions, ledger, outbox, staged.stage_id


def _scope_args(stage_id: str) -> dict[str, str]:
    return {
        "org_id": "org-effect-review",
        "user_id": "user-1",
        "run_id": scope().run_id,
        "stage_id": stage_id,
    }


async def test_review_projects_agent_hold_and_exact_initial_action() -> None:
    service, _decisions, _ledger, _outbox, stage_id = await _service_bundle()

    review = await service.review(**_scope_args(stage_id))

    assert review.status == "staged"
    assert review.counts.model_dump() == {
        "total": 2,
        "approved": 1,
        "held": 1,
        "applied": 0,
        "failed": 0,
    }
    assert review.rows[1].decision == "hold"
    assert review.rows[1].decision_source == "agent"
    assert review.rows[1].hold_reason == "Recent customer reply"
    assert review.action is not None
    assert review.action.kind == "apply"
    assert review.action.row_keys == ("row-a",)


async def test_user_override_changes_server_authoritative_apply_scope() -> None:
    service, _decisions, _ledger, outbox, stage_id = await _service_bundle()
    current = await service.review(**_scope_args(stage_id))

    updated = await service.record_row_decisions(
        **_scope_args(stage_id),
        revision=current.revision,
        proposal_digest=current.proposal_digest,
        target_digest=current.target_digest,
        decisions={"row-b": "approve"},
    )

    assert outbox.enqueue_calls == 0
    assert updated.rows[1].decision == "approve"
    assert updated.rows[1].decision_source == "user"
    assert updated.rows[1].hold_reason == "Recent customer reply"
    assert updated.action is not None
    assert updated.action.row_keys == ("row-a", "row-b")


async def test_partial_review_retries_every_and_only_latest_failed_row() -> None:
    service, decisions, ledger, outbox, stage_id = await _service_bundle()
    current = await service.review(**_scope_args(stage_id))
    assert current.action is not None
    await service.apply(
        **_scope_args(stage_id),
        revision=current.revision,
        proposal_digest=current.proposal_digest,
        target_digest=current.target_digest,
        row_keys=current.action.row_keys,
        basis_sequence_no=current.action.basis_sequence_no,
    )
    assert next(iter(outbox.commands.values())).row_keys == ("row-a",)

    result = StructuralEvent(
        run_id=scope().run_id,
        ledger_id="rtest·003",
        sequence_no=3,
        event_type="effect.applied",
        payload={
            "v": 1,
            "stage_id": stage_id,
            "result": "partial",
            "row_results": [
                {"row_key": "row-a", "outcome": "failed", "detail": None},
            ],
        },
        created_at="2026-07-24T00:00:03+00:00",
    )
    ledger.events_by_stage[stage_id].append(result)

    partial = await service.review(**_scope_args(stage_id))

    assert partial.status == "partial"
    assert partial.action is not None
    assert partial.action.kind == "retry_failed"
    assert partial.action.row_keys == ("row-a",)
    assert partial.action.basis_ledger_id == "rtest·003"

    await service.retry(
        **_scope_args(stage_id),
        revision=partial.revision,
        proposal_digest=partial.proposal_digest,
        target_digest=partial.target_digest,
        row_keys=partial.action.row_keys,
        basis_sequence_no=partial.action.basis_sequence_no,
        basis_ledger_id=partial.action.basis_ledger_id,
    )

    assert decisions.retried == ("row-a",)
    assert decisions.retry_basis == "rtest·003"


@pytest.mark.parametrize(
    "row_keys",
    [
        (),
        ("row-a", "row-b"),
        ("row-b",),
    ],
)
async def test_retry_rejects_empty_widened_or_held_scope(
    row_keys: tuple[str, ...],
) -> None:
    service, _decisions, ledger, _outbox, stage_id = await _service_bundle()
    current = await service.review(**_scope_args(stage_id))
    assert current.action is not None
    await service.apply(
        **_scope_args(stage_id),
        revision=current.revision,
        proposal_digest=current.proposal_digest,
        target_digest=current.target_digest,
        row_keys=current.action.row_keys,
        basis_sequence_no=current.action.basis_sequence_no,
    )
    result = StructuralEvent(
        run_id=scope().run_id,
        ledger_id="rtest·002",
        sequence_no=2,
        event_type="effect.applied",
        payload={
            "v": 1,
            "stage_id": stage_id,
            "result": "partial",
            "row_results": [{"row_key": "row-a", "outcome": "failed"}],
        },
        created_at="2026-07-24T00:00:02+00:00",
    )
    ledger.events_by_stage[stage_id].append(result)
    partial = await service.review(**_scope_args(stage_id))
    assert partial.action is not None

    with pytest.raises(EffectStageInvalidTransition):
        await service.retry(
            **_scope_args(stage_id),
            revision=partial.revision,
            proposal_digest=partial.proposal_digest,
            target_digest=partial.target_digest,
            row_keys=row_keys,
            basis_sequence_no=partial.action.basis_sequence_no,
            basis_ledger_id=partial.action.basis_ledger_id or "",
        )
