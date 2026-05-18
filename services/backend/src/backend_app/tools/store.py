"""Tools store — adapter contract + in-memory implementation (Phase 10 P10-A2).

Storage shape mirrors :mod:`backend_app.tools.schema.sql`. The in-memory
adapter is the dev / test default; the Postgres adapter (deployment-
injected) implements the same Protocol against the ``tools`` /
``tool_audit_events`` tables.

Authorization is **not** enforced here. The service layer
(:class:`ToolsService`) composes the store with the canonical
:class:`ProjectMembershipPort` to decide read / write authority; the
store exposes raw queries scoped to ``tenant_id``.

Soft-delete (``deleted_at``) keeps rows visible to compliance reads
(``include_deleted=True``) but invisible to the public list / get paths.
The cleanup job (out of scope for P10-A2) hard-deletes after the 90-day
retention window (tools-prd §5.4).

Usage projection is computed at read time by the service layer over
``runtime_tool_invocations`` + ``runtime_model_call_usage`` (cross-audit
§5.5 TU-1 — there is NO parallel ``tool_usage_daily`` table here).
"""

from __future__ import annotations

from collections.abc import Iterable
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterator, Literal, Protocol
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _tool_id() -> str:
    return f"tool_{uuid4().hex}"


def _audit_id() -> str:
    return f"audtool_{uuid4().hex}"


def _invocation_id() -> str:
    return f"toolinv_{uuid4().hex}"


# ---------------------------------------------------------------------------
# Enum literals (mirror packages/api-types/src/tools.ts §3.1)
# ---------------------------------------------------------------------------


ToolKindLiteral = Literal["mcp", "openapi", "builtin", "code", "skill"]
ToolScopeLiteral = Literal["read", "write", "both"]
ToolStatusLiteral = Literal["enabled", "disabled", "error", "pending_review"]
ToolTransportKindLiteral = Literal["mcp", "http", "in_process", "sandbox"]
ToolInvocationStatusLiteral = Literal["ok", "error"]
ToolInvocationCallerKindLiteral = Literal["agent", "routine", "chat"]
ToolInvocationErrorKindLiteral = Literal[
    "auth_required",
    "scope_missing",
    "schema_invalid",
    "timeout",
    "sandbox_crash",
    "transport_error",
    "unknown",
]


# ---------------------------------------------------------------------------
# Records (Pydantic; shared with the Postgres + in-memory adapters)
# ---------------------------------------------------------------------------


class ToolRecord(BaseModel):
    """One row in the ``tools`` table.

    JSONB columns are accepted as ``dict[str, Any]`` here without being
    recast into typed sub-models so the Postgres adapter can round-trip
    them without a redundant marshalling pass. The service layer
    validates shape at the route boundary.
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=_tool_id)
    tenant_id: str
    name: str
    description: str = ""
    kind: ToolKindLiteral
    scope: ToolScopeLiteral
    status: ToolStatusLiteral = "enabled"
    status_reason: str | None = None
    args_schema: dict[str, Any] = Field(default_factory=dict)
    returns_schema: dict[str, Any] = Field(default_factory=dict)
    transport: dict[str, Any]
    owner_user_id: str
    project_id: str | None = None
    skill_page_ref: dict[str, Any] | None = None
    code_ref: dict[str, Any] | None = None
    tags: list[str] = Field(default_factory=list)
    consecutive_error_count: int = 0
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)
    deleted_at: datetime | None = None


class ToolAuditRecord(BaseModel):
    """Append-only audit row written on every state change.

    Same shape as ``ProjectAuditRecord`` / ``AgentAuditRecord`` so the
    audit-chain integration (``packages/audit-chain``) signs + chains
    rows in production via the same path. The in-memory adapter appends
    raw rows for tests.
    """

    model_config = ConfigDict(extra="forbid")

    audit_id: str = Field(default_factory=_audit_id)
    tenant_id: str
    actor_user_id: str
    action: str
    target_kind: str = "tool"
    target_id: str
    before_state: dict[str, Any] | None = None
    after_state: dict[str, Any] | None = None
    context: dict[str, Any] | None = None  # cross-audit §1.4
    correlation_id: str | None = None
    ts: datetime = Field(default_factory=_now)


class ToolInvocationRecord(BaseModel):
    """One row in ``runtime_tool_invocations`` (the existing Phase 0 table).

    Tools destination reads this table; ai-backend writes it via
    ``POST /internal/v1/tools/{id}/invocations``. The wire shape mirrors
    ``ToolInvocation`` in ``api-types/src/tools.ts``.
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=_invocation_id)
    tool_id: str
    tenant_id: str
    run_id: str
    caller_kind: ToolInvocationCallerKindLiteral
    caller_ref: dict[str, Any]  # ItemRef shape, JSONB on disk
    args_summary: str = ""  # truncated to 240 chars at the service layer
    result_summary: str | None = None
    status: ToolInvocationStatusLiteral
    error_kind: ToolInvocationErrorKindLiteral | None = None
    started_at: datetime = Field(default_factory=_now)
    ended_at: datetime = Field(default_factory=_now)
    latency_ms: int = 0


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


