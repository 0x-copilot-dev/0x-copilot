"""Agents store — adapter contract + in-memory implementation (Phase 8 P8-A1).

Storage shape mirrors ``schema.sql`` in this package. The in-memory adapter
is the dev / test default; the Postgres adapter (production-only) implements
the same Protocol and reads/writes the same tables. Service-layer
authorization is enforced ABOVE this layer — the store exposes raw queries
scoped to ``tenant_id``.

Soft-delete (``deleted_at``) hides the row from the default list / get path
but leaves it visible to compliance reads via ``include_deleted=True``. The
cleanup job in ``agents/cleanup.py`` hard-deletes after the 90-day retention
window (agents-prd §5.4).

The store DOES persist the canonical agent shape (``AgentRecord``), the
version snapshots (``AgentVersionRecord``), the per-user install + override
layer (``AgentInstallRecord``), and the append-only audit chain
(``AgentAuditRecord``). P8-A1 ships every table because the cascade rules
+ audit chain reference all four; the operational routes for versions and
installs live in P8-A2 / P8-A3.
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


def _agent_id() -> str:
    return f"agt_{uuid4().hex}"


def _version_id() -> str:
    return f"agver_{uuid4().hex}"


def _install_id() -> str:
    return f"aginst_{uuid4().hex}"


def _audit_id() -> str:
    return f"audagt_{uuid4().hex}"


# ---------------------------------------------------------------------------
# Records
# ---------------------------------------------------------------------------


AgentOrigin = Literal["system", "community", "custom"]
AgentStatus = Literal["installed", "available", "disabled", "draft"]


class AgentRecord(BaseModel):
    """One row in the ``agents`` table.

    Pydantic model so the Postgres + in-memory adapters share one
    read/write contract. ``viewer_install_status`` is **derived** at the
    route layer (caller-relative) and does NOT live on the storage row.

    The ``permissions`` / ``skills`` / ``connectors_default`` / ``memory_ref``
    are JSONB on disk — we accept the dict shapes here without recasting
    into typed sub-models so the Postgres adapter can round-trip them
    without a redundant marshalling pass. The service layer is responsible
    for shape validation at the route boundary.
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=_agent_id)
    tenant_id: str
    name: str
    slug: str
    description: str = ""
    icon_emoji: str = "🤖"
    color_hue: int = 220
    # Monotonic counter — bumps only on POST /versions snapshot (P8-A2).
    version: int = 1
    status: AgentStatus = "draft"
    origin: AgentOrigin
    # ``custom`` MUST carry an owner; ``system``/``community`` MUST NOT.
    # Service layer enforces the invariant on every write.
    owner_user_id: str | None = None
    instructions: str = ""
    model_id: str
    reasoning_depth: Literal["fast", "balanced", "deep"] = "balanced"
    skills: list[str] = Field(default_factory=list)
    connectors_default: list[str] = Field(default_factory=list)
    permissions: dict[str, Any] = Field(default_factory=dict)
    memory_ref: dict[str, Any] | None = None
    forked_from_agent_id: str | None = None
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)
    deleted_at: datetime | None = None


class AgentVersionRecord(BaseModel):
    """One row in the ``agent_versions`` table — immutable snapshot.

    Created by ``POST /v1/agents/<id>/versions`` (P8-A2). P8-A1 ships the
    table because the cascade rules + audit chain reference it.
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=_version_id)
    agent_id: str
    tenant_id: str
    version: int
    instructions_snapshot: str
    model_id_snapshot: str
    reasoning_depth_snapshot: Literal["fast", "balanced", "deep"]
    skills_snapshot: list[str] = Field(default_factory=list)
    connectors_default_snapshot: list[str] = Field(default_factory=list)
    permissions_snapshot: dict[str, Any] = Field(default_factory=dict)
    label: str | None = None
    created_at: datetime = Field(default_factory=_now)
    created_by: str


class AgentInstallRecord(BaseModel):
    """One row in the ``agent_installs`` table.

    Per-user install + thin override layer. P8-A3 owns the routes; the
    record + store APIs land here so the gallery query (P8-A1's
    ``list_agents``) can join through to compute ``viewer_install_status``.
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=_install_id)
    tenant_id: str
    agent_id: str
    user_id: str
    installed_at: datetime = Field(default_factory=_now)
    disabled: bool = False
    overrides: dict[str, Any] | None = None
    pinned_version_id: str | None = None


