"""Read-only ``/internal/v1/me/*`` routes — caller's own profile + memberships.

Mounted under the internal plane and consumed by the facade's ``/v1/me/*``
proxy. The facade is the only legitimate caller; the service-token guard
plus ``RUNTIME_USE`` scope keep these endpoints off the public Internet.

Today this surfaces a single endpoint — ``GET /internal/v1/me/workspaces``
— used by the frontend's UserCard popover (PR 2.2 sidebar) to render the
workspace switcher.

Why "the caller's *current* workspace only" instead of "all workspaces
the user is a member of":

  1. The session row already binds the caller to a single ``org_id``;
     ``BackendServiceAuthenticator`` returns that ``org_id`` from the
     ``ORG_HEADER`` for every authenticated request. Listing other orgs
     would require either a per-user-cross-org index or an
     unauthenticated identity lookup — both bigger surfaces than v1
     warrants.
  2. The Atlas design is fine with single-workspace deploys today;
     the picker ships disabled when it sees one entry. Multi-workspace
     enrolment lands when the IdP / SSO surface gains a "switch
     workspace" affordance — that PR widens this endpoint to walk
     ``organization_members`` by ``user_id`` and re-scope the session.
  3. No new SQL, no new index, no new migration. The endpoint composes
     existing reads (``IdentityStore.get_organization`` /
     ``list_role_assignments`` / ``get_role`` / ``list_members``).

The response shape mirrors ``packages/api-types`` — every field maps
1-to-1 to ``Workspace`` in TypeScript.
"""

from __future__ import annotations

from datetime import datetime, timezone

from copilot_service_contracts.scopes import RUNTIME_USE
from fastapi import Depends, FastAPI, HTTPException, Query, Request, status
from pydantic import BaseModel, Field

from backend_app.auth import BackendServiceAuthenticator
from backend_app.contracts import RoleAssignmentRecord
from backend_app.identity.rbac import RequireScopes
from backend_app.identity.store import IdentityStore


class Workspace(BaseModel):
    """One row in the workspace switcher.

    Mirrors ``packages/api-types/src/index.ts::Workspace`` shape — keep
    in lockstep on rename / removal.
    """

    org_id: str = Field(min_length=1)
    display_name: str = Field(min_length=1)
    slug: str = Field(min_length=1)
    role: str | None = None
    member_count: int = Field(ge=0)
    last_active_at: str | None = None
    is_current: bool


class WorkspaceListResponse(BaseModel):
    workspaces: list[Workspace]


def register_me_routes(app: FastAPI) -> None:
    """Attach the caller-scoped read endpoints to a backend FastAPI app."""

    @app.get(
        "/internal/v1/me/workspaces",
        response_model=WorkspaceListResponse,
        dependencies=[Depends(RequireScopes(RUNTIME_USE))],
    )
    def list_my_workspaces(
        request: Request,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> WorkspaceListResponse:
        # Same dev-vs-prod identity resolution as audit_export: with the
        # service token set, header identity wins; without it (dev only)
        # the query params govern. The facade always sends the headers,
        # so production callers never depend on the query path.
        identity = BackendServiceAuthenticator.internal_scoped_identity(
            request, org_id=org_id, user_id=user_id
        )

        store: IdentityStore = app.state.identity_store
        organization = store.get_organization(org_id=identity.org_id)
        if organization is None or organization.deleted_at is not None:
            # The session header points at an org we don't know about —
            # treat as 404 rather than leaking which orgs do/don't exist.
            raise HTTPException(status.HTTP_404_NOT_FOUND, "workspace_not_found")

        role_name = _resolve_role_name(
            store=store, org_id=identity.org_id, user_id=identity.user_id
        )
        members = store.list_members(org_id=identity.org_id)
        active_member_count = sum(1 for m in members if m.removed_at is None)

        user = store.get_user(org_id=identity.org_id, user_id=identity.user_id)
        last_active_at = _utc_iso(user.last_seen_at if user is not None else None)

        return WorkspaceListResponse(
            workspaces=[
                Workspace(
                    org_id=organization.org_id,
                    display_name=organization.display_name,
                    slug=organization.slug,
                    role=role_name,
                    member_count=active_member_count,
                    last_active_at=last_active_at,
                    is_current=True,
                )
            ]
        )


def _resolve_role_name(
    *, store: IdentityStore, org_id: str, user_id: str
) -> str | None:
    """Return the display name of the caller's primary role, or ``None``.

    A user can hold multiple role assignments; we surface the most
    recently granted one for the picker label. The picker text is purely
    informational — RBAC checks remain elsewhere.
    """

    assignments: tuple[RoleAssignmentRecord, ...] = store.list_role_assignments(
        org_id=org_id, user_id=user_id
    )
    if not assignments:
        return None
    primary = max(assignments, key=lambda r: r.granted_at)
    role = store.get_role(role_id=primary.role_id)
    if role is None:
        return None
    return role.display_name


def _utc_iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()
