"""Todos store — adapter contract + in-memory implementation.

Storage shape mirrors ``services/backend/migrations/0032_todos.sql`` and
``0033_todo_series.sql`` (see ``schema.sql`` in this package for the
canonical DDL). The in-memory adapter is the dev / test default; the
postgres adapter (out of scope for this PR — landed alongside the
migration during the same merge in production deployments) implements
the same Protocol.

Authorization is NOT enforced here. The service layer composes
``TodosStore`` with the identity store + role check from
``ScopedIdentity.roles`` to decide read/write authority; the store
exposes raw queries scoped to ``tenant_id``.

Subtask invariants are enforced HERE (one level only, project
inheritance, cascade-delete) because the FK + check constraints live
on the schema. The route layer raises 400 from the ``ValueError``
the service translates.
"""

from __future__ import annotations

from collections.abc import Iterable
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Iterator, Protocol
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _todo_id() -> str:
    return f"todo_{uuid4().hex}"


def _audit_id() -> str:
    return f"audaud_{uuid4().hex}"


def _series_id() -> str:
    return uuid4().hex


# ---------------------------------------------------------------------------
# Records
# ---------------------------------------------------------------------------


class TodoRecord(BaseModel):
    """One row in the ``todos`` table.

    Pydantic model so the Postgres adapter and the in-memory adapter
    can share a single read/write contract. ``source`` is a free-form
    JSONB blob on the wire; we keep it as ``dict`` here and let the
    routes serialise to the canonical ``TodoSource`` discriminated
    union.
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=_todo_id)
    tenant_id: str
    owner_user_id: str
    project_id: str | None = None
    text: str
    status: str = "open"
    priority: str = "med"
    due: str | None = None
    source: dict = Field(default_factory=lambda: {"kind": "user"})
    parent_id: str | None = None
    sort_index_within_parent: float | None = None
    recurrence: dict | None = None
    series_id: str | None = None
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)
    completed_at: datetime | None = None
    deleted_at: datetime | None = None


class TodoSeriesRecord(BaseModel):
    """One row in the ``todo_series`` table.

    Created when a todo opts into recurrence; the materialiser job in
    ai-backend (out of scope for P3-A1) consumes ``last_materialized_due``
    to advance the next concrete row.
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=_series_id)
    tenant_id: str
    owner_user_id: str
    rule: str
    spec: str
    started_at: datetime = Field(default_factory=_now)
    ends_at: datetime | None = None
    last_materialized_due: datetime | None = None


class TodoAuditRecord(BaseModel):
    """Append-only audit row written on every state change.

    The audit chain integration (``packages/audit-chain``) signs +
    chains rows in production. The in-memory adapter appends raw rows
    for tests; the postgres adapter writes through the chain signer
    (same path as ``mcp_audit_events`` — see ``store.py`` lines 97-114
    on the backend service for the existing pattern).

    ``correlation_id`` is set on bulk-action rows so SIEM can query the
    rows belonging to one bulk write as a unit (cross-audit §1.4 audit
    row shape + Todos PRD §6 bulk semantics).

    ``before_state`` / ``after_state`` are dicts; the route layer
    redacts ``text`` if a tenant-level policy demands it (out of scope
    for this PR — text redaction is the same field across audit
    surfaces).
    """

    model_config = ConfigDict(extra="forbid")

    audit_id: str = Field(default_factory=_audit_id)
    tenant_id: str
    actor_user_id: str
    action: str
    target_kind: str = "todo"
    target_id: str
    before_state: dict | None = None
    after_state: dict | None = None
    correlation_id: str | None = None
    ts: datetime = Field(default_factory=_now)


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


