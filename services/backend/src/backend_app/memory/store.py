"""Memory destination store — adapter contract + in-memory implementation.

Storage shape mirrors :mod:`backend_app.memory.schema.sql`. The in-memory
adapter is the dev / test default; the Postgres adapter (deployment
composer's job) implements the same Protocol.

Two record kinds:

* :class:`MemoryItemRecord` — durable rows the runtime reads via
  ``Purpose.MEMORY_RETRIEVAL`` (sub-PRD §3.2 + §4.2 / sibling P12-A5).
* :class:`MemoryProposalRecord` — auto-extracted proposals awaiting an
  accept/reject decision (sub-PRD §9.1).

Embeddings are NOT stored here — memory chunks live in
``library_embeddings`` under ``target_kind="memory"`` (sub-PRD §5.1 DRY).
The indexer is wired in :mod:`backend_app.memory.indexer`.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterator, Literal, Protocol
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _memory_id() -> str:
    return f"mem_{uuid4().hex}"


def _proposal_id() -> str:
    return f"memprop_{uuid4().hex}"


def _audit_id() -> str:
    return f"audmem_{uuid4().hex}"


MemoryScopeLiteral = Literal["user", "workspace"]
MemoryKindLiteral = Literal["skill", "fact", "preference"]
MemoryProposalStatusLiteral = Literal["pending", "accepted", "rejected", "snoozed"]


# ---------------------------------------------------------------------------
# Records (Pydantic; shared between in-memory + future Postgres adapter)
# ---------------------------------------------------------------------------


class MemoryItemRecord(BaseModel):
    """One row in ``memory_items`` (sub-PRD §5.2 / §3.2 wire mirror).

    ``created_by`` keeps the wire JSON shape verbatim (``{"kind": ..., "id":
    ...}``) so the route layer is a pass-through marshaler.
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=_memory_id)
    tenant_id: str
    owner_user_id: str
    scope: MemoryScopeLiteral = "user"
    kind: MemoryKindLiteral
    title: str = Field(..., min_length=1, max_length=200)
    body: str = ""
    tags: list[str] = Field(default_factory=list)
    created_by: dict[str, Any] = Field(default_factory=dict)
    last_used_at: datetime | None = None
    project_id: str | None = None
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)
    deleted_at: datetime | None = None