class AgentAuditRecord(BaseModel):
    """Append-only audit row written on every state change.

    Same shape as ``ProjectAuditRecord`` so the audit-chain integration
    (``packages/audit-chain``) signs + chains rows in production with the
    same path. The in-memory adapter appends raw rows for tests.
    """

    model_config = ConfigDict(extra="forbid")

    audit_id: str = Field(default_factory=_audit_id)
    tenant_id: str
    actor_user_id: str
    action: str
    target_kind: str = "agent"
    target_id: str
    before_state: dict[str, Any] | None = None
    after_state: dict[str, Any] | None = None
    context: dict[str, Any] | None = None  # cross-audit §1.4
    correlation_id: str | None = None
    ts: datetime = Field(default_factory=_now)


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


class AgentsStore(Protocol):
    """Adapter contract for the Postgres + in-memory agents stores."""

    @contextmanager
    def transaction(self) -> Iterator[None]: ...  # pragma: no cover

    # -- agents --------------------------------------------------------

    def insert_agent(self, record: AgentRecord) -> AgentRecord: ...

    def get_agent(
        self, *, tenant_id: str, agent_id: str, include_deleted: bool = False
    ) -> AgentRecord | None: ...

    def get_agent_by_slug(
        self, *, tenant_id: str, slug: str, include_deleted: bool = False
    ) -> AgentRecord | None: ...

    def list_agents(
        self,
        *,
        tenant_id: str,
        origins: tuple[str, ...] | None = None,
        statuses: tuple[str, ...] | None = None,
        skill_ids: tuple[str, ...] | None = None,
        connector_ids: tuple[str, ...] | None = None,
        owner_user_id: str | None = None,
        # Visibility-scope filter; the service-layer applies the canonical
        # 404-not-403 ACL on top of these. ``visible_to_user_id`` narrows
        # to "system + community + caller's customs + caller's installs"
        # so non-owner customs never leak into the list.
        visible_to_user_id: str | None = None,
        q: str | None = None,
        cursor: str | None = None,
        limit: int = 50,
        sort: str = "updated_at:desc",
        include_deleted: bool = False,
    ) -> tuple[tuple[AgentRecord, ...], str | None]: ...

    def update_agent(self, record: AgentRecord) -> AgentRecord: ...

    def soft_delete_agent(self, *, tenant_id: str, agent_id: str) -> bool: ...

    def restore_agent(self, *, tenant_id: str, agent_id: str) -> bool: ...

    # -- installs (P8-A3 routes ride on these) -------------------------

    def get_install(
        self, *, tenant_id: str, agent_id: str, user_id: str
    ) -> AgentInstallRecord | None: ...

    def upsert_install(self, record: AgentInstallRecord) -> AgentInstallRecord: ...

    def delete_install(
        self, *, tenant_id: str, agent_id: str, user_id: str
    ) -> bool: ...

    # -- versions (P8-A2 routes ride on these) -------------------------

    def insert_version(self, record: AgentVersionRecord) -> AgentVersionRecord: ...

    def get_version(
        self, *, tenant_id: str, version_id: str
    ) -> AgentVersionRecord | None: ...

    def list_versions(
        self,
        *,
        tenant_id: str,
        agent_id: str,
        cursor: str | None = None,
        limit: int = 20,
    ) -> tuple[tuple[AgentVersionRecord, ...], str | None]: ...

    # -- audit ---------------------------------------------------------

    def append_audit(self, record: AgentAuditRecord) -> AgentAuditRecord: ...

    def list_audit_for_agent(
        self, *, tenant_id: str, agent_id: str
    ) -> tuple[AgentAuditRecord, ...]: ...


# ---------------------------------------------------------------------------
# In-memory adapter
# ---------------------------------------------------------------------------


