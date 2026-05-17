"""Service-layer tests for the tier-2 adapter registry.

Covers the must-test items from the Phase 7A PRD:

* tenant isolation negatives,
* audit immutability (chain hash chain),
* opt-out honored on the promoted-listing endpoint,
* admin-only review gating is enforced at the route layer (see
  ``integration/api/test_adapter_registry_routes.py``).
"""

from __future__ import annotations

import pytest

from backend_app.adapter_registry.models import (
    AdapterCandidateStatus,
    AdapterCandidateSubmission,
    AdapterReviewAction,
    HarvestMetrics,
)
from backend_app.adapter_registry.registry_service import (
    ACTION_CANDIDATE_SUBMITTED,
    ACTION_OPT_OUT_CHANGED,
    ACTION_PROMOTED,
    ACTION_REVIEWED,
    AdapterRegistryService,
)
from backend_app.adapter_registry.storage import InMemorySourceStorage
from backend_app.adapter_registry.store import InMemoryAdapterRegistryStore


def _service() -> AdapterRegistryService:
    return AdapterRegistryService(
        store=InMemoryAdapterRegistryStore(),
        source_storage=InMemorySourceStorage(),
    )


def _submission(
    *, scheme: str = "saas:salesforce", version: int = 1
) -> AdapterCandidateSubmission:
    return AdapterCandidateSubmission(
        scheme=scheme,
        version=version,
        layout="form",
        source=f"// adapter source for {scheme} v{version}",
        harvest_metrics=HarvestMetrics(
            zero_error_sessions=10,
            total_sessions=10,
            user_reported_issues=0,
        ),
    )


class TestSubmitCandidate:
    def test_records_origin_tenant_and_audits(self) -> None:
        service = _service()
        record = service.submit_candidate(
            tenant_id="org_acme",
            submitter_user_id="usr_alice",
            submission=_submission(),
        )
        assert record.tenant_id == "org_acme"
        assert record.submitter_user_id == "usr_alice"
        assert record.status == AdapterCandidateStatus.SUBMITTED
        events = service.store.list_audit(tenant_id="org_acme")
        assert any(event.action == ACTION_CANDIDATE_SUBMITTED for event in events)

    def test_source_persisted_to_storage(self) -> None:
        service = _service()
        record = service.submit_candidate(
            tenant_id="org_acme",
            submitter_user_id="usr_alice",
            submission=_submission(),
        )
        assert service.source_storage.get(key=record.storage_key) is not None


class TestTenantIsolation:
    def test_get_candidate_blocks_cross_tenant_read(self) -> None:
        service = _service()
        owned = service.submit_candidate(
            tenant_id="org_acme",
            submitter_user_id="usr_alice",
            submission=_submission(),
        )
        view = service.get_candidate(
            candidate_id=owned.candidate_id,
            viewer_tenant_id="org_globex",
        )
        assert view is None

    def test_owner_can_read_own_candidate(self) -> None:
        service = _service()
        owned = service.submit_candidate(
            tenant_id="org_acme",
            submitter_user_id="usr_alice",
            submission=_submission(),
        )
        view = service.get_candidate(
            candidate_id=owned.candidate_id,
            viewer_tenant_id="org_acme",
        )
        assert view is not None
        assert view.candidate_id == owned.candidate_id

    def test_admin_can_read_any_candidate(self) -> None:
        service = _service()
        owned = service.submit_candidate(
            tenant_id="org_acme",
            submitter_user_id="usr_alice",
            submission=_submission(),
        )
        view = service.get_candidate(
            candidate_id=owned.candidate_id,
            viewer_is_admin=True,
        )
        assert view is not None

    def test_list_promoted_returns_per_tenant_view(self) -> None:
        service = _service()
        owned = service.submit_candidate(
            tenant_id="org_acme",
            submitter_user_id="usr_alice",
            submission=_submission(),
        )
        service.decide(
            candidate_id=owned.candidate_id,
            reviewer_user_id="usr_admin",
            reviewer_org_id="org_admin",
            action=AdapterReviewAction.APPROVE,
            notes=None,
        )
        # Promoted adapters are cross-tenant by design (every tenant sees
        # them unless opted out); the route layer rebinds the caller's
        # tenant id from the verified bearer.
        for tenant in ("org_acme", "org_globex"):
            promoted = service.list_promoted_for_tenant(tenant_id=tenant)
            assert len(promoted) == 1
            assert promoted[0].scheme == "saas:salesforce"
            assert promoted[0].origin == "community"


