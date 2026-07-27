"""Owner-scoped semantic review and exact recovery for canonical row-set effects."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import hashlib
import json
from typing import Literal, Protocol

from pydantic import Field

from agent_runtime.api.effect_stage_decision_service import (
    EffectStageDecisionService,
)
from agent_runtime.effects.contracts import EffectStageState, EffectStageStatus
from agent_runtime.effects.errors import (
    EffectStageInvalidTransition,
    EffectStageMalformedEvent,
)
from agent_runtime.effects.ports import StructuralEvent
from agent_runtime.execution.contracts import RuntimeContract
from agent_runtime.surfaces_v2.ledger_models import (
    EffectDecisionKind,
    EffectExecutorKind,
    EffectProposalKind,
    LedgerEventType,
)
from agent_runtime.surfaces_v2.rowset import RowFieldChange
from agent_runtime.capabilities.tools.builtin.stage_rowset_write import (
    RowSetEffectProposal,
)

_EVENT_APPLIED = LedgerEventType.EFFECT_APPLIED.value
_EVENT_CLAIMED = LedgerEventType.EFFECT_CLAIMED.value


class RowSetProposalResolverPort(Protocol):
    """Resolve one owner-scoped immutable row-set proposal."""

    async def resolve(
        self,
        *,
        org_id: str,
        user_id: str,
        content_ref: str,
        digest: str,
    ) -> RowSetEffectProposal | None: ...


class RowSetReviewRow(RuntimeContract):
    row_key: str = Field(min_length=1, max_length=256)
    title: str = Field(min_length=1, max_length=512)
    changes: tuple[RowFieldChange, ...]
    decision: Literal["approve", "hold"]
    decision_source: Literal["default", "agent", "user"]
    hold_reason: str | None = Field(default=None, max_length=512)
    apply_outcome: Literal["applied", "failed"] | None = None
    can_decide: bool


class RowSetReviewCounts(RuntimeContract):
    total: int = Field(ge=0)
    approved: int = Field(ge=0)
    held: int = Field(ge=0)
    applied: int = Field(ge=0)
    failed: int = Field(ge=0)


class RowSetReviewAction(RuntimeContract):
    kind: Literal["apply", "retry_failed"]
    row_keys: tuple[str, ...]
    basis_sequence_no: int = Field(ge=1)
    basis_ledger_id: str | None = None


class RowSetEffectReview(RuntimeContract):
    stage_id: str
    revision: int = Field(ge=1)
    proposal_digest: str
    target_digest: str
    title: str
    source_connector: str
    source_op: str
    status: Literal[
        "staged",
        "held",
        "apply_pending",
        "partial",
        "applied",
    ]
    rows: tuple[RowSetReviewRow, ...]
    counts: RowSetReviewCounts
    action: RowSetReviewAction | None
    ledger_id: str
    last_sequence_no: int = Field(ge=1)


@dataclass(frozen=True)
class RowSetEffectReviewService:
    """Compose immutable material with the canonical effect ledger."""

    decisions: EffectStageDecisionService
    material: RowSetProposalResolverPort

    async def review(
        self,
        *,
        org_id: str,
        user_id: str,
        run_id: str,
        stage_id: str,
    ) -> RowSetEffectReview:
        state, events = await self._state_and_events(
            org_id=org_id,
            user_id=user_id,
            run_id=run_id,
            stage_id=stage_id,
        )
        proposal = await self._proposal(
            org_id=org_id,
            user_id=user_id,
            state=state,
        )
        return _project_review(state=state, proposal=proposal, events=events)

    async def record_row_decisions(
        self,
        *,
        org_id: str,
        user_id: str,
        run_id: str,
        stage_id: str,
        revision: int,
        proposal_digest: str,
        target_digest: str,
        decisions: Mapping[str, Literal["approve", "hold"]],
    ) -> RowSetEffectReview:
        current = await self.review(
            org_id=org_id,
            user_id=user_id,
            run_id=run_id,
            stage_id=stage_id,
        )
        _assert_snapshot(
            current,
            revision=revision,
            proposal_digest=proposal_digest,
            target_digest=target_digest,
        )
        known = {row.row_key for row in current.rows if row.apply_outcome is None}
        if (
            current.status not in {"staged", "held"}
            or not decisions
            or not set(decisions).issubset(known)
        ):
            raise EffectStageInvalidTransition()
        await self.decisions.record_row_decisions(
            org_id=org_id,
            user_id=user_id,
            run_id=run_id,
            stage_id=stage_id,
            revision=revision,
            decisions=dict(decisions),
            proposal_digest=proposal_digest,
            target_digest=target_digest,
            allowed_executors=frozenset({EffectExecutorKind.BUILTIN}),
            idempotency_key=_idempotency_key(
                "rowset-decisions",
                {
                    "run_id": run_id,
                    "stage_id": stage_id,
                    "revision": revision,
                    "proposal_digest": proposal_digest,
                    "target_digest": target_digest,
                    "decisions": sorted(decisions.items()),
                },
            ),
        )
        return await self.review(
            org_id=org_id,
            user_id=user_id,
            run_id=run_id,
            stage_id=stage_id,
        )

    async def apply(
        self,
        *,
        org_id: str,
        user_id: str,
        run_id: str,
        stage_id: str,
        revision: int,
        proposal_digest: str,
        target_digest: str,
        row_keys: tuple[str, ...],
        basis_sequence_no: int,
    ) -> RowSetEffectReview:
        current = await self.review(
            org_id=org_id,
            user_id=user_id,
            run_id=run_id,
            stage_id=stage_id,
        )
        _assert_action(
            current,
            kind="apply",
            revision=revision,
            proposal_digest=proposal_digest,
            target_digest=target_digest,
            row_keys=row_keys,
            basis_sequence_no=basis_sequence_no,
            basis_ledger_id=None,
        )
        await self.decisions.record_decision(
            org_id=org_id,
            user_id=user_id,
            run_id=run_id,
            stage_id=stage_id,
            revision=revision,
            decision=EffectDecisionKind.APPROVE,
            proposal_digest=proposal_digest,
            target_digest=target_digest,
            row_keys=row_keys,
            allowed_executors=frozenset({EffectExecutorKind.BUILTIN}),
            idempotency_namespace="effect-rowset-apply",
        )
        return await self.review(
            org_id=org_id,
            user_id=user_id,
            run_id=run_id,
            stage_id=stage_id,
        )

    async def retry(
        self,
        *,
        org_id: str,
        user_id: str,
        run_id: str,
        stage_id: str,
        revision: int,
        proposal_digest: str,
        target_digest: str,
        row_keys: tuple[str, ...],
        basis_sequence_no: int,
        basis_ledger_id: str,
    ) -> RowSetEffectReview:
        current = await self.review(
            org_id=org_id,
            user_id=user_id,
            run_id=run_id,
            stage_id=stage_id,
        )
        _assert_action(
            current,
            kind="retry_failed",
            revision=revision,
            proposal_digest=proposal_digest,
            target_digest=target_digest,
            row_keys=row_keys,
            basis_sequence_no=basis_sequence_no,
            basis_ledger_id=basis_ledger_id,
        )
        await self.decisions.enqueue_rowset_retry(
            org_id=org_id,
            user_id=user_id,
            run_id=run_id,
            stage_id=stage_id,
            revision=revision,
            proposal_digest=proposal_digest,
            target_digest=target_digest,
            row_keys=row_keys,
            basis_ledger_id=basis_ledger_id,
            allowed_executors=frozenset({EffectExecutorKind.BUILTIN}),
        )
        return current

    async def _state_and_events(
        self,
        *,
        org_id: str,
        user_id: str,
        run_id: str,
        stage_id: str,
    ) -> tuple[EffectStageState, tuple[StructuralEvent, ...]]:
        state, events = await self.decisions.stage_history(
            org_id=org_id,
            user_id=user_id,
            run_id=run_id,
            stage_id=stage_id,
            allowed_executors=frozenset({EffectExecutorKind.BUILTIN}),
        )
        if (
            state.current_revision.proposal_kind is not EffectProposalKind.ROW_SET
            or state.current_revision.proposal_content_ref is None
        ):
            raise EffectStageInvalidTransition()
        return state, tuple(events)

    async def _proposal(
        self,
        *,
        org_id: str,
        user_id: str,
        state: EffectStageState,
    ) -> RowSetEffectProposal:
        revision = state.current_revision
        content_ref = revision.proposal_content_ref
        if content_ref is None:
            raise EffectStageMalformedEvent()
        proposal = await self.material.resolve(
            org_id=org_id,
            user_id=user_id,
            content_ref=content_ref,
            digest=revision.proposal_digest,
        )
        if proposal is None:
            raise EffectStageMalformedEvent()
        return proposal


def _project_review(
    *,
    state: EffectStageState,
    proposal: RowSetEffectProposal,
    events: Sequence[StructuralEvent],
) -> RowSetEffectReview:
    ordered = sorted(events, key=lambda item: (item.sequence_no, item.ledger_id))
    if not ordered:
        raise EffectStageMalformedEvent()
    agent_holds = {item.row_key: item.reason for item in proposal.agent_holds}
    user_decisions = {item.row_key: item.decision for item in state.row_decisions}
    proposal_row_keys = {item.row_key for item in proposal.rows}
    approved_scope = (
        set(state.decision.row_keys or ())
        if state.decision is not None
        and state.decision.decision is EffectDecisionKind.APPROVE
        else None
    )
    outcomes: dict[str, Literal["applied", "failed"]] = {}
    latest_result: StructuralEvent | None = None
    latest_claim_sequence = 0
    for event in ordered:
        if event.event_type == _EVENT_CLAIMED:
            latest_claim_sequence = max(latest_claim_sequence, event.sequence_no)
        if event.event_type != _EVENT_APPLIED:
            continue
        row_results = event.payload.get("row_results")
        if not isinstance(row_results, (list, tuple)):
            continue
        parsed: dict[str, Literal["applied", "failed"]] = {}
        for item in row_results:
            if not isinstance(item, dict):
                raise EffectStageMalformedEvent()
            key = item.get("row_key")
            outcome = item.get("outcome")
            if (
                not isinstance(key, str)
                or key in parsed
                or outcome not in {"applied", "failed"}
            ):
                raise EffectStageMalformedEvent()
            parsed[key] = outcome
        if (
            not parsed
            or approved_scope is None
            or not set(parsed).issubset(proposal_row_keys)
            or not set(parsed).issubset(approved_scope)
        ):
            raise EffectStageMalformedEvent()
        outcomes.update(parsed)
        latest_result = event

    pending = state.status is EffectStageStatus.APPROVED and (
        latest_result is None or latest_claim_sequence > latest_result.sequence_no
    )
    rows: list[RowSetReviewRow] = []
    for row in proposal.rows:
        if row.row_key in user_decisions:
            decision = user_decisions[row.row_key]
            source: Literal["default", "agent", "user"] = "user"
        elif row.row_key in agent_holds:
            decision = "hold"
            source = "agent"
        else:
            decision = "approve"
            source = "default"
        rows.append(
            RowSetReviewRow(
                row_key=row.row_key,
                title=row.title,
                changes=row.changes,
                decision=decision,
                decision_source=source,
                hold_reason=agent_holds.get(row.row_key),
                apply_outcome=outcomes.get(row.row_key),
                can_decide=(
                    not pending
                    and latest_result is None
                    and state.status
                    in {
                        EffectStageStatus.PROPOSED,
                        EffectStageStatus.HELD,
                        EffectStageStatus.REVISED,
                    }
                ),
            )
        )

    approved = tuple(
        row.row_key
        for row in rows
        if row.decision == "approve" and row.apply_outcome is None
    )
    failed = tuple(row.row_key for row in rows if row.apply_outcome == "failed")
    applied_count = sum(row.apply_outcome == "applied" for row in rows)
    held_count = sum(row.decision == "hold" for row in rows)
    action: RowSetReviewAction | None = None
    if not pending and latest_result is None and approved:
        action = RowSetReviewAction(
            kind="apply",
            row_keys=approved,
            basis_sequence_no=ordered[-1].sequence_no,
        )
    elif not pending and latest_result is not None and failed:
        action = RowSetReviewAction(
            kind="retry_failed",
            row_keys=failed,
            basis_sequence_no=latest_result.sequence_no,
            basis_ledger_id=latest_result.ledger_id,
        )

    if pending:
        status = "apply_pending"
    elif failed:
        status = "partial"
    elif latest_result is not None:
        status = "applied"
    elif state.status is EffectStageStatus.HELD and not approved:
        status = "held"
    else:
        status = "staged"
    return RowSetEffectReview(
        stage_id=state.stage_id,
        revision=state.current_revision.revision,
        proposal_digest=state.current_revision.proposal_digest,
        target_digest=state.target_digest,
        title=proposal.title,
        source_connector=proposal.target_connector,
        source_op=proposal.target_op,
        status=status,
        rows=tuple(rows),
        counts=RowSetReviewCounts(
            total=len(rows),
            approved=sum(row.decision == "approve" for row in rows),
            held=held_count,
            applied=applied_count,
            failed=len(failed),
        ),
        action=action,
        ledger_id=ordered[-1].ledger_id,
        last_sequence_no=ordered[-1].sequence_no,
    )


def _assert_snapshot(
    review: RowSetEffectReview,
    *,
    revision: int,
    proposal_digest: str,
    target_digest: str,
) -> None:
    if (
        review.revision != revision
        or review.proposal_digest != proposal_digest
        or review.target_digest != target_digest
    ):
        raise EffectStageInvalidTransition()


def _assert_action(
    review: RowSetEffectReview,
    *,
    kind: Literal["apply", "retry_failed"],
    revision: int,
    proposal_digest: str,
    target_digest: str,
    row_keys: tuple[str, ...],
    basis_sequence_no: int,
    basis_ledger_id: str | None,
) -> None:
    _assert_snapshot(
        review,
        revision=revision,
        proposal_digest=proposal_digest,
        target_digest=target_digest,
    )
    action = review.action
    if (
        action is None
        or action.kind != kind
        or set(action.row_keys) != set(row_keys)
        or action.basis_sequence_no != basis_sequence_no
        or action.basis_ledger_id != basis_ledger_id
    ):
        raise EffectStageInvalidTransition()


def _idempotency_key(namespace: str, value: object) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return f"{namespace}-{hashlib.sha256(encoded).hexdigest()}"


__all__ = (
    "RowSetEffectReview",
    "RowSetEffectReviewService",
    "RowSetProposalResolverPort",
    "RowSetReviewAction",
    "RowSetReviewCounts",
    "RowSetReviewRow",
)
