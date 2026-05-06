"""Unified read surface for the four backend audit streams (PR 7.1).

The backend owns four append-only audit chains:

* ``mcp_audit_events`` (MCP server install / OAuth / scope changes)
* ``skill_audit_events`` (skill enable / disable / edit)
* ``identity_audit_events`` (login / role grant / member add / SCIM)
* ``deploy_audit_events`` (release deploys)

Each is owned by a separate store in :mod:`backend_app.store` /
:mod:`backend_app.identity.store`. ``AuditReader.list`` fans out to all
four with one bounded read each, merges by ``created_at`` descending,
and returns a stable cursor so subsequent pages skip already-seen rows.

Cursor encoding is opaque base64-JSON of
``{stream -> last_seen_marker}``. Streams with monotonic ``seq`` (mcp,
skill, deploy) advance by ``seq``; identity has no ``seq`` and advances
by ``created_at`` (the cursor stores the timestamp ISO).

The reader is read-only — there is no mutation surface. Append-only
enforcement remains the chain triggers + the ``audit_writer`` role
grant; this module never touches them.
"""

from __future__ import annotations

import base64
import json
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal

from backend_app.contracts import (
    AuditEventRecord,
    DeployAuditEventRecord,
    IdentityAuditEventRecord,
    SkillAuditEventRecord,
)


AuditStream = Literal[
    "mcp_audit_events",
    "skill_audit_events",
    "identity_audit_events",
    "deploy_audit_events",
]


@dataclass(frozen=True)
class AuditFilters:
    """Caller-supplied filters that restrict the listing."""

    actor_user_id: str | None = None
    action_prefix: str | None = None
    resource_type: str | None = None
    since: datetime | None = None
    until: datetime | None = None


@dataclass(frozen=True)
class AuditCursor:
    """Per-stream cursor markers.

    ``seq_by_stream`` carries the highest seq seen per chain. Identity
    has no seq column; ``identity_before`` is its independent ISO
    timestamp marker (the next page asks for rows strictly older than
    this).
    """

    seq_by_stream: dict[str, int] = field(default_factory=dict)
    identity_before: datetime | None = None

    def for_stream(self, stream: str) -> int:
        return self.seq_by_stream.get(stream, 0)

    def encode(self) -> str:
        payload: dict[str, Any] = {"seq_by_stream": dict(self.seq_by_stream)}
        if self.identity_before is not None:
            payload["identity_before"] = self.identity_before.isoformat()
        raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        return base64.urlsafe_b64encode(raw).decode("ascii")

    @classmethod
    def decode(cls, raw: str | None) -> "AuditCursor":
        if raw is None or raw.strip() == "":
            return cls()
        try:
            decoded = base64.urlsafe_b64decode(raw.encode("ascii"))
            payload = json.loads(decoded.decode("utf-8"))
        except (ValueError, json.JSONDecodeError) as exc:
            raise ValueError("invalid_cursor") from exc
        seq_by_stream = payload.get("seq_by_stream") or {}
        if not isinstance(seq_by_stream, dict):
            raise ValueError("invalid_cursor")
        identity_before_raw = payload.get("identity_before")
        identity_before: datetime | None = None
        if isinstance(identity_before_raw, str):
            try:
                identity_before = datetime.fromisoformat(identity_before_raw)
            except ValueError as exc:
                raise ValueError("invalid_cursor") from exc
        return cls(
            seq_by_stream={str(k): int(v) for k, v in seq_by_stream.items()},
            identity_before=identity_before,
        )


@dataclass(frozen=True)
class AuditPage:
    """One page of the unified audit feed."""

    rows: tuple["AuditRowView", ...]
    next_cursor: str | None
    has_more: bool
    degraded_streams: tuple[str, ...] = ()


@dataclass(frozen=True)
class AuditRowView:
    """Uniform row shape across the four streams.

    Producers carry their own metadata shape (different columns per
    stream); the reader normalises into this view. Chain fields are
    optional because identity does not (yet) participate in the chain.
    """

    stream: AuditStream
    seq: int | None
    audit_id: str
    org_id: str
    actor_user_id: str | None
    actor_kind: Literal["user", "ci", "system"]
    subject_user_id: str | None
    action: str
    resource_type: str
    resource_id: str
    outcome: Literal["success", "failure", "denied"]
    metadata: dict[str, Any]
    prev_hash_hex: str | None
    signature_hex: str | None
    key_version: int | None
    created_at: datetime


