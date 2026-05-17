"""Domain service orchestrating the tier-2 adapter registry.

Composes ``SourceStorage`` (object bytes) and ``AdapterRegistryStore``
(Postgres rows) into the verbs the routes call. Every method that
writes appends to the audit chain in the same transaction so the
visible state and its audit row stay aligned.
"""

from __future__ import annotations

from datetime import datetime, timezone

from backend_app.adapter_registry.models import (
    AdapterCandidateRecord,
    AdapterCandidateStatus,
    AdapterCandidateSubmission,
    AdapterCandidateView,
    AdapterRegistryAuditEventRecord,
    AdapterReviewAction,
    AdapterReviewRecord,
    HarvestMetrics,
    PromotedAdapterRecord,
    PromotedAdapterView,
    TenantAdapterSettingsRecord,
)
from backend_app.adapter_registry.storage import (
    InMemorySourceStorage,
    SourceStorage,
)
from backend_app.adapter_registry.store import (
    AdapterRegistryStore,
    InMemoryAdapterRegistryStore,
)


ACTION_CANDIDATE_SUBMITTED = "adapter_candidate_submitted"
ACTION_REVIEWED = "adapter_reviewed"
ACTION_PROMOTED = "adapter_promoted"
ACTION_OPT_OUT_CHANGED = "tenant_opt_out_changed"


def _now() -> datetime:
    return datetime.now(timezone.utc)


