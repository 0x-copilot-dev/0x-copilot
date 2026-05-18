"""Canonical project-scoped ACL — the cross-audit §1.3 master rule.

This module ships the **single source of truth** for "is user X a
member of project P with role R-or-stronger?". Every destination that
carries ``project_id`` (Todos / Inbox / Routines / Library / Memory /
Chats / …) consumes this predicate via in-process import; ``ai-backend``
consumes it via the internal HTTP endpoint at
``/internal/v1/projects/{id}/membership/{user_id}`` which is a thin
wrapper around the same store.

A second implementation of the membership query in another destination
is a bug — converge it on this port or call this resolver.

Authorization shape (cross-audit §1.3, binding 2026-05-17):

* A project is **visible** when the caller is the owner, OR a member
  (any role), OR a tenant admin (compliance read; audited at the
  consuming route).
* Mutations on the project (PATCH / archive / members / transfer)
  are owner-only.
* Mutations on a child resource filed under a project follow the
  child resource's own ACL — project membership is a **read scope**,
  not a permission lift.
* Non-readers see **404** (existence not leaked), NEVER 403.

The historical stand-in ``ProjectMembershipPort`` Protocols inside
``inbox/`` / ``todos/`` / ``routines/`` are P6-A2's rewire target;
this module ships the canonical contract their adapters consume.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Protocol


# Project-local roles. Distinct from tenant roles (cross-audit §1.3).
ProjectRole = Literal["owner", "editor", "viewer"]

_VALID_ROLES: frozenset[ProjectRole] = frozenset({"owner", "editor", "viewer"})


class ProjectMembershipPort(Protocol):
    """Canonical adapter contract for project-membership lookups.

    Implemented twice:

    * :class:`InMemoryProjectMembershipAdapter` — dev / test default.
    * :class:`PostgresProjectMembershipAdapter` — production; reads from
      the ``project_memberships`` table (projects-prd §5.1) under the
      tenant-isolation RLS policy.

    Consumers MUST treat caller-supplied ``tenant_id`` / ``user_id`` as
    untrusted unless they came from a verified session. The route layer
    is responsible for binding them to the verified identity before
    calling here.
    """

    def is_member(
        self, *, tenant_id: str, project_id: str, user_id: str
    ) -> bool:  # pragma: no cover - protocol
        """Returns True iff the user has any role on the project."""

    def member_role(
        self, *, tenant_id: str, project_id: str, user_id: str
    ) -> ProjectRole | None:  # pragma: no cover - protocol
        """Returns the user's role on the project, or None if not a member.

        ``None`` means "not a member" — distinct from "project doesn't
        exist". Callers that need to distinguish must use
        :meth:`project_exists`.
        """

    def list_projects_for_user(
        self, *, tenant_id: str, user_id: str
    ) -> tuple[str, ...]:  # pragma: no cover - protocol
        """Returns the project ids the user is a member of, any role.

        Used by destinations to fan out the "project-member reads" path
        on their list endpoints. Stable ordering is NOT required.
        """

    # -- back-compat alias --------------------------------------------
    # The stand-in ports defined inside todos/inbox/routines used
    # ``is_project_member``; P6-A2 will rewire those callers but for
    # now we expose the alias so the canonical adapter is drop-in
    # substitutable. Once P6-A2 lands, this can be removed.
    def is_project_member(
        self, *, tenant_id: str, project_id: str, user_id: str
    ) -> bool:  # pragma: no cover - protocol
        """Alias for :meth:`is_member` — kept for stand-in port compat.

        Implementations must delegate; do not re-implement.
        """


# ---------------------------------------------------------------------------
# In-memory adapter (tests + dev default)
# ---------------------------------------------------------------------------


@dataclass
class InMemoryProjectMembershipAdapter:
    """Dict-backed adapter for tests + the default dev wiring.

    The shape mirrors the in-memory adapters in ``routines.store`` /
    ``inbox.store`` / ``todos.store`` — tenant scoping is a filter on
    every lookup, no soft-delete semantics here (memberships have no
    deleted_at; they cascade with the parent project).
    """

    # Key: (tenant_id, project_id) -> {user_id: role}.
    memberships: dict[tuple[str, str], dict[str, ProjectRole]] = field(
        default_factory=dict
    )

    # -- write affordances (test-only) --------------------------------

    def add(
        self,
        *,
        tenant_id: str,
        project_id: str,
        user_id: str,
        role: ProjectRole,
    ) -> None:
        """Test helper — register a membership row.

        Validates ``role`` against the allowlist so a typo in a test
        fixture (e.g. ``"member"``) fails loudly instead of silently
        creating an invalid row.
        """

        if role not in _VALID_ROLES:
            raise ValueError(f"invalid project role: {role!r}")
        key = (tenant_id, project_id)
        bucket = self.memberships.setdefault(key, {})
        bucket[user_id] = role

    def remove(self, *, tenant_id: str, project_id: str, user_id: str) -> None:
        """Test helper — drop a membership row. No-op if absent."""

        key = (tenant_id, project_id)
        bucket = self.memberships.get(key)
        if bucket is not None:
            bucket.pop(user_id, None)
            if not bucket:
                self.memberships.pop(key, None)

    # -- ProjectMembershipPort ----------------------------------------

    def is_member(self, *, tenant_id: str, project_id: str, user_id: str) -> bool:
        return (
            self.member_role(
                tenant_id=tenant_id, project_id=project_id, user_id=user_id
            )
            is not None
        )

    def member_role(
        self, *, tenant_id: str, project_id: str, user_id: str
    ) -> ProjectRole | None:
        bucket = self.memberships.get((tenant_id, project_id))
        if bucket is None:
            return None
        return bucket.get(user_id)

    def list_projects_for_user(
        self, *, tenant_id: str, user_id: str
    ) -> tuple[str, ...]:
        out = [
            project_id
            for (tid, project_id), bucket in self.memberships.items()
            if tid == tenant_id and user_id in bucket
        ]
        return tuple(out)

    def is_project_member(
        self, *, tenant_id: str, project_id: str, user_id: str
    ) -> bool:
        # Stand-in-port alias. Delegate to :meth:`is_member` so the
        # canonical predicate is the only place membership is decided.
        return self.is_member(
            tenant_id=tenant_id, project_id=project_id, user_id=user_id
        )


# ---------------------------------------------------------------------------
# Postgres adapter skeleton
# ---------------------------------------------------------------------------


@dataclass
class PostgresProjectMembershipAdapter:
    """Postgres adapter — reads from the ``project_memberships`` table.

    Skeleton at P6-A1 (CRUD lands; the Postgres connection-pool wiring
    is the production deployment's job to inject). Methods raise
    :class:`NotImplementedError` until the full adapter ships; the
    in-memory adapter above is the dev / test path.

    The adapter MUST be tenant-first on every query: tenant_id is
    filtered server-side via the RLS policy on ``project_memberships``,
    and the application-side WHERE clause repeats it (cross-audit §3.1
    "no caller-supplied tenant trust" — the verified bearer's tenant
    claim is the source of truth).
    """

    pool: object | None = None  # PostgresConnectionPool — wired at boot

    def is_member(
        self, *, tenant_id: str, project_id: str, user_id: str
    ) -> bool:  # pragma: no cover - skeleton
        return (
            self.member_role(
                tenant_id=tenant_id, project_id=project_id, user_id=user_id
            )
            is not None
        )

    def member_role(
        self, *, tenant_id: str, project_id: str, user_id: str
    ) -> ProjectRole | None:  # pragma: no cover - skeleton
        # SELECT role FROM project_memberships
        #  WHERE tenant_id = %s AND project_id = %s AND user_id = %s
        #  LIMIT 1;
        # The RLS policy on the table is the second wall; the
        # application-side WHERE is the primary tenant filter.
        raise NotImplementedError(
            "PostgresProjectMembershipAdapter.member_role: wire the "
            "connection pool in the deployment composer (see "
            "backend_app.store.PostgresConnectionPool)."
        )

    def list_projects_for_user(
        self, *, tenant_id: str, user_id: str
    ) -> tuple[str, ...]:  # pragma: no cover - skeleton
        # SELECT project_id FROM project_memberships
        #  WHERE tenant_id = %s AND user_id = %s;
        raise NotImplementedError(
            "PostgresProjectMembershipAdapter.list_projects_for_user: "
            "wire the connection pool."
        )

    def is_project_member(
        self, *, tenant_id: str, project_id: str, user_id: str
    ) -> bool:  # pragma: no cover - skeleton
        return self.is_member(
            tenant_id=tenant_id, project_id=project_id, user_id=user_id
        )


# ---------------------------------------------------------------------------
# Module-level convenience helpers
# ---------------------------------------------------------------------------


def is_member(
    port: ProjectMembershipPort,
    *,
    tenant_id: str,
    project_id: str,
    user_id: str,
) -> bool:
    """Module-level wrapper — the canonical name in the cross-audit doc.

    Equivalent to ``port.is_member(...)``. Provided so call sites can
    import the function rather than threading the adapter through every
    helper:

        from backend_app.projects.acl import is_member, ProjectMembershipPort

        def can_read(port: ProjectMembershipPort, ...) -> bool:
            return is_member(port, tenant_id=..., project_id=..., user_id=...)
    """

    return port.is_member(tenant_id=tenant_id, project_id=project_id, user_id=user_id)


def member_role(
    port: ProjectMembershipPort,
    *,
    tenant_id: str,
    project_id: str,
    user_id: str,
) -> ProjectRole | None:
    """Module-level wrapper around :meth:`ProjectMembershipPort.member_role`."""

    return port.member_role(tenant_id=tenant_id, project_id=project_id, user_id=user_id)


__all__ = [
    "InMemoryProjectMembershipAdapter",
    "PostgresProjectMembershipAdapter",
    "ProjectMembershipPort",
    "ProjectRole",
    "is_member",
    "member_role",
]