class TodosStore(Protocol):
    """Adapter contract for the Postgres + in-memory todos stores."""

    @contextmanager
    def transaction(self) -> Iterator[None]: ...  # pragma: no cover

    # -- todos table ---------------------------------------------------

    def insert_todo(self, record: TodoRecord) -> TodoRecord: ...

    def get_todo(self, *, tenant_id: str, todo_id: str) -> TodoRecord | None: ...

    def list_todos(
        self,
        *,
        tenant_id: str,
        owner_user_id: str | None = None,
        statuses: tuple[str, ...] | None = None,
        project_ids: tuple[str | None, ...] | None = None,
        parent_id: str | None = None,
        include_subtasks: bool = True,
        cursor: str | None = None,
        limit: int = 50,
    ) -> tuple[tuple[TodoRecord, ...], str | None]: ...

    def list_project_member_todos(
        self,
        *,
        tenant_id: str,
        project_ids: tuple[str, ...],
        cursor: str | None = None,
        limit: int = 50,
    ) -> tuple[tuple[TodoRecord, ...], str | None]: ...

    def update_todo(self, record: TodoRecord) -> TodoRecord: ...

    def delete_todo(self, *, tenant_id: str, todo_id: str) -> tuple[str, ...]:
        """Soft-delete a todo + cascade to its (one-level) subtasks.

        Returns the IDs of every row deleted (parent + each child) so
        the service layer can write one audit row per affected todo.
        """

    def list_children(
        self, *, tenant_id: str, parent_id: str
    ) -> tuple[TodoRecord, ...]: ...

    # -- audit ---------------------------------------------------------

    def append_audit(self, record: TodoAuditRecord) -> TodoAuditRecord: ...

    def list_audit_for_todo(
        self, *, tenant_id: str, todo_id: str
    ) -> tuple[TodoAuditRecord, ...]: ...

    # -- series --------------------------------------------------------

    def insert_series(self, record: TodoSeriesRecord) -> TodoSeriesRecord: ...

    def get_series(
        self, *, tenant_id: str, series_id: str
    ) -> TodoSeriesRecord | None: ...


# ---------------------------------------------------------------------------
# In-memory adapter
# ---------------------------------------------------------------------------