class AdapterRegistryService:
    """Tenant-scoped operations on the registry."""

    def __init__(
        self,
        *,
        store: AdapterRegistryStore | None = None,
        source_storage: SourceStorage | None = None,
    ) -> None:
        self._store: AdapterRegistryStore = store or InMemoryAdapterRegistryStore()
        self._storage: SourceStorage = source_storage or InMemorySourceStorage()

    @property
    def store(self) -> AdapterRegistryStore:
        return self._store

    @property
    def source_storage(self) -> SourceStorage:
        return self._storage

    def submit_candidate(
        self,
        *,
        tenant_id: str,
        submitter_user_id: str,
        submission: AdapterCandidateSubmission,
    ) -> AdapterCandidateRecord:
        stored = self._storage.put(
            scheme=submission.scheme,
            version=submission.version,
            source=submission.source.encode("utf-8"),
        )
        record = AdapterCandidateRecord(
            tenant_id=tenant_id,
            submitter_user_id=submitter_user_id,
            scheme=submission.scheme,
            version=submission.version,
            layout=submission.layout,
            storage_key=stored.key,
            source_digest=stored.digest,
            source_bytes=stored.size_bytes,
            harvest_metrics=submission.harvest_metrics,
        )
        with self._store.transaction() as conn:
            self._store.insert_candidate(record, conn=conn)
            self._store.append_audit(
                AdapterRegistryAuditEventRecord(
                    tenant_id=tenant_id,
                    actor_user_id=submitter_user_id,
                    candidate_id=record.candidate_id,
                    action=ACTION_CANDIDATE_SUBMITTED,
                    metadata={
                        "scheme": record.scheme,
                        "version": record.version,
                        "layout": record.layout,
                        "source_digest": record.source_digest,
                        "zero_error_sessions": record.harvest_metrics.zero_error_sessions,
                    },
                ),
                conn=conn,
            )
        return record

    def list_candidates(
        self,
        *,
        tenant_id: str | None = None,
        status: AdapterCandidateStatus | None = None,
        limit: int = 50,
    ) -> tuple[AdapterCandidateView, ...]:
        rows = self._store.list_candidates(
            tenant_id=tenant_id, status=status, limit=limit
        )
        return tuple(self._candidate_to_view(row) for row in rows)

    def get_candidate(
        self,
        *,
        candidate_id: str,
        viewer_tenant_id: str | None = None,
        viewer_is_admin: bool = False,
    ) -> AdapterCandidateView | None:
        record = self._store.get_candidate(candidate_id=candidate_id)
        if record is None:
            return None
        if not viewer_is_admin and viewer_tenant_id is not None:
            if record.tenant_id != viewer_tenant_id:
                return None
        return self._candidate_to_view(record)

    def decide(
        self,
        *,
        candidate_id: str,
        reviewer_user_id: str,
        reviewer_org_id: str,
        action: AdapterReviewAction,
        notes: str | None,
    ) -> tuple[AdapterReviewRecord, PromotedAdapterRecord | None]:
        candidate = self._store.get_candidate(candidate_id=candidate_id)
        if candidate is None:
            raise ValueError("candidate not found")
        if candidate.status in {
            AdapterCandidateStatus.APPROVED,
            AdapterCandidateStatus.REJECTED,
        }:
            raise ValueError("candidate is already in a terminal state")

        promoted_record: PromotedAdapterRecord | None = None
        review = AdapterReviewRecord(
            candidate_id=candidate.candidate_id,
            reviewer_user_id=reviewer_user_id,
            reviewer_org_id=reviewer_org_id,
            action=action,
            notes=notes,
        )
        next_status = _status_for_action(action)
        with self._store.transaction() as conn:
            self._store.insert_review(review, conn=conn)
            self._store.update_candidate_status(
                candidate_id=candidate.candidate_id,
                status=next_status,
                conn=conn,
            )
            self._store.append_audit(
                AdapterRegistryAuditEventRecord(
                    tenant_id=candidate.tenant_id,
                    actor_user_id=reviewer_user_id,
                    candidate_id=candidate.candidate_id,
                    action=ACTION_REVIEWED,
                    metadata={
                        "review_id": review.review_id,
                        "action": action.value,
                        "reviewer_org_id": reviewer_org_id,
                    },
                ),
                conn=conn,
            )
            if action == AdapterReviewAction.APPROVE:
                schema_version = (
                    self._store.max_schema_version(scheme=candidate.scheme) + 1
                )
                promoted_record = PromotedAdapterRecord(
                    scheme=candidate.scheme,
                    version=candidate.version,
                    schema_version=schema_version,
                    layout=candidate.layout,
                    storage_key=candidate.storage_key,
                    source_digest=candidate.source_digest,
                    source_bytes=candidate.source_bytes,
                    origin_tenant_id=candidate.tenant_id,
                    source_candidate_id=candidate.candidate_id,
                    promoted_by_user_id=reviewer_user_id,
                )
                self._store.insert_promoted(promoted_record, conn=conn)
                self._store.append_audit(
                    AdapterRegistryAuditEventRecord(
                        tenant_id=candidate.tenant_id,
                        actor_user_id=reviewer_user_id,
                        candidate_id=candidate.candidate_id,
                        promoted_id=promoted_record.promoted_id,
                        action=ACTION_PROMOTED,
                        metadata={
                            "scheme": promoted_record.scheme,
                            "schema_version": promoted_record.schema_version,
                            "source_digest": promoted_record.source_digest,
                            "origin_tenant_id": promoted_record.origin_tenant_id,
                        },
                    ),
                    conn=conn,
                )
        return review, promoted_record

    def list_promoted_for_tenant(
        self, *, tenant_id: str
    ) -> tuple[PromotedAdapterView, ...]:
        settings = self._store.get_tenant_settings(tenant_id=tenant_id)
        if settings is not None and settings.opted_out:
            return ()
        promoted_rows = self._store.list_promoted()
        latest: dict[str, PromotedAdapterRecord] = {}
        for record in promoted_rows:
            existing = latest.get(record.scheme)
            if existing is None or record.schema_version > existing.schema_version:
                latest[record.scheme] = record
        return tuple(self._promoted_to_view(record) for record in latest.values())

    def set_tenant_opt_out(
        self,
        *,
        tenant_id: str,
        actor_user_id: str,
        opted_out: bool,
    ) -> TenantAdapterSettingsRecord:
        record = TenantAdapterSettingsRecord(
            tenant_id=tenant_id,
            opted_out=opted_out,
            updated_by_user_id=actor_user_id,
        )
        with self._store.transaction() as conn:
            saved = self._store.upsert_tenant_settings(record, conn=conn)
            self._store.append_audit(
                AdapterRegistryAuditEventRecord(
                    tenant_id=tenant_id,
                    actor_user_id=actor_user_id,
                    action=ACTION_OPT_OUT_CHANGED,
                    metadata={"opted_out": saved.opted_out},
                ),
                conn=conn,
            )
        return saved

    def get_tenant_opt_out(self, *, tenant_id: str) -> TenantAdapterSettingsRecord:
        existing = self._store.get_tenant_settings(tenant_id=tenant_id)
        if existing is not None:
            return existing
        return TenantAdapterSettingsRecord(tenant_id=tenant_id, opted_out=False)

    def _candidate_to_view(
        self, record: AdapterCandidateRecord
    ) -> AdapterCandidateView:
        source_bytes = self._storage.get(key=record.storage_key)
        if source_bytes is None:
            raise RuntimeError(
                f"source bytes missing for candidate {record.candidate_id}"
            )
        return AdapterCandidateView(
            candidate_id=record.candidate_id,
            tenant_id=record.tenant_id,
            submitter_user_id=record.submitter_user_id,
            scheme=record.scheme,
            version=record.version,
            layout=record.layout,
            source=source_bytes.decode("utf-8"),
            source_digest=record.source_digest,
            harvest_metrics=record.harvest_metrics,
            status=record.status,
            created_at=record.created_at,
            updated_at=record.updated_at,
        )

    def _promoted_to_view(self, record: PromotedAdapterRecord) -> PromotedAdapterView:
        source_bytes = self._storage.get(key=record.storage_key)
        if source_bytes is None:
            raise RuntimeError(
                f"source bytes missing for promoted adapter {record.promoted_id}"
            )
        return PromotedAdapterView(
            promoted_id=record.promoted_id,
            scheme=record.scheme,
            version=record.version,
            schema_version=record.schema_version,
            layout=record.layout,
            source=source_bytes.decode("utf-8"),
            source_digest=record.source_digest,
            origin="community",
            promoted_at=record.promoted_at,
        )


def _status_for_action(action: AdapterReviewAction) -> AdapterCandidateStatus:
    if action == AdapterReviewAction.APPROVE:
        return AdapterCandidateStatus.APPROVED
    if action == AdapterReviewAction.REJECT:
        return AdapterCandidateStatus.REJECTED
    return AdapterCandidateStatus.CHANGES_REQUESTED


__all__ = [
    "ACTION_CANDIDATE_SUBMITTED",
    "ACTION_OPT_OUT_CHANGED",
    "ACTION_PROMOTED",
    "ACTION_REVIEWED",
    "AdapterRegistryService",
    "HarvestMetrics",
]
