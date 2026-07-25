"""Adversarial contracts for the pure PRD-E1 D10/D11 retention planner."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from agent_runtime.surfaces_v2.lifecycle_refs import LifecycleReferenceScheme
from agent_runtime.surfaces_v2.retention import (
    RetentionCandidate,
    RetentionCandidateKind,
    RetentionCandidateState,
    RetentionDecisionState,
    RetentionEnumerationCoverage,
    RetentionLegalHoldCoverage,
    RetentionLegalHoldScope,
    RetentionLegalHoldState,
    RetentionLogicalReference,
    RetentionPlanCursor,
    RetentionPlanner,
    RetentionPlanningError,
    RetentionPlanningErrorCode,
    RetentionPlanningPolicy,
    RetentionPlanningRequest,
    RetentionReasonCode,
    RetentionReferenceEnumeration,
    RetentionReferenceLifecycleState,
    RetentionReferencePresence,
    RetentionReferenceRole,
)


NOW = datetime(2026, 7, 25, 12, 0, tzinfo=UTC)
TENANT = "org_alpha"
OTHER_TENANT = "org_beta"


def _reference(
    reference_id: str = "ref_01",
    *,
    tenant_id: str = TENANT,
    scheme: str = LifecycleReferenceScheme.ARTIFACT_BLOB.value,
    role: RetentionReferenceRole = RetentionReferenceRole.ARTIFACT,
    lifecycle_state: RetentionReferenceLifecycleState = (
        RetentionReferenceLifecycleState.TERMINAL
    ),
    presence: RetentionReferencePresence = RetentionReferencePresence.GONE,
    expires_at: datetime | None = None,
) -> RetentionLogicalReference:
    return RetentionLogicalReference(
        reference_id=reference_id,
        tenant_id=tenant_id,
        scheme=scheme,
        role=role,
        lifecycle_state=lifecycle_state,
        presence=presence,
        expires_at=expires_at,
    )


def _candidate(
    candidate_id: str = "candidate_01",
    *,
    state: RetentionCandidateState = RetentionCandidateState.LOGICALLY_TOMBSTONED,
    retention_expires_at: datetime = NOW - timedelta(days=8),
    tombstoned_at: datetime | None = NOW - timedelta(days=8),
    coverage: RetentionEnumerationCoverage = RetentionEnumerationCoverage.COMPLETE_TENANT,
    references: tuple[RetentionLogicalReference, ...] = (),
    holds: tuple[RetentionLegalHoldCoverage, ...] = (),
) -> RetentionCandidate:
    if state is RetentionCandidateState.ACTIVE:
        tombstoned_at = None
    return RetentionCandidate(
        candidate_id=candidate_id,
        tenant_id=TENANT,
        kind=RetentionCandidateKind.ARTIFACT_BLOB,
        state=state,
        retention_expires_at=retention_expires_at,
        tombstoned_at=tombstoned_at,
        enumeration=RetentionReferenceEnumeration(
            coverage=coverage,
            references=references,
        ),
        legal_hold_coverage=holds,
    )


def _request(
    *candidates: RetentionCandidate,
    as_of: datetime = NOW,
    grace: timedelta = timedelta(days=7),
    cursor: RetentionPlanCursor | None = None,
    limit: int = 100,
    snapshot_id: str = "snapshot_01",
) -> RetentionPlanningRequest:
    return RetentionPlanningRequest(
        tenant_id=TENANT,
        snapshot_id=snapshot_id,
        as_of=as_of,
        policy=RetentionPlanningPolicy(physical_grace_period=grace),
        candidates=candidates,
        cursor=cursor,
        limit=limit,
    )


def _single_decision(request: RetentionPlanningRequest):
    plan = RetentionPlanner().plan(request)
    assert len(plan.decisions) == 1
    return plan.decisions[0]


def test_active_candidate_is_retained_then_requires_logical_tombstone_after_expiry() -> (
    None
):
    candidate = _candidate(
        state=RetentionCandidateState.ACTIVE,
        retention_expires_at=NOW + timedelta(seconds=1),
    )

    retained = _single_decision(_request(candidate))
    assert retained.state is RetentionDecisionState.RETAIN
    assert retained.reasons == (RetentionReasonCode.RETENTION_WINDOW_OPEN,)

    tombstone = _single_decision(_request(candidate, as_of=NOW + timedelta(days=1)))
    assert tombstone.state is RetentionDecisionState.LOGICAL_TOMBSTONE_ONLY
    assert tombstone.reasons == (RetentionReasonCode.LOGICAL_TOMBSTONE_REQUIRED,)


def test_expiry_and_physical_grace_are_separate_required_gates() -> None:
    terminal_ref = _reference(
        role=RetentionReferenceRole.ARTIFACT,
        presence=RetentionReferencePresence.PRESENT,
        expires_at=NOW - timedelta(seconds=1),
    )
    candidate = _candidate(
        tombstoned_at=NOW - timedelta(hours=1),
        references=(terminal_ref,),
    )

    held_by_grace = _single_decision(_request(candidate, grace=timedelta(hours=2)))
    assert held_by_grace.state is RetentionDecisionState.LOGICAL_TOMBSTONE_ONLY
    assert held_by_grace.reasons == (RetentionReasonCode.PHYSICAL_GRACE_OPEN,)

    eligible = _single_decision(
        _request(candidate, as_of=NOW + timedelta(hours=2), grace=timedelta(hours=2))
    )
    assert eligible.state is RetentionDecisionState.PHYSICALLY_ELIGIBLE


def test_terminal_stage_releases_only_after_its_logical_ref_is_gone_or_expired() -> (
    None
):
    active_stage = _reference(
        role=RetentionReferenceRole.STAGE,
        lifecycle_state=RetentionReferenceLifecycleState.ACTIVE,
        presence=RetentionReferencePresence.PRESENT,
        expires_at=NOW - timedelta(days=30),
    )
    blocked = _single_decision(_request(_candidate(references=(active_stage,))))
    assert blocked.state is RetentionDecisionState.LOGICAL_TOMBSTONE_ONLY
    assert RetentionReasonCode.ACTIVE_OR_PENDING_STAGE in blocked.reasons

    terminal_stage = active_stage.model_copy(
        update={
            "lifecycle_state": RetentionReferenceLifecycleState.TERMINAL,
            "presence": RetentionReferencePresence.GONE,
        }
    )
    eligible = _single_decision(_request(_candidate(references=(terminal_stage,))))
    assert eligible.state is RetentionDecisionState.PHYSICALLY_ELIGIBLE


@pytest.mark.parametrize(
    ("role", "lifecycle_state", "expected_reason"),
    (
        (
            RetentionReferenceRole.STAGE,
            RetentionReferenceLifecycleState.PENDING,
            RetentionReasonCode.ACTIVE_OR_PENDING_STAGE,
        ),
        (
            RetentionReferenceRole.EFFECT,
            RetentionReferenceLifecycleState.ACTIVE,
            RetentionReasonCode.ACTIVE_OR_PENDING_EFFECT,
        ),
    ),
)
def test_active_or_pending_stage_and_effect_refs_fail_closed(
    role: RetentionReferenceRole,
    lifecycle_state: RetentionReferenceLifecycleState,
    expected_reason: RetentionReasonCode,
) -> None:
    reference = _reference(
        role=role,
        lifecycle_state=lifecycle_state,
        presence=RetentionReferencePresence.PRESENT,
    )

    decision = _single_decision(_request(_candidate(references=(reference,))))

    assert decision.state is RetentionDecisionState.LOGICAL_TOMBSTONE_ONLY
    assert expected_reason in decision.reasons


@pytest.mark.parametrize(
    ("role", "expected_reason"),
    (
        (RetentionReferenceRole.RECEIPT, RetentionReasonCode.LIVE_RECEIPT_REFERENCE),
        (RetentionReferenceRole.LEGAL_HOLD, RetentionReasonCode.LIVE_HOLD_REFERENCE),
        (RetentionReferenceRole.RECOVERY, RetentionReasonCode.LIVE_RECOVERY_REFERENCE),
    ),
)
def test_live_receipt_hold_and_recovery_references_are_never_physically_eligible(
    role: RetentionReferenceRole,
    expected_reason: RetentionReasonCode,
) -> None:
    reference = _reference(
        role=role,
        presence=RetentionReferencePresence.PRESENT,
        expires_at=NOW + timedelta(days=1),
    )

    decision = _single_decision(_request(_candidate(references=(reference,))))

    assert decision.state is RetentionDecisionState.LOGICAL_TOMBSTONE_ONLY
    assert expected_reason in decision.reasons


def test_active_legal_hold_blocks_then_audited_release_allows_eligibility() -> None:
    active_hold = RetentionLegalHoldCoverage(
        hold_id="hold_01",
        scope=RetentionLegalHoldScope.CONVERSATION,
        state=RetentionLegalHoldState.ACTIVE,
    )
    held = _single_decision(_request(_candidate(holds=(active_hold,))))
    assert held.state is RetentionDecisionState.LOGICAL_TOMBSTONE_ONLY
    assert held.reasons == (RetentionReasonCode.ACTIVE_LEGAL_HOLD,)

    released_hold = active_hold.model_copy(
        update={"state": RetentionLegalHoldState.RELEASED}
    )
    released = _single_decision(_request(_candidate(holds=(released_hold,))))
    assert released.state is RetentionDecisionState.PHYSICALLY_ELIGIBLE


def test_shared_cross_tenant_blob_needs_global_complete_and_released_enumeration() -> (
    None
):
    shared_ref = _reference(reference_id="ref_shared", tenant_id=OTHER_TENANT)
    tenant_only = _single_decision(
        _request(
            _candidate(
                coverage=RetentionEnumerationCoverage.COMPLETE_TENANT,
                references=(shared_ref,),
            )
        )
    )
    assert tenant_only.state is RetentionDecisionState.LOGICAL_TOMBSTONE_ONLY
    assert RetentionReasonCode.CROSS_TENANT_REFERENCE in tenant_only.reasons

    global_complete = _single_decision(
        _request(
            _candidate(
                coverage=RetentionEnumerationCoverage.COMPLETE_GLOBAL,
                references=(shared_ref,),
            )
        )
    )
    assert global_complete.state is RetentionDecisionState.PHYSICALLY_ELIGIBLE

    live_shared_ref = shared_ref.model_copy(
        update={"presence": RetentionReferencePresence.PRESENT}
    )
    live = _single_decision(
        _request(
            _candidate(
                coverage=RetentionEnumerationCoverage.COMPLETE_GLOBAL,
                references=(live_shared_ref,),
            )
        )
    )
    assert live.state is RetentionDecisionState.LOGICAL_TOMBSTONE_ONLY
    assert RetentionReasonCode.CROSS_TENANT_REFERENCE in live.reasons


def test_unknown_reference_scheme_and_incomplete_enumeration_fail_closed_without_leak() -> (
    None
):
    unknown = _reference(scheme="future-owner", reference_id="ref_secret")
    decision = _single_decision(
        _request(
            _candidate(
                coverage=RetentionEnumerationCoverage.INCOMPLETE,
                references=(unknown,),
            )
        )
    )

    assert decision.state is RetentionDecisionState.LOGICAL_TOMBSTONE_ONLY
    assert decision.reasons == (
        RetentionReasonCode.ENUMERATION_INCOMPLETE,
        RetentionReasonCode.UNKNOWN_REFERENCE_SCHEME,
    )
    serialized = json.dumps(decision.model_dump(mode="json"))
    assert "future-owner" not in serialized
    assert "ref_secret" not in serialized


def test_registered_lifecycle_schemes_with_underscores_remain_accepted() -> None:
    surface_ref = _reference(scheme=LifecycleReferenceScheme.BARE_SURFACE.value)

    decision = _single_decision(_request(_candidate(references=(surface_ref,))))

    assert decision.state is RetentionDecisionState.PHYSICALLY_ELIGIBLE


def test_corrupt_tombstoned_model_without_timestamp_is_never_physically_eligible() -> (
    None
):
    # Pydantic's model_copy(update=...) intentionally does not re-validate, so
    # the planner itself must preserve the physical-deletion fail-closed rule.
    corrupt = _candidate().model_copy(update={"tombstoned_at": None})
    unvalidated_snapshot = RetentionPlanningRequest.model_construct(
        tenant_id=TENANT,
        snapshot_id="snapshot_01",
        as_of=NOW,
        policy=RetentionPlanningPolicy(physical_grace_period=timedelta(days=7)),
        candidates=(corrupt,),
        cursor=None,
        limit=100,
    )

    plan = RetentionPlanner().plan(unvalidated_snapshot)
    assert len(plan.decisions) == 1
    decision = plan.decisions[0]

    assert decision.state is RetentionDecisionState.LOGICAL_TOMBSTONE_ONLY
    assert decision.reasons == (RetentionReasonCode.MISSING_TOMBSTONE_TIMESTAMP,)


def test_cursor_is_snapshot_bound_keyset_paged_and_idempotent() -> None:
    candidates = tuple(_candidate(f"candidate_{index:02d}") for index in range(1, 4))
    first_request = _request(*reversed(candidates), limit=2)

    first = RetentionPlanner().plan(first_request)
    assert RetentionPlanner().plan(first_request) == first
    assert [row.candidate_id for row in first.decisions] == [
        "candidate_01",
        "candidate_02",
    ]
    assert first.has_more is True
    assert first.next_cursor is not None

    second = RetentionPlanner().plan(
        _request(*candidates, limit=2, cursor=first.next_cursor)
    )
    assert [row.candidate_id for row in second.decisions] == ["candidate_03"]
    assert second.next_cursor is None
    assert second.has_more is False

    wrong_snapshot = RetentionPlanCursor(
        tenant_id=TENANT,
        snapshot_id="snapshot_other",
        after_candidate_id="candidate_02",
    )
    with pytest.raises(RetentionPlanningError) as raised:
        RetentionPlanner().plan(_request(*candidates, cursor=wrong_snapshot))
    assert raised.value.code is RetentionPlanningErrorCode.CURSOR_SCOPE_MISMATCH


def test_untrusted_paths_are_rejected_from_opaque_candidate_and_reference_handles() -> (
    None
):
    with pytest.raises(ValidationError):
        _candidate(candidate_id="../../private")
    with pytest.raises(ValidationError):
        _reference(reference_id="file:///private/secret")
