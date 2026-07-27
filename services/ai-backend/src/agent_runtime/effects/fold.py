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
    EffectProjectionBinding,
    EffectRowDecisionState,
    EffectStageDecision,
    EffectStageRevision,
    EffectStageScope,
    EffectStageState,
    EffectStageStatus,
    validate_proposal_content_ref,
)
from agent_runtime.effects.errors import EffectStageNotFound
from agent_runtime.effects.ports import StructuralEvent
from agent_runtime.surfaces_v2.entities import EffectTarget
from agent_runtime.surfaces_v2.ledger_ids import ProposalUriCodec
from agent_runtime.surfaces_v2.ledger_models import (
    EffectActor,
    EffectClass,
    EffectDecisionKind,
    EffectExecutorKind,
    EffectPolicy,
    EffectProposalKind,
    LedgerEventType,
)

_EVENT_STAGED = LedgerEventType.EFFECT_STAGED.value
_EVENT_REVISED = LedgerEventType.EFFECT_REVISED.value
_EVENT_DECISION = LedgerEventType.EFFECT_DECISION_RECORDED.value
_EVENT_PROJECTION_BOUND = LedgerEventType.EFFECT_PROJECTION_BOUND.value
_EVENT_ROW_DECISIONS = LedgerEventType.EFFECT_ROW_DECISIONS_RECORDED.value


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
            elif event.event_type == _EVENT_PROJECTION_BOUND:
                state = cls._apply_projection_binding(state, event)
            elif event.event_type == _EVENT_DECISION:
                state = cls._apply_decision(state, event)
            elif event.event_type == _EVENT_ROW_DECISIONS:
                state = cls._apply_row_decisions(state, event)
        if state is None:
            raise EffectStageNotFound()
        return state

    @classmethod
    def _state_from_staged(cls, event: StructuralEvent) -> EffectStageState | None:
        payload = event.payload
        try:
            _validate_payload_version(payload)
            stage_id = _string(payload, "stage_id")
            proposal_ref, proposal_content_ref = _normalise_proposal_references(
                payload=payload,
                stage_id=stage_id,
                revision=1,
            )
            executor = EffectExecutorKind(_string(payload, "executor"))
            target = EffectTarget(
                executor=executor,
                capability=_string(payload, "capability"),
                op=_string(payload, "op"),
                target_ref=_string(payload, "target_ref"),
                precondition_ref=_optional_string(payload, "precondition_ref"),
                display_label=_string(payload, "display_target"),
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
                proposal_kind=_string(payload, "proposal_kind"),
                proposal_ref=proposal_ref,
                proposal_content_ref=proposal_content_ref,
                proposal_digest=_string(payload, "proposal_digest"),
                proposal_media_type=_string(payload, "proposal_media_type"),
                target_ref=target.target_ref,
                target_digest=_string(payload, "target_digest"),
                display_target=_string(payload, "display_target"),
                precondition_ref=_optional_string(payload, "precondition_ref"),
                precondition_digest=_optional_string(payload, "precondition_digest"),
                safe_diff_ref=None,
                author=author,
                created_at=created_at,
            )
            return EffectStageState(
                stage_id=stage_id,
                scope=EffectStageScope(run_id=event.run_id, owner_ref=owner_ref),
                operation_id=_string(payload, "operation_id"),
                executor=executor,
                target=target,
                target_digest=_string(payload, "target_digest"),
                display_target=_string(payload, "display_target"),
                effect_class=EffectClass(_string(payload, "effect_class")),
                policy_snapshot_ref=_string(payload, "policy_snapshot_ref"),
                policy=policy,
                agent_hold=_bool(payload, "agent_hold"),
                revisions=(revision,),
                status=(
                    EffectStageStatus.PROPOSED
                    if policy is EffectPolicy.AUTO
                    else EffectStageStatus.HELD
                ),
                projection_required=bool(payload.get("projection_required", False)),
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
            _validate_payload_version(payload)
            revision_no = _integer(payload, "revision")
            if revision_no != state.current_revision.revision + 1:
                return state
            proposal_ref, proposal_content_ref = _normalise_proposal_references(
                payload=payload,
                stage_id=state.stage_id,
                revision=revision_no,
            )
            revision = EffectStageRevision(
                revision=revision_no,
                proposal_kind=_string(payload, "proposal_kind"),
                proposal_ref=proposal_ref,
                proposal_content_ref=proposal_content_ref,
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
                "row_decisions": (),
                "projection_binding": None,
                "superseded_revision": approved_revision,
                "updated_at": event.created_at,
            }
        )

    @classmethod
    def _apply_projection_binding(
        cls,
        state: EffectStageState,
        event: StructuralEvent,
    ) -> EffectStageState:
        """Accept only an exact binding for the current required revision."""

        if not state.projection_required or state.status is EffectStageStatus.CANCELLED:
            return state
        payload = event.payload
        try:
            _validate_payload_version(payload)
            binding = EffectProjectionBinding(
                revision=_integer(payload, "revision"),
                projection_ref=_string(payload, "projection_ref"),
                proposal_digest=_string(payload, "proposal_digest"),
                target_digest=_string(payload, "target_digest"),
                bound_at=_string(payload, "bound_at"),
                ledger_id=event.ledger_id,
            )
        except (KeyError, TypeError, ValueError, ValidationError):
            return state
        current = state.current_revision
        if (
            binding.revision != current.revision
            or binding.proposal_digest != current.proposal_digest
            or binding.target_digest != state.target_digest
        ):
            return state
        existing = state.projection_binding
        if existing is not None:
            if existing == binding:
                return state
            return state
        return state.model_copy(
            update={"projection_binding": binding, "updated_at": event.created_at}
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
            _validate_payload_version(payload)
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
                row_keys=_optional_row_keys(payload),
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
        if decision.decision is EffectDecisionKind.APPROVE and not state.approval_ready:
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

    @classmethod
    def _apply_row_decisions(
        cls,
        state: EffectStageState,
        event: StructuralEvent,
    ) -> EffectStageState:
        if (
            state.current_revision.proposal_kind is not EffectProposalKind.ROW_SET
            or state.status
            not in {
                EffectStageStatus.PROPOSED,
                EffectStageStatus.HELD,
                EffectStageStatus.REVISED,
            }
        ):
            return state
        payload = event.payload
        try:
            _validate_payload_version(payload)
            if (
                _integer(payload, "revision") != state.current_revision.revision
                or _string(payload, "proposal_digest")
                != state.current_revision.proposal_digest
                or _string(payload, "target_digest") != state.target_digest
            ):
                return state
            actor = EffectActorIdentity(
                actor=EffectActor(_string(payload, "actor")),
                principal_ref=_string(payload, "actor_ref"),
            )
            decided_at = _string(payload, "decided_at")
            raw_decisions = payload["decisions"]
            if not isinstance(raw_decisions, list | tuple) or not raw_decisions:
                return state
            updates: dict[str, EffectRowDecisionState] = {}
            for item in raw_decisions:
                if not isinstance(item, dict):
                    return state
                row_key = _string(item, "row_key")
                decision = _string(item, "decision")
                if decision not in {"approve", "hold"} or row_key in updates:
                    return state
                updates[row_key] = EffectRowDecisionState(
                    row_key=row_key,
                    decision=decision,
                    actor=actor,
                    decided_at=decided_at,
                    ledger_id=event.ledger_id,
                )
        except (KeyError, TypeError, ValueError, ValidationError):
            return state
        merged = {item.row_key: item for item in state.row_decisions}
        merged.update(updates)
        return state.model_copy(
            update={
                "row_decisions": tuple(merged[key] for key in sorted(merged)),
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


def _normalise_proposal_references(
    *,
    payload: dict[str, object],
    stage_id: str,
    revision: int,
) -> tuple[str, str | None]:
    """Normalize historical v1 events into the two-reference domain form."""

    raw_identity = _string(payload, "proposal_ref")
    content_ref = _optional_string(payload, "proposal_content_ref")
    canonical_identity = ProposalUriCodec.format(stage_id, revision)
    try:
        parsed = ProposalUriCodec.parse(raw_identity)
    except ValueError:
        if content_ref is not None:
            raise ValueError(
                "proposal_ref is neither canonical nor a legacy content ref"
            )
        return canonical_identity, validate_proposal_content_ref(raw_identity)
    if parsed.stage_id != stage_id or parsed.revision != revision:
        raise ValueError("proposal_ref does not identify this stage revision")
    return raw_identity, content_ref


def _validate_payload_version(payload: dict[str, object]) -> None:
    version = payload.get("v", 1)
    if not isinstance(version, int) or isinstance(version, bool) or version != 1:
        raise ValueError("effect event payload version must be 1")


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


def _optional_row_keys(payload: dict[str, object]) -> tuple[str, ...] | None:
    value = payload.get("row_keys")
    if value is None:
        return None
    if not isinstance(value, list | tuple) or not value:
        raise ValueError("row_keys must be a non-empty array")
    result = tuple(value)
    if any(not isinstance(item, str) or not item for item in result):
        raise ValueError("row_keys must contain strings")
    if len(result) != len(set(result)):
        raise ValueError("row_keys must be unique")
    return result


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