class MemoryProposalRecord(BaseModel):
    """One row in ``memory_proposals`` (sub-PRD §5.2 / §3.2)."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=_proposal_id)
    tenant_id: str
    user_id: str
    status: MemoryProposalStatusLiteral = "pending"
    proposed_at: datetime = Field(default_factory=_now)
    proposed_kind: MemoryKindLiteral
    proposed_title: str = Field(..., min_length=1, max_length=200)
    proposed_body: str = ""
    # ``source`` mirrors the wire ItemRef ({"kind": ..., "id": ...}).
    source: dict[str, Any] = Field(default_factory=dict)
    decided_at: datetime | None = None
    accepted_memory_id: str | None = None


class MemoryAuditRecord(BaseModel):
    """Append-only audit row written on every state change."""

    model_config = ConfigDict(extra="forbid")

    audit_id: str = Field(default_factory=_audit_id)
    tenant_id: str
    actor_user_id: str
    action: str
    target_kind: str = "memory_item"  # or "memory_proposal"
    target_id: str
    before_state: dict[str, Any] | None = None
    after_state: dict[str, Any] | None = None
    correlation_id: str | None = None
    ts: datetime = Field(default_factory=_now)


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


class MemoryStore(Protocol):
    """Adapter contract for the in-memory + (future) Postgres memory stores."""

    @contextmanager
    def transaction(self) -> Iterator[None]: ...  # pragma: no cover

    # -- memory_items --------------------------------------------------

    def insert_item(self, record: MemoryItemRecord) -> MemoryItemRecord: ...

    def get_item(
        self, *, tenant_id: str, item_id: str, include_deleted: bool = False
    ) -> MemoryItemRecord | None: ...

    def list_items(
        self,
        *,
        tenant_id: str,
        owner_user_id: str | None = None,
        scopes: tuple[str, ...] | None = None,
        kinds: tuple[str, ...] | None = None,
        project_ids: tuple[str | None, ...] | None = None,
        tags: tuple[str, ...] | None = None,
        q: str | None = None,
        cursor: str | None = None,
        limit: int = 50,
        sort: str = "last_used:desc",
        include_deleted: bool = False,
    ) -> tuple[tuple[MemoryItemRecord, ...], str | None]: ...

    def update_item(self, record: MemoryItemRecord) -> MemoryItemRecord: ...

    def soft_delete_item(
        self, *, tenant_id: str, item_id: str, now: datetime | None = None
    ) -> MemoryItemRecord | None: ...

    def touch_item(
        self, *, tenant_id: str, item_id: str, now: datetime | None = None
    ) -> MemoryItemRecord | None: ...

    # -- memory_proposals ----------------------------------------------

    def insert_proposal(self, record: MemoryProposalRecord) -> MemoryProposalRecord: ...

    def get_proposal(
        self, *, tenant_id: str, proposal_id: str
    ) -> MemoryProposalRecord | None: ...

    def list_proposals(
        self,
        *,
        tenant_id: str,
        user_id: str,
        statuses: tuple[str, ...] | None = None,
        cursor: str | None = None,
        limit: int = 50,
    ) -> tuple[tuple[MemoryProposalRecord, ...], str | None]: ...

    def update_proposal(self, record: MemoryProposalRecord) -> MemoryProposalRecord: ...

    # -- audit ---------------------------------------------------------

    def append_audit(self, record: MemoryAuditRecord) -> MemoryAuditRecord: ...

    def list_audit_for_target(
        self, *, tenant_id: str, target_id: str
    ) -> tuple[MemoryAuditRecord, ...]: ...


# ---------------------------------------------------------------------------
# In-memory adapter (dev / test default)
# ---------------------------------------------------------------------------


@dataclass
class InMemoryMemoryStore:
    """Dict-backed adapter for tests + the default dev wiring.

    Mirrors Postgres semantics where it matters: tenant scoping is the
    first filter on every query; soft-delete (``deleted_at``) leaves
    rows out of the public list / get paths but visible to
    compliance reads (``include_deleted=True``).
    """

    items: dict[str, MemoryItemRecord] = field(default_factory=dict)
    proposals: dict[str, MemoryProposalRecord] = field(default_factory=dict)
    audits: list[MemoryAuditRecord] = field(default_factory=list)

    @contextmanager
    def transaction(self) -> Iterator[None]:
        # Single-process in-memory; no actual transactional boundary.
        # The service layer still calls ``transaction()`` so the same
        # composition works against the Postgres adapter without a branch.
        yield

    # -- items ---------------------------------------------------------

    def insert_item(self, record: MemoryItemRecord) -> MemoryItemRecord:
        self.items[record.id] = record
        return record

    def get_item(
        self,
        *,
        tenant_id: str,
        item_id: str,
        include_deleted: bool = False,
    ) -> MemoryItemRecord | None:
        record = self.items.get(item_id)
        if record is None or record.tenant_id != tenant_id:
            return None
        if record.deleted_at is not None and not include_deleted:
            return None
        return record

    def list_items(
        self,
        *,
        tenant_id: str,
        owner_user_id: str | None = None,
        scopes: tuple[str, ...] | None = None,
        kinds: tuple[str, ...] | None = None,
        project_ids: tuple[str | None, ...] | None = None,
        tags: tuple[str, ...] | None = None,
        q: str | None = None,
        cursor: str | None = None,
        limit: int = 50,
        sort: str = "last_used:desc",
        include_deleted: bool = False,
    ) -> tuple[tuple[MemoryItemRecord, ...], str | None]:
        candidates: list[MemoryItemRecord] = []
        q_lower = q.strip().lower() if q else None
        for record in self.items.values():
            if record.tenant_id != tenant_id:
                continue
            if not include_deleted and record.deleted_at is not None:
                continue
            if owner_user_id is not None and record.owner_user_id != owner_user_id:
                continue
            if scopes is not None and record.scope not in scopes:
                continue
            if kinds is not None and record.kind not in kinds:
                continue
            if project_ids is not None and record.project_id not in project_ids:
                continue
            if tags is not None:
                if not any(tag in record.tags for tag in tags):
                    continue
            if q_lower is not None:
                blob = f"{record.title}\n{record.body}\n{' '.join(record.tags)}".lower()
                if q_lower not in blob:
                    continue
            candidates.append(record)

        candidates.sort(key=_sort_key_for(sort), reverse=_sort_reverse(sort))

        start = 0
        if cursor is not None:
            try:
                start = max(int(cursor), 0)
            except ValueError:
                start = 0
        page = candidates[start : start + limit]
        next_cursor: str | None = None
        if start + limit < len(candidates):
            next_cursor = str(start + limit)
        return tuple(page), next_cursor

    def update_item(self, record: MemoryItemRecord) -> MemoryItemRecord:
        self.items[record.id] = record
        return record

    def soft_delete_item(
        self,
        *,
        tenant_id: str,
        item_id: str,
        now: datetime | None = None,
    ) -> MemoryItemRecord | None:
        record = self.items.get(item_id)
        if record is None or record.tenant_id != tenant_id:
            return None
        if record.deleted_at is not None:
            return record
        deleted = record.model_copy(
            update={"deleted_at": now or _now(), "updated_at": now or _now()}
        )
        self.items[item_id] = deleted
        return deleted

    def touch_item(
        self,
        *,
        tenant_id: str,
        item_id: str,
        now: datetime | None = None,
    ) -> MemoryItemRecord | None:
        record = self.items.get(item_id)
        if record is None or record.tenant_id != tenant_id:
            return None
        if record.deleted_at is not None:
            return None
        touched = record.model_copy(
            update={"last_used_at": now or _now()},
        )
        self.items[item_id] = touched
        return touched

    # -- proposals -----------------------------------------------------

    def insert_proposal(self, record: MemoryProposalRecord) -> MemoryProposalRecord:
        self.proposals[record.id] = record
        return record

    def get_proposal(
        self, *, tenant_id: str, proposal_id: str
    ) -> MemoryProposalRecord | None:
        record = self.proposals.get(proposal_id)
        if record is None or record.tenant_id != tenant_id:
            return None
        return record

    def list_proposals(
        self,
        *,
        tenant_id: str,
        user_id: str,
        statuses: tuple[str, ...] | None = None,
        cursor: str | None = None,
        limit: int = 50,
    ) -> tuple[tuple[MemoryProposalRecord, ...], str | None]:
        candidates = [
            record
            for record in self.proposals.values()
            if record.tenant_id == tenant_id
            and record.user_id == user_id
            and (statuses is None or record.status in statuses)
        ]
        candidates.sort(key=lambda r: (r.proposed_at, r.id), reverse=True)
        start = 0
        if cursor is not None:
            try:
                start = max(int(cursor), 0)
            except ValueError:
                start = 0
        page = candidates[start : start + limit]
        next_cursor = str(start + limit) if start + limit < len(candidates) else None
        return tuple(page), next_cursor

    def update_proposal(self, record: MemoryProposalRecord) -> MemoryProposalRecord:
        self.proposals[record.id] = record
        return record

    # -- audit ---------------------------------------------------------

    def append_audit(self, record: MemoryAuditRecord) -> MemoryAuditRecord:
        self.audits.append(record)
        return record

    def list_audit_for_target(
        self, *, tenant_id: str, target_id: str
    ) -> tuple[MemoryAuditRecord, ...]:
        return tuple(
            r
            for r in self.audits
            if r.tenant_id == tenant_id and r.target_id == target_id
        )


# ---------------------------------------------------------------------------
# Sort helpers
# ---------------------------------------------------------------------------


_SORT_TOKENS = {
    "last_used:desc",
    "created_at:desc",
    "created_at:asc",
    "updated_at:desc",
}


def _sort_key_for(token: str):
    if token == "created_at:desc" or token == "created_at:asc":
        return lambda r: (r.created_at, r.id)
    if token == "updated_at:desc":
        return lambda r: (r.updated_at, r.id)
    # default — last_used:desc; treat None last_used_at as oldest.
    return lambda r: (
        r.last_used_at or datetime.min.replace(tzinfo=timezone.utc),
        r.updated_at,
        r.id,
    )


def _sort_reverse(token: str) -> bool:
    return token != "created_at:asc"


def is_valid_sort_token(token: str) -> bool:
    return token in _SORT_TOKENS


__all__ = [
    "InMemoryMemoryStore",
    "MemoryAuditRecord",
    "MemoryItemRecord",
    "MemoryKindLiteral",
    "MemoryProposalRecord",
    "MemoryProposalStatusLiteral",
    "MemoryScopeLiteral",
    "MemoryStore",
    "is_valid_sort_token",
]