class TestDecide:
    def test_approve_promotes_and_audits(self) -> None:
        service = _service()
        record = service.submit_candidate(
            tenant_id="org_acme",
            submitter_user_id="usr_alice",
            submission=_submission(),
        )
        review, promoted = service.decide(
            candidate_id=record.candidate_id,
            reviewer_user_id="usr_admin",
            reviewer_org_id="org_platform",
            action=AdapterReviewAction.APPROVE,
            notes="LGTM",
        )
        assert promoted is not None
        assert promoted.schema_version == 1
        assert promoted.origin_tenant_id == "org_acme"
        assert review.action == AdapterReviewAction.APPROVE
        actions = {event.action for event in service.store.list_audit()}
        assert {ACTION_REVIEWED, ACTION_PROMOTED} <= actions

    def test_reject_does_not_promote(self) -> None:
        service = _service()
        record = service.submit_candidate(
            tenant_id="org_acme",
            submitter_user_id="usr_alice",
            submission=_submission(),
        )
        _, promoted = service.decide(
            candidate_id=record.candidate_id,
            reviewer_user_id="usr_admin",
            reviewer_org_id="org_platform",
            action=AdapterReviewAction.REJECT,
            notes=None,
        )
        assert promoted is None
        updated = service.store.get_candidate(candidate_id=record.candidate_id)
        assert updated is not None
        assert updated.status == AdapterCandidateStatus.REJECTED

    def test_request_changes_marks_status(self) -> None:
        service = _service()
        record = service.submit_candidate(
            tenant_id="org_acme",
            submitter_user_id="usr_alice",
            submission=_submission(),
        )
        service.decide(
            candidate_id=record.candidate_id,
            reviewer_user_id="usr_admin",
            reviewer_org_id="org_platform",
            action=AdapterReviewAction.REQUEST_CHANGES,
            notes="please tighten the schema",
        )
        updated = service.store.get_candidate(candidate_id=record.candidate_id)
        assert updated is not None
        assert updated.status == AdapterCandidateStatus.CHANGES_REQUESTED

    def test_decide_twice_rejects_second_terminal(self) -> None:
        service = _service()
        record = service.submit_candidate(
            tenant_id="org_acme",
            submitter_user_id="usr_alice",
            submission=_submission(),
        )
        service.decide(
            candidate_id=record.candidate_id,
            reviewer_user_id="usr_admin",
            reviewer_org_id="org_platform",
            action=AdapterReviewAction.APPROVE,
            notes=None,
        )
        with pytest.raises(ValueError):
            service.decide(
                candidate_id=record.candidate_id,
                reviewer_user_id="usr_admin",
                reviewer_org_id="org_platform",
                action=AdapterReviewAction.APPROVE,
                notes=None,
            )

    def test_schema_version_increments_per_scheme(self) -> None:
        service = _service()
        first = service.submit_candidate(
            tenant_id="org_acme",
            submitter_user_id="usr_alice",
            submission=_submission(version=1),
        )
        service.decide(
            candidate_id=first.candidate_id,
            reviewer_user_id="usr_admin",
            reviewer_org_id="org_platform",
            action=AdapterReviewAction.APPROVE,
            notes=None,
        )
        second = service.submit_candidate(
            tenant_id="org_acme",
            submitter_user_id="usr_alice",
            submission=_submission(version=2),
        )
        _, promoted = service.decide(
            candidate_id=second.candidate_id,
            reviewer_user_id="usr_admin",
            reviewer_org_id="org_platform",
            action=AdapterReviewAction.APPROVE,
            notes=None,
        )
        assert promoted is not None
        assert promoted.schema_version == 2


class TestOptOut:
    def test_opted_out_tenant_gets_empty_list(self) -> None:
        service = _service()
        record = service.submit_candidate(
            tenant_id="org_acme",
            submitter_user_id="usr_alice",
            submission=_submission(),
        )
        service.decide(
            candidate_id=record.candidate_id,
            reviewer_user_id="usr_admin",
            reviewer_org_id="org_platform",
            action=AdapterReviewAction.APPROVE,
            notes=None,
        )
        service.set_tenant_opt_out(
            tenant_id="org_globex",
            actor_user_id="usr_globex_admin",
            opted_out=True,
        )
        assert service.list_promoted_for_tenant(tenant_id="org_globex") == ()
        assert len(service.list_promoted_for_tenant(tenant_id="org_acme")) == 1

    def test_opt_out_emits_audit(self) -> None:
        service = _service()
        service.set_tenant_opt_out(
            tenant_id="org_acme",
            actor_user_id="usr_admin",
            opted_out=True,
        )
        events = service.store.list_audit(tenant_id="org_acme")
        assert any(event.action == ACTION_OPT_OUT_CHANGED for event in events)

    def test_get_opt_out_defaults_to_opted_in(self) -> None:
        service = _service()
        existing = service.get_tenant_opt_out(tenant_id="org_acme")
        assert existing.opted_out is False


class TestAuditImmutability:
    def test_audit_chain_links_consecutive_appends(self) -> None:
        service = _service()
        record = service.submit_candidate(
            tenant_id="org_acme",
            submitter_user_id="usr_alice",
            submission=_submission(),
        )
        service.decide(
            candidate_id=record.candidate_id,
            reviewer_user_id="usr_admin",
            reviewer_org_id="org_platform",
            action=AdapterReviewAction.APPROVE,
            notes=None,
        )
        events = service.store.list_audit(tenant_id="org_acme")
        assert len(events) >= 3
        for index in range(1, len(events)):
            previous_signature = events[index - 1].signature
            current_prev_hash = events[index].prev_hash
            assert previous_signature is not None
            assert current_prev_hash is not None
            assert previous_signature == current_prev_hash
        # First event in a chain has no predecessor.
        assert events[0].prev_hash is None
