from __future__ import annotations

import pytest

from agent_runtime.effects.contracts import EffectProposalKind, EffectStageStatus
from agent_runtime.effects.errors import (
    EffectStageDigestMismatch,
    EffectStageForbidden,
    EffectStageIdempotencyConflict,
    EffectStageImmutableTarget,
    EffectStageInvalidTransition,
    EffectStageStaleRevision,
)
from agent_runtime.effects.staging import EffectStager
from agent_runtime.surfaces_v2.ledger_models import (
    EffectActor,
    EffectClass,
    EffectDecisionKind,
)

from .fakes import (
    FakeClock,
    FakeLedger,
    FakeOutbox,
    FakeStageIds,
    foreign_user,
    policy_actor,
    policy_snapshot,
    proposal,
    revision_from,
    scope,
    user,
)


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


async def _stage(stager: EffectStager, *, key: str = "stage-1"):
    proposed = proposal()
    state = await stager.stage(
        scope=scope(),
        proposed_effect=proposed,
        policy_snapshot=policy_snapshot(),
        actor=user(),
        idempotency_key=key,
    )
    return state, proposed


async def test_approve_pins_exact_digests_and_enqueues_one_body_free_command() -> None:
    stager, ledger, outbox = _stager()
    state, proposed = await _stage(stager)

    approved = await stager.decide(
        scope=scope(),
        stage_id=state.stage_id,
        revision=1,
        decision=EffectDecisionKind.APPROVE,
        proposal_digest=proposed.proposal_digest,
        target_digest=proposed.target_digest,
        actor=user(),
        idempotency_key="approve-1",
    )

    assert approved.status is EffectStageStatus.APPROVED
    assert ledger.append_calls == 2
    assert outbox.enqueue_calls == 1
    command = outbox.commands["approve-1"]
    assert command.stage_id == state.stage_id
    assert command.proposal_digest == proposed.proposal_digest
    assert command.target_digest == proposed.target_digest
    assert "proposal_ref" not in type(command).model_fields
    assert "executor" not in type(command).model_fields
    emitted_payload = ledger.events_by_stage[state.stage_id][0].payload
    assert "body" not in emitted_payload
    assert "raw_args" not in emitted_payload
    assert all(
        not (isinstance(value, str) and value.startswith(("file://", "/")))
        for value in emitted_payload.values()
    )

    replay = await stager.decide(
        scope=scope(),
        stage_id=state.stage_id,
        revision=1,
        decision=EffectDecisionKind.APPROVE,
        proposal_digest=proposed.proposal_digest,
        target_digest=proposed.target_digest,
        actor=user(),
        idempotency_key="approve-1",
    )
    assert replay == approved
    assert outbox.enqueue_calls == 1


async def test_invalid_or_stale_mutations_emit_nothing() -> None:
    stager, ledger, outbox = _stager()
    state, proposed = await _stage(stager)
    baseline = ledger.append_calls

    with pytest.raises(EffectStageDigestMismatch):
        await stager.decide(
            scope=scope(),
            stage_id=state.stage_id,
            revision=1,
            decision=EffectDecisionKind.APPROVE,
            proposal_digest="f" * 64,
            target_digest=proposed.target_digest,
            actor=user(),
            idempotency_key="bad-digest",
        )
    with pytest.raises(EffectStageStaleRevision):
        await stager.revise(
            scope=scope(),
            stage_id=state.stage_id,
            expected_revision=2,
            proposal=revision_from(proposed),
            actor=user(),
            idempotency_key="stale-revision",
        )
    with pytest.raises(EffectStageForbidden):
        await stager.revise(
            scope=scope(),
            stage_id=state.stage_id,
            expected_revision=1,
            proposal=revision_from(proposed),
            actor=foreign_user(),
            idempotency_key="foreign",
        )
    with pytest.raises(EffectStageImmutableTarget):
        await stager.revise(
            scope=scope(),
            stage_id=state.stage_id,
            expected_revision=1,
            proposal=revision_from(proposed, target_digest="e" * 64),
            actor=user(),
            idempotency_key="changed-target",
        )

    assert ledger.append_calls == baseline
    assert outbox.enqueue_calls == 0


@pytest.mark.parametrize(
    "effect_class",
    [EffectClass.NONE, EffectClass.INTERNAL_REVERSIBLE],
)
async def test_non_external_effects_never_create_a_stage(
    effect_class: EffectClass,
) -> None:
    stager, ledger, outbox = _stager()

    from agent_runtime.effects.errors import EffectStageNotStageable

    with pytest.raises(EffectStageNotStageable):
        await stager.stage(
            scope=scope(),
            proposed_effect=proposal(effect_class=effect_class),
            policy_snapshot=policy_snapshot(),
            actor=user(),
            idempotency_key=f"non-external-{effect_class.value}",
        )
    assert ledger.append_calls == 0
    assert outbox.enqueue_calls == 0