class ToolsStore(Protocol):
    """Adapter contract for the Postgres + in-memory tools stores."""

    @contextmanager
    def transaction(self) -> Iterator[None]: ...  # pragma: no cover

    # -- tools --------------------------------------------------------

    def insert_tool(self, record: ToolRecord) -> ToolRecord: ...

    def get_tool(
        self, *, tenant_id: str, tool_id: str, include_deleted: bool = False
    ) -> ToolRecord | None: ...

    def update_tool(self, record: ToolRecord) -> ToolRecord: ...

    def soft_delete_tool(self, *, tenant_id: str, tool_id: str) -> bool: ...

    def list_tools(
        self,
        *,
        tenant_id: str,
        kinds: tuple[str, ...] | None = None,
        scopes: tuple[str, ...] | None = None,
        statuses: tuple[str, ...] | None = None,
        project_ids: tuple[str, ...] | None = None,
        owner_user_ids: tuple[str, ...] | None = None,
        tags: tuple[str, ...] | None = None,
        q: str | None = None,
        # Visibility predicate inputs — the service pre-computes the
        # caller's project memberships so we make ONE membership lookup
        # per request instead of N. ``admin=True`` short-circuits the
        # gate (compliance read, audited at the route layer).
        visible_to_user_id: str | None = None,
        readable_project_ids: tuple[str, ...] = (),
        admin: bool = False,
        cursor: str | None = None,
        limit: int = 50,
        sort: str = "name",
        include_deleted: bool = False,
    ) -> tuple[tuple[ToolRecord, ...], str | None]: ...

    # -- invocations (read-only; ai-backend writes via the internal route)

    def list_invocations(
        self,
        *,
        tenant_id: str,
        tool_id: str,
        after_id: str | None = None,
        since: datetime | None = None,
        caller_kinds: tuple[str, ...] | None = None,
        statuses: tuple[str, ...] | None = None,
        limit: int = 50,
    ) -> tuple[tuple[ToolInvocationRecord, ...], str | None]: ...

    def insert_invocation(
        self, record: ToolInvocationRecord
    ) -> ToolInvocationRecord: ...

    # -- audit ---------------------------------------------------------

    def append_audit(self, record: ToolAuditRecord) -> ToolAuditRecord: ...

    def list_audit_for_tool(
        self, *, tenant_id: str, tool_id: str
    ) -> tuple[ToolAuditRecord, ...]: ...


# ---------------------------------------------------------------------------
# In-memory adapter
# ---------------------------------------------------------------------------


