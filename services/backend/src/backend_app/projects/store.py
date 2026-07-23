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

import contextvars
import json
from collections.abc import Iterable
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterator, Protocol
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from copilot_audit_chain import AuditChainSigner

# Reuse the backend's canonical connection-hardening primitives (same
# deployable component) so the projects store stamps RLS session vars and
# serialises audit-chain inserts through exactly one implementation:
#   * ``_apply_rls_session_vars`` — SET LOCAL app.current_org_id / app.role
#   * ``_take_audit_chain_lock``  — pg_advisory_xact_lock per (table, org)
# See ``backend_app/store.py`` (PostgresMcpStore.append_audit / _connect).
from backend_app.store import _apply_rls_session_vars, _take_audit_chain_lock


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
_EMPTY_COUNTS: dict[str, int | None] = {
    "chats": None,
    "files": 0,
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
    # Three semantics:
    # * ``None`` — no project default; chats/routines inherit owner's
    #   workspace default at create time (Phase 1 behavior).
    # * ``[]`` — explicit deny: no connectors allowed in this project;
    #   materialize step seeds an empty connector map and stops.
    # * ``["salesforce", ...]`` — allowlist of ConnectorSlug values;
    #   each slug becomes an active entry on the new chat/routine at
    #   create time.
    #
    # JSONB on disk (forward-compatible with a richer {slug, scope}
    # shape later). List shape matches api-types/projects.ts wire
    # contract; consumers (P6.5-A2 routine inheritance) coerce as
    # needed for in-memory dedup.
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
    """Per-project rollup — the COMPUTED wire shape (PRD-07).

    No longer a stored row: the projects service composes this on read from
    each destination's grouped ``count_by_project`` (the per-project rollup
    counter table was dropped in migration 0047). ``chats`` is ``None`` here
    because its rows live in ``ai-backend``; the facade fills it from a batched
    counts call
    (backend counting cross-service rows would invert the dependency direction).
    ``files`` (kind=file) is the design's "N files" — distinct from
    ``library_items`` (file + page + dataset).
    """

    model_config = ConfigDict(extra="forbid")

    tenant_id: str
    project_id: str
    # ``None`` = "backend is not entitled to an opinion" (the facade fills it).
    # Defaults to ``None`` so backend never emits a fabricated 0.
    chats: int | None = None
    files: int = 0
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
    def transaction(  # pragma: no cover
        self, *, org_id: str | None = None
    ) -> Iterator[None]: ...

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

    def count_members_by_project(
        self, *, tenant_id: str, project_ids: tuple[str, ...]
    ) -> dict[str, int]: ...

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
    #
    # PRD-07 — there is no counts read/write on the store anymore. The
    # per-project rollup counter table was dropped (migration 0047); per-project
    # rollups are computed on read by the service from each destination's grouped
    # ``count_by_project``. The ``ProjectActivityCounts`` model stays as the
    # computed wire shape only.

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
    audits: list[ProjectAuditRecord] = field(default_factory=list)

    @contextmanager
    def transaction(self, *, org_id: str | None = None) -> Iterator[None]:
        # Single-process in-memory; no actual transactional boundary.
        # The service layer still calls ``transaction()`` so the same
        # composition works against the Postgres adapter without a
        # branch. ``org_id`` is accepted (and ignored) for signature
        # parity with the Postgres adapter, which stamps it as an RLS
        # session var on the shared write connection.
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

    def count_members_by_project(
        self, *, tenant_id: str, project_ids: tuple[str, ...]
    ) -> dict[str, int]:
        """Group membership rows by project (PRD-07) — batched, no N+1."""
        wanted = set(project_ids)
        result: dict[str, int] = {}
        for m in self.memberships.values():
            if m.tenant_id != tenant_id or m.project_id not in wanted:
                continue
            result[m.project_id] = result.get(m.project_id, 0) + 1
        return result

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
    # PRD-07 — no stored counts; rollups are computed on read by the service.

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
# Postgres adapter (PRD-H FR-H.3)
# ---------------------------------------------------------------------------
#
# Durable projects store implementing the same :class:`ProjectsStore`
# Protocol as :class:`InMemoryProjectsStore`, against the DDL in
# ``schema.sql``. Selected in ``desktop_app.py`` alongside the other
# Postgres adapters (the in-memory store stays the default for tests/dev).
#
# The :class:`ProjectsStore` Protocol methods take no ``conn`` argument
# (the service composes ``with store.transaction(): store.write(...)``
# without threading a connection). To keep the composed writes atomic on
# ONE connection while staying safe under concurrent requests, the active
# transaction connection is held in a :class:`contextvars.ContextVar` —
# each request's execution context (sync handlers run in their own
# thread-copied context via Starlette's threadpool) reads back the same
# connection its ``transaction()`` opened, and unrelated requests never
# share it. Outside a ``transaction()`` block each method checks out its
# own short-lived pooled connection.
#
# RLS session-var wiring and audit-chain signing of ``project_audit_events``
# are now live in this adapter (PRD-H.3 hardening):
#   * ``transaction(org_id=...)`` and the fresh-connection path in
#     ``_cursor(tenant_id=...)`` stamp ``app.current_org_id`` / ``app.role``
#     so the tenant-isolation policies in ``schema.sql`` back the
#     application-side ``WHERE tenant_id = %s`` scoping.
#   * ``append_audit`` reads the per-tenant chain head under an advisory
#     lock, then signs (seq / prev_hash / signature / key_version) through
#     the shared :class:`AuditChainSigner` — the same path
#     ``PostgresMcpStore.append_audit`` uses for ``mcp_audit_events``.
# Live-Postgres SQL execution stays DEFERRED (no live DB in this
# workstream): the supervised-boot smoke exercises the real RLS + chain.

_ACTIVE_PROJECTS_CONN: contextvars.ContextVar[Any] = contextvars.ContextVar(
    "projects_active_conn", default=None
)


class PostgresProjectsStore:
    """psycopg-backed projects store. Uses the shared backend pool.

    ``pool`` is duck-typed (tests pass a fake) but in production it is the
    shared ``PostgresConnectionPool``. Every query is scoped to
    ``tenant_id`` in the application-side ``WHERE`` clause (the RLS policy
    in ``schema.sql`` is the second wall).
    """

    def __init__(self, pool: Any) -> None:
        self._pool = pool

    # -- connection / transaction plumbing ----------------------------

    @contextmanager
    def transaction(self, *, org_id: str | None = None) -> Iterator[Any]:
        """Open a transaction and publish its connection to the context.

        Composed store writes inside the ``with`` block run on this one
        connection so a partial failure rolls back every row together.

        ``org_id`` (the caller's ``tenant_id``) is stamped as the
        ``app.current_org_id`` RLS session var on the shared connection so
        every composed write inside the block is backed by the
        tenant-isolation policies in ``schema.sql`` — matching
        :meth:`PostgresMcpStore.transaction`. ``app.role='api'`` is always
        stamped. Defaults to ``None`` (no stamp) for signature parity with
        the in-memory adapter and callers not yet passing a tenant.
        """

        existing = _ACTIVE_PROJECTS_CONN.get()
        if existing is not None:
            # Re-entrant: already inside a transaction on this context.
            yield existing
            return
        with self._pool.connection() as conn:
            token = _ACTIVE_PROJECTS_CONN.set(conn)
            try:
                with conn.transaction():
                    # Stamp inside the transaction so the SET LOCAL scope
                    # matches the composed writes' atomic unit.
                    _apply_rls_session_vars(conn, org_id=org_id, role="api")
                    yield conn
            finally:
                _ACTIVE_PROJECTS_CONN.reset(token)

    @contextmanager
    def _cursor(self, *, tenant_id: str | None = None) -> Iterator[Any]:
        """Yield a cursor on the active transaction conn, or a fresh one.

        When a fresh (non-transaction) connection is checked out, stamp the
        RLS session vars from ``tenant_id`` so standalone reads/writes are
        tenant-scoped by the policy as well as the ``WHERE`` clause. Inside
        a transaction the connection was already stamped by
        :meth:`transaction`, so we don't restamp.
        """

        active = _ACTIVE_PROJECTS_CONN.get()
        if active is not None:
            with active.cursor() as cur:
                yield cur
            return
        with self._pool.connection() as owned:
            _apply_rls_session_vars(owned, org_id=tenant_id, role="api")
            with owned.cursor() as cur:
                yield cur

    # -- projects ------------------------------------------------------

    def insert_project(self, record: ProjectRecord) -> ProjectRecord:
        with self._cursor(tenant_id=record.tenant_id) as cur:
            cur.execute(
                """
                INSERT INTO projects (
                    id, tenant_id, owner_user_id, name, description,
                    icon_emoji, color_hue, status, archived_at,
                    last_activity_at, created_at, updated_at, deleted_at,
                    default_connector_allowlist
                ) VALUES (
                    %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb
                )
                """,
                (
                    record.id,
                    record.tenant_id,
                    record.owner_user_id,
                    record.name,
                    record.description,
                    record.icon_emoji,
                    record.color_hue,
                    record.status,
                    record.archived_at,
                    record.last_activity_at,
                    record.created_at,
                    record.updated_at,
                    record.deleted_at,
                    _jsonb(record.default_connector_allowlist),
                ),
            )
        return record

    def get_project(
        self, *, tenant_id: str, project_id: str, include_deleted: bool = False
    ) -> ProjectRecord | None:
        clause = "" if include_deleted else " AND deleted_at IS NULL"
        with self._cursor(tenant_id=tenant_id) as cur:
            cur.execute(
                f"SELECT * FROM projects WHERE tenant_id = %s AND id = %s{clause}",
                (tenant_id, project_id),
            )
            row = cur.fetchone()
        return _row_to_project(row) if row else None

    def get_project_by_name(self, *, tenant_id: str, name: str) -> ProjectRecord | None:
        with self._cursor(tenant_id=tenant_id) as cur:
            cur.execute(
                """
                SELECT * FROM projects
                WHERE tenant_id = %s AND lower(name) = lower(%s)
                  AND deleted_at IS NULL
                LIMIT 1
                """,
                (tenant_id, name.strip()),
            )
            row = cur.fetchone()
        return _row_to_project(row) if row else None

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
        where: list[str] = ["p.tenant_id = %s"]
        params: list[Any] = [tenant_id]
        if not include_deleted:
            where.append("p.deleted_at IS NULL")
        if owner_user_id is not None:
            where.append("p.owner_user_id = %s")
            params.append(owner_user_id)
        if member_user_id is not None:
            where.append(
                "EXISTS (SELECT 1 FROM project_memberships m "
                "WHERE m.project_id = p.id AND m.tenant_id = p.tenant_id "
                "AND m.user_id = %s)"
            )
            params.append(member_user_id)
        if starred_by_user_id is not None:
            where.append(
                "EXISTS (SELECT 1 FROM project_stars s "
                "WHERE s.project_id = p.id AND s.tenant_id = p.tenant_id "
                "AND s.user_id = %s)"
            )
            params.append(starred_by_user_id)
        if statuses is not None:
            where.append("p.status = ANY(%s)")
            params.append(list(statuses))
        if q and q.strip():
            where.append("(p.name || ' ' || p.description) ILIKE %s")
            params.append(f"%{q.strip()}%")

        order_by = _sql_order_by(sort)
        offset = _decode_cursor(cursor)
        # Fetch one extra row to compute the next cursor without COUNT(*).
        sql = (
            "SELECT p.* FROM projects p WHERE "
            + " AND ".join(where)
            + f" ORDER BY {order_by} OFFSET %s LIMIT %s"
        )
        params.extend([offset, limit + 1])
        with self._cursor(tenant_id=tenant_id) as cur:
            cur.execute(sql, tuple(params))
            rows = cur.fetchall()
        has_more = len(rows) > limit
        page = tuple(_row_to_project(r) for r in rows[:limit])
        next_cursor = str(offset + limit) if has_more else None
        return page, next_cursor

    def update_project(self, record: ProjectRecord) -> ProjectRecord:
        with self._cursor(tenant_id=record.tenant_id) as cur:
            cur.execute(
                """
                UPDATE projects SET
                    name = %s, description = %s, icon_emoji = %s,
                    color_hue = %s, status = %s, archived_at = %s,
                    last_activity_at = %s, updated_at = %s, deleted_at = %s,
                    default_connector_allowlist = %s::jsonb
                WHERE tenant_id = %s AND id = %s
                """,
                (
                    record.name,
                    record.description,
                    record.icon_emoji,
                    record.color_hue,
                    record.status,
                    record.archived_at,
                    record.last_activity_at,
                    record.updated_at,
                    record.deleted_at,
                    _jsonb(record.default_connector_allowlist),
                    record.tenant_id,
                    record.id,
                ),
            )
        return record

    def soft_delete_project(self, *, tenant_id: str, project_id: str) -> bool:
        now = _now()
        with self._cursor(tenant_id=tenant_id) as cur:
            cur.execute(
                """
                UPDATE projects SET deleted_at = %s, updated_at = %s
                WHERE tenant_id = %s AND id = %s
                """,
                (now, now, tenant_id, project_id),
            )
            return bool(cur.rowcount)

    # -- memberships ---------------------------------------------------

    def insert_membership(
        self, record: ProjectMembershipRecord
    ) -> ProjectMembershipRecord:
        with self._cursor(tenant_id=record.tenant_id) as cur:
            cur.execute(
                """
                INSERT INTO project_memberships (
                    project_id, user_id, tenant_id, role, added_at, added_by
                ) VALUES (%s,%s,%s,%s,%s,%s)
                ON CONFLICT (project_id, user_id) DO UPDATE SET
                    role = EXCLUDED.role, added_by = EXCLUDED.added_by
                """,
                (
                    record.project_id,
                    record.user_id,
                    record.tenant_id,
                    record.role,
                    record.added_at,
                    record.added_by,
                ),
            )
        return record

    def get_membership(
        self, *, tenant_id: str, project_id: str, user_id: str
    ) -> ProjectMembershipRecord | None:
        with self._cursor(tenant_id=tenant_id) as cur:
            cur.execute(
                """
                SELECT * FROM project_memberships
                WHERE tenant_id = %s AND project_id = %s AND user_id = %s
                """,
                (tenant_id, project_id, user_id),
            )
            row = cur.fetchone()
        return _row_to_membership(row) if row else None

    def list_memberships_for_project(
        self,
        *,
        tenant_id: str,
        project_id: str,
        cursor: str | None = None,
        limit: int = 50,
    ) -> tuple[tuple[ProjectMembershipRecord, ...], str | None]:
        offset = _decode_cursor(cursor)
        with self._cursor(tenant_id=tenant_id) as cur:
            cur.execute(
                """
                SELECT * FROM project_memberships
                WHERE tenant_id = %s AND project_id = %s
                ORDER BY added_at ASC, user_id ASC
                OFFSET %s LIMIT %s
                """,
                (tenant_id, project_id, offset, limit + 1),
            )
            rows = cur.fetchall()
        has_more = len(rows) > limit
        page = tuple(_row_to_membership(r) for r in rows[:limit])
        next_cursor = str(offset + limit) if has_more else None
        return page, next_cursor

    def list_memberships_for_user(
        self, *, tenant_id: str, user_id: str
    ) -> tuple[ProjectMembershipRecord, ...]:
        with self._cursor(tenant_id=tenant_id) as cur:
            cur.execute(
                """
                SELECT * FROM project_memberships
                WHERE tenant_id = %s AND user_id = %s
                """,
                (tenant_id, user_id),
            )
            rows = cur.fetchall()
        return tuple(_row_to_membership(r) for r in rows)

    def count_members_by_project(
        self, *, tenant_id: str, project_ids: tuple[str, ...]
    ) -> dict[str, int]:
        """Group membership rows by project (PRD-07) — one batched scan, no N+1."""
        if not project_ids:
            return {}
        with self._cursor(tenant_id=tenant_id) as cur:
            cur.execute(
                """
                SELECT project_id, COUNT(*) AS count
                  FROM project_memberships
                 WHERE tenant_id = %s AND project_id = ANY(%s)
                 GROUP BY project_id
                """,
                (tenant_id, list(project_ids)),
            )
            rows = cur.fetchall()
        return {str(r["project_id"]): int(r["count"]) for r in rows}

    def update_membership_role(
        self, *, tenant_id: str, project_id: str, user_id: str, role: str
    ) -> ProjectMembershipRecord | None:
        with self._cursor(tenant_id=tenant_id) as cur:
            cur.execute(
                """
                UPDATE project_memberships SET role = %s
                WHERE tenant_id = %s AND project_id = %s AND user_id = %s
                RETURNING *
                """,
                (role, tenant_id, project_id, user_id),
            )
            row = cur.fetchone()
        return _row_to_membership(row) if row else None

    def delete_membership(
        self, *, tenant_id: str, project_id: str, user_id: str
    ) -> bool:
        with self._cursor(tenant_id=tenant_id) as cur:
            cur.execute(
                """
                DELETE FROM project_memberships
                WHERE tenant_id = %s AND project_id = %s AND user_id = %s
                """,
                (tenant_id, project_id, user_id),
            )
            return bool(cur.rowcount)

    # -- stars ---------------------------------------------------------

    def upsert_star(self, record: ProjectStarRecord) -> ProjectStarRecord:
        with self._cursor(tenant_id=record.tenant_id) as cur:
            cur.execute(
                """
                INSERT INTO project_stars (tenant_id, user_id, project_id, created_at)
                VALUES (%s,%s,%s,%s)
                ON CONFLICT (tenant_id, user_id, project_id) DO NOTHING
                """,
                (
                    record.tenant_id,
                    record.user_id,
                    record.project_id,
                    record.created_at,
                ),
            )
        return record

    def delete_star(self, *, tenant_id: str, project_id: str, user_id: str) -> bool:
        with self._cursor(tenant_id=tenant_id) as cur:
            cur.execute(
                """
                DELETE FROM project_stars
                WHERE tenant_id = %s AND user_id = %s AND project_id = %s
                """,
                (tenant_id, user_id, project_id),
            )
            return bool(cur.rowcount)

    def is_starred(self, *, tenant_id: str, project_id: str, user_id: str) -> bool:
        with self._cursor(tenant_id=tenant_id) as cur:
            cur.execute(
                """
                SELECT 1 FROM project_stars
                WHERE tenant_id = %s AND user_id = %s AND project_id = %s
                """,
                (tenant_id, user_id, project_id),
            )
            return cur.fetchone() is not None

    # -- activity ------------------------------------------------------

    def append_activity(
        self, record: ProjectActivityRecord
    ) -> ProjectActivityRecord | None:
        with self._cursor(tenant_id=record.tenant_id) as cur:
            cur.execute(
                """
                INSERT INTO project_activity (
                    id, tenant_id, project_id, audit_id, actor_user_id,
                    actor_display_name, action, kind, ref_kind, ref_id,
                    preview, occurred_at
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (tenant_id, audit_id) DO NOTHING
                RETURNING id
                """,
                (
                    record.id,
                    record.tenant_id,
                    record.project_id,
                    record.audit_id,
                    record.actor_user_id,
                    record.actor_display_name,
                    record.action,
                    record.kind,
                    record.ref_kind,
                    record.ref_id,
                    record.preview,
                    record.occurred_at,
                ),
            )
            inserted = cur.fetchone()
        # Idempotency: a replayed (tenant, audit_id) inserts nothing.
        return record if inserted else None

    def list_activity(
        self,
        *,
        tenant_id: str,
        project_id: str,
        kinds: tuple[str, ...] | None = None,
        cursor: str | None = None,
        limit: int = 50,
    ) -> tuple[tuple[ProjectActivityRecord, ...], str | None]:
        where = ["tenant_id = %s", "project_id = %s"]
        params: list[Any] = [tenant_id, project_id]
        if kinds is not None:
            where.append("kind = ANY(%s)")
            params.append(list(kinds))
        offset = _decode_cursor(cursor)
        params.extend([offset, limit + 1])
        with self._cursor(tenant_id=tenant_id) as cur:
            cur.execute(
                "SELECT * FROM project_activity WHERE "
                + " AND ".join(where)
                + " ORDER BY occurred_at DESC, id DESC OFFSET %s LIMIT %s",
                tuple(params),
            )
            rows = cur.fetchall()
        has_more = len(rows) > limit
        page = tuple(_row_to_activity(r) for r in rows[:limit])
        next_cursor = str(offset + limit) if has_more else None
        return page, next_cursor

    # -- counts --------------------------------------------------------
    # PRD-07 — no stored counts (migration 0047 dropped the table); the service
    # computes per-project rollups on read from each destination's grouped
    # ``count_by_project``.

    # -- audit ---------------------------------------------------------

    def append_audit(self, record: ProjectAuditRecord) -> ProjectAuditRecord:
        # Per-tenant HMAC hash chain, signed through the shared
        # :class:`AuditChainSigner` — same path as
        # ``PostgresMcpStore.append_audit`` (``mcp_audit_events``). We take a
        # per-(table, tenant) advisory xact lock, read the chain head, then
        # sign seq/prev_hash/signature/key_version over the canonical
        # business payload and insert. The chain columns are DB-only; the
        # ``ProjectAuditRecord`` model (``extra='forbid'``) does not carry
        # them, so the returned record is unchanged.
        signer = AuditChainSigner.from_env(environment_env_var="BACKEND_ENVIRONMENT")
        payload = _project_audit_payload(record)
        with self._cursor(tenant_id=record.tenant_id) as cur:
            _take_audit_chain_lock(
                cur, table="project_audit_events", org_id=record.tenant_id
            )
            cur.execute(
                """
                SELECT seq, signature
                  FROM project_audit_events
                 WHERE tenant_id = %s
                 ORDER BY seq DESC NULLS LAST
                 LIMIT 1
                """,
                (record.tenant_id,),
            )
            head = cur.fetchone()
            last_seq, prev_hash = _chain_head(head)
            seq = last_seq + 1
            sig = signer.sign(prev_hash=prev_hash, payload=payload)
            cur.execute(
                """
                INSERT INTO project_audit_events (
                    audit_id, tenant_id, actor_user_id, action, target_kind,
                    target_id, before_state, after_state, context,
                    correlation_id, ts,
                    seq, prev_hash, signature, key_version
                ) VALUES (
                    %s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s::jsonb,%s,%s,
                    %s,%s,%s,%s
                )
                """,
                (
                    record.audit_id,
                    record.tenant_id,
                    record.actor_user_id,
                    record.action,
                    record.target_kind,
                    record.target_id,
                    _jsonb(record.before_state),
                    _jsonb(record.after_state),
                    _jsonb(record.context),
                    record.correlation_id,
                    record.ts,
                    seq,
                    sig.prev_hash,
                    sig.signature,
                    sig.key_version,
                ),
            )
        return record

    def list_audit_for_project(
        self, *, tenant_id: str, project_id: str
    ) -> tuple[ProjectAuditRecord, ...]:
        with self._cursor(tenant_id=tenant_id) as cur:
            cur.execute(
                """
                SELECT * FROM project_audit_events
                WHERE tenant_id = %s AND target_id = %s
                ORDER BY ts ASC
                """,
                (tenant_id, project_id),
            )
            rows = cur.fetchall()
        return tuple(_row_to_audit(r) for r in rows)


# ---------------------------------------------------------------------------
# Row mapping + Postgres helpers
# ---------------------------------------------------------------------------


def _jsonb(value: Any) -> str | None:
    """Serialise a JSON-able value for a ``%s::jsonb`` placeholder.

    ``None`` stays ``NULL`` (distinct from a JSON ``null``); everything
    else is ``json.dumps``-ed so psycopg binds a text param the cast
    turns into JSONB.
    """

    if value is None:
        return None
    return json.dumps(value)


def _coerce_json(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, (bytes, bytearray)):
        return json.loads(bytes(value).decode("utf-8"))
    if isinstance(value, str):
        return json.loads(value)
    return value


def _row_to_project(row: dict[str, Any]) -> ProjectRecord:
    data = dict(row)
    data["default_connector_allowlist"] = _coerce_json(
        data.get("default_connector_allowlist")
    )
    return ProjectRecord.model_validate(data)


def _row_to_membership(row: dict[str, Any]) -> ProjectMembershipRecord:
    return ProjectMembershipRecord.model_validate(dict(row))


def _row_to_activity(row: dict[str, Any]) -> ProjectActivityRecord:
    return ProjectActivityRecord.model_validate(dict(row))


def _project_audit_payload(record: ProjectAuditRecord) -> dict[str, Any]:
    """Canonical business payload signed into the audit hash chain.

    Mirrors the payload shape ``PostgresMcpStore.append_audit`` builds for
    ``mcp_audit_events`` — the exact fields that must not change without
    breaking verification. Chain columns (seq/prev_hash/signature/
    key_version) are intentionally excluded; they live in the envelope the
    :class:`AuditChainSigner` wraps around this payload.
    """

    return {
        "audit_id": record.audit_id,
        "tenant_id": record.tenant_id,
        "actor_user_id": record.actor_user_id,
        "action": record.action,
        "target_kind": record.target_kind,
        "target_id": record.target_id,
        "before_state": record.before_state,
        "after_state": record.after_state,
        "context": record.context,
        "correlation_id": record.correlation_id,
        "ts": record.ts,
    }


def _chain_head(head: Any) -> tuple[int, bytes | None]:
    """Read ``(last_seq, prev_hash)`` from the chain-head row (or empty chain).

    ``head`` is the ``SELECT seq, signature ... ORDER BY seq DESC`` row (a
    mapping) or ``None`` when the tenant's chain is empty. Matches the head
    decoding in ``PostgresMcpStore.append_audit``.
    """

    if not head:
        return 0, None
    last_seq = int(head["seq"]) if head.get("seq") is not None else 0
    prev_hash = bytes(head["signature"]) if head.get("signature") is not None else None
    return last_seq, prev_hash


def _row_to_audit(row: dict[str, Any]) -> ProjectAuditRecord:
    data = dict(row)
    for key in ("before_state", "after_state", "context"):
        if data.get(key) is not None:
            data[key] = _coerce_json(data[key])
    # Drop chain columns the Pydantic model doesn't declare.
    for key in ("seq", "prev_hash", "signature", "key_version"):
        data.pop(key, None)
    return ProjectAuditRecord.model_validate(data)


def _sql_order_by(sort: str) -> str:
    """Map the wire ``field:dir`` sort to a safe ``ORDER BY`` clause.

    Only whitelisted sorts (``_VALID_SORTS``) are honoured; anything else
    falls back to ``updated_at DESC`` — the column names are never
    interpolated from raw user input.
    """

    if sort not in _VALID_SORTS:
        sort = "updated_at:desc"
    field_name, direction = sort.split(":", 1)
    direction_sql = "DESC" if direction == "desc" else "ASC"
    if field_name == "last_activity_at":
        # NULLs LAST on DESC to match the in-memory epoch-substitution.
        nulls = "NULLS LAST" if direction_sql == "DESC" else "NULLS FIRST"
        return f"last_activity_at {direction_sql} {nulls}, id {direction_sql}"
    if field_name == "name":
        return f"lower(name) {direction_sql}, id {direction_sql}"
    return f"{field_name} {direction_sql}, id {direction_sql}"


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
    "PostgresProjectsStore",
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
