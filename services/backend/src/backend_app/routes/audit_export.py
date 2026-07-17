"""SIEM export endpoint for audit events.

Streams a tenant's audit log as NDJSON between two ``seq`` watermarks so the
customer's collector can pull events into Splunk / Sentinel / Elastic. This is
an internal-plane endpoint (auth via ``ENTERPRISE_SERVICE_TOKEN``); per the
service-boundary rules in CLAUDE.md, the facade does not expose it -- only
trusted service callers (sidecars, schedulers, audit-pull jobs) reach it.

The response is one JSON object per line. Each row includes the chain fields
(``seq``, ``prev_hash``, ``signature``, ``key_version``) so the consumer can
verify integrity end-to-end without trusting our app process.

Records flow only out of the in-memory or Postgres-backed store; this route
does not touch the chain itself, just serializes what's already stored.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass
import json
from typing import Any

from copilot_service_contracts.scopes import ADMIN_AUDIT_EXPORT
from fastapi import Depends, FastAPI, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from backend_app.auth import BackendServiceAuthenticator
from backend_app.identity.rbac import RequireScopes
from backend_app.contracts import (
    AuditEventRecord,
    DeployAuditEventRecord,
    SkillAuditEventRecord,
)


_ALLOWED_TABLES = frozenset(
    {"mcp_audit_events", "skill_audit_events", "deploy_audit_events"}
)
_DEFAULT_LIMIT = 1_000
_MAX_LIMIT = 10_000


class AuditExportSummary(BaseModel):
    """First line of the NDJSON stream summarizing the export window."""

    table: str = Field(min_length=1)
    org_id: str = Field(min_length=1)
    after_seq: int = Field(ge=0)
    limit: int = Field(ge=1, le=_MAX_LIMIT)


@dataclass(frozen=True)
class _ExportRow:
    seq: int
    payload: dict[str, Any]
    prev_hash_hex: str | None
    signature_hex: str | None
    key_version: int | None


def register_audit_export_routes(app: FastAPI) -> None:
    """Attach the SIEM export endpoint to a backend FastAPI app."""

    @app.post(
        "/internal/v1/audit/export",
        dependencies=[Depends(RequireScopes(ADMIN_AUDIT_EXPORT))],
    )
    def audit_export(
        request: Request,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
        table: str = Query(..., min_length=1),
        after_seq: int = Query(0, ge=0),
        limit: int = Query(_DEFAULT_LIMIT, ge=1, le=_MAX_LIMIT),
    ) -> StreamingResponse:
        if table not in _ALLOWED_TABLES:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"table must be one of: {sorted(_ALLOWED_TABLES)}",
            )

        # Service-token-only: this is the internal plane.
        identity = BackendServiceAuthenticator.internal_scoped_identity(
            request, org_id=org_id, user_id=user_id
        )

        records = _select_records(app, table=table, org_id=identity.org_id)
        bounded = _bound_window(records, after_seq=after_seq, limit=limit)
        summary = AuditExportSummary(
            table=table, org_id=identity.org_id, after_seq=after_seq, limit=limit
        )

        return StreamingResponse(
            _emit_ndjson(summary=summary, rows=bounded),
            media_type="application/x-ndjson",
        )


def _select_records(app: FastAPI, *, table: str, org_id: str) -> Iterable[Any]:
    """Pull records from the in-memory store the service was built with.

    Postgres-backed export is a follow-up; the in-memory path covers dev,
    tests, and the service's audit_writer-role contract.
    """

    if table == "mcp_audit_events":
        store = getattr(app.state.mcp_service, "store", None)
        if store is None:
            return []
        return [r for r in getattr(store, "audit_events", []) if r.org_id == org_id]
    if table == "skill_audit_events":
        store = getattr(app.state.skill_service, "store", None)
        if store is None:
            return []
        return [r for r in getattr(store, "audit_events", []) if r.org_id == org_id]
    if table == "deploy_audit_events":
        store = getattr(app.state.deploy_audit_service, "store", None)
        if store is None:
            return []
        return [r for r in getattr(store, "audit_events", []) if r.org_id == org_id]
    return []


def _bound_window(
    records: Iterable[Any], *, after_seq: int, limit: int
) -> list[_ExportRow]:
    rows: list[_ExportRow] = []
    for record in sorted(records, key=lambda r: r.seq or 0):
        seq = record.seq or 0
        if seq <= after_seq:
            continue
        rows.append(_to_export_row(record))
        if len(rows) >= limit:
            break
    return rows


def _to_export_row(record: object) -> _ExportRow:
    payload = _payload_for_export(record)
    return _ExportRow(
        seq=getattr(record, "seq", 0) or 0,
        payload=payload,
        prev_hash_hex=(
            bytes(record.prev_hash).hex()  # type: ignore[attr-defined]
            if getattr(record, "prev_hash", None) is not None
            else None
        ),
        signature_hex=(
            bytes(record.signature).hex()  # type: ignore[attr-defined]
            if getattr(record, "signature", None) is not None
            else None
        ),
        key_version=getattr(record, "key_version", None),
    )


def _payload_for_export(record: object) -> dict[str, Any]:
    if isinstance(record, AuditEventRecord):
        return {
            "audit_id": record.audit_id,
            "org_id": record.org_id,
            "user_id": record.user_id,
            "server_id": record.server_id,
            "action": record.action,
            "metadata": record.metadata,
            "created_at": record.created_at.isoformat(),
        }
    if isinstance(record, SkillAuditEventRecord):
        return {
            "audit_id": record.audit_id,
            "org_id": record.org_id,
            "user_id": record.user_id,
            "skill_id": record.skill_id,
            "action": record.action,
            "metadata": record.metadata,
            "created_at": record.created_at.isoformat(),
        }
    if isinstance(record, DeployAuditEventRecord):
        return {
            "audit_id": record.audit_id,
            "org_id": record.org_id,
            "user_id": record.user_id,
            "tenant_id": record.tenant_id,
            "environment": record.environment,
            "release_sha": record.release_sha,
            "image_digests": [d.model_dump() for d in record.image_digests],
            "approver": record.approver,
            "workflow_run_url": record.workflow_run_url,
            "started_at": record.started_at.isoformat(),
            "completed_at": record.completed_at.isoformat(),
            "outcome": record.outcome,
            "force_deploy": record.force_deploy,
            "actor_kind": record.actor_kind,
            "created_at": record.created_at.isoformat(),
        }
    return {}


def _emit_ndjson(
    *, summary: AuditExportSummary, rows: list[_ExportRow]
) -> Iterator[bytes]:
    summary_line = summary.model_dump_json() + "\n"
    yield summary_line.encode("utf-8")
    for row in rows:
        line = (
            json.dumps(
                {
                    "seq": row.seq,
                    "prev_hash": row.prev_hash_hex,
                    "signature": row.signature_hex,
                    "key_version": row.key_version,
                    "payload": row.payload,
                },
                separators=(",", ":"),
            )
            + "\n"
        )
        yield line.encode("utf-8")
