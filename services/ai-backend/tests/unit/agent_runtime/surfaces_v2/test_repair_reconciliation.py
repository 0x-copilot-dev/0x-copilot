"""Focused D12 repair/reconciliation candidate-fold tests."""

from __future__ import annotations

from datetime import UTC, datetime
from itertools import permutations
import json

import pytest
from pydantic import ValidationError

from agent_runtime.surfaces_v2.lifecycle_refs import LifecycleReferenceScheme
from agent_runtime.surfaces_v2.repair_reconciliation import (
    RepairAction,
    RepairCandidateKind,
    RepairDecisionState,
    RepairEffectState,
    RepairEvidenceState,
    RepairGraphCoverage,
    RepairLegalHoldState,
    RepairOwnerState,
    RepairPlanCursor,
    RepairPlanner,
    RepairPlanningError,
    RepairPlanningErrorCode,
    RepairPlanningRequest,
    RepairReasonCode,
    RepairSnapshotRecord,
)


NOW = datetime(2026, 7, 25, 10, 30, tzinfo=UTC)
TENANT = "tenant-a"
SNAPSHOT = "snapshot-a"


def _record(
    *,
    candidate_id: str = "candidate-a",
    tenant_id: str = TENANT,
    kind: RepairCandidateKind = RepairCandidateKind.METADATA_OUTBOX,
    reference_scheme: str = LifecycleReferenceScheme.ARTIFACT_BLOB.value,
    graph_coverage: RepairGraphCoverage = RepairGraphCoverage.COMPLETE,
    legal_hold: RepairLegalHoldState = RepairLegalHoldState.NONE,
    evidence_state: RepairEvidenceState = RepairEvidenceState.VERIFIED,
    evidence_id: str | None = "evidence-a",
    owner_state: RepairOwnerState = RepairOwnerState.TERMINAL,
    effect_state: RepairEffectState = RepairEffectState.NOT_APPLICABLE,
    reconcile_supported: bool | None = None,
    quiet_period_elapsed: bool | None = None,
) -> RepairSnapshotRecord:
    return RepairSnapshotRecord(
        candidate_id=candidate_id,
        tenant_id=tenant_id,
        kind=kind,
        reference_scheme=reference_scheme,
        graph_coverage=graph_coverage,
        legal_hold=legal_hold,
        evidence_state=evidence_state,
        evidence_id=evidence_id,
        owner_state=owner_state,
        effect_state=effect_state,
        reconcile_supported=reconcile_supported,
        quiet_period_elapsed=quiet_period_elapsed,
    )


def _request(
    *records: RepairSnapshotRecord,
    cursor: RepairPlanCursor | None = None,
    limit: int = 100,
) -> RepairPlanningRequest:
    return RepairPlanningRequest(
        tenant_id=TENANT,
        snapshot_id=SNAPSHOT,
        as_of=NOW,
        records=records,
        cursor=cursor,
        limit=limit,
    )


@pytest.mark.parametrize(
    ("kind", "action", "kwargs"),
    [
        (
            RepairCandidateKind.METADATA_OUTBOX,
            RepairAction.METADATA_OUTBOX_REPAIR_CANDIDATE,
            {},
        ),
        (
            RepairCandidateKind.ORPHAN_ARTIFACT_OR_TEMP,
            RepairAction.ORPHAN_CLEANUP_CANDIDATE,
            {"owner_state": RepairOwnerState.TERMINAL},
        ),
        (
            RepairCandidateKind.STALE_PREPARED_RESOURCE,
            RepairAction.STALE_PREPARED_CLEANUP_CANDIDATE,
            {"owner_state": RepairOwnerState.TERMINAL},
        ),
        (
            RepairCandidateKind.RECEIPT_SOURCE_PROJECTION,
            RepairAction.RECEIPT_SOURCE_REBUILD_CANDIDATE,
            {},
        ),
        (
            RepairCandidateKind.USAGE_EDGE,
            RepairAction.USAGE_EDGE_REPAIR_CANDIDATE,
            {},
        ),
        (
            RepairCandidateKind.AUDIT_VERIFICATION,
            RepairAction.AUDIT_VERIFICATION_SAMPLE,
            {},
        ),
        (
            RepairCandidateKind.EFFECT_RECONCILIATION,
            RepairAction.EFFECT_RECONCILE_CANDIDATE,
            {
                "owner_state": RepairOwnerState.TERMINAL,
                "effect_state": RepairEffectState.CLAIMED,
                "reconcile_supported": True,
                "quiet_period_elapsed": True,
            },
        ),
    ],
)
def test_verified_records_classify_each_d12_family_as_a_candidate(
    kind: RepairCandidateKind,
    action: RepairAction,
    kwargs: dict[str, object],
) -> None:
    decision = RepairPlanner().plan(_request(_record(kind=kind, **kwargs))).decisions[0]

    assert decision.state is RepairDecisionState.CANDIDATE
    assert decision.action is action
    assert decision.reasons == (RepairReasonCode.VERIFIED_REPAIR_SIGNAL,)


