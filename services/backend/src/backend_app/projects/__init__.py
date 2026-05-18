"""Projects destination (Phase 6) — CRUD + ACL + membership + ownership transfer.

This package ships the **canonical** project-scoped ACL predicate
(:func:`backend_app.projects.acl.is_member`). Every destination carrying
``project_id`` (Todos / Inbox / Routines / Library / Memory / Chats)
consumes this resolver instead of re-implementing the membership query
— cross-audit §1.3 binding (2026-05-17).

Public surface:

* CRUD: ``GET /v1/projects``, ``GET /v1/projects/{id}``,
  ``POST /v1/projects``, ``PATCH /v1/projects/{id}``,
  ``DELETE /v1/projects/{id}``, ``POST /v1/projects/{id}/restore``.
* Members: ``GET /v1/projects/{id}/members``,
  ``POST /v1/projects/{id}/members``,
  ``PATCH /v1/projects/{id}/members/{user_id}``,
  ``DELETE /v1/projects/{id}/members/{user_id}`` (or ``…/members/me``).
* Transfer: ``POST /v1/projects/{id}/transfer`` (owner-only),
  ``POST /v1/admin/projects/{id}/force-transfer`` (admin-only, projects-
  prd §12 Q1 — orchestrator-approved).
* Stars: ``POST /v1/projects/{id}/star`` / ``POST /v1/projects/{id}/unstar``.

Authorization (cross-audit §1.3, projects-prd §7):

* Owner-only writes on the project (PATCH / archive / delete / member-
  add / member-remove / member-role-change / transfer).
* A member can self-remove via ``DELETE …/members/me``.
* Reads: owner OR project-member OR tenant admin (compliance read).
* Non-readers see 404, not 403.

Ownership transfer is atomic — the PARTIAL UNIQUE on
``(project_id) WHERE role='owner'`` is honored by demoting the old
owner and promoting the new one in a single transaction; no reader
sees a two-owner state.

Wire shape is canonical at ``packages/api-types/src/projects.ts``;
the Python mirrors live in ``projects.routes``.
"""

from __future__ import annotations

from backend_app.projects.acl import (
    InMemoryProjectMembershipAdapter,
    PostgresProjectMembershipAdapter,
    ProjectMembershipPort,
    ProjectRole,
    is_member,
    member_role,
)
from backend_app.projects.routes import register_projects_routes
from backend_app.projects.service import (
    ProjectConflict,
    ProjectForbidden,
    ProjectInvalidRequest,
    ProjectNotFound,
    ProjectsService,
)
from backend_app.projects.store import (
    InMemoryProjectsStore,
    ProjectActivityCounts,
    ProjectActivityRecord,
    ProjectAuditRecord,
    ProjectMembershipRecord,
    ProjectRecord,
    ProjectStarRecord,
    ProjectsStore,
)


__all__ = [
    "InMemoryProjectMembershipAdapter",
    "InMemoryProjectsStore",
    "PostgresProjectMembershipAdapter",
    "ProjectActivityCounts",
    "ProjectActivityRecord",
    "ProjectAuditRecord",
    "ProjectConflict",
    "ProjectForbidden",
    "ProjectInvalidRequest",
    "ProjectMembershipPort",
    "ProjectMembershipRecord",
    "ProjectNotFound",
    "ProjectRecord",
    "ProjectRole",
    "ProjectStarRecord",
    "ProjectsService",
    "ProjectsStore",
    "is_member",
    "member_role",
    "register_projects_routes",
]
