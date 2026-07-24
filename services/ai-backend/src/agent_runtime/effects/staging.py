"""Transport-neutral proposal, revision, and decision orchestration.

This module has a deliberately narrow object graph: structural ledger port, command
outbox port, policy resolver, clock, and stage-id generator.  It cannot claim, prepare,
apply, or reconcile an effect, and it imports none of the capability/executor layers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
import hashlib
import json
from uuid import uuid4

from agent_runtime.effects.contracts import (
    EffectActorIdentity,
    EffectCommitCommand,
    EffectPolicySnapshot,
    EffectRevisionProposal,
    EffectStageScope,
    EffectStageState,
    EffectStageStatus,
    ProposedEffect,
    validate_proposal_executor_pair,
    validate_idempotency_key,
)
from agent_runtime.effects.errors import (
    EffectStageDigestMismatch,
    EffectStageForbidden,
    EffectStageImmutableTarget,
    EffectStageInvalidTransition,
    EffectStageNotStageable,
    EffectStagePolicyBlocked,
    EffectStageStaleRevision,
)
from agent_runtime.effects.fold import EffectStageFold
from agent_runtime.effects.policy import EffectStagePolicyResolver
from agent_runtime.effects.ports import (
    EffectClockPort,
    EffectCommitOutboxPort,
    EffectStageIdGeneratorPort,
    EffectStageLedgerPort,
)
from agent_runtime.surfaces_v2.ledger_ids import EffectStageIdCodec
from agent_runtime.surfaces_v2.ledger_models import (
    EffectActor,
    EffectClass,
    EffectDecisionKind,
    EffectPolicy,
    LedgerEventType,
)

_EVENT_STAGED = LedgerEventType.EFFECT_STAGED.value
_EVENT_REVISED = LedgerEventType.EFFECT_REVISED.value
_EVENT_DECISION = LedgerEventType.EFFECT_DECISION_RECORDED.value


@dataclass(frozen=True)
class UtcEffectClock:
    """Small default clock; production integration can inject a canonical clock."""

    def now(self) -> str:
        return datetime.now(UTC).isoformat()


@dataclass(frozen=True)
class UuidEffectStageIdGenerator:
    """Generate only A1-valid ``stg_`` ids; proposals never supply one."""

    def new_stage_id(self) -> str:
        return EffectStageIdCodec.format(uuid4())


@dataclass(frozen=True)
class EffectStager:
    """Stage exact proposals and record intent, with no effect execution capability."""

    ledger: EffectStageLedgerPort
    outbox: EffectCommitOutboxPort
    policy_resolver: EffectStagePolicyResolver = field(
        default_factory=EffectStagePolicyResolver
    )
    clock: EffectClockPort = field(default_factory=UtcEffectClock)
    stage_ids: EffectStageIdGeneratorPort = field(
        default_factory=UuidEffectStageIdGenerator
    )

    async def stage(
        self,
        *,
        scope: EffectStageScope,
        proposed_effect: ProposedEffect,
        policy_snapshot: EffectPolicySnapshot,
        actor: EffectActorIdentity,
        idempotency_key: str,
    ) -> EffectStageState:
        """Create one stage with revision one.

        A policy may make the resulting stage ``PROPOSED`` (eligible for an explicit
        policy decision), but this method never creates a command itself.  Commands are
        produced only by an ``approve`` decision below.
        """

        validate_idempotency_key(idempotency_key)
        self._assert_stageable(proposed_effect)
        self._assert_owner(scope, actor, allow_policy_or_system=True)
        if proposed_effect.policy_snapshot_ref != policy_snapshot.snapshot_ref:
            raise EffectStageDigestMismatch(
                "The proposal was built for another policy snapshot."
            )
        resolution = self.policy_resolver.resolve(
            proposed_effect=proposed_effect,
            snapshot=policy_snapshot,
        )
        if resolution.policy is EffectPolicy.BLOCK:
            raise EffectStagePolicyBlocked()

        stage_id = self.stage_ids.new_stage_id()
        EffectStageIdCodec.parse(stage_id)
        created_at = self.clock.now()
        payload = {
            "stage_id": stage_id,
            "operation_id": proposed_effect.operation_id,
            "executor": proposed_effect.executor.value,
            "capability": proposed_effect.target.capability,
            "op": proposed_effect.target.op,
            "target_ref": proposed_effect.target.target_ref,
            "target_digest": proposed_effect.target_digest,
            "display_target": proposed_effect.display_target,
            "proposal_kind": proposed_effect.proposal_kind.value,
            "proposal_ref": proposed_effect.proposal_ref,
            "proposal_digest": proposed_effect.proposal_digest,
            "proposal_media_type": proposed_effect.proposal_media_type,
            "precondition_ref": proposed_effect.precondition_ref,
            "precondition_digest": proposed_effect.precondition_digest,
            "effect_class": proposed_effect.effect_class.value,
            "policy_snapshot_ref": proposed_effect.policy_snapshot_ref,
            "policy": resolution.policy.value,
            "agent_hold": proposed_effect.agent_hold,
            "safe_summary_ref": proposed_effect.safe_summary_ref,
            "owner_ref": scope.owner_ref,
            "author_actor": actor.actor.value,
            "author_ref": actor.principal_ref,
            "created_at": created_at,
        }
        event = await self.ledger.append_stage_event(
            scope=scope,
            event_type=_EVENT_STAGED,
            payload=payload,
            idempotency_key=idempotency_key,
            request_fingerprint=_fingerprint(
                "stage",
                {
                    "scope": scope.model_dump(mode="json"),
                    "proposed_effect": proposed_effect.model_dump(mode="json"),
                    "policy_snapshot": policy_snapshot.model_dump(mode="json"),
                    "actor": actor.model_dump(mode="json"),
                },
            ),
        )
        returned_stage_id = _event_stage_id(event.payload)
        return await self.get_state(scope=scope, stage_id=returned_stage_id)

    async def revise(
        self,
        *,
        scope: EffectStageScope,
        stage_id: str,
        expected_revision: int,
        proposal: EffectRevisionProposal,
        actor: EffectActorIdentity,
        idempotency_key: str,
    ) -> EffectStageState:
        """Replace proposal content only; target and precondition stay immutable."""

        validate_idempotency_key(idempotency_key)
        state = await self.get_state(scope=scope, stage_id=stage_id)
        self._assert_owner(scope, actor)
        if expected_revision != state.current_revision.revision:
            raise EffectStageStaleRevision()
        if state.status is EffectStageStatus.CANCELLED:
            raise EffectStageInvalidTransition()
        validate_proposal_executor_pair(proposal.proposal_kind, state.executor)
        self._assert_revision_retains_target(state, proposal)
        revised_at = self.clock.now()
        payload = {
            "stage_id": stage_id,
            "revision": expected_revision + 1,
            "proposal_kind": proposal.proposal_kind.value,
            "proposal_ref": proposal.proposal_ref,
            "proposal_digest": proposal.proposal_digest,
            "proposal_media_type": proposal.proposal_media_type,
            "target_ref": proposal.target_ref,
            "target_digest": proposal.target_digest,
            "display_target": proposal.display_target,
            "precondition_ref": proposal.precondition_ref,
            "precondition_digest": proposal.precondition_digest,
            "safe_diff_ref": proposal.safe_diff_ref,
            "author_actor": actor.actor.value,
            "author_ref": actor.principal_ref,
            "created_at": revised_at,
        }
        await self.ledger.append_stage_event(
            scope=scope,
            event_type=_EVENT_REVISED,
            payload=payload,
            idempotency_key=idempotency_key,
            request_fingerprint=_fingerprint(
                "revise",
                {
                    "stage_id": stage_id,
                    "expected_revision": expected_revision,
                    "proposal": proposal.model_dump(mode="json"),
                    "actor": actor.model_dump(mode="json"),
                },
            ),
        )
        return await self.get_state(scope=scope, stage_id=stage_id)

    async def decide(
        self,
        *,
        scope: EffectStageScope,
        stage_id: str,
        revision: int,
        decision: EffectDecisionKind,
        proposal_digest: str,
        target_digest: str,
        actor: EffectActorIdentity,
        idempotency_key: str,
    ) -> EffectStageState:
        """Record a digest-pinned decision and enqueue only an approved command."""

        validate_idempotency_key(idempotency_key)
        state = await self.get_state(scope=scope, stage_id=stage_id)
        self._assert_owner(scope, actor, allow_policy_or_system=True)
        if revision != state.current_revision.revision:
            raise EffectStageStaleRevision()
        if (
            proposal_digest != state.current_revision.proposal_digest
            or target_digest != state.target_digest
        ):
            raise EffectStageDigestMismatch()
        if decision is EffectDecisionKind.RESTORE:
            raise EffectStageInvalidTransition()
        if decision not in {
            EffectDecisionKind.APPROVE,
            EffectDecisionKind.REJECT,
            EffectDecisionKind.CANCEL,
        }:
            raise EffectStageInvalidTransition()
        if state.status in {
            EffectStageStatus.APPROVED,
            EffectStageStatus.REJECTED,
            EffectStageStatus.CANCELLED,
        }:
            if _is_identical_decision(
                state, decision, actor, proposal_digest, target_digest
            ):
                return state
            raise EffectStageInvalidTransition()
        if state.status not in {
            EffectStageStatus.PROPOSED,
            EffectStageStatus.HELD,
            EffectStageStatus.REVISED,
        }:
            raise EffectStageInvalidTransition()
        if state.policy is EffectPolicy.BLOCK:
            raise EffectStagePolicyBlocked()
        if actor.actor is EffectActor.POLICY and state.policy is not EffectPolicy.AUTO:
            raise EffectStageForbidden("Policy cannot approve this held effect.")
        if (
            actor.actor is EffectActor.POLICY
            and decision is not EffectDecisionKind.APPROVE
        ):
            raise EffectStageForbidden("Policy can only record an eligible approval.")

        decided_at = self.clock.now()
        event = await self.ledger.append_stage_event(
            scope=scope,
            event_type=_EVENT_DECISION,
            payload={
                "stage_id": stage_id,
                "revision": revision,
                "decision": decision.value,
                "actor": actor.actor.value,
                "actor_ref": actor.principal_ref,
                "proposal_digest": proposal_digest,
                "target_digest": target_digest,
                "decided_at": decided_at,
            },
            idempotency_key=idempotency_key,
            request_fingerprint=_fingerprint(
                "decide",
                {
                    "stage_id": stage_id,
                    "revision": revision,
                    "decision": decision.value,
                    "proposal_digest": proposal_digest,
                    "target_digest": target_digest,
                    "actor": actor.model_dump(mode="json"),
                },
            ),
        )
        if decision is EffectDecisionKind.APPROVE:
            await self.outbox.enqueue_after_decision(
                EffectCommitCommand(
                    run_id=scope.run_id,
                    stage_id=stage_id,
                    revision=revision,
                    decision_ledger_id=event.ledger_id,
                    proposal_digest=proposal_digest,
                    target_digest=target_digest,
                    idempotency_key=idempotency_key,
                )
            )
        return await self.get_state(scope=scope, stage_id=stage_id)

    async def get_state(
        self,
        *,
        scope: EffectStageScope,
        stage_id: str,
    ) -> EffectStageState:
        """Read one state exclusively by folding its structural ledger history."""

        EffectStageIdCodec.parse(stage_id)
        events = await self.ledger.list_stage_events(scope=scope, stage_id=stage_id)
        state = EffectStageFold.fold(events)
        if state.scope != scope:
            raise EffectStageForbidden()
        return state

    @staticmethod
    def _assert_stageable(proposed_effect: ProposedEffect) -> None:
        if proposed_effect.effect_class in {
            EffectClass.NONE,
            EffectClass.INTERNAL_REVERSIBLE,
        }:
            raise EffectStageNotStageable()

    @staticmethod
    def _assert_owner(
        scope: EffectStageScope,
        actor: EffectActorIdentity,
        *,
        allow_policy_or_system: bool = False,
    ) -> None:
        if actor.actor is EffectActor.USER and actor.principal_ref == scope.owner_ref:
            return
        if allow_policy_or_system and actor.actor in {
            EffectActor.POLICY,
            EffectActor.SYSTEM,
        }:
            return
        raise EffectStageForbidden()

    @staticmethod
    def _assert_revision_retains_target(
        state: EffectStageState,
        proposal: EffectRevisionProposal,
    ) -> None:
        current = state.current_revision
        if (
            proposal.target_ref != state.target.target_ref
            or proposal.target_digest != state.target_digest
            or proposal.display_target != state.display_target
            or proposal.precondition_ref != current.precondition_ref
            or proposal.precondition_digest != current.precondition_digest
        ):
            raise EffectStageImmutableTarget()


def _event_stage_id(payload: dict[str, object]) -> str:
    stage_id = payload.get("stage_id")
    if not isinstance(stage_id, str):
        raise EffectStageInvalidTransition("The persistence port returned no stage id.")
    return stage_id


def _is_identical_decision(
    state: EffectStageState,
    decision: EffectDecisionKind,
    actor: EffectActorIdentity,
    proposal_digest: str,
    target_digest: str,
) -> bool:
    current = state.decision
    return bool(
        current
        and current.decision is decision
        and current.actor == actor
        and current.proposal_digest == proposal_digest
        and current.target_digest == target_digest
    )


def _fingerprint(kind: str, value: dict[str, object]) -> str:
    """Hash bounded metadata for port-level idempotency without storing a body."""

    canonical = json.dumps(
        {"kind": kind, "value": value},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


__all__ = ["EffectStager", "UtcEffectClock", "UuidEffectStageIdGenerator"]
