"""Team destination store (P12-A2 §3.1 / §5).

The Team destination is a **read projection** over the existing
``users`` + ``organization_members`` + ``role_assignments`` tables (no
new identity, no new tenant data). This module defines:

* :class:`TeamStore` — Protocol the routes consume. Methods:
  ``list_people``, ``get_person``, ``count_assets_for_user`` (agents +
  projects projection).
* :class:`InMemoryTeamStore` — adapter that delegates to the existing
  :class:`IdentityStore` + (optional) agents / projects stores. The
  dev / test default.
* :class:`PresenceKv` — Protocol for the volatile ``presence_state``
  KV. v1 ships :class:`InMemoryPresenceKv`; production injects a
  Redis-backed adapter via the same Protocol so the substitution rule
  is satisfied (sub-PRD §5.2).

No new SQL, no new schema. Audit lives on the identity audit table
(``IdentityAuditEventRecord``) because every Team mutation is identical
in shape to an existing identity audit row (role change, member remove);
adding a parallel ``team_audit`` table would violate cross-audit §1.1
(no parallel audit chains).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal, Protocol


# ---------------------------------------------------------------------------
# Value types — small read-projections built off existing UserRecord +
# OrganizationMemberRecord. Wire shape lives in api-types/team.ts; this
# is the in-process domain type the service layer composes.
# ---------------------------------------------------------------------------


TeamRole = Literal["owner", "admin", "member", "guest"]
Presence = Literal["active", "away", "in_meeting", "offline"]


@dataclass(frozen=True)
class PersonRow:
    """One row in the Team list / detail projection.

    Composed from ``UserRecord`` + ``OrganizationMemberRecord`` +
    ``RoleAssignmentRecord`` + ``PresenceState`` + asset-count
    projections. The service layer builds this; stores never construct
    it directly.
    """

    id: str
    tenant_id: str
    display_name: str
    email: str
    avatar_url: str | None
    role: TeamRole
    presence: Presence
    last_seen_at: datetime | None
    joined_at: datetime
    agents_count: int
    projects_count: int


@dataclass(frozen=True)
class PresenceState:
    """Volatile presence row from the in-process KV.

    ``last_seen_at`` is the audit-trail truth; ``state`` is best-effort
    (sub-PRD §3.1 ``Person`` wire docstring).
    """

    state: Presence
    last_seen_at: datetime | None


# ---------------------------------------------------------------------------
# Presence KV Protocol — dev = in-proc dict; prod = Redis adapter
# (sub-PRD §5.2 "presence comes from app.state.presence_kv (in-process
# dict for v1; admin-injectable Redis-backed adapter in production)").
# ---------------------------------------------------------------------------


class PresenceKv(Protocol):
    """Substitutable contract for the volatile presence store.

    Production injects a Redis-backed adapter; dev uses the in-process
    dict. The Protocol is the SoT — any adapter that satisfies it can
    plug into :class:`backend_app.team.service.TeamService` without
    code change.
    """

    def get(
        self, *, tenant_id: str, user_id: str
    ) -> PresenceState: ...  # pragma: no cover - protocol

    def set(
        self,
        *,
        tenant_id: str,
        user_id: str,
        state: Presence,
        last_seen_at: datetime | None = None,
    ) -> PresenceState: ...  # pragma: no cover - protocol


@dataclass
class InMemoryPresenceKv:
    """Dict-backed presence KV — process-local, dev default.

    Volatile by design (sub-PRD §5.3 "presence_state: in-memory; no
    retention"). Reset across process restarts; the routes return
    ``offline`` for any user not present in the dict.
    """

    rows: dict[tuple[str, str], PresenceState] = field(default_factory=dict)

    def get(self, *, tenant_id: str, user_id: str) -> PresenceState:
        return self.rows.get(
            (tenant_id, user_id),
            PresenceState(state="offline", last_seen_at=None),
        )

    def set(
        self,
        *,
        tenant_id: str,
        user_id: str,
        state: Presence,
        last_seen_at: datetime | None = None,
    ) -> PresenceState:
        row = PresenceState(
            state=state,
            last_seen_at=last_seen_at or datetime.now(timezone.utc),
        )
        self.rows[(tenant_id, user_id)] = row
        return row


# ---------------------------------------------------------------------------
# Asset-count projection ports — narrow Protocols so the in-memory
# adapter can plug in any agents/projects store (no hard dep). Mirrors
# how home/service.py composes per-section readers off app.state.
# ---------------------------------------------------------------------------


class AssetCountsPort(Protocol):
    """Returns the agents_count / projects_count projection for a person.

    Implemented by :class:`InMemoryAssetCountsAdapter` which reads from
    the existing agents/projects stores. Returning ``(0, 0)`` is a
    valid fallback when the host hasn't wired the underlying stores
    (e.g. early-bootstrap tests).
    """

    def counts_for(
        self, *, tenant_id: str, user_id: str
    ) -> tuple[int, int]:  # pragma: no cover - protocol
        """Return ``(agents_count, projects_count)`` for the user."""


@dataclass
class ZeroAssetCounts:
    """Fallback when no real stores are wired — every user shows 0/0."""

    def counts_for(self, *, tenant_id: str, user_id: str) -> tuple[int, int]:
        return (0, 0)


@dataclass
class StoreBackedAssetCounts:
    """Adapter that delegates to the live agents + projects stores.

    Built lazily from ``app.state`` at request time (see
    ``backend_app.team.service``) so registration order in
    ``create_app`` is flexible.
    """

    agents_store: Any | None = None  # AgentsStore
    projects_store: Any | None = None  # ProjectsStore

    def counts_for(self, *, tenant_id: str, user_id: str) -> tuple[int, int]:
        agents = self._agents_for(tenant_id=tenant_id, user_id=user_id)
        projects = self._projects_for(tenant_id=tenant_id, user_id=user_id)
        return (agents, projects)

    def _agents_for(self, *, tenant_id: str, user_id: str) -> int:
        if self.agents_store is None:
            return 0
        try:
            rows, _ = self.agents_store.list_agents(
                tenant_id=tenant_id,
                owner_user_id=user_id,
                limit=1000,
            )
        except Exception:  # noqa: BLE001 — projection must never block a Team read
            return 0
        return len(rows)

    def _projects_for(self, *, tenant_id: str, user_id: str) -> int:
        if self.projects_store is None:
            return 0
        try:
            rows, _ = self.projects_store.list_projects(
                tenant_id=tenant_id,
                owner_user_id=user_id,
                limit=1000,
            )
        except Exception:  # noqa: BLE001 — projection must never block a Team read
            return 0
        return len(rows)


# ---------------------------------------------------------------------------
# Team store — Protocol + InMemory adapter. The store is intentionally
# thin: it composes the IdentityStore + RoleStore + PresenceKv +
# AssetCountsPort into a single ``list_people`` / ``get_person`` lens.
# ---------------------------------------------------------------------------


class TeamStore(Protocol):
    """Read-projection contract for the Team destination."""

    def list_people(
        self,
        *,
        tenant_id: str,
        caller_user_id: str,
        role: TeamRole | None = None,
        presence: Presence | None = None,
        q: str | None = None,
        sort: str = "display_name:asc",
        cursor: str | None = None,
        limit: int = 50,
    ) -> tuple[tuple[PersonRow, ...], str | None]:
        """Return person rows + opaque next-cursor for the tenant."""
        ...  # pragma: no cover - protocol

    def get_person(
        self, *, tenant_id: str, user_id: str
    ) -> PersonRow | None: ...  # pragma: no cover - protocol


@dataclass
class InMemoryTeamStore:
    """Dev / test adapter — composes the in-memory IdentityStore.

    Every read recomputes from the underlying ``users`` /
    ``organization_members`` / ``role_assignments`` rows so a mutation
    in the existing identity path (e.g. a role-change via
    ``/internal/v1/workspace/members``) is visible to the Team
    projection on the next call. No cache, no parallel state.
    """

    identity_store: Any  # IdentityStore Protocol — typed via duck
    presence_kv: PresenceKv = field(default_factory=InMemoryPresenceKv)
    asset_counts: AssetCountsPort = field(default_factory=ZeroAssetCounts)

    # -- list ----------------------------------------------------------

    def list_people(
        self,
        *,
        tenant_id: str,
        caller_user_id: str,
        role: TeamRole | None = None,
        presence: Presence | None = None,
        q: str | None = None,
        sort: str = "display_name:asc",
        cursor: str | None = None,
        limit: int = 50,
    ) -> tuple[tuple[PersonRow, ...], str | None]:
        users = self.identity_store.list_users(org_id=tenant_id, include_deleted=False)
        members = {
            m.user_id: m
            for m in self.identity_store.list_members(org_id=tenant_id)
            if m.removed_at is None
        }
        rows: list[PersonRow] = []
        for user in users:
            membership = members.get(user.user_id)
            if membership is None:
                # User row without an active membership = not a tenant
                # member; the Team destination never lists them.
                continue
            row = self._compose(tenant_id=tenant_id, user=user, membership=membership)
            if role is not None and row.role != role:
                continue
            if presence is not None and row.presence != presence:
                continue
            if q is not None and not _matches_q(row, q):
                continue
            rows.append(row)

        rows.sort(key=_sort_key(sort))

        # Opaque-cursor paging: ``cursor`` is the integer offset of the
        # next page. Stable + simple; matches the inbox/connectors
        # convention (cross-audit §2.5 "opaque cursors only").
        offset = _parse_offset(cursor)
        sliced = rows[offset : offset + limit]
        next_cursor = (
            _serialise_offset(offset + limit) if offset + limit < len(rows) else None
        )
        return tuple(sliced), next_cursor

    # -- detail --------------------------------------------------------

    def get_person(self, *, tenant_id: str, user_id: str) -> PersonRow | None:
        user = self.identity_store.get_user(org_id=tenant_id, user_id=user_id)
        if user is None or user.deleted_at is not None:
            return None
        membership = next(
            (
                m
                for m in self.identity_store.list_members(org_id=tenant_id)
                if m.user_id == user_id and m.removed_at is None
            ),
            None,
        )
        if membership is None:
            return None
        return self._compose(tenant_id=tenant_id, user=user, membership=membership)

    # -- internal ------------------------------------------------------

    def _compose(self, *, tenant_id: str, user: Any, membership: Any) -> PersonRow:
        role = self._resolve_role(tenant_id=tenant_id, user_id=user.user_id)
        presence_row = self.presence_kv.get(tenant_id=tenant_id, user_id=user.user_id)
        agents_count, projects_count = self.asset_counts.counts_for(
            tenant_id=tenant_id, user_id=user.user_id
        )
        avatar = (
            user.metadata.get("avatar_url") if isinstance(user.metadata, dict) else None
        )
        return PersonRow(
            id=user.user_id,
            tenant_id=tenant_id,
            display_name=user.display_name,
            email=user.primary_email,
            avatar_url=avatar if isinstance(avatar, str) and avatar else None,
            role=role,
            presence=presence_row.state,
            last_seen_at=presence_row.last_seen_at or user.last_seen_at,
            joined_at=membership.joined_at,
            agents_count=agents_count,
            projects_count=projects_count,
        )

    def _resolve_role(self, *, tenant_id: str, user_id: str) -> TeamRole:
        """Map the user's primary identity-role-assignment to a team
        role (sub-PRD §3.1 ``TeamRole`` enum)."""

        assignments = self.identity_store.list_role_assignments(
            org_id=tenant_id, user_id=user_id
        )
        if not assignments:
            return "member"
        primary = max(assignments, key=lambda r: r.granted_at)
        role_record = self.identity_store.get_role(role_id=primary.role_id)
        if role_record is None:
            return "member"
        return _system_role_to_team_role(role_record.name)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _system_role_to_team_role(system_role_name: str) -> TeamRole:
    """Map system-role names to the design-doc Team roles.

    The system roles ('admin' / 'employee' / 'auditor' / 'owner') come
    from ``0004b_seed_system_roles.sql``. ``employee`` projects as
    ``member``; ``auditor`` projects as ``guest`` (read-only). Mirrors
    :func:`backend_app.identity.invitations.design_role_alias_for` so
    the wire roles stay consistent across destinations.
    """

    if system_role_name == "owner":
        return "owner"
    if system_role_name == "admin":
        return "admin"
    if system_role_name == "auditor":
        return "guest"
    return "member"


def team_role_to_system_role(role: TeamRole) -> str:
    """Reverse mapping for PATCH role payloads (admin role change)."""

    if role == "owner":
        return "owner"
    if role == "admin":
        return "admin"
    if role == "guest":
        return "auditor"
    return "employee"


def _matches_q(row: PersonRow, q: str) -> bool:
    needle = q.strip().lower()
    if not needle:
        return True
    return needle in row.display_name.lower() or needle in row.email.lower()


def _sort_key(sort: str):
    """Return a row → key function for the configured sort token.

    The sort vocabulary is locked at the api-types level
    (``TeamListSort``); any unknown value falls back to display_name
    ascending so the projection never crashes on an unrecognised hint.
    """

    if sort == "display_name:desc":
        return lambda r: r.display_name.lower()[::-1]
    if sort == "last_seen:desc":
        # Negate by sorting on the absent-last sentinel, then negative
        # epoch seconds. ``None`` sorts last (offline / never-seen).
        def key(r: PersonRow):
            ts = r.last_seen_at
            return (0 if ts is not None else 1, -(ts.timestamp() if ts else 0))

        return key
    if sort == "joined_at:desc":
        return lambda r: -r.joined_at.timestamp()
    return lambda r: r.display_name.lower()


def _parse_offset(cursor: str | None) -> int:
    if cursor is None:
        return 0
    try:
        return max(0, int(cursor))
    except (ValueError, TypeError):
        return 0


def _serialise_offset(offset: int) -> str:
    return str(offset)


__all__ = [
    "AssetCountsPort",
    "InMemoryPresenceKv",
    "InMemoryTeamStore",
    "PersonRow",
    "Presence",
    "PresenceKv",
    "PresenceState",
    "StoreBackedAssetCounts",
    "TeamRole",
    "TeamStore",
    "ZeroAssetCounts",
    "team_role_to_system_role",
]
