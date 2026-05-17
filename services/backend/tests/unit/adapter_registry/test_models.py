"""Pydantic model validation tests for the tier-2 adapter registry."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from backend_app.adapter_registry.models import (
    AdapterCandidateRecord,
    AdapterCandidateStatus,
    AdapterCandidateSubmission,
    AdapterReviewAction,
    AdapterReviewRecord,
    HarvestMetrics,
)


def _valid_metrics() -> HarvestMetrics:
    return HarvestMetrics(
        zero_error_sessions=10,
        total_sessions=10,
        user_reported_issues=0,
    )


class TestAdapterCandidateSubmission:
    def test_accepts_valid_payload(self) -> None:
        payload = AdapterCandidateSubmission(
            scheme="saas:salesforce",
            version=1,
            layout="form",
            source="export const renderCurrent = () => null;",
            harvest_metrics=_valid_metrics(),
        )
        assert payload.scheme == "saas:salesforce"
        assert payload.layout == "form"

    def test_rejects_invalid_scheme(self) -> None:
        with pytest.raises(ValidationError):
            AdapterCandidateSubmission(
                scheme="../etc/passwd",
                version=1,
                layout="form",
                source="x",
                harvest_metrics=_valid_metrics(),
            )

    def test_rejects_unknown_layout(self) -> None:
        with pytest.raises(ValidationError):
            AdapterCandidateSubmission(
                scheme="saas:sf",
                version=1,
                layout="grid",
                source="x",
                harvest_metrics=_valid_metrics(),
            )

    def test_rejects_version_zero(self) -> None:
        with pytest.raises(ValidationError):
            AdapterCandidateSubmission(
                scheme="saas:sf",
                version=0,
                layout="form",
                source="x",
                harvest_metrics=_valid_metrics(),
            )


class TestAdapterCandidateRecord:
    def test_default_status_is_submitted(self) -> None:
        record = AdapterCandidateRecord(
            tenant_id="org_acme",
            submitter_user_id="usr_alice",
            scheme="saas:slack",
            version=1,
            layout="form",
            storage_key="memory://saas:slack/1.js",
            source_digest="a" * 64,
            source_bytes=12,
            harvest_metrics=_valid_metrics(),
        )
        assert record.status == AdapterCandidateStatus.SUBMITTED

    def test_rejects_bad_digest_length(self) -> None:
        with pytest.raises(ValidationError):
            AdapterCandidateRecord(
                tenant_id="org",
                submitter_user_id="u",
                scheme="saas:s",
                version=1,
                layout="form",
                storage_key="k",
                source_digest="abc",
                source_bytes=1,
                harvest_metrics=_valid_metrics(),
            )


class TestAdapterReviewRecord:
    def test_action_enum_round_trip(self) -> None:
        record = AdapterReviewRecord(
            candidate_id="acan_x",
            reviewer_user_id="usr_admin",
            reviewer_org_id="org_admin",
            action=AdapterReviewAction.REQUEST_CHANGES,
            notes="please redo",
        )
        assert record.action == AdapterReviewAction.REQUEST_CHANGES
        assert record.notes == "please redo"


class TestHarvestMetrics:
    def test_rejects_negative_counts(self) -> None:
        with pytest.raises(ValidationError):
            HarvestMetrics(
                zero_error_sessions=-1,
                total_sessions=0,
            )
