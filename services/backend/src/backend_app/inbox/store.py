"""Inbox store — adapter contract + in-memory implementation.

Storage shape mirrors ``schema.sql`` in this package. The in-memory
adapter is the dev / test default; the postgres adapter (out of scope
for this PR — landed alongside the migration during the same merge in
production deployments) implements the same Protocol.

Authorization is NOT enforced here. The service layer composes
:class:`InboxStore` with the identity store + project-membership port
to decide read/write authority; the store exposes raw queries scoped
to ``tenant_id``.

Body split (inbox-prd §3 + §10): list rows carry a ``body_ref`` opaque
pointer; the body row lives in a separate ``inbox_bodies`` table so
list queries don't pay for body bytes. The store exposes ``get_body``
for the detail-mount lazy fetch path.
"""

from __future__ import annotations

from collections.abc import Iterable
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterator, Protocol
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _item_id() -> str:
    return f"inbox_{uuid4().hex}"


def _audit_id() -> str:
    return f"audinb_{uuid4().hex}"


def _body_ref() -> str:
    return f"body_{uuid4().hex}"


# ---------------------------------------------------------------------------
# Records
# ---------------------------------------------------------------------------


class InboxItemRecord(BaseModel):
    """One row in the ``inbox_items`` table.

    Pydantic model so the Postgres + in-memory adapters share one
    read/write contract. ``links`` and ``sender`` are JSONB blobs on
    the wire; we keep them as plain ``list[dict]`` / ``dict`` here and
    let the routes serialise to the canonical
    :class:`InboxItem` / :class:`InboxItemSender` typescript shapes.
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=_item_id)
    tenant_id: str
    owner_user_id: str
    project_id: str | None = None
    kind: str
    title: str
    body_ref: str | None = None
    links: list[dict[str, Any]] = Field(default_factory=list)
    sender: dict[str, Any] = Field(default_factory=dict)
    state: str = "unread"
    received_at: datetime = Field(default_factory=_now)
    read_at: datetime | None = None
    snoozed_until: datetime | None = None
    dismissed_at: datetime | None = None
    # Producer idempotency. ``(tenant_id, producer_id, external_ref)``
    # is UNIQUE in the schema; the producer (P4-A2) supplies these so
    # network-flake retries return the existing row instead of a dupe.
    producer_id: str | None = None
    external_ref: str | None = None
    updated_at: datetime = Field(default_factory=_now)


class InboxBodyRecord(BaseModel):
    """One row in the ``inbox_bodies`` table.

    Body text is split out so list responses don't pay for body bytes
    (inbox-prd §3 + §10). ``inbox_items.body_ref`` references this row's
    ``body_ref`` PK.
    """

    model_config = ConfigDict(extra="forbid")

    body_ref: str = Field(default_factory=_body_ref)
    tenant_id: str
    body_markdown: str = ""
    created_at: datetime = Field(default_factory=_now)


class InboxAuditRecord(BaseModel):
    """Append-only audit row written on every state change.

    The audit chain integration (``packages/audit-chain``) signs +
    chains rows in production. The in-memory adapter appends raw rows
    for tests; the postgres adapter writes through the chain signer
    (same path as ``todo_audit_events`` — see ``backend_app/todos/store.py``
    for the existing pattern).

    ``correlation_id`` is set on bulk-action rows so SIEM can query the
    rows belonging to one bulk write as a unit (cross-audit §1.4 audit
    row shape + inbox-prd §6 bulk semantics).

    ``before_state`` / ``after_state`` are dicts; the route layer
    redacts ``title`` / ``body`` if a tenant-level policy demands it
    (out of scope for P4-A1 — redaction shape is the same field across
    audit surfaces).
    """

    model_config = ConfigDict(extra="forbid")

    audit_id: str = Field(default_factory=_audit_id)
    tenant_id: str
    actor_user_id: str
    action: str
    target_kind: str = "inbox_item"
    target_id: str
    before_state: dict[str, Any] | None = None
    after_state: dict[str, Any] | None = None
    correlation_id: str | None = None
    ts: datetime = Field(default_factory=_now)


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


class InboxStore(Protocol):
    """Adapter contract for the Postgres + in-memory inbox stores."""

    @contextmanager
    def transaction(self) -> Iterator[None]: ...  # pragma: no cover

    # -- items ---------------------------------------------------------

    def insert_item(self, record: InboxItemRecord) -> InboxItemRecord: ...

    def get_item(self, *, tenant_id: str, item_id: str) -> InboxItemRecord | None: ...

    def list_items(
        self,
        *,
        tenant_id: str,
        owner_user_id: str | None = None,
        states: tuple[str, ...] | None = None,
        kinds: tuple[str, ...] | None = None,
        project_ids: tuple[str | None, ...] | None = None,
        cursor: str | None = None,
        limit: int = 50,
    ) -> tuple[tuple[InboxItemRecord, ...], str | None]: ...

    def list_project_member_items(
        self,
        *,
        tenant_id: str,
        project_ids: tuple[str, ...],
        cursor: str | None = None,
        limit: int = 50,
    ) -> tuple[tuple[InboxItemRecord, ...], str | None]: ...

    def update_item(self, record: InboxItemRecord) -> InboxItemRecord: ...

    def count_unread(self, *, tenant_id: str, owner_user_id: str) -> int: ...

    # -- bodies --------------------------------------------------------

    def insert_body(self, record: InboxBodyRecord) -> InboxBodyRecord: ...

    def get_body(self, *, tenant_id: str, body_ref: str) -> InboxBodyRecord | None: ...

    # -- audit ---------------------------------------------------------

    def append_audit(self, record: InboxAuditRecord) -> InboxAuditRecord: ...

    def list_audit_for_item(
        self, *, tenant_id: str, item_id: str
    ) -> tuple[InboxAuditRecord, ...]: ...


# ---------------------------------------------------------------------------
# In-memory adapter
# ---------------------------------------------------------------------------


@dataclass
class InMemoryInboxStore:
    """Dict-backed adapter for tests + the default dev wiring.

    Mirrors the Postgres semantics where it matters: tenant scoping is
    a filter on every query; bodies live in a separate dict so list
    queries don't carry body bytes; soft-delete (dismissed) sets
    ``dismissed_at`` and leaves the row visible to compliance reads.
    """

    items: dict[str, InboxItemRecord] = field(default_factory=dict)
    bodies: dict[str, InboxBodyRecord] = field(default_factory=dict)
    audits: list[InboxAuditRecord] = field(default_factory=list)

    @contextmanager
    def transaction(self) -> Iterator[None]:
        # Single-process in-memory; no actual transactional boundary.
        # The service layer still calls ``transaction()`` so the same
        # composition works against the postgres adapter without a
        # branch.
        yield

    # -- items ---------------------------------------------------------

    def insert_item(self, record: InboxItemRecord) -> InboxItemRecord:
        self.items[record.id] = record
        return record

    def get_item(self, *, tenant_id: str, item_id: str) -> InboxItemRecord | None:
        record = self.items.get(item_id)
        if record is None or record.tenant_id != tenant_id:
            return None
        return record

    def list_items(
        self,
        *,
        tenant_id: str,
        owner_user_id: str | None = None,
        states: tuple[str, ...] | None = None,
        kinds: tuple[str, ...] | None = None,
        project_ids: tuple[str | None, ...] | None = None,
        cursor: str | None = None,
        limit: int = 50,
    ) -> tuple[tuple[InboxItemRecord, ...], str | None]:
        candidates: list[InboxItemRecord] = []
        for record in self.items.values():
            if record.tenant_id != tenant_id:
                continue
            if owner_user_id is not None and record.owner_user_id != owner_user_id:
                continue
            if states is not None and record.state not in states:
                continue
            if kinds is not None and record.kind not in kinds:
                continue
            if project_ids is not None:
                if record.project_id not in project_ids:
                    continue
            candidates.append(record)

        # Sort newest-first by received_at; the cursor is the index
        # into the sorted list (opaque to the client). Postgres adapter
        # uses ``(received_at, id)`` keyset pagination — see schema.sql.
        candidates.sort(key=lambda r: (r.received_at, r.id), reverse=True)
        start = 0
        if cursor is not None:
            try:
                start = int(cursor)
            except ValueError:
                start = 0
        page = candidates[start : start + limit]
        next_cursor: str | None = None
        if start + limit < len(candidates):
            next_cursor = str(start + limit)
        return tuple(page), next_cursor

    def list_project_member_items(
        self,
        *,
        tenant_id: str,
        project_ids: tuple[str, ...],
        cursor: str | None = None,
        limit: int = 50,
    ) -> tuple[tuple[InboxItemRecord, ...], str | None]:
        candidates = [
            record
            for record in self.items.values()
            if record.tenant_id == tenant_id and record.project_id in project_ids
        ]
        candidates.sort(key=lambda r: (r.received_at, r.id), reverse=True)
        start = int(cursor) if cursor else 0
        page = candidates[start : start + limit]
        next_cursor = str(start + limit) if start + limit < len(candidates) else None
        return tuple(page), next_cursor

    def update_item(self, record: InboxItemRecord) -> InboxItemRecord:
        self.items[record.id] = record
        return record

    def count_unread(self, *, tenant_id: str, owner_user_id: str) -> int:
        return sum(
            1
            for r in self.items.values()
            if r.tenant_id == tenant_id
            and r.owner_user_id == owner_user_id
            and r.state == "unread"
        )

    # -- bodies --------------------------------------------------------

    def insert_body(self, record: InboxBodyRecord) -> InboxBodyRecord:
        self.bodies[record.body_ref] = record
        return record

    def get_body(self, *, tenant_id: str, body_ref: str) -> InboxBodyRecord | None:
        record = self.bodies.get(body_ref)
        if record is None or record.tenant_id != tenant_id:
            return None
        return record

    # -- audit ---------------------------------------------------------

    def append_audit(self, record: InboxAuditRecord) -> InboxAuditRecord:
        self.audits.append(record)
        return record

    def list_audit_for_item(
        self, *, tenant_id: str, item_id: str
    ) -> tuple[InboxAuditRecord, ...]:
        return tuple(
            r
            for r in self.audits
            if r.tenant_id == tenant_id and r.target_id == item_id
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def iter_audit_rows_for_bulk(
    records: Iterable[InboxAuditRecord],
    *,
    correlation_id: str,
) -> Iterator[InboxAuditRecord]:
    """Stamp ``correlation_id`` on every audit row in a bulk write."""

    for record in records:
        yield record.model_copy(update={"correlation_id": correlation_id})


__all__ = [
    "InMemoryInboxStore",
    "InboxAuditRecord",
    "InboxBodyRecord",
    "InboxItemRecord",
    "InboxStore",
    "iter_audit_rows_for_bulk",
]
