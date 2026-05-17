"""Routines store — adapter contract + in-memory implementation.

Storage shape mirrors ``schema.sql`` in this package. The in-memory
adapter is the dev / test default; the postgres adapter (out of scope
for P5-A1 — landed alongside the migration during the same merge in
production deployments) implements the same Protocol.

Authorization is NOT enforced here. The service layer composes
:class:`RoutinesStore` with the identity store + project-membership
port to decide read/write authority; the store exposes raw queries
scoped to ``tenant_id``.

Soft-delete (``deleted_at``) keeps the row visible to compliance
reads but invisible to the public list / get paths. The cleanup job
in P5-A2 hard-deletes after the retention window (routines-prd §5.3).
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


def _routine_id() -> str:
    return f"rt_{uuid4().hex}"


def _fire_id() -> str:
    return f"rfire_{uuid4().hex}"


def _audit_id() -> str:
    return f"audrt_{uuid4().hex}"


# ---------------------------------------------------------------------------
# Records
# ---------------------------------------------------------------------------


class RoutineRecord(BaseModel):
    """One row in the ``routines`` table.

    Pydantic model so the Postgres + in-memory adapters share one
    read/write contract. JSONB columns (``triggers``, ``connectors_scope``,
    ``behavior``, ``permissions``, ``code``) are kept as plain Python
    structures here; the routes serialise to the canonical TypeScript
    shapes (``Routine`` in ``packages/api-types/src/routines.ts``).
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=_routine_id)
    tenant_id: str
    owner_user_id: str
    project_id: str | None = None
    name: str
    instructions: str = ""
    agent_id: str
    agent_version_pin: str | None = None
    triggers: list[dict[str, Any]] = Field(default_factory=list)
    connectors_scope: dict[str, list[str]] = Field(default_factory=dict)
    behavior: dict[str, Any] = Field(default_factory=dict)
    # Default per cross-audit §9.7 Q2 — owner-only manual fire unless
    # the routine owner widens it explicitly.
    permissions: dict[str, Any] = Field(
        default_factory=lambda: {"manual_fire": "owner"}
    )
    code: dict[str, Any] | None = None
    status: str = "draft"
    pause_reason: str | None = None
    missed_fire_policy: str = "fire_once"
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)
    deleted_at: datetime | None = None