def test_plan_is_idempotent_and_invariant_to_snapshot_record_order() -> None:
    records = (
        _record(candidate_id="candidate-c"),
        _record(candidate_id="candidate-a"),
        _record(candidate_id="candidate-b"),
    )
    planner = RepairPlanner()
    expected = planner.plan(_request(*records))

    assert planner.plan(_request(*records)) == expected
    for ordering in permutations(records):
        assert planner.plan(_request(*ordering)) == expected


def test_plan_output_is_opaque_and_contains_no_execution_or_approval_instruction() -> (
    None
):
    plan = RepairPlanner().plan(
        _request(
            _record(
                candidate_id="candidate-opaque",
                evidence_id="evidence-secret-token",
                reference_scheme=LifecycleReferenceScheme.ARTIFACT_BLOB.value,
            )
        )
    )
    rendered = json.dumps(plan.model_dump(mode="json"), sort_keys=True)

    assert "candidate-opaque" in rendered
    assert "evidence-secret-token" not in rendered
    assert LifecycleReferenceScheme.ARTIFACT_BLOB.value not in rendered
    assert "approval" not in rendered
    assert "apply" not in rendered
    assert "resend" not in rendered
    assert "path" not in rendered


def test_physical_path_like_identifiers_are_rejected_at_the_snapshot_boundary() -> None:
    with pytest.raises(ValidationError):
        _record(candidate_id="/private/workspace/secret.md")
    with pytest.raises(ValidationError):
        _record(evidence_id="file:///private/workspace/secret.md")


def test_nonterminal_indeterminate_effect_is_withheld_without_an_action() -> None:
    decision = (
        RepairPlanner()
        .plan(
            _request(
                _record(
                    kind=RepairCandidateKind.EFFECT_RECONCILIATION,
                    effect_state=RepairEffectState.INDETERMINATE,
                    owner_state=RepairOwnerState.ACTIVE,
                    reconcile_supported=True,
                    quiet_period_elapsed=True,
                )
            )
        )
        .decisions[0]
    )

    assert decision.state is RepairDecisionState.WITHHELD
    assert decision.action is None
    assert RepairReasonCode.NONTERMINAL_UNCERTAIN_EFFECT in decision.reasons


def test_terminal_claimed_effect_is_only_a_reconcile_candidate() -> None:
    decision = (
        RepairPlanner()
        .plan(
            _request(
                _record(
                    kind=RepairCandidateKind.EFFECT_RECONCILIATION,
                    effect_state=RepairEffectState.CLAIMED,
                    owner_state=RepairOwnerState.TERMINAL,
                    reconcile_supported=True,
                    quiet_period_elapsed=True,
                )
            )
        )
        .decisions[0]
    )

    assert decision.state is RepairDecisionState.CANDIDATE
    assert decision.action is RepairAction.EFFECT_RECONCILE_CANDIDATE
    assert all(
        forbidden not in action.value
        for action in RepairAction
        for forbidden in ("approval", "apply", "resend", "enqueue")
    )


