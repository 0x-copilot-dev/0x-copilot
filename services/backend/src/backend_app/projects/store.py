"""Projects store — adapter contract + in-memory implementation.

Storage shape mirrors ``schema.sql`` in this package. The in-memory
adapter is the dev / test default; the Postgres adapter (shipped in
production deployments alongside the migration) implements the same
Protocol.

Authorization is NOT enforced here. The service layer
(:class:`ProjectsService`) composes the store with the identity store
and the canonical :class:`ProjectMembershipPort` to decide read / write
authority; the store exposes raw queries scoped to ``tenant_id``.

Soft-delete (``deleted_at``) keeps the row visible to compliance reads
but invisible to the public list / get paths. The cleanup job in
``jobs/projects_retention.py`` hard-deletes after the 30-day window
(projects-prd §5.3).
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


def _project_id() -> str:
    return f"prj_{uuid4().hex}"


def _activity_id() -> str:
    return f"pact_{uuid4().hex}"


def _audit_id() -> str:
    return f"audprj_{uuid4().hex}"


# Public default counts shape — used when the projector hasn't run yet
# (fresh-created project) so list responses don't carry ``null`` and
# the wire shape stays uniform.
_EMPTY_COUNTS: dict[str, int] = {
    "chats": 0,
    "todos_open": 0,
    "todos_done": 0,
    "inbox_items": 0,
    "library_items": 0,
    "routines_active": 0,
    "members": 0,
}


# ---------------------------------------------------------------------------
# Records
# ---------------------------------------------------------------------------


class ProjectRecord(BaseModel):
    """One row in the ``projects`` table.

    Pydantic model so the Postgres + in-memory adapters share one
    read/write contract. ``viewer_role`` / ``viewer_starred`` /
    ``counts`` are **derived** at the route layer (caller-relative);
    they do NOT live on the storage row.
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=_project_id)
    tenant_id: str
    owner_user_id: str
    name: str
    description: str = ""
    icon_emoji: str = "📁"
    color_hue: int = 210
    status: str = "active"
    archived_at: datetime | None = None
    last_activity_at: datetime | None = None
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)
    deleted_at: datetime | None = None
    # Phase 6.5 §5 — connector inheritance for new chats / routines.
    # ``None`` = inherit owner defaults; ``[]`` = explicit deny;
    # ``["salesforce", ...]`` = allowlist of ConnectorSlug values. The
    # field travels as kinds (not ConnectorIds) so a re-grant doesn't
    # invalidate the rule.
    default_connector_allowlist: list[str] | None = None


class ProjectMembershipRecord(BaseModel):
    """One row in the ``project_memberships`` table.

    The (project_id, user_id) pair is the natural primary key; the
    canonical PARTIAL UNIQUE on ``(project_id) WHERE role='owner'`` is
    enforced at write time by the service layer (atomic ownership
    transfer; never two owners in the same project at any moment).
    """

    model_config = ConfigDict(extra="forbid")

    project_id: str
    user_id: str
    tenant_id: str
    role: str
    added_at: datetime = Field(default_factory=_now)
    added_by: str


class ProjectStarRecord(BaseModel):
    """Per-user star. Cascades on project hard-delete."""

    model_config = ConfigDict(extra="forbid")

    tenant_id: str
    user_id: str
    project_id: str
    created_at: datetime = Field(default_factory=_now)