@dataclass
class InMemoryToolsStore:
    """Dict-backed adapter for tests + the default dev wiring.

    Mirrors the Postgres semantics: tenant scoping is the first filter on
    every query; soft-delete (``deleted_at``) hides rows from default
    list/get paths but leaves them visible to ``include_deleted=True``.
    Filter axes compose in-process (the Postgres adapter pushes the same
    predicates into SQL with the indexes from schema.sql).
    """

    tools: dict[str, ToolRecord] = field(default_factory=dict)
    audits: list[ToolAuditRecord] = field(default_factory=list)
    invocations: dict[str, ToolInvocationRecord] = field(default_factory=dict)

    @contextmanager
    def transaction(self) -> Iterator[None]:
        # Single-process in-memory; no actual transactional boundary.
        # Same shape as :class:`InMemoryProjectsStore` so the service
        # layer composes against both stores without branching.
        yield

    # -- tools --------------------------------------------------------

    def insert_tool(self, record: ToolRecord) -> ToolRecord:
        self.tools[record.id] = record
        return record

    def get_tool(
        self, *, tenant_id: str, tool_id: str, include_deleted: bool = False
    ) -> ToolRecord | None:
        record = self.tools.get(tool_id)
        if record is None or record.tenant_id != tenant_id:
            return None
        if record.deleted_at is not None and not include_deleted:
            return None
        return record

    def update_tool(self, record: ToolRecord) -> ToolRecord:
        self.tools[record.id] = record
        return record

    def soft_delete_tool(self, *, tenant_id: str, tool_id: str) -> bool:
        record = self.tools.get(tool_id)
        if record is None or record.tenant_id != tenant_id:
            return False
        if record.deleted_at is not None:
            return True
        self.tools[tool_id] = record.model_copy(
            update={"deleted_at": _now(), "updated_at": _now()}
        )
        return True

    def list_tools(
        self,
        *,
        tenant_id: str,
        kinds: tuple[str, ...] | None = None,
        scopes: tuple[str, ...] | None = None,
        statuses: tuple[str, ...] | None = None,
        project_ids: tuple[str, ...] | None = None,
        owner_user_ids: tuple[str, ...] | None = None,
        tags: tuple[str, ...] | None = None,
        q: str | None = None,
        visible_to_user_id: str | None = None,
        readable_project_ids: tuple[str, ...] = (),
        admin: bool = False,
        cursor: str | None = None,
        limit: int = 50,
        sort: str = "name",
        include_deleted: bool = False,
    ) -> tuple[tuple[ToolRecord, ...], str | None]:
        q_normalized = q.strip().lower() if q else None
        tags_set = set(tags) if tags else None
        owner_set = set(owner_user_ids) if owner_user_ids else None
        project_set = set(project_ids) if project_ids else None
        kind_set = set(kinds) if kinds else None
        scope_set = set(scopes) if scopes else None
        status_set = set(statuses) if statuses else None
        readable_set = set(readable_project_ids)

        candidates: list[ToolRecord] = []
        for record in self.tools.values():
            if record.tenant_id != tenant_id:
                continue
            if record.deleted_at is not None and not include_deleted:
                continue

            # Visibility gate (cross-audit §1.3). Project-scoped rows are
            # readable to caller IFF the caller is in readable_project_ids
            # OR is the owner. Unscoped rows are readable tenant-wide.
            if visible_to_user_id is not None and not admin:
                if record.project_id is None:
                    pass  # tenant-readable
                elif record.owner_user_id == visible_to_user_id:
                    pass  # owner always reads
                elif record.project_id in readable_set:
                    pass  # project member
                else:
                    continue

            # Public filter[*] axes (multi-value OR per cross-audit §1.5).
            if kind_set is not None and record.kind not in kind_set:
                continue
            if scope_set is not None and record.scope not in scope_set:
                continue
            if status_set is not None and record.status not in status_set:
                continue
            if owner_set is not None and record.owner_user_id not in owner_set:
                continue
            if project_set is not None and record.project_id not in project_set:
                continue
            if tags_set is not None and not tags_set.intersection(record.tags):
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

    # -- invocations --------------------------------------------------

    def insert_invocation(self, record: ToolInvocationRecord) -> ToolInvocationRecord:
        self.invocations[record.id] = record
        return record

    def list_invocations(
        self,
        *,
        tenant_id: str,
        tool_id: str,
        after_id: str | None = None,
        since: datetime | None = None,
        caller_kinds: tuple[str, ...] | None = None,
        statuses: tuple[str, ...] | None = None,
        limit: int = 50,
    ) -> tuple[tuple[ToolInvocationRecord, ...], str | None]:
        rows = [
            r
            for r in self.invocations.values()
            if r.tenant_id == tenant_id and r.tool_id == tool_id
        ]
        if since is not None:
            rows = [r for r in rows if r.started_at >= since]
        if caller_kinds is not None:
            kset = set(caller_kinds)
            rows = [r for r in rows if r.caller_kind in kset]
        if statuses is not None:
            sset = set(statuses)
            rows = [r for r in rows if r.status in sset]
        # Sort by started_at DESC (matches the schema.sql idx) then id for
        # deterministic ordering when two rows share started_at.
        rows.sort(key=lambda r: (r.started_at, r.id), reverse=True)
        # after_id pagination — drop everything up to + including the row
        # with id == after_id. Deterministic with the sort order above.
        if after_id is not None:
            picked: list[ToolInvocationRecord] = []
            seen = False
            for r in rows:
                if seen:
                    picked.append(r)
                elif r.id == after_id:
                    seen = True
            rows = picked
        page = rows[:limit]
        next_cursor = page[-1].id if len(rows) > limit else None
        return tuple(page), next_cursor

    # -- audit --------------------------------------------------------

    def append_audit(self, record: ToolAuditRecord) -> ToolAuditRecord:
        self.audits.append(record)
        return record

    def list_audit_for_tool(
        self, *, tenant_id: str, tool_id: str
    ) -> tuple[ToolAuditRecord, ...]:
        return tuple(
            r
            for r in self.audits
            if r.tenant_id == tenant_id and r.target_id == tool_id
        )


