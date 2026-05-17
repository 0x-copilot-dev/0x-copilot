"""Persistence adapters for the tier-2 adapter registry.

In-memory adapter mirrors the Postgres adapter semantics so the same
service-level tests cover both. Postgres adapter speaks SQL against
``services/backend/migrations/0031_adapter_registry.sql``.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol

from backend_app.adapter_registry.models import (
    AdapterCandidateRecord,
    AdapterCandidateStatus,
    AdapterRegistryAuditEventRecord,
    AdapterReviewRecord,
    HarvestMetrics,
    PromotedAdapterRecord,
    TenantAdapterSettingsRecord,
)
from backend_app.store import _AuditChain


def _now() -> datetime:
    return datetime.now(timezone.utc)


class AdapterRegistryStore(Protocol):
    """Adapter contract — every adapter implements every method."""

    @contextmanager
    def transaction(self) -> Iterator[Any]: ...  # pragma: no cover

    def insert_candidate(
        self,
        record: AdapterCandidateRecord,
        *,
        conn: Any | None = None,
    ) -> AdapterCandidateRecord: ...

    def update_candidate_status(
        self,
        *,
        candidate_id: str,
        status: AdapterCandidateStatus,
        conn: Any | None = None,
    ) -> AdapterCandidateRecord | None: ...

    def get_candidate(self, *, candidate_id: str) -> AdapterCandidateRecord | None: ...

    def list_candidates(
        self,
        *,
        tenant_id: str | None = None,
        status: AdapterCandidateStatus | None = None,
        limit: int = 50,
    ) -> tuple[AdapterCandidateRecord, ...]: ...

    def insert_review(
        self,
        record: AdapterReviewRecord,
        *,
        conn: Any | None = None,
    ) -> AdapterReviewRecord: ...

    def list_reviews_for_candidate(
        self, *, candidate_id: str
    ) -> tuple[AdapterReviewRecord, ...]: ...

    def insert_promoted(
        self,
        record: PromotedAdapterRecord,
        *,
        conn: Any | None = None,
    ) -> PromotedAdapterRecord: ...

    def max_schema_version(self, *, scheme: str) -> int: ...

    def list_promoted(self) -> tuple[PromotedAdapterRecord, ...]: ...

    def get_tenant_settings(
        self, *, tenant_id: str
    ) -> TenantAdapterSettingsRecord | None: ...

    def upsert_tenant_settings(
        self,
        record: TenantAdapterSettingsRecord,
        *,
        conn: Any | None = None,
    ) -> TenantAdapterSettingsRecord: ...

    def append_audit(
        self,
        record: AdapterRegistryAuditEventRecord,
        *,
        conn: Any | None = None,
    ) -> AdapterRegistryAuditEventRecord: ...

    def list_audit(
        self, *, tenant_id: str | None = None
    ) -> tuple[AdapterRegistryAuditEventRecord, ...]: ...


def _sign_audit(
    record: AdapterRegistryAuditEventRecord,
    chain: _AuditChain,
) -> AdapterRegistryAuditEventRecord:
    payload = {
        "audit_id": record.audit_id,
        "tenant_id": record.tenant_id,
        "actor_user_id": record.actor_user_id,
        "candidate_id": record.candidate_id,
        "promoted_id": record.promoted_id,
        "action": record.action,
        "metadata": dict(record.metadata),
        "created_at": record.created_at,
    }
    seq, prev_hash, signature, key_version = chain.next(
        org_id=record.tenant_id, payload=payload
    )
    return record.model_copy(
        update={
            "seq": seq,
            "prev_hash": prev_hash,
            "signature": signature,
            "key_version": key_version,
        }
    )


@dataclass
class InMemoryAdapterRegistryStore:
    """Dict-backed adapter for dev + tests. Mirrors postgres semantics."""

    candidates: dict[str, AdapterCandidateRecord] = field(default_factory=dict)
    reviews: dict[str, AdapterReviewRecord] = field(default_factory=dict)
    promoted: dict[str, PromotedAdapterRecord] = field(default_factory=dict)
    tenant_settings: dict[str, TenantAdapterSettingsRecord] = field(
        default_factory=dict
    )
    audit_events: list[AdapterRegistryAuditEventRecord] = field(default_factory=list)
    _chain: _AuditChain = field(default_factory=_AuditChain, init=False, repr=False)

    @contextmanager
    def transaction(self) -> Iterator[None]:
        yield None

    def insert_candidate(
        self,
        record: AdapterCandidateRecord,
        *,
        conn: Any | None = None,
    ) -> AdapterCandidateRecord:
        del conn
        self.candidates[record.candidate_id] = record
        return record

    def update_candidate_status(
        self,
        *,
        candidate_id: str,
        status: AdapterCandidateStatus,
        conn: Any | None = None,
    ) -> AdapterCandidateRecord | None:
        del conn
        existing = self.candidates.get(candidate_id)
        if existing is None:
            return None
        updated = existing.model_copy(update={"status": status, "updated_at": _now()})
        self.candidates[candidate_id] = updated
        return updated

    def get_candidate(self, *, candidate_id: str) -> AdapterCandidateRecord | None:
        return self.candidates.get(candidate_id)

    def list_candidates(
        self,
        *,
        tenant_id: str | None = None,
        status: AdapterCandidateStatus | None = None,
        limit: int = 50,
    ) -> tuple[AdapterCandidateRecord, ...]:
        rows = [
            record
            for record in self.candidates.values()
            if (tenant_id is None or record.tenant_id == tenant_id)
            and (status is None or record.status == status)
        ]
        rows.sort(key=lambda r: r.created_at, reverse=True)
        return tuple(rows[:limit])

    def insert_review(
        self,
        record: AdapterReviewRecord,
        *,
        conn: Any | None = None,
    ) -> AdapterReviewRecord:
        del conn
        self.reviews[record.review_id] = record
        return record

    def list_reviews_for_candidate(
        self, *, candidate_id: str
    ) -> tuple[AdapterReviewRecord, ...]:
        rows = [
            review
            for review in self.reviews.values()
            if review.candidate_id == candidate_id
        ]
        rows.sort(key=lambda r: r.decided_at)
        return tuple(rows)

    def insert_promoted(
        self,
        record: PromotedAdapterRecord,
        *,
        conn: Any | None = None,
    ) -> PromotedAdapterRecord:
        del conn
        self.promoted[record.promoted_id] = record
        return record

    def max_schema_version(self, *, scheme: str) -> int:
        versions = [
            record.schema_version
            for record in self.promoted.values()
            if record.scheme == scheme
        ]
        return max(versions) if versions else 0

    def list_promoted(self) -> tuple[PromotedAdapterRecord, ...]:
        rows = list(self.promoted.values())
        rows.sort(key=lambda r: (r.scheme, r.schema_version), reverse=True)
        return tuple(rows)

    def get_tenant_settings(
        self, *, tenant_id: str
    ) -> TenantAdapterSettingsRecord | None:
        return self.tenant_settings.get(tenant_id)

    def upsert_tenant_settings(
        self,
        record: TenantAdapterSettingsRecord,
        *,
        conn: Any | None = None,
    ) -> TenantAdapterSettingsRecord:
        del conn
        saved = record.model_copy(update={"updated_at": _now()})
        self.tenant_settings[record.tenant_id] = saved
        return saved

    def append_audit(
        self,
        record: AdapterRegistryAuditEventRecord,
        *,
        conn: Any | None = None,
    ) -> AdapterRegistryAuditEventRecord:
        del conn
        signed = _sign_audit(record, self._chain)
        self.audit_events.append(signed)
        return signed

    def list_audit(
        self, *, tenant_id: str | None = None
    ) -> tuple[AdapterRegistryAuditEventRecord, ...]:
        rows = [
            event
            for event in self.audit_events
            if tenant_id is None or event.tenant_id == tenant_id
        ]
        return tuple(rows)


class PostgresAdapterRegistryStore:
    """Postgres-backed adapter for ``adapter_registry`` tables.

    Mirrors the in-memory adapter's semantics row-for-row. The
    ``adapter_registry_audit_events`` chain takes the same per-tenant
    advisory lock as the existing ``mcp_audit_events`` chain so
    concurrent appends serialise.
    """

    def __init__(self, pool: Any) -> None:
        self._pool = pool
        self._chain = _AuditChain()

    @contextmanager
    def transaction(self) -> Iterator[Any]:
        with self._pool.connection() as conn:
            with conn.transaction():
                yield conn

    @contextmanager
    def _cursor(self, conn: Any | None) -> Iterator[Any]:
        if conn is not None:
            with conn.cursor() as cur:
                yield cur
            return
        with self._pool.connection() as owned:
            with owned.cursor() as cur:
                yield cur

    def insert_candidate(
        self,
        record: AdapterCandidateRecord,
        *,
        conn: Any | None = None,
    ) -> AdapterCandidateRecord:
        with self._cursor(conn) as cur:
            cur.execute(
                """
                INSERT INTO adapter_candidates (
                    candidate_id, tenant_id, submitter_user_id, scheme, version,
                    layout, storage_key, source_digest, source_bytes,
                    harvest_metrics, status, created_at, updated_at
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s
                )
                """,
                (
                    record.candidate_id,
                    record.tenant_id,
                    record.submitter_user_id,
                    record.scheme,
                    record.version,
                    record.layout,
                    record.storage_key,
                    record.source_digest,
                    record.source_bytes,
                    json.dumps(record.harvest_metrics.model_dump()),
                    record.status.value,
                    record.created_at,
                    record.updated_at,
                ),
            )
        return record

    def update_candidate_status(
        self,
        *,
        candidate_id: str,
        status: AdapterCandidateStatus,
        conn: Any | None = None,
    ) -> AdapterCandidateRecord | None:
        with self._cursor(conn) as cur:
            cur.execute(
                """
                UPDATE adapter_candidates
                SET status = %s, updated_at = NOW()
                WHERE candidate_id = %s
                RETURNING candidate_id, tenant_id, submitter_user_id, scheme,
                          version, layout, storage_key, source_digest,
                          source_bytes, harvest_metrics, status,
                          created_at, updated_at
                """,
                (status.value, candidate_id),
            )
            row = cur.fetchone()
        return _row_to_candidate(row) if row is not None else None

    def get_candidate(self, *, candidate_id: str) -> AdapterCandidateRecord | None:
        with self._cursor(None) as cur:
            cur.execute(
                """
                SELECT candidate_id, tenant_id, submitter_user_id, scheme,
                       version, layout, storage_key, source_digest,
                       source_bytes, harvest_metrics, status,
                       created_at, updated_at
                FROM adapter_candidates
                WHERE candidate_id = %s
                """,
                (candidate_id,),
            )
            row = cur.fetchone()
        return _row_to_candidate(row) if row is not None else None

    def list_candidates(
        self,
        *,
        tenant_id: str | None = None,
        status: AdapterCandidateStatus | None = None,
        limit: int = 50,
    ) -> tuple[AdapterCandidateRecord, ...]:
        sql = """
            SELECT candidate_id, tenant_id, submitter_user_id, scheme,
                   version, layout, storage_key, source_digest,
                   source_bytes, harvest_metrics, status,
                   created_at, updated_at
            FROM adapter_candidates
        """
        clauses: list[str] = []
        params: list[Any] = []
        if tenant_id is not None:
            clauses.append("tenant_id = %s")
            params.append(tenant_id)
        if status is not None:
            clauses.append("status = %s")
            params.append(status.value)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY created_at DESC LIMIT %s"
        params.append(limit)
        with self._cursor(None) as cur:
            cur.execute(sql, tuple(params))
            rows = cur.fetchall()
        return tuple(_row_to_candidate(row) for row in rows)

    def insert_review(
        self,
        record: AdapterReviewRecord,
        *,
        conn: Any | None = None,
    ) -> AdapterReviewRecord:
        with self._cursor(conn) as cur:
            cur.execute(
                """
                INSERT INTO adapter_reviews (
                    review_id, candidate_id, reviewer_user_id, reviewer_org_id,
                    action, notes, decided_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    record.review_id,
                    record.candidate_id,
                    record.reviewer_user_id,
                    record.reviewer_org_id,
                    record.action.value,
                    record.notes,
                    record.decided_at,
                ),
            )
        return record

    def list_reviews_for_candidate(
        self, *, candidate_id: str
    ) -> tuple[AdapterReviewRecord, ...]:
        with self._cursor(None) as cur:
            cur.execute(
                """
                SELECT review_id, candidate_id, reviewer_user_id, reviewer_org_id,
                       action, notes, decided_at
                FROM adapter_reviews
                WHERE candidate_id = %s
                ORDER BY decided_at ASC
                """,
                (candidate_id,),
            )
            rows = cur.fetchall()
        return tuple(_row_to_review(row) for row in rows)

    def insert_promoted(
        self,
        record: PromotedAdapterRecord,
        *,
        conn: Any | None = None,
    ) -> PromotedAdapterRecord:
        with self._cursor(conn) as cur:
            cur.execute(
                """
                INSERT INTO promoted_adapters (
                    promoted_id, scheme, version, schema_version, layout,
                    storage_key, source_digest, source_bytes,
                    origin_tenant_id, source_candidate_id, promoted_by_user_id,
                    promoted_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    record.promoted_id,
                    record.scheme,
                    record.version,
                    record.schema_version,
                    record.layout,
                    record.storage_key,
                    record.source_digest,
                    record.source_bytes,
                    record.origin_tenant_id,
                    record.source_candidate_id,
                    record.promoted_by_user_id,
                    record.promoted_at,
                ),
            )
        return record

    def max_schema_version(self, *, scheme: str) -> int:
        with self._cursor(None) as cur:
            cur.execute(
                "SELECT COALESCE(MAX(schema_version), 0) AS m "
                "FROM promoted_adapters WHERE scheme = %s",
                (scheme,),
            )
            row = cur.fetchone()
        if row is None:
            return 0
        record = dict(row)
        return int(record["m"] or 0)

    def list_promoted(self) -> tuple[PromotedAdapterRecord, ...]:
        with self._cursor(None) as cur:
            cur.execute(
                """
                SELECT promoted_id, scheme, version, schema_version, layout,
                       storage_key, source_digest, source_bytes,
                       origin_tenant_id, source_candidate_id, promoted_by_user_id,
                       promoted_at
                FROM promoted_adapters
                ORDER BY scheme ASC, schema_version DESC
                """
            )
            rows = cur.fetchall()
        return tuple(_row_to_promoted(row) for row in rows)

    def get_tenant_settings(
        self, *, tenant_id: str
    ) -> TenantAdapterSettingsRecord | None:
        with self._cursor(None) as cur:
            cur.execute(
                """
                SELECT tenant_id, opted_out, updated_at, updated_by_user_id
                FROM tenant_adapter_settings
                WHERE tenant_id = %s
                """,
                (tenant_id,),
            )
            row = cur.fetchone()
        return _row_to_tenant_settings(row) if row is not None else None

    def upsert_tenant_settings(
        self,
        record: TenantAdapterSettingsRecord,
        *,
        conn: Any | None = None,
    ) -> TenantAdapterSettingsRecord:
        saved = record.model_copy(update={"updated_at": _now()})
        with self._cursor(conn) as cur:
            cur.execute(
                """
                INSERT INTO tenant_adapter_settings (
                    tenant_id, opted_out, updated_at, updated_by_user_id
                ) VALUES (%s, %s, %s, %s)
                ON CONFLICT (tenant_id) DO UPDATE SET
                    opted_out = EXCLUDED.opted_out,
                    updated_at = EXCLUDED.updated_at,
                    updated_by_user_id = EXCLUDED.updated_by_user_id
                """,
                (
                    saved.tenant_id,
                    saved.opted_out,
                    saved.updated_at,
                    saved.updated_by_user_id,
                ),
            )
        return saved

    def append_audit(
        self,
        record: AdapterRegistryAuditEventRecord,
        *,
        conn: Any | None = None,
    ) -> AdapterRegistryAuditEventRecord:
        signed = _sign_audit(record, self._chain)
        with self._cursor(conn) as cur:
            cur.execute(
                """
                INSERT INTO adapter_registry_audit_events (
                    audit_id, tenant_id, actor_user_id, candidate_id, promoted_id,
                    action, metadata, created_at, seq, prev_hash, signature,
                    key_version
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s
                )
                """,
                (
                    signed.audit_id,
                    signed.tenant_id,
                    signed.actor_user_id,
                    signed.candidate_id,
                    signed.promoted_id,
                    signed.action,
                    json.dumps(signed.metadata),
                    signed.created_at,
                    signed.seq,
                    signed.prev_hash,
                    signed.signature,
                    signed.key_version,
                ),
            )
        return signed

    def list_audit(
        self, *, tenant_id: str | None = None
    ) -> tuple[AdapterRegistryAuditEventRecord, ...]:
        sql = """
            SELECT audit_id, tenant_id, actor_user_id, candidate_id, promoted_id,
                   action, metadata, created_at, seq, prev_hash, signature,
                   key_version
            FROM adapter_registry_audit_events
        """
        params: list[Any] = []
        if tenant_id is not None:
            sql += " WHERE tenant_id = %s"
            params.append(tenant_id)
        sql += " ORDER BY seq ASC"
        with self._cursor(None) as cur:
            cur.execute(sql, tuple(params))
            rows = cur.fetchall()
        return tuple(_row_to_audit(row) for row in rows)


def _row_to_candidate(row: Any) -> AdapterCandidateRecord:
    record = dict(row)
    raw_metrics = record.get("harvest_metrics")
    if isinstance(raw_metrics, str):
        record["harvest_metrics"] = HarvestMetrics.model_validate_json(raw_metrics)
    elif isinstance(raw_metrics, (bytes, bytearray)):
        record["harvest_metrics"] = HarvestMetrics.model_validate_json(
            bytes(raw_metrics).decode("utf-8")
        )
    elif isinstance(raw_metrics, dict):
        record["harvest_metrics"] = HarvestMetrics.model_validate(raw_metrics)
    return AdapterCandidateRecord.model_validate(record)


def _row_to_review(row: Any) -> AdapterReviewRecord:
    return AdapterReviewRecord.model_validate(dict(row))


def _row_to_promoted(row: Any) -> PromotedAdapterRecord:
    return PromotedAdapterRecord.model_validate(dict(row))


def _row_to_tenant_settings(row: Any) -> TenantAdapterSettingsRecord:
    return TenantAdapterSettingsRecord.model_validate(dict(row))


def _row_to_audit(row: Any) -> AdapterRegistryAuditEventRecord:
    record = dict(row)
    raw_metadata = record.get("metadata")
    if isinstance(raw_metadata, str):
        record["metadata"] = json.loads(raw_metadata) if raw_metadata else {}
    elif isinstance(raw_metadata, (bytes, bytearray)):
        record["metadata"] = json.loads(bytes(raw_metadata).decode("utf-8"))
    return AdapterRegistryAuditEventRecord.model_validate(record)


__all__ = [
    "AdapterRegistryStore",
    "InMemoryAdapterRegistryStore",
    "PostgresAdapterRegistryStore",
]