class ProjectActivityRecord(BaseModel):
    """Projected audit-row copy keyed by ``project_id``.

    Written by the projector (out of scope for P6-A1 — the projector
    lands alongside the cross-destination activity feed in P6-A's
    follow-on sub-PRD). P6-A1 ships the table + the audit-id
    idempotency key so producers can write through the same shape
    once the projector arrives.
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=_activity_id)
    tenant_id: str
    project_id: str
    audit_id: str
    actor_user_id: str | None = None
    actor_display_name: str = ""
    action: str
    kind: str
    ref_kind: str
    ref_id: str
    preview: str = ""
    occurred_at: datetime = Field(default_factory=_now)


class ProjectActivityCounts(BaseModel):
    """Denormalized per-project counts for list-view perf.

    Updated incrementally by the projector; reconciled nightly from
    authoritative tables to repair drift (projects-prd §5.4).
    """

    model_config = ConfigDict(extra="forbid")

    tenant_id: str
    project_id: str
    chats: int = 0
    todos_open: int = 0
    todos_done: int = 0
    inbox_items: int = 0
    library_items: int = 0
    routines_active: int = 0
    members: int = 0
    recomputed_at: datetime = Field(default_factory=_now)


class ProjectAuditRecord(BaseModel):
    """Append-only audit row written on every state change.

    The audit-chain integration (``packages/audit-chain``) signs + chains
    rows in production. The in-memory adapter appends raw rows for tests;
    the Postgres adapter writes through the chain signer (same path as
    ``routine_audit_events`` / ``todo_audit_events`` / ``inbox_audit_events``).
    """

    model_config = ConfigDict(extra="forbid")

    audit_id: str = Field(default_factory=_audit_id)
    tenant_id: str
    actor_user_id: str
    action: str
    target_kind: str = "project"
    target_id: str
    before_state: dict[str, Any] | None = None
    after_state: dict[str, Any] | None = None
    context: dict[str, Any] | None = None  # cross-audit §1.4
    correlation_id: str | None = None
    ts: datetime = Field(default_factory=_now)


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


class ProjectsStore(Protocol):
    """Adapter contract for the Postgres + in-memory projects stores."""

    @contextmanager
    def transaction(self) -> Iterator[None]: ...  # pragma: no cover

    # -- projects ------------------------------------------------------

    def insert_project(self, record: ProjectRecord) -> ProjectRecord: ...

    def get_project(
        self, *, tenant_id: str, project_id: str, include_deleted: bool = False
    ) -> ProjectRecord | None: ...

    def get_project_by_name(
        self, *, tenant_id: str, name: str
    ) -> ProjectRecord | None: ...

    def list_projects(
        self,
        *,
        tenant_id: str,
        owner_user_id: str | None = None,
        member_user_id: str | None = None,
        statuses: tuple[str, ...] | None = None,
        q: str | None = None,
        starred_by_user_id: str | None = None,
        cursor: str | None = None,
        limit: int = 50,
        sort: str = "updated_at:desc",
        include_deleted: bool = False,
    ) -> tuple[tuple[ProjectRecord, ...], str | None]: ...

    def update_project(self, record: ProjectRecord) -> ProjectRecord: ...

    def soft_delete_project(self, *, tenant_id: str, project_id: str) -> bool: ...

    # -- memberships ---------------------------------------------------

    def insert_membership(
        self, record: ProjectMembershipRecord
    ) -> ProjectMembershipRecord: ...

    def get_membership(
        self, *, tenant_id: str, project_id: str, user_id: str
    ) -> ProjectMembershipRecord | None: ...

    def list_memberships_for_project(
        self,
        *,
        tenant_id: str,
        project_id: str,
        cursor: str | None = None,
        limit: int = 50,
    ) -> tuple[tuple[ProjectMembershipRecord, ...], str | None]: ...

    def list_memberships_for_user(
        self, *, tenant_id: str, user_id: str
    ) -> tuple[ProjectMembershipRecord, ...]: ...

    def update_membership_role(
        self,
        *,
        tenant_id: str,
        project_id: str,
        user_id: str,
        role: str,
    ) -> ProjectMembershipRecord | None: ...

    def delete_membership(
        self, *, tenant_id: str, project_id: str, user_id: str
    ) -> bool: ...

    # -- stars ---------------------------------------------------------

    def upsert_star(self, record: ProjectStarRecord) -> ProjectStarRecord: ...

    def delete_star(self, *, tenant_id: str, project_id: str, user_id: str) -> bool: ...

    def is_starred(self, *, tenant_id: str, project_id: str, user_id: str) -> bool: ...

    # -- activity ------------------------------------------------------

    def append_activity(
        self, record: ProjectActivityRecord
    ) -> ProjectActivityRecord | None: ...

    def list_activity(
        self,
        *,
        tenant_id: str,
        project_id: str,
        kinds: tuple[str, ...] | None = None,
        cursor: str | None = None,
        limit: int = 50,
    ) -> tuple[tuple[ProjectActivityRecord, ...], str | None]: ...

    # -- counts --------------------------------------------------------

    def get_counts(
        self, *, tenant_id: str, project_id: str
    ) -> ProjectActivityCounts | None: ...

    # -- audit ---------------------------------------------------------

    def append_audit(self, record: ProjectAuditRecord) -> ProjectAuditRecord: ...

    def list_audit_for_project(
        self, *, tenant_id: str, project_id: str
    ) -> tuple[ProjectAuditRecord, ...]: ...


# ---------------------------------------------------------------------------
# In-memory adapter
# ---------------------------------------------------------------------------


@dataclass
class InMemoryProjectsStore:
    """Dict-backed adapter for tests + the default dev wiring.

    Mirrors the Postgres semantics where it matters: tenant scoping is a
    filter on every query; soft-delete (``deleted_at``) hides rows from
    the default list / get paths but leaves them visible to compliance
    reads via ``include_deleted=True``. Multi-axis filtering composes
    in-process (the Postgres adapter pushes the same predicates into
    SQL).
    """

    projects: dict[str, ProjectRecord] = field(default_factory=dict)
    memberships: dict[tuple[str, str], ProjectMembershipRecord] = field(
        default_factory=dict
    )
    stars: dict[tuple[str, str, str], ProjectStarRecord] = field(default_factory=dict)
    activity: list[ProjectActivityRecord] = field(default_factory=list)
    _activity_audit_keys: set[tuple[str, str]] = field(default_factory=set)
    counts: dict[tuple[str, str], ProjectActivityCounts] = field(default_factory=dict)
    audits: list[ProjectAuditRecord] = field(default_factory=list)

    @contextmanager
    def transaction(self) -> Iterator[None]:
        # Single-process in-memory; no actual transactional boundary.
        # The service layer still calls ``transaction()`` so the same
        # composition works against the Postgres adapter without a
        # branch.
        yield

    # -- projects ------------------------------------------------------

    def insert_project(self, record: ProjectRecord) -> ProjectRecord:
        self.projects[record.id] = record
        return record

    def get_project(
        self, *, tenant_id: str, project_id: str, include_deleted: bool = False
    ) -> ProjectRecord | None:
        record = self.projects.get(project_id)
        if record is None or record.tenant_id != tenant_id:
            return None
        if record.deleted_at is not None and not include_deleted:
            return None
        return record

    def get_project_by_name(self, *, tenant_id: str, name: str) -> ProjectRecord | None:
        wanted = name.strip().lower()
        for record in self.projects.values():
            if record.tenant_id != tenant_id or record.deleted_at is not None:
                continue
            if record.name.strip().lower() == wanted:
                return record
        return None

    def list_projects(
        self,
        *,
        tenant_id: str,
        owner_user_id: str | None = None,
        member_user_id: str | None = None,
        statuses: tuple[str, ...] | None = None,
        q: str | None = None,
        starred_by_user_id: str | None = None,
        cursor: str | None = None,
        limit: int = 50,
        sort: str = "updated_at:desc",
        include_deleted: bool = False,
    ) -> tuple[tuple[ProjectRecord, ...], str | None]:
        candidates: list[ProjectRecord] = []
        member_projects: set[str] | None = None
        if member_user_id is not None:
            member_projects = {
                m.project_id
                for m in self.memberships.values()
                if m.tenant_id == tenant_id and m.user_id == member_user_id
            }
        starred_projects: set[str] | None = None
        if starred_by_user_id is not None:
            starred_projects = {
                s.project_id
                for s in self.stars.values()
                if s.tenant_id == tenant_id and s.user_id == starred_by_user_id
            }
        q_normalized = q.strip().lower() if q else None
        for record in self.projects.values():
            if record.tenant_id != tenant_id:
                continue
            if record.deleted_at is not None and not include_deleted:
                continue
            if owner_user_id is not None and record.owner_user_id != owner_user_id:
                continue
            if member_projects is not None and record.id not in member_projects:
                continue
            if statuses is not None and record.status not in statuses:
                continue
            if starred_projects is not None and record.id not in starred_projects:
                continue
            if q_normalized:
                haystack = f"{record.name} {record.description}".lower()
                if q_normalized not in haystack:
                    continue
            candidates.append(record)

        candidates.sort(key=_sort_key(sort), reverse=_sort_descending(sort))
        start = _decode_cursor(cursor)
        page = candidates[start : start + limit]
        next_cursor: str | None = None
        if start + limit < len(candidates):
            next_cursor = str(start + limit)
        return tuple(page), next_cursor

    def update_project(self, record: ProjectRecord) -> ProjectRecord:
        self.projects[record.id] = record
        return record

    def soft_delete_project(self, *, tenant_id: str, project_id: str) -> bool:
        record = self.projects.get(project_id)
        if record is None or record.tenant_id != tenant_id:
            return False
        if record.deleted_at is not None:
            return True
        self.projects[project_id] = record.model_copy(
            update={"deleted_at": _now(), "updated_at": _now()}
        )
        return True

    # -- memberships ---------------------------------------------------

    def insert_membership(
        self, record: ProjectMembershipRecord
    ) -> ProjectMembershipRecord:
        key = (record.project_id, record.user_id)
        self.memberships[key] = record
        return record

    def get_membership(
        self, *, tenant_id: str, project_id: str, user_id: str
    ) -> ProjectMembershipRecord | None:
        record = self.memberships.get((project_id, user_id))
        if record is None or record.tenant_id != tenant_id:
            return None
        return record

    def list_memberships_for_project(
        self,
        *,
        tenant_id: str,
        project_id: str,
        cursor: str | None = None,
        limit: int = 50,
    ) -> tuple[tuple[ProjectMembershipRecord, ...], str | None]:
        rows = [
            m
            for m in self.memberships.values()
            if m.tenant_id == tenant_id and m.project_id == project_id
        ]
        rows.sort(key=lambda m: (m.added_at, m.user_id))
        start = _decode_cursor(cursor)
        page = rows[start : start + limit]
        next_cursor = str(start + limit) if start + limit < len(rows) else None
        return tuple(page), next_cursor

    def list_memberships_for_user(
        self, *, tenant_id: str, user_id: str
    ) -> tuple[ProjectMembershipRecord, ...]:
        return tuple(
            m
            for m in self.memberships.values()
            if m.tenant_id == tenant_id and m.user_id == user_id
        )

    def update_membership_role(
        self,
        *,
        tenant_id: str,
        project_id: str,
        user_id: str,
        role: str,
    ) -> ProjectMembershipRecord | None:
        existing = self.get_membership(
            tenant_id=tenant_id, project_id=project_id, user_id=user_id
        )
        if existing is None:
            return None
        updated = existing.model_copy(update={"role": role})
        self.memberships[(project_id, user_id)] = updated
        return updated

    def delete_membership(
        self, *, tenant_id: str, project_id: str, user_id: str
    ) -> bool:
        record = self.memberships.get((project_id, user_id))
        if record is None or record.tenant_id != tenant_id:
            return False
        self.memberships.pop((project_id, user_id), None)
        return True

    # -- stars ---------------------------------------------------------

    def upsert_star(self, record: ProjectStarRecord) -> ProjectStarRecord:
        key = (record.tenant_id, record.user_id, record.project_id)
        existing = self.stars.get(key)
        if existing is not None:
            return existing
        self.stars[key] = record
        return record

    def delete_star(self, *, tenant_id: str, project_id: str, user_id: str) -> bool:
        return self.stars.pop((tenant_id, user_id, project_id), None) is not None

    def is_starred(self, *, tenant_id: str, project_id: str, user_id: str) -> bool:
        return (tenant_id, user_id, project_id) in self.stars

    # -- activity ------------------------------------------------------

    def append_activity(
        self, record: ProjectActivityRecord
    ) -> ProjectActivityRecord | None:
        # Idempotency: UNIQUE (tenant_id, audit_id). If a producer
        # replays the same audit event the projector must not double-
        # project the row.
        key = (record.tenant_id, record.audit_id)
        if key in self._activity_audit_keys:
            return None
        self._activity_audit_keys.add(key)
        self.activity.append(record)
        return record

    def list_activity(
        self,
        *,
        tenant_id: str,
        project_id: str,
        kinds: tuple[str, ...] | None = None,
        cursor: str | None = None,
        limit: int = 50,
    ) -> tuple[tuple[ProjectActivityRecord, ...], str | None]:
        rows = [
            a
            for a in self.activity
            if a.tenant_id == tenant_id and a.project_id == project_id
        ]
        if kinds is not None:
            rows = [a for a in rows if a.kind in kinds]
        rows.sort(key=lambda a: (a.occurred_at, a.id), reverse=True)
        start = _decode_cursor(cursor)
        page = rows[start : start + limit]
        next_cursor = str(start + limit) if start + limit < len(rows) else None
        return tuple(page), next_cursor

    # -- counts --------------------------------------------------------

    def get_counts(
        self, *, tenant_id: str, project_id: str
    ) -> ProjectActivityCounts | None:
        return self.counts.get((tenant_id, project_id))

    def upsert_counts(self, record: ProjectActivityCounts) -> ProjectActivityCounts:
        self.counts[(record.tenant_id, record.project_id)] = record
        return record

    # -- audit ---------------------------------------------------------

    def append_audit(self, record: ProjectAuditRecord) -> ProjectAuditRecord:
        self.audits.append(record)
        return record

    def list_audit_for_project(
        self, *, tenant_id: str, project_id: str
    ) -> tuple[ProjectAuditRecord, ...]:
        return tuple(
            r
            for r in self.audits
            if r.tenant_id == tenant_id and r.target_id == project_id
        )


# ---------------------------------------------------------------------------
# Sort + cursor helpers
# ---------------------------------------------------------------------------


_VALID_SORTS: frozenset[str] = frozenset(
    {
        "updated_at:desc",
        "updated_at:asc",
        "name:asc",
        "name:desc",
        "created_at:desc",
        "created_at:asc",
        "last_activity_at:desc",
    }
)


def _sort_descending(sort: str) -> bool:
    return sort.endswith(":desc")


def _sort_key(sort: str):
    field_name, _ = sort.split(":", 1) if ":" in sort else (sort, "desc")
    if field_name == "name":
        return lambda r: (r.name.lower(), r.id)
    if field_name == "created_at":
        return lambda r: (r.created_at, r.id)
    if field_name == "last_activity_at":
        # NULLs LAST: treat None as the epoch so DESC sort moves them to
        # the bottom — matches the SQL index spec in projects-prd §5.2.
        return lambda r: (
            r.last_activity_at or datetime.min.replace(tzinfo=timezone.utc),
            r.id,
        )
    # Default: updated_at.
    return lambda r: (r.updated_at, r.id)


def _decode_cursor(cursor: str | None) -> int:
    if cursor is None:
        return 0
    try:
        return max(0, int(cursor))
    except ValueError:
        return 0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def empty_counts(*, tenant_id: str, project_id: str) -> ProjectActivityCounts:
    """Build a zeroed counts record — used for fresh projects that the
    projector has not yet touched."""

    return ProjectActivityCounts(tenant_id=tenant_id, project_id=project_id)


def iter_audit_rows_for_bulk(
    records: Iterable[ProjectAuditRecord],
    *,
    correlation_id: str,
) -> Iterator[ProjectAuditRecord]:
    """Stamp ``correlation_id`` on every audit row in a bulk write."""

    for record in records:
        yield record.model_copy(update={"correlation_id": correlation_id})


__all__ = [
    "InMemoryProjectsStore",
    "ProjectActivityCounts",
    "ProjectActivityRecord",
    "ProjectAuditRecord",
    "ProjectMembershipRecord",
    "ProjectRecord",
    "ProjectStarRecord",
    "ProjectsStore",
    "empty_counts",
    "iter_audit_rows_for_bulk",
]
