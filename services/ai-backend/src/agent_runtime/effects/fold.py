"""Pure fold for canonical A4 effect-stage events.

The fold is deliberately tolerant of unrelated or malformed structural events: an
invalid history entry cannot manufacture a stage transition.  Writers perform the
strict validation before append; replay treats a bad entry as non-authoritative and
keeps the last valid state.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from pydantic import ValidationError

from agent_runtime.effects.contracts import (
    EffectActorIdentity,
    EffectStageDecision,
    EffectStageRevision,
    EffectStageScope,
    EffectStageState,
    EffectStageStatus,
    ProposedEffect,
)
from agent_runtime.effects.errors import EffectStageNotFound
from agent_runtime.effects.ports import StructuralEvent
from agent_runtime.surfaces_v2.entities import EffectTarget
from agent_runtime.surfaces_v2.ledger_models import (
    EffectActor,
    EffectClass,
    EffectDecisionKind,
    EffectExecutorKind,
    EffectPolicy,
)

_EVENT_STAGED = "effect.staged"
_EVENT_REVISED = "effect.revised"
_EVENT_DECISION = "effect.decision_recorded"


class EffectStageFold:
    """Fold one stage history without I/O, execution, or a transport dependency."""

    @classmethod
    def fold(cls, events: Sequence[StructuralEvent]) -> EffectStageState:
        """Return the last valid state represented by canonical event prefixes.

        The ledger port is responsible for returning one stage's events.  A missing
        ``effect.staged`` event is an honest not-found result rather than an invented
        empty stage.
        """

        state: EffectStageState | None = None
        for event in sorted(
            events, key=lambda item: (item.sequence_no, item.ledger_id)
        ):
            if event.event_type == _EVENT_STAGED:
                if state is None:
                    state = cls._state_from_staged(event)
                continue
            if state is None:
                continue
            if _event_stage_id(event) != state.stage_id:
                continue
            if event.event_type == _EVENT_REVISED:
                state = cls._apply_revision(state, event)
            elif event.event_type == _EVENT_DECISION:
                state = cls._apply_decision(state, event)
        if state is None:
            raise EffectStageNotFound()
        return state

    @classmethod
    def _state_from_staged(cls, event: StructuralEvent) -> EffectStageState | None:
        payload = event.payload
        try:
            target = EffectTarget(
                executor=EffectExecutorKind(_string(payload, "executor")),
                capability=_string(payload, "capability"),
                op=_string(payload, "op"),
                target_ref=_string(payload, "target_ref"),
                precondition_ref=_optional_string(payload, "precondition_ref"),
                display_label=_string(payload, "display_target"),
            )
            proposed = ProposedEffect(
                operation_id=_string(payload, "operation_id"),
                executor=EffectExecutorKind(_string(payload, "executor")),
                target=target,
                target_digest=_string(payload, "target_digest"),
                display_target=_string(payload, "display_target"),
                proposal_kind=_string(payload, "proposal_kind"),
                proposal_ref=_string(payload, "proposal_ref"),
                proposal_digest=_string(payload, "proposal_digest"),
                proposal_media_type=_string(payload, "proposal_media_type"),
                precondition_ref=_optional_string(payload, "precondition_ref"),
                precondition_digest=_optional_string(payload, "precondition_digest"),
                effect_class=EffectClass(_string(payload, "effect_class")),
                policy_snapshot_ref=_string(payload, "policy_snapshot_ref"),
                agent_hold=_bool(payload, "agent_hold"),
                safe_summary_ref=_optional_string(payload, "safe_summary_ref"),
            )
            policy = EffectPolicy(_string(payload, "policy"))
            author = EffectActorIdentity(
                actor=EffectActor(_string(payload, "author_actor")),
                principal_ref=_string(payload, "author_ref"),
            )
            owner_ref = _string(payload, "owner_ref")
            created_at = _string(payload, "created_at")
            revision = EffectStageRevision(
                revision=1,
                proposal_kind=proposed.proposal_kind,
                proposal_ref=proposed.proposal_ref,
                proposal_digest=proposed.proposal_digest,
                proposal_media_type=proposed.proposal_media_type,
                target_ref=proposed.target.target_ref,
                target_digest=proposed.target_digest,
                display_target=proposed.display_target,
                precondition_ref=proposed.precondition_ref,
                precondition_digest=proposed.precondition_digest,
                safe_diff_ref=None,
                author=author,
                created_at=created_at,
            )
            return EffectStageState(
                stage_id=_string(payload, "stage_id"),
                scope=EffectStageScope(run_id=event.run_id, owner_ref=owner_ref),
                operation_id=proposed.operation_id,
                executor=proposed.executor,
                target=proposed.target,
                target_digest=proposed.target_digest,
                display_target=proposed.display_target,
                effect_class=proposed.effect_class,
                policy_snapshot_ref=proposed.policy_snapshot_ref,
                policy=policy,
                agent_hold=proposed.agent_hold,
                revisions=(revision,),
                status=(
                    EffectStageStatus.PROPOSED
                    if policy is EffectPolicy.AUTO
                    else EffectStageStatus.HELD
                ),
                decision=None,
                created_at=created_at,
                updated_at=created_at,
            )
        except (KeyError, TypeError, ValueError, ValidationError):
            return None

    @classmethod
    def _apply_revision(
        cls,
        state: EffectStageState,
        event: StructuralEvent,
    ) -> EffectStageState:
        if state.status is EffectStageStatus.CANCELLED:
            return state
        payload = event.payload
        try:
            revision_no = _integer(payload, "revision")
            if revision_no != state.current_revision.revision + 1:
                return state
            revision = EffectStageRevision(
                revision=revision_no,
                proposal_kind=_string(payload, "proposal_kind"),
                proposal_ref=_string(payload, "proposal_ref"),
                proposal_digest=_string(payload, "proposal_digest"),
                proposal_media_type=_string(payload, "proposal_media_type"),
                target_ref=_string(payload, "target_ref"),
                target_digest=_string(payload, "target_digest"),
                display_target=_string(payload, "display_target"),
                precondition_ref=_optional_string(payload, "precondition_ref"),
                precondition_digest=_optional_string(payload, "precondition_digest"),
                safe_diff_ref=_optional_string(payload, "safe_diff_ref"),
                author=EffectActorIdentity(
                    actor=EffectActor(_string(payload, "author_actor")),
                    principal_ref=_string(payload, "author_ref"),
                ),
                created_at=_string(payload, "created_at"),
            )
        except (KeyError, TypeError, ValueError, ValidationError):
            return state
        if not _revision_retains_target(state, revision):
            return state

        approved_revision = (
            state.decision.revision
            if state.status is EffectStageStatus.APPROVED and state.decision is not None
            else None
        )
        return state.model_copy(
            update={
                "revisions": (*state.revisions, revision),
                "status": (
                    EffectStageStatus.HELD
                    if approved_revision is not None
                    else EffectStageStatus.REVISED
                ),
                "decision": None,
                "superseded_revision": approved_revision,
                "updated_at": event.created_at,
            }
        )

    @classmethod
    def _apply_decision(
        cls,
        state: EffectStageState,
        event: StructuralEvent,
    ) -> EffectStageState:
        if state.status not in {
            EffectStageStatus.PROPOSED,
            EffectStageStatus.HELD,
            EffectStageStatus.REVISED,
        }:
            return state
        if state.policy is EffectPolicy.BLOCK:
            return state
        payload = event.payload
        try:
            decision = EffectStageDecision(
                revision=_integer(payload, "revision"),
                decision=EffectDecisionKind(_string(payload, "decision")),
                actor=EffectActorIdentity(
                    actor=EffectActor(_string(payload, "actor")),
                    principal_ref=_string(payload, "actor_ref"),
                ),
                proposal_digest=_string(payload, "proposal_digest"),
                target_digest=_string(payload, "target_digest"),
                decided_at=_string(payload, "decided_at"),
                ledger_id=event.ledger_id,
            )
        except (KeyError, TypeError, ValueError, ValidationError):
            return state
        if (
            decision.revision != state.current_revision.revision
            or decision.proposal_digest != state.current_revision.proposal_digest
            or decision.target_digest != state.target_digest
            or decision.decision
            not in {
                EffectDecisionKind.APPROVE,
                EffectDecisionKind.REJECT,
                EffectDecisionKind.CANCEL,
            }
            or (
                decision.actor.actor is EffectActor.POLICY
                and state.policy is not EffectPolicy.AUTO
            )
        ):
            return state
        status_by_decision = {
            EffectDecisionKind.APPROVE: EffectStageStatus.APPROVED,
            EffectDecisionKind.REJECT: EffectStageStatus.REJECTED,
            EffectDecisionKind.CANCEL: EffectStageStatus.CANCELLED,
        }
        return state.model_copy(
            update={
                "status": status_by_decision[decision.decision],
                "decision": decision,
                "updated_at": event.created_at,
            }
        )


def _revision_retains_target(
    state: EffectStageState,
    revision: EffectStageRevision,
) -> bool:
    current = state.current_revision
    return (
        revision.target_ref == state.target.target_ref
        and revision.target_digest == state.target_digest
        and revision.display_target == state.display_target
        and revision.precondition_ref == current.precondition_ref
        and revision.precondition_digest == current.precondition_digest
    )


def _event_stage_id(event: StructuralEvent) -> str | None:
    value = event.payload.get("stage_id")
    return value if isinstance(value, str) else None


def _string(payload: dict[str, object], key: str) -> str:
    value = payload[key]
    if not isinstance(value, str):
        raise ValueError(f"{key} must be a string")
    return value


def _optional_string(payload: dict[str, object], key: str) -> str | None:
    value: Any = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{key} must be a string or null")
    return value


def _integer(payload: dict[str, object], key: str) -> int:
    value = payload[key]
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{key} must be an integer")
    return value


def _bool(payload: dict[str, object], key: str) -> bool:
    value = payload.get(key, False)
    if not isinstance(value, bool):
        raise ValueError(f"{key} must be a boolean")
    return value


__all__ = ["EffectStageFold"]
