from __future__ import annotations

import pytest

from agent_runtime.effects.contracts import EffectStageStatus
from agent_runtime.effects.errors import EffectStageNotFound
from agent_runtime.effects.fold import EffectStageFold
from agent_runtime.effects.staging import EffectStager
from agent_runtime.effects.ports import StructuralEvent
from agent_runtime.surfaces_v2.ledger_models import EffectDecisionKind

from .fakes import (
    FakeClock,
    FakeLedger,
    FakeOutbox,
    FakeStageIds,
    policy_snapshot,
    proposal,
    scope,
    user,
)


async def test_fold_is_order_independent_over_sequence_and_ignores_invalid_prefixes() -> (
    None
):
    ledger = FakeLedger()
    stager = EffectStager(
        ledger=ledger,
        outbox=FakeOutbox(),
        clock=FakeClock(),
        stage_ids=FakeStageIds(),
    )
    proposed = proposal()
    initial = await stager.stage(
        scope=scope(),
        proposed_effect=proposed,
        policy_snapshot=policy_snapshot(),
        actor=user(),
        idempotency_key="stage",
    )
    await stager.decide(
        scope=scope(),
        stage_id=initial.stage_id,
        revision=1,
        decision=EffectDecisionKind.APPROVE,
        proposal_digest=proposed.proposal_digest,
        target_digest=proposed.target_digest,
        actor=user(),
        idempotency_key="approve",
    )
    events = list(ledger.events_by_stage[initial.stage_id])
    invalid = StructuralEvent(
        run_id=scope().run_id,
        ledger_id="rtest·999",
        sequence_no=999,
        event_type="effect.revised",
        payload={"stage_id": initial.stage_id, "revision": 99},
        created_at="2026-07-24T00:00:59+00:00",
    )

    forward = EffectStageFold.fold([*events, invalid])
    reverse = EffectStageFold.fold(list(reversed([*events, invalid])))

    assert forward == reverse
    assert forward.status is EffectStageStatus.APPROVED
    assert forward.current_revision.revision == 1


async def test_fold_rejects_a_revision_that_mutates_the_pinned_target() -> None:
    ledger = FakeLedger()
    stager = EffectStager(
        ledger=ledger,
        outbox=FakeOutbox(),
        clock=FakeClock(),
        stage_ids=FakeStageIds(),
    )
    proposed = proposal()
    state = await stager.stage(
        scope=scope(),
        proposed_effect=proposed,
        policy_snapshot=policy_snapshot(),
        actor=user(),
        idempotency_key="stage-target-pin",
    )
    staged = ledger.events_by_stage[state.stage_id][0]
    invalid_payload = dict(staged.payload)
    invalid_payload.update(
        {
            "revision": 2,
            "proposal_digest": "d" * 64,
            "proposal_ref": "artifact://art_00000000-0000-4000-8000-000000000001/revisions/2",
            "safe_diff_ref": "diff://stages/a4-test/1-2",
            "target_digest": "e" * 64,
        }
    )
    invalid = StructuralEvent(
        run_id=scope().run_id,
        ledger_id="rtest·002",
        sequence_no=2,
        event_type="effect.revised",
        payload=invalid_payload,
        created_at="2026-07-24T00:00:02+00:00",
    )

    folded = EffectStageFold.fold([staged, invalid])

    assert folded.current_revision.revision == 1
    assert folded.status is EffectStageStatus.HELD


def test_fold_without_authoritative_stage_is_honest_not_found() -> None:
    with pytest.raises(EffectStageNotFound):
        EffectStageFold.fold(())