class RoutineFireRecord(BaseModel):
    """One row in the ``routine_fires`` table.

    Lightweight metadata for each fire — the actual run record lives
    in ai-backend via ``run.source.kind = "routine"``. This row is
    written eagerly on fire so the "last fire" rail can render
    without a cross-service join.
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=_fire_id)
    tenant_id: str
    routine_id: str
    trigger_kind: str
    fired_at: datetime = Field(default_factory=_now)
    source_ip: str | None = None
    source_payload: dict[str, Any] | None = None
    run_id: str | None = None
    status: str = "queued"


class RoutineAuditRecord(BaseModel):
    """Append-only audit row written on every state change.

    The audit chain integration (``packages/audit-chain``) signs +
    chains rows in production. The in-memory adapter appends raw rows
    for tests; the postgres adapter writes through the chain signer
    (same path as ``todo_audit_events`` and ``inbox_audit_events``).
    """

    model_config = ConfigDict(extra="forbid")

    audit_id: str = Field(default_factory=_audit_id)
    tenant_id: str
    actor_user_id: str
    action: str
    target_kind: str = "routine"
    target_id: str
    before_state: dict[str, Any] | None = None
    after_state: dict[str, Any] | None = None
    correlation_id: str | None = None
    ts: datetime = Field(default_factory=_now)


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


class RoutinesStore(Protocol):
    """Adapter contract for the Postgres + in-memory routines stores."""

    @contextmanager
    def transaction(self) -> Iterator[None]: ...  # pragma: no cover

    # -- routines ------------------------------------------------------

    def insert_routine(self, record: RoutineRecord) -> RoutineRecord: ...

    def get_routine(
        self, *, tenant_id: str, routine_id: str, include_deleted: bool = False
    ) -> RoutineRecord | None: ...

    def list_routines(
        self,
        *,
        tenant_id: str,
        owner_user_id: str | None = None,
        statuses: tuple[str, ...] | None = None,
        project_ids: tuple[str | None, ...] | None = None,
        cursor: str | None = None,
        limit: int = 50,
        include_deleted: bool = False,
    ) -> tuple[tuple[RoutineRecord, ...], str | None]: ...

    def list_project_member_routines(
        self,
        *,
        tenant_id: str,
        project_ids: tuple[str, ...],
        cursor: str | None = None,
        limit: int = 50,
    ) -> tuple[tuple[RoutineRecord, ...], str | None]: ...

    def update_routine(self, record: RoutineRecord) -> RoutineRecord: ...

    def soft_delete_routine(self, *, tenant_id: str, routine_id: str) -> bool: ...

    def count_active_for_user(self, *, tenant_id: str, owner_user_id: str) -> int: ...

    # -- fires ---------------------------------------------------------

    def insert_fire(self, record: RoutineFireRecord) -> RoutineFireRecord: ...

    def list_fires_for_routine(
        self, *, tenant_id: str, routine_id: str, limit: int = 50
    ) -> tuple[RoutineFireRecord, ...]: ...

    # -- audit ---------------------------------------------------------

    def append_audit(self, record: RoutineAuditRecord) -> RoutineAuditRecord: ...

    def list_audit_for_routine(
        self, *, tenant_id: str, routine_id: str
    ) -> tuple[RoutineAuditRecord, ...]: ...


# ---------------------------------------------------------------------------
# In-memory adapter
# ---------------------------------------------------------------------------


@dataclass
class InMemoryRoutinesStore:
    """Dict-backed adapter for tests + the default dev wiring.

    Mirrors the Postgres semantics where it matters: tenant scoping is
    a filter on every query; soft-delete (``deleted_at``) hides rows
    from the default list / get paths but leaves them visible to
    compliance reads via ``include_deleted=True``.
    """

    routines: dict[str, RoutineRecord] = field(default_factory=dict)
    fires: list[RoutineFireRecord] = field(default_factory=list)
    audits: list[RoutineAuditRecord] = field(default_factory=list)

    @contextmanager
    def transaction(self) -> Iterator[None]:
        # Single-process in-memory; no actual transactional boundary.
        # The service layer still calls ``transaction()`` so the same
        # composition works against the postgres adapter without a
        # branch.
        yield

    # -- routines ------------------------------------------------------

    def insert_routine(self, record: RoutineRecord) -> RoutineRecord:
        self.routines[record.id] = record
        return record

    def get_routine(
        self, *, tenant_id: str, routine_id: str, include_deleted: bool = False
    ) -> RoutineRecord | None:
        record = self.routines.get(routine_id)
        if record is None or record.tenant_id != tenant_id:
            return None
        if record.deleted_at is not None and not include_deleted:
            return None
        return record

    def list_routines(
        self,
        *,
        tenant_id: str,
        owner_user_id: str | None = None,
        statuses: tuple[str, ...] | None = None,
        project_ids: tuple[str | None, ...] | None = None,
        cursor: str | None = None,
        limit: int = 50,
        include_deleted: bool = False,
    ) -> tuple[tuple[RoutineRecord, ...], str | None]:
        candidates: list[RoutineRecord] = []
        for record in self.routines.values():
            if record.tenant_id != tenant_id:
                continue
            if record.deleted_at is not None and not include_deleted:
                continue
            if owner_user_id is not None and record.owner_user_id != owner_user_id:
                continue
            if statuses is not None and record.status not in statuses:
                continue
            if project_ids is not None:
                if record.project_id not in project_ids:
                    continue
            candidates.append(record)

        # Sort newest-first by created_at; the cursor is the index
        # into the sorted list (opaque to the client). Postgres adapter
        # uses ``(created_at, id)`` keyset pagination — see schema.sql.
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

    def list_project_member_routines(
        self,
        *,
        tenant_id: str,
        project_ids: tuple[str, ...],
        cursor: str | None = None,
        limit: int = 50,
    ) -> tuple[tuple[RoutineRecord, ...], str | None]:
        candidates = [
            record
            for record in self.routines.values()
            if record.tenant_id == tenant_id
            and record.deleted_at is None
            and record.project_id in project_ids
        ]
        candidates.sort(key=lambda r: (r.created_at, r.id), reverse=True)
        start = int(cursor) if cursor else 0
        page = candidates[start : start + limit]
        next_cursor = str(start + limit) if start + limit < len(candidates) else None
        return tuple(page), next_cursor

    def update_routine(self, record: RoutineRecord) -> RoutineRecord:
        self.routines[record.id] = record
        return record

    def soft_delete_routine(self, *, tenant_id: str, routine_id: str) -> bool:
        record = self.routines.get(routine_id)
        if record is None or record.tenant_id != tenant_id:
            return False
        if record.deleted_at is not None:
            # Already deleted; idempotent.
            return True
        self.routines[routine_id] = record.model_copy(update={"deleted_at": _now()})
        return True

    def count_active_for_user(self, *, tenant_id: str, owner_user_id: str) -> int:
        return sum(
            1
            for r in self.routines.values()
            if r.tenant_id == tenant_id
            and r.owner_user_id == owner_user_id
            and r.status == "active"
            and r.deleted_at is None
        )

    # -- fires ---------------------------------------------------------

    def insert_fire(self, record: RoutineFireRecord) -> RoutineFireRecord:
        self.fires.append(record)
        return record

    def list_fires_for_routine(
        self, *, tenant_id: str, routine_id: str, limit: int = 50
    ) -> tuple[RoutineFireRecord, ...]:
        rows = [
            f
            for f in self.fires
            if f.tenant_id == tenant_id and f.routine_id == routine_id
        ]
        rows.sort(key=lambda r: (r.fired_at, r.id), reverse=True)
        return tuple(rows[:limit])

    # -- audit ---------------------------------------------------------

    def append_audit(self, record: RoutineAuditRecord) -> RoutineAuditRecord:
        self.audits.append(record)
        return record

    def list_audit_for_routine(
        self, *, tenant_id: str, routine_id: str
    ) -> tuple[RoutineAuditRecord, ...]:
        return tuple(
            r
            for r in self.audits
            if r.tenant_id == tenant_id and r.target_id == routine_id
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def iter_audit_rows_for_bulk(
    records: Iterable[RoutineAuditRecord],
    *,
    correlation_id: str,
) -> Iterator[RoutineAuditRecord]:
    """Stamp ``correlation_id`` on every audit row in a bulk write."""

    for record in records:
        yield record.model_copy(update={"correlation_id": correlation_id})


__all__ = [
    "InMemoryRoutinesStore",
    "RoutineAuditRecord",
    "RoutineFireRecord",
    "RoutineRecord",
    "RoutinesStore",
    "iter_audit_rows_for_bulk",
]