@pytest.mark.parametrize(
    "legal_hold",
    [RepairLegalHoldState.ACTIVE, RepairLegalHoldState.UNKNOWN],
)
def test_active_or_unverified_legal_hold_fails_closed(
    legal_hold: RepairLegalHoldState,
) -> None:
    decision = (
        RepairPlanner().plan(_request(_record(legal_hold=legal_hold))).decisions[0]
    )

    assert decision.state is RepairDecisionState.WITHHELD
    assert decision.action is None
    expected = (
        RepairReasonCode.LIVE_LEGAL_HOLD
        if legal_hold is RepairLegalHoldState.ACTIVE
        else RepairReasonCode.MISSING_EVIDENCE
    )
    assert expected in decision.reasons


def test_cross_tenant_snapshot_record_rejects_the_entire_plan() -> None:
    with pytest.raises(RepairPlanningError) as raised:
        RepairPlanner().plan(_request(_record(tenant_id="tenant-b")))

    assert raised.value.code is RepairPlanningErrorCode.CANDIDATE_TENANT_MISMATCH
    assert "tenant-b" not in str(raised.value)


def test_unknown_reference_scheme_and_incomplete_graph_are_withheld() -> None:
    decision = (
        RepairPlanner()
        .plan(
            _request(
                _record(
                    reference_scheme="future-unknown-ref",
                    graph_coverage=RepairGraphCoverage.INCOMPLETE,
                )
            )
        )
        .decisions[0]
    )

    assert decision.state is RepairDecisionState.WITHHELD
    assert decision.action is None
    assert decision.reasons == (
        RepairReasonCode.INCOMPLETE_GRAPH,
        RepairReasonCode.UNKNOWN_REFERENCE_SCHEME,
    )


def test_missing_or_corrupt_evidence_stays_withheld_even_after_model_copy() -> None:
    corrupt = _record().model_copy(
        update={"evidence_state": RepairEvidenceState.VERIFIED, "evidence_id": None}
    )

    decision = RepairPlanner().plan(_request(corrupt)).decisions[0]

    assert decision.state is RepairDecisionState.WITHHELD
    assert decision.action is None
    assert decision.reasons == (RepairReasonCode.MISSING_EVIDENCE,)


def test_cleanup_candidate_requires_a_terminal_owner() -> None:
    decision = (
        RepairPlanner()
        .plan(
            _request(
                _record(
                    kind=RepairCandidateKind.STALE_PREPARED_RESOURCE,
                    owner_state=RepairOwnerState.UNKNOWN,
                )
            )
        )
        .decisions[0]
    )

    assert decision.state is RepairDecisionState.WITHHELD
    assert decision.reasons == (RepairReasonCode.NONTERMINAL_RESOURCE,)


def test_cursor_pagination_is_deterministic_and_snapshot_bound() -> None:
    records = (
        _record(candidate_id="candidate-c"),
        _record(candidate_id="candidate-a"),
        _record(candidate_id="candidate-b"),
    )
    planner = RepairPlanner()

    first = planner.plan(_request(*records, limit=2))
    assert tuple(row.candidate_id for row in first.decisions) == (
        "candidate-a",
        "candidate-b",
    )
    assert first.has_more is True
    assert first.next_cursor == RepairPlanCursor(
        tenant_id=TENANT,
        snapshot_id=SNAPSHOT,
        after_candidate_id="candidate-b",
    )

    second = planner.plan(
        _request(*reversed(records), cursor=first.next_cursor, limit=2)
    )
    assert tuple(row.candidate_id for row in second.decisions) == ("candidate-c",)
    assert second.next_cursor is None
    assert second.has_more is False

    wrong_cursor = RepairPlanCursor(
        tenant_id=TENANT,
        snapshot_id="other-snapshot",
        after_candidate_id="candidate-b",
    )
    with pytest.raises(RepairPlanningError) as raised:
        planner.plan(_request(*records, cursor=wrong_cursor))
    assert raised.value.code is RepairPlanningErrorCode.CURSOR_SCOPE_MISMATCH


def test_duplicate_candidate_ids_reject_the_snapshot_before_a_plan_is_emitted() -> None:
    with pytest.raises(RepairPlanningError) as raised:
        RepairPlanner().plan(
            _request(
                _record(candidate_id="candidate-a"), _record(candidate_id="candidate-a")
            )
        )

    assert raised.value.code is RepairPlanningErrorCode.DUPLICATE_CANDIDATE