async def test_revision_kind_must_remain_compatible_with_immutable_executor() -> None:
    stager, ledger, _ = _stager()
    state, proposed = await _stage(stager)
    baseline = ledger.append_calls
    incompatible = revision_from(proposed).model_copy(
        update={"proposal_kind": EffectProposalKind.BROWSER_SUBMISSION}
    )

    with pytest.raises(ValueError, match="incompatible"):
        await stager.revise(
            scope=scope(),
            stage_id=state.stage_id,
            expected_revision=1,
            proposal=incompatible,
            actor=user(),
            idempotency_key="incompatible-kind",
        )
    assert ledger.append_calls == baseline


async def test_revision_after_approval_supersedes_that_approval_and_returns_held() -> (
    None
):
    stager, _, outbox = _stager()
    state, proposed = await _stage(stager)
    await stager.decide(
        scope=scope(),
        stage_id=state.stage_id,
        revision=1,
        decision=EffectDecisionKind.APPROVE,
        proposal_digest=proposed.proposal_digest,
        target_digest=proposed.target_digest,
        actor=user(),
        idempotency_key="approve",
    )

    revised = await stager.revise(
        scope=scope(),
        stage_id=state.stage_id,
        expected_revision=1,
        proposal=revision_from(proposed),
        actor=user(),
        idempotency_key="revise",
    )

    assert revised.status is EffectStageStatus.HELD
    assert revised.current_revision.revision == 2
    assert revised.decision is None
    assert revised.superseded_revision == 1
    assert outbox.enqueue_calls == 1


@pytest.mark.parametrize(
    ("decision", "expected"),
    [
        (EffectDecisionKind.REJECT, EffectStageStatus.REJECTED),
        (EffectDecisionKind.CANCEL, EffectStageStatus.CANCELLED),
    ],
)
async def test_non_approval_terminal_decisions_do_not_enqueue(
    decision: EffectDecisionKind,
    expected: EffectStageStatus,
) -> None:
    stager, _, outbox = _stager()
    state, proposed = await _stage(stager)

    result = await stager.decide(
        scope=scope(),
        stage_id=state.stage_id,
        revision=1,
        decision=decision,
        proposal_digest=proposed.proposal_digest,
        target_digest=proposed.target_digest,
        actor=user(),
        idempotency_key=f"{decision.value}-1",
    )

    assert result.status is expected
    assert outbox.enqueue_calls == 0


async def test_restore_and_second_nonmatching_terminal_decision_are_invalid() -> None:
    stager, ledger, _ = _stager()
    state, proposed = await _stage(stager)
    baseline = ledger.append_calls

    with pytest.raises(EffectStageInvalidTransition):
        await stager.decide(
            scope=scope(),
            stage_id=state.stage_id,
            revision=1,
            decision=EffectDecisionKind.RESTORE,
            proposal_digest=proposed.proposal_digest,
            target_digest=proposed.target_digest,
            actor=user(),
            idempotency_key="restore",
        )
    assert ledger.append_calls == baseline


async def test_stage_replay_is_idempotent_and_changed_request_conflicts() -> None:
    stager, ledger, _ = _stager()
    first, proposed = await _stage(stager, key="same-stage-key")
    replay = await stager.stage(
        scope=scope(),
        proposed_effect=proposed,
        policy_snapshot=policy_snapshot(),
        actor=user(),
        idempotency_key="same-stage-key",
    )
    assert replay == first
    assert ledger.append_calls == 1

    with pytest.raises(EffectStageIdempotencyConflict):
        await stager.stage(
            scope=scope(),
            proposed_effect=proposal(proposal_digest="f" * 64),
            policy_snapshot=policy_snapshot(),
            actor=user(),
            idempotency_key="same-stage-key",
        )
    assert ledger.append_calls == 1


async def test_policy_actor_can_only_approve_a_known_reversible_allow_always_stage() -> (
    None
):
    stager, _, outbox = _stager()
    proposed = proposal()
    state = await stager.stage(
        scope=scope(),
        proposed_effect=proposed,
        policy_snapshot=policy_snapshot(allow_always=True),
        actor=user(),
        idempotency_key="auto-stage",
    )
    assert state.status is EffectStageStatus.PROPOSED

    approved = await stager.decide(
        scope=scope(),
        stage_id=state.stage_id,
        revision=1,
        decision=EffectDecisionKind.APPROVE,
        proposal_digest=proposed.proposal_digest,
        target_digest=proposed.target_digest,
        actor=policy_actor(),
        idempotency_key="auto-approve",
    )
    assert approved.status is EffectStageStatus.APPROVED
    assert approved.decision is not None
    assert approved.decision.actor.actor is EffectActor.POLICY
    assert outbox.enqueue_calls == 1