# ---------------------------------------------------------------------------
# Sort + cursor helpers
# ---------------------------------------------------------------------------


VALID_SORTS: frozenset[str] = frozenset(
    {
        "name",
        "calls_30d_desc",
        "last_used_desc",
        "created_at_desc",
    }
)


def _sort_descending(sort: str) -> bool:
    # name → ascending; everything else suffixed with "_desc" → descending.
    return sort.endswith("_desc")


def _sort_key(sort: str):
    # ``calls_30d_desc`` and ``last_used_desc`` require the usage projection,
    # which is computed at the service layer. The store layer falls back to
    # name-asc for those — the service applies the usage-aware reordering
    # post-projection. ``created_at_desc`` is a pure-store sort.
    if sort == "created_at_desc":
        return lambda r: (r.created_at, r.id)
    if sort == "name":
        return lambda r: (r.name.lower(), r.id)
    # calls_30d_desc / last_used_desc — name-asc fallback (service reorders).
    return lambda r: (r.name.lower(), r.id)


def _decode_cursor(cursor: str | None) -> int:
    if cursor is None:
        return 0
    try:
        return max(0, int(cursor))
    except ValueError:
        return 0


def iter_audit_rows_for_bulk(
    records: Iterable[ToolAuditRecord],
    *,
    correlation_id: str,
) -> Iterator[ToolAuditRecord]:
    """Stamp ``correlation_id`` on every audit row in a bulk write."""

    for record in records:
        yield record.model_copy(update={"correlation_id": correlation_id})


__all__ = [
    "InMemoryToolsStore",
    "ToolAuditRecord",
    "ToolInvocationCallerKindLiteral",
    "ToolInvocationErrorKindLiteral",
    "ToolInvocationRecord",
    "ToolInvocationStatusLiteral",
    "ToolKindLiteral",
    "ToolRecord",
    "ToolScopeLiteral",
    "ToolStatusLiteral",
    "ToolTransportKindLiteral",
    "ToolsStore",
    "VALID_SORTS",
    "iter_audit_rows_for_bulk",
]