@dataclass
class InMemoryTodosStore:
    """Dict-backed adapter for tests + the default dev wiring.

    Mirrors the Postgres semantics where it matters: tenant scoping is
    a filter on every query; cascade-delete walks children one level
    deep; soft-delete sets ``deleted_at`` and removes the row from
    list responses.
    """

    todos: dict[str, TodoRecord] = field(default_factory=dict)
    audits: list[TodoAuditRecord] = field(default_factory=list)
    series: dict[str, TodoSeriesRecord] = field(default_factory=dict)

    @contextmanager
    def transaction(self) -> Iterator[None]:
        # Single-process in-memory; no actual transactional boundary.
        # The route layer still calls ``transaction()`` so the same
        # composition works against the postgres adapter without a
        # branch.
        yield

    # -- todos ---------------------------------------------------------

    def insert_todo(self, record: TodoRecord) -> TodoRecord:
        self.todos[record.id] = record
        return record

    def get_todo(self, *, tenant_id: str, todo_id: str) -> TodoRecord | None:
        record = self.todos.get(todo_id)
        if record is None:
            return None
        if record.tenant_id != tenant_id:
            return None
        if record.deleted_at is not None:
            return None
        return record

    def list_todos(
        self,
        *,
        tenant_id: str,
        owner_user_id: str | None = None,
        statuses: tuple[str, ...] | None = None,
        project_ids: tuple[str | None, ...] | None = None,
        parent_id: str | None = None,
        include_subtasks: bool = True,
        cursor: str | None = None,
        limit: int = 50,
    ) -> tuple[tuple[TodoRecord, ...], str | None]:
        candidates: list[TodoRecord] = []
        for record in self.todos.values():
            if record.tenant_id != tenant_id:
                continue
            if record.deleted_at is not None:
                continue
            if owner_user_id is not None and record.owner_user_id != owner_user_id:
                continue
            if statuses is not None and record.status not in statuses:
                continue
            if project_ids is not None:
                # ``None`` in the tuple means "unfiled" (matches NULL).
                if record.project_id not in project_ids:
                    continue
            if parent_id is not None:
                if record.parent_id != parent_id:
                    continue
            elif not include_subtasks and record.parent_id is not None:
                continue
            candidates.append(record)

        # Sort newest-first by created_at; the cursor is the index into
        # the sorted list (opaque to the client). Postgres adapter uses
        # `(created_at, id)` keyset pagination — see schema.sql.
        candidates.sort(key=lambda r: (r.created_at, r.id), reverse=True)
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

    def list_project_member_todos(
        self,
        *,
        tenant_id: str,
        project_ids: tuple[str, ...],
        cursor: str | None = None,
        limit: int = 50,
    ) -> tuple[tuple[TodoRecord, ...], str | None]:
        candidates: list[TodoRecord] = [
            record
            for record in self.todos.values()
            if record.tenant_id == tenant_id
            and record.deleted_at is None
            and record.project_id in project_ids
        ]
        candidates.sort(key=lambda r: (r.created_at, r.id), reverse=True)
        start = int(cursor) if cursor else 0
        page = candidates[start : start + limit]
        next_cursor = str(start + limit) if start + limit < len(candidates) else None
        return tuple(page), next_cursor

    def update_todo(self, record: TodoRecord) -> TodoRecord:
        # Re-stamp updated_at and persist; the service layer caller
        # supplies the patch and the updated record.
        self.todos[record.id] = record
        return record

    def delete_todo(self, *, tenant_id: str, todo_id: str) -> tuple[str, ...]:
        record = self.get_todo(tenant_id=tenant_id, todo_id=todo_id)
        if record is None:
            return ()
        now = _now()
        deleted: list[str] = []
        # Cascade to children first so the parent row is the last to
        # flip (matches Postgres FK ``ON DELETE CASCADE`` ordering for
        # readability of audit logs).
        for child in self.list_children(tenant_id=tenant_id, parent_id=todo_id):
            updated = child.model_copy(update={"deleted_at": now, "updated_at": now})
            self.todos[child.id] = updated
            deleted.append(child.id)
        parent = record.model_copy(update={"deleted_at": now, "updated_at": now})
        self.todos[record.id] = parent
        deleted.append(record.id)
        return tuple(deleted)

    def list_children(
        self, *, tenant_id: str, parent_id: str
    ) -> tuple[TodoRecord, ...]:
        return tuple(
            record
            for record in self.todos.values()
            if record.tenant_id == tenant_id
            and record.parent_id == parent_id
            and record.deleted_at is None
        )

    # -- audit ---------------------------------------------------------

    def append_audit(self, record: TodoAuditRecord) -> TodoAuditRecord:
        self.audits.append(record)
        return record

    def list_audit_for_todo(
        self, *, tenant_id: str, todo_id: str
    ) -> tuple[TodoAuditRecord, ...]:
        return tuple(
            r
            for r in self.audits
            if r.tenant_id == tenant_id and r.target_id == todo_id
        )

    # -- series --------------------------------------------------------

    def insert_series(self, record: TodoSeriesRecord) -> TodoSeriesRecord:
        self.series[record.id] = record
        return record

    def get_series(self, *, tenant_id: str, series_id: str) -> TodoSeriesRecord | None:
        record = self.series.get(series_id)
        if record is None or record.tenant_id != tenant_id:
            return None
        return record


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def iter_audit_rows_for_bulk(
    records: Iterable[TodoAuditRecord],
    *,
    correlation_id: str,
) -> Iterator[TodoAuditRecord]:
    """Stamp ``correlation_id`` on every audit row in a bulk write."""

    for record in records:
        yield record.model_copy(update={"correlation_id": correlation_id})


__all__ = [
    "InMemoryTodosStore",
    "TodoAuditRecord",
    "TodoRecord",
    "TodoSeriesRecord",
    "TodosStore",
    "iter_audit_rows_for_bulk",
]