@dataclass
class InMemoryAgentsStore:
    """Dict-backed adapter for tests + the default dev wiring.

    Mirrors the Postgres semantics where it matters: tenant scoping is a
    filter on every query; soft-delete hides rows from the default list/
    get paths but leaves them visible to ``include_deleted=True``. The
    filter axes compose in-process (the Postgres adapter pushes the same
    predicates into SQL).
    """

    agents: dict[str, AgentRecord] = field(default_factory=dict)
    versions: dict[str, AgentVersionRecord] = field(default_factory=dict)
    installs: dict[tuple[str, str, str], AgentInstallRecord] = field(
        default_factory=dict
    )
    audits: list[AgentAuditRecord] = field(default_factory=list)

    @contextmanager
    def transaction(self) -> Iterator[None]:
        # Single-process in-memory; no actual transactional boundary.
        # The service layer still calls ``transaction()`` so the same
        # composition works against the Postgres adapter without a
        # branch.
        yield

    # -- agents ------------------------------------------------------------

    def insert_agent(self, record: AgentRecord) -> AgentRecord:
        self.agents[record.id] = record
        return record

    def get_agent(
        self, *, tenant_id: str, agent_id: str, include_deleted: bool = False
    ) -> AgentRecord | None:
        record = self.agents.get(agent_id)
        if record is None or record.tenant_id != tenant_id:
            return None
        if record.deleted_at is not None and not include_deleted:
            return None
        return record

    def get_agent_by_slug(
        self, *, tenant_id: str, slug: str, include_deleted: bool = False
    ) -> AgentRecord | None:
        wanted = slug.strip().lower()
        for record in self.agents.values():
            if record.tenant_id != tenant_id:
                continue
            if record.deleted_at is not None and not include_deleted:
                continue
            if record.slug.lower() == wanted:
                return record
        return None

    def list_agents(
        self,
        *,
        tenant_id: str,
        origins: tuple[str, ...] | None = None,
        statuses: tuple[str, ...] | None = None,
        skill_ids: tuple[str, ...] | None = None,
        connector_ids: tuple[str, ...] | None = None,
        owner_user_id: str | None = None,
        visible_to_user_id: str | None = None,
        q: str | None = None,
        cursor: str | None = None,
        limit: int = 50,
        sort: str = "updated_at:desc",
        include_deleted: bool = False,
    ) -> tuple[tuple[AgentRecord, ...], str | None]:
        q_normalized = q.strip().lower() if q else None
        installed_agent_ids: set[str] | None = None
        if visible_to_user_id is not None:
            installed_agent_ids = {
                inst.agent_id
                for (tid, agent_id, uid), inst in self.installs.items()
                if tid == tenant_id and uid == visible_to_user_id
            }

        candidates: list[AgentRecord] = []
        for record in self.agents.values():
            if record.tenant_id != tenant_id:
                continue
            if record.deleted_at is not None and not include_deleted:
                continue
            if origins is not None and record.origin not in origins:
                continue
            if statuses is not None and record.status not in statuses:
                continue
            if owner_user_id is not None and record.owner_user_id != owner_user_id:
                continue
            if skill_ids is not None:
                # Multi-value OR: any overlap qualifies.
                if not any(s in skill_ids for s in record.skills):
                    continue
            if connector_ids is not None:
                if not any(c in connector_ids for c in record.connectors_default):
                    continue
            if q_normalized:
                haystack = f"{record.name} {record.description} {record.slug}".lower()
                if q_normalized not in haystack:
                    continue
            if visible_to_user_id is not None:
                # Visibility filter — system + community are universally
                # visible; custom is visible only to its owner OR if the
                # caller has installed it.
                if record.origin == "custom":
                    is_owner = record.owner_user_id == visible_to_user_id
                    is_installed = (
                        installed_agent_ids is not None
                        and record.id in installed_agent_ids
                    )
                    if not is_owner and not is_installed:
                        continue
            candidates.append(record)

        candidates.sort(key=_sort_key(sort), reverse=_sort_descending(sort))
        start = _decode_cursor(cursor)
        page = candidates[start : start + limit]
        next_cursor: str | None = None
        if start + limit < len(candidates):
            next_cursor = str(start + limit)
        return tuple(page), next_cursor

    def update_agent(self, record: AgentRecord) -> AgentRecord:
        self.agents[record.id] = record
        return record

    def soft_delete_agent(self, *, tenant_id: str, agent_id: str) -> bool:
        record = self.agents.get(agent_id)
        if record is None or record.tenant_id != tenant_id:
            return False
        if record.deleted_at is not None:
            return True
        self.agents[agent_id] = record.model_copy(
            update={"deleted_at": _now(), "updated_at": _now()}
        )
        return True

    def restore_agent(self, *, tenant_id: str, agent_id: str) -> bool:
        record = self.agents.get(agent_id)
        if record is None or record.tenant_id != tenant_id:
            return False
        if record.deleted_at is None:
            return True
        self.agents[agent_id] = record.model_copy(
            update={"deleted_at": None, "updated_at": _now()}
        )
        return True

    # -- installs ----------------------------------------------------------

    def get_install(
        self, *, tenant_id: str, agent_id: str, user_id: str
    ) -> AgentInstallRecord | None:
        return self.installs.get((tenant_id, agent_id, user_id))

    def upsert_install(self, record: AgentInstallRecord) -> AgentInstallRecord:
        key = (record.tenant_id, record.agent_id, record.user_id)
        existing = self.installs.get(key)
        if existing is not None:
            merged = existing.model_copy(
                update=record.model_dump(
                    exclude={"id", "installed_at"}, exclude_unset=False
                )
            )
            self.installs[key] = merged
            return merged
        self.installs[key] = record
        return record

    def delete_install(self, *, tenant_id: str, agent_id: str, user_id: str) -> bool:
        return self.installs.pop((tenant_id, agent_id, user_id), None) is not None

    # -- versions ----------------------------------------------------------

    def insert_version(self, record: AgentVersionRecord) -> AgentVersionRecord:
        self.versions[record.id] = record
        return record

    def get_version(
        self, *, tenant_id: str, version_id: str
    ) -> AgentVersionRecord | None:
        record = self.versions.get(version_id)
        if record is None or record.tenant_id != tenant_id:
            return None
        return record

    def list_versions(
        self,
        *,
        tenant_id: str,
        agent_id: str,
        cursor: str | None = None,
        limit: int = 20,
    ) -> tuple[tuple[AgentVersionRecord, ...], str | None]:
        rows = [
            v
            for v in self.versions.values()
            if v.tenant_id == tenant_id and v.agent_id == agent_id
        ]
        rows.sort(key=lambda v: (v.version, v.id), reverse=True)
        start = _decode_cursor(cursor)
        page = rows[start : start + limit]
        next_cursor = str(start + limit) if start + limit < len(rows) else None
        return tuple(page), next_cursor

    # -- audit ----------------------------------------------------------

    def append_audit(self, record: AgentAuditRecord) -> AgentAuditRecord:
        self.audits.append(record)
        return record

    def list_audit_for_agent(
        self, *, tenant_id: str, agent_id: str
    ) -> tuple[AgentAuditRecord, ...]:
        return tuple(
            r
            for r in self.audits
            if r.tenant_id == tenant_id and r.target_id == agent_id
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
    # Default: updated_at.
    return lambda r: (r.updated_at, r.id)


def _decode_cursor(cursor: str | None) -> int:
    if cursor is None:
        return 0
    try:
        return max(0, int(cursor))
    except ValueError:
        return 0


def iter_audit_rows_for_bulk(
    records: Iterable[AgentAuditRecord],
    *,
    correlation_id: str,
) -> Iterator[AgentAuditRecord]:
    """Stamp ``correlation_id`` on every audit row in a bulk write."""

    for record in records:
        yield record.model_copy(update={"correlation_id": correlation_id})


__all__ = [
    "AgentAuditRecord",
    "AgentInstallRecord",
    "AgentRecord",
    "AgentVersionRecord",
    "AgentsStore",
    "InMemoryAgentsStore",
    "iter_audit_rows_for_bulk",
]