class AuditReader:
    """Fan out reads across the four backend audit streams (PR 7.1)."""

    def __init__(
        self,
        *,
        mcp_store: Any | None,
        skill_store: Any | None,
        deploy_store: Any | None,
        identity_store: Any | None,
    ) -> None:
        self._mcp_store = mcp_store
        self._skill_store = skill_store
        self._deploy_store = deploy_store
        self._identity_store = identity_store

    def list(
        self,
        *,
        org_id: str,
        filters: AuditFilters,
        cursor: AuditCursor,
        limit: int,
    ) -> AuditPage:
        results: list[tuple[AuditRowView, ...]] = []
        degraded: list[str] = []
        for stream, fetcher in self._fetchers().items():
            try:
                rows = fetcher(
                    org_id=org_id,
                    filters=filters,
                    cursor=cursor,
                    limit=limit,
                )
            except Exception:  # noqa: BLE001 — degrade-on-failure is the spec
                degraded.append(stream)
                continue
            results.append(rows)

        merged = sorted(
            (row for chunk in results for row in chunk),
            key=lambda r: (r.created_at, r.stream, r.seq or 0),
            reverse=True,
        )[:limit]

        if not merged:
            next_cursor: str | None = None
        else:
            next_cursor = self._advance_cursor(cursor, merged).encode()
        return AuditPage(
            rows=tuple(merged),
            next_cursor=next_cursor if len(merged) == limit else None,
            has_more=len(merged) == limit,
            degraded_streams=tuple(degraded),
        )

    # --- per-stream fetchers --------------------------------------------------

    def _fetchers(self):  # type: ignore[no-untyped-def]
        return {
            "mcp_audit_events": self._fetch_mcp,
            "skill_audit_events": self._fetch_skill,
            "deploy_audit_events": self._fetch_deploy,
            "identity_audit_events": self._fetch_identity,
        }

    def _fetch_mcp(
        self,
        *,
        org_id: str,
        filters: AuditFilters,
        cursor: AuditCursor,
        limit: int,
    ) -> tuple[AuditRowView, ...]:
        store = self._mcp_store
        if store is None or not hasattr(store, "list_audit_events"):
            return ()
        rows = store.list_audit_events(
            org_id=org_id,
            after_seq=cursor.for_stream("mcp_audit_events"),
            limit=limit,
            action_prefix=filters.action_prefix,
            actor_user_id=filters.actor_user_id,
            since=filters.since,
            until=filters.until,
        )
        return tuple(self._mcp_to_view(record) for record in rows)

    def _fetch_skill(
        self,
        *,
        org_id: str,
        filters: AuditFilters,
        cursor: AuditCursor,
        limit: int,
    ) -> tuple[AuditRowView, ...]:
        store = self._skill_store
        if store is None or not hasattr(store, "list_skill_audit_events"):
            return ()
        rows = store.list_skill_audit_events(
            org_id=org_id,
            after_seq=cursor.for_stream("skill_audit_events"),
            limit=limit,
            action_prefix=filters.action_prefix,
            actor_user_id=filters.actor_user_id,
            since=filters.since,
            until=filters.until,
        )
        return tuple(self._skill_to_view(record) for record in rows)

    def _fetch_deploy(
        self,
        *,
        org_id: str,
        filters: AuditFilters,
        cursor: AuditCursor,
        limit: int,
    ) -> tuple[AuditRowView, ...]:
        store = self._deploy_store
        if store is None or not hasattr(store, "list_deploy_audit_events"):
            return ()
        rows = store.list_deploy_audit_events(
            org_id=org_id,
            after_seq=cursor.for_stream("deploy_audit_events"),
            limit=limit,
            action_prefix=filters.action_prefix,
            actor_user_id=filters.actor_user_id,
            since=filters.since,
            until=filters.until,
        )
        return tuple(self._deploy_to_view(record) for record in rows)

    def _fetch_identity(
        self,
        *,
        org_id: str,
        filters: AuditFilters,
        cursor: AuditCursor,
        limit: int,
    ) -> tuple[AuditRowView, ...]:
        store = self._identity_store
        if store is None or not hasattr(store, "list_identity_audit"):
            return ()
        # Identity events have no seq; the cursor uses ``before`` on
        # ``created_at``. The first page passes ``before=None`` (no
        # filter); subsequent pages pass the oldest timestamp seen so
        # far (we cross-reference at advance_cursor time).
        rows = store.list_identity_audit(
            org_id=org_id,
            limit=limit,
            actor_user_id=filters.actor_user_id,
            since=filters.since,
            until=filters.until,
            before=cursor.identity_before,
        )
        # The store's list method does NOT support action_prefix —
        # apply that here so prefix matching is consistent with the
        # other streams.
        if filters.action_prefix is not None:
            rows = tuple(r for r in rows if r.action.startswith(filters.action_prefix))
        return tuple(self._identity_to_view(record) for record in rows)

    # --- mapping helpers ------------------------------------------------------

    @staticmethod
    def _mcp_to_view(record: AuditEventRecord) -> AuditRowView:
        outcome = AuditReader._derive_outcome(record.action)
        return AuditRowView(
            stream="mcp_audit_events",
            seq=record.seq,
            audit_id=record.audit_id,
            org_id=record.org_id,
            actor_user_id=record.user_id,
            actor_kind="user",
            subject_user_id=None,
            action=record.action,
            resource_type="mcp_server",
            resource_id=record.server_id,
            outcome=outcome,
            metadata=dict(record.metadata),
            prev_hash_hex=(
                bytes(record.prev_hash).hex() if record.prev_hash is not None else None
            ),
            signature_hex=(
                bytes(record.signature).hex() if record.signature is not None else None
            ),
            key_version=record.key_version,
            created_at=AuditReader._coerce_dt(record.created_at),
        )

    @staticmethod
    def _skill_to_view(record: SkillAuditEventRecord) -> AuditRowView:
        return AuditRowView(
            stream="skill_audit_events",
            seq=record.seq,
            audit_id=record.audit_id,
            org_id=record.org_id,
            actor_user_id=record.user_id,
            actor_kind="user",
            subject_user_id=None,
            action=record.action,
            resource_type="skill",
            resource_id=record.skill_id,
            outcome=AuditReader._derive_outcome(record.action),
            metadata=dict(record.metadata),
            prev_hash_hex=(
                bytes(record.prev_hash).hex() if record.prev_hash is not None else None
            ),
            signature_hex=(
                bytes(record.signature).hex() if record.signature is not None else None
            ),
            key_version=record.key_version,
            created_at=AuditReader._coerce_dt(record.created_at),
        )

    @staticmethod
    def _deploy_to_view(record: DeployAuditEventRecord) -> AuditRowView:
        action = f"deploy.{record.outcome}"
        return AuditRowView(
            stream="deploy_audit_events",
            seq=record.seq,
            audit_id=record.audit_id,
            org_id=record.org_id,
            actor_user_id=record.user_id or None,
            actor_kind="ci" if record.actor_kind == "ci" else "user",
            subject_user_id=None,
            action=action,
            resource_type="deploy",
            resource_id=record.release_sha,
            outcome="success" if record.outcome == "success" else "failure",
            metadata={
                "tenant_id": record.tenant_id,
                "environment": record.environment,
                "release_sha": record.release_sha,
                "approver": record.approver,
                "workflow_run_url": record.workflow_run_url,
                "force_deploy": record.force_deploy,
            },
            prev_hash_hex=(
                bytes(record.prev_hash).hex() if record.prev_hash is not None else None
            ),
            signature_hex=(
                bytes(record.signature).hex() if record.signature is not None else None
            ),
            key_version=record.key_version,
            created_at=AuditReader._coerce_dt(record.created_at),
        )

    @staticmethod
    def _identity_to_view(record: IdentityAuditEventRecord) -> AuditRowView:
        return AuditRowView(
            stream="identity_audit_events",
            seq=None,
            audit_id=record.audit_id,
            org_id=record.org_id,
            actor_user_id=record.actor_user_id,
            actor_kind="user" if record.actor_user_id else "system",
            subject_user_id=record.subject_user_id,
            action=record.action,
            resource_type="user",
            resource_id=record.subject_user_id or record.org_id,
            outcome=AuditReader._derive_outcome(record.action),
            metadata=dict(record.metadata),
            prev_hash_hex=None,
            signature_hex=None,
            key_version=None,
            created_at=AuditReader._coerce_dt(record.created_at),
        )

    @staticmethod
    def _derive_outcome(
        action: str,
    ) -> Literal["success", "failure", "denied"]:
        # Producers don't carry an outcome column; we project from the
        # action namespace. Anything ending in ``.failed`` / ``.error``
        # → failure; ``.denied`` / ``.rejected`` → denied; else
        # success.
        if action.endswith((".failed", ".error", ".invalid")):
            return "failure"
        if action.endswith((".denied", ".rejected")):
            return "denied"
        return "success"

    @staticmethod
    def _coerce_dt(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value

    @staticmethod
    def _advance_cursor(
        cursor: AuditCursor, rows: Iterable[AuditRowView]
    ) -> AuditCursor:
        seq_by_stream = dict(cursor.seq_by_stream)
        identity_before = cursor.identity_before
        for row in rows:
            if row.stream == "identity_audit_events":
                # Advance to the oldest seen — paging older.
                if identity_before is None or row.created_at < identity_before:
                    identity_before = row.created_at
            elif row.seq is not None:
                seq_by_stream[row.stream] = max(
                    seq_by_stream.get(row.stream, 0), row.seq
                )
        return AuditCursor(seq_by_stream=seq_by_stream, identity_before=identity_before)
