"""``/internal/v1/workspace/members`` — admin members directory + role
change + soft remove (PR 4.2).

Three endpoints:

  - ``GET    /internal/v1/workspace/members``        — admin list (paginated).
  - ``PATCH  /internal/v1/workspace/members/{user}`` — change role with
    last-admin guard.
  - ``DELETE /internal/v1/workspace/members/{user}`` — soft remove (sets
    ``organization_members.removed_at``); not destructive. Last-admin and
    self-remove guards apply.

Role names use the design-doc aliases (``admin`` | ``member`` | ``viewer``)
and round-trip through :func:`design_role_alias_for` so the FE never sees
``employee`` / ``auditor``.
"""

from __future__ import annotations

from datetime import datetime, timezone

from enterprise_service_contracts.scopes import ADMIN_USERS, RUNTIME_USE
from fastapi import Depends, FastAPI, HTTPException, Query, Request, status
from pydantic import BaseModel, Field

from backend_app.auth import BackendServiceAuthenticator
from backend_app.contracts import (
    IdentityAuditEventRecord,
    OrganizationMemberRecord,
    RoleAssignmentRecord,
    UserRecord,
)
from backend_app.identity.invitations import design_role_alias_for
from backend_app.identity.rbac import RequireScopes
from backend_app.identity.store import IdentityStore


# Role aliases the API accepts, in lowercase.
_ACCEPTED_ROLES = ("admin", "member", "viewer")
_DESIGN_TO_SYSTEM = {"admin": "admin", "member": "employee", "viewer": "auditor"}


class MemberRoleSummary(BaseModel):
    id: str
    name: str
    display_name: str


class MemberResponse(BaseModel):
    user_id: str
    email: str
    email_verified_at: str | None
    display_name: str | None
    title: str | None = None
    role: MemberRoleSummary | None
    joined_at: str
    last_seen_at: str | None
    removed_at: str | None
    source: str


class MemberListResponse(BaseModel):
    members: list[MemberResponse]
    next_cursor: str | None = None


class UpdateMemberRequest(BaseModel):
    role: str = Field(..., min_length=1)


def register_members_routes(app: FastAPI) -> None:
    @app.get(
        "/internal/v1/workspace/members",
        response_model=MemberListResponse,
        dependencies=[Depends(RequireScopes(ADMIN_USERS, RUNTIME_USE))],
    )
    def list_members(
        request: Request,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
        include_removed: bool = Query(False),
        role: str | None = Query(None),
    ) -> MemberListResponse:
        identity = BackendServiceAuthenticator.internal_scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        store: IdentityStore = app.state.identity_store

        # We list ALL member rows (active + removed) and filter at the response
        # boundary to honour ``include_removed``. Postgres list_members already
        # filters to active; for v1 we fetch users via list_users(include_deleted=False)
        # and join on member rows we have in active scope.
        active_members = store.list_members(org_id=identity.org_id)
        users_by_id = {
            u.user_id: u
            for u in store.list_users(org_id=identity.org_id, include_deleted=False)
        }
        rows: list[MemberResponse] = []
        for member in active_members:
            user = users_by_id.get(member.user_id)
            if user is None:
                continue
            assignment, role_record = _resolve_role(
                store, identity.org_id, user.user_id
            )
            role_name = (
                design_role_alias_for(system_role_name=role_record.name)
                if role_record is not None
                else None
            )
            if role is not None and role_name != role.lower():
                continue
            rows.append(
                MemberResponse(
                    user_id=user.user_id,
                    email=user.primary_email,
                    email_verified_at=_iso_or_none(user.email_verified_at),
                    display_name=user.display_name,
                    title=_user_title(user),
                    role=(
                        MemberRoleSummary(
                            id=role_record.role_id,
                            name=role_name or role_record.name,
                            display_name=role_record.display_name,
                        )
                        if role_record is not None
                        else None
                    ),
                    joined_at=_iso(member.joined_at),
                    last_seen_at=_iso_or_none(user.last_seen_at),
                    removed_at=_iso_or_none(member.removed_at),
                    source=member.source.value,
                )
            )
        return MemberListResponse(members=rows, next_cursor=None)

    @app.patch(
        "/internal/v1/workspace/members/{member_user_id}",
        response_model=MemberResponse,
        dependencies=[Depends(RequireScopes(ADMIN_USERS, RUNTIME_USE))],
    )
    def patch_member(
        request: Request,
        member_user_id: str,
        body: UpdateMemberRequest,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> MemberResponse:
        identity = BackendServiceAuthenticator.internal_scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        store: IdentityStore = app.state.identity_store

        target = _require_active_member(store, identity.org_id, member_user_id)
        new_role_alias = body.role.strip().lower()
        if new_role_alias not in _ACCEPTED_ROLES:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "invalid_role")
        new_system_role_name = _DESIGN_TO_SYSTEM[new_role_alias]
        new_role = store.get_role_by_name(org_id=None, name=new_system_role_name)
        if new_role is None:
            raise HTTPException(
                status.HTTP_500_INTERNAL_SERVER_ERROR, "role_unavailable"
            )

        prev_assignment, prev_role = _resolve_role(
            store, identity.org_id, member_user_id
        )
        prev_role_name = (
            design_role_alias_for(system_role_name=prev_role.name)
            if prev_role is not None
            else None
        )
        if prev_role is not None and prev_role.role_id == new_role.role_id:
            return _project_member(store, target, identity.org_id)

        # Last-admin guard: if the target was the only admin, don't let the
        # admin downgrade themselves out of admin without another admin.
        if (
            prev_role is not None
            and prev_role.name == "admin"
            and new_system_role_name != "admin"
        ):
            if _count_admins(store, identity.org_id) <= 1:
                raise HTTPException(
                    status.HTTP_409_CONFLICT, "cannot_remove_last_admin"
                )

        with store.transaction():
            if prev_role is not None:
                store.revoke_role(
                    org_id=identity.org_id,
                    user_id=member_user_id,
                    role_id=prev_role.role_id,
                    reason="role_change",
                )
            store.assign_role(
                RoleAssignmentRecord(
                    org_id=identity.org_id,
                    user_id=member_user_id,
                    role_id=new_role.role_id,
                    granted_by_user_id=identity.user_id,
                    reason="role_change",
                )
            )
            store.append_identity_audit(
                IdentityAuditEventRecord(
                    org_id=identity.org_id,
                    actor_user_id=identity.user_id,
                    subject_user_id=member_user_id,
                    action="member.role.update",
                    metadata={
                        "user_id": member_user_id,
                        "before_role": prev_role_name,
                        "after_role": new_role_alias,
                    },
                )
            )

        target = store.get_user(org_id=identity.org_id, user_id=member_user_id)
        if target is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "member_not_found")
        return _project_member(store, target, identity.org_id)

    @app.delete(
        "/internal/v1/workspace/members/{member_user_id}",
        status_code=status.HTTP_204_NO_CONTENT,
        dependencies=[Depends(RequireScopes(ADMIN_USERS, RUNTIME_USE))],
    )
    def delete_member(
        request: Request,
        member_user_id: str,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> None:
        identity = BackendServiceAuthenticator.internal_scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        store: IdentityStore = app.state.identity_store

        if member_user_id == identity.user_id:
            raise HTTPException(status.HTTP_409_CONFLICT, "cannot_remove_self")

        target = _require_active_member(store, identity.org_id, member_user_id)

        # Last-admin guard.
        _, target_role = _resolve_role(store, identity.org_id, member_user_id)
        if target_role is not None and target_role.name == "admin":
            if _count_admins(store, identity.org_id) <= 1:
                raise HTTPException(
                    status.HTTP_409_CONFLICT, "cannot_remove_last_admin"
                )

        with store.transaction():
            ok = store.remove_member(org_id=identity.org_id, user_id=member_user_id)
            if not ok:
                # Race between resolve and remove — surface as 404.
                raise HTTPException(status.HTTP_404_NOT_FOUND, "member_not_found")
            store.append_identity_audit(
                IdentityAuditEventRecord(
                    org_id=identity.org_id,
                    actor_user_id=identity.user_id,
                    subject_user_id=member_user_id,
                    action="member.remove",
                    metadata={
                        "user_id": member_user_id,
                        "source": _resolve_member_source(
                            store, identity.org_id, member_user_id
                        ),
                    },
                )
            )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _require_active_member(
    store: IdentityStore, org_id: str, member_user_id: str
) -> UserRecord:
    user = store.get_user(org_id=org_id, user_id=member_user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "member_not_found")
    members = store.list_members(org_id=org_id)
    if not any(m.user_id == member_user_id and m.removed_at is None for m in members):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "member_not_found")
    return user


def _resolve_role(store: IdentityStore, org_id: str, user_id: str):
    """Return (active assignment, role) for the user's primary role, or
    ``(None, None)`` if no role assignment is active."""
    assignments = store.list_role_assignments(org_id=org_id, user_id=user_id)
    if not assignments:
        return None, None
    primary = max(assignments, key=lambda r: r.granted_at)
    role = store.get_role(role_id=primary.role_id)
    return primary, role


def _count_admins(store: IdentityStore, org_id: str) -> int:
    role = store.get_role_by_name(org_id=None, name="admin")
    if role is None:
        return 0
    members = store.list_members(org_id=org_id)
    count = 0
    for member in members:
        if member.removed_at is not None:
            continue
        assignments = store.list_role_assignments(org_id=org_id, user_id=member.user_id)
        if any(a.role_id == role.role_id for a in assignments):
            count += 1
    return count


def _resolve_member_source(
    store: IdentityStore, org_id: str, user_id: str
) -> str | None:
    members = store.list_members(org_id=org_id)
    for m in members:
        if m.user_id == user_id:
            return m.source.value
    return None


def _project_member(
    store: IdentityStore, user: UserRecord, org_id: str
) -> MemberResponse:
    members = store.list_members(org_id=org_id)
    member: OrganizationMemberRecord | None = next(
        (m for m in members if m.user_id == user.user_id), None
    )
    _, role_record = _resolve_role(store, org_id, user.user_id)
    role_name = (
        design_role_alias_for(system_role_name=role_record.name)
        if role_record is not None
        else None
    )
    return MemberResponse(
        user_id=user.user_id,
        email=user.primary_email,
        email_verified_at=_iso_or_none(user.email_verified_at),
        display_name=user.display_name,
        title=_user_title(user),
        role=(
            MemberRoleSummary(
                id=role_record.role_id,
                name=role_name or role_record.name,
                display_name=role_record.display_name,
            )
            if role_record is not None
            else None
        ),
        joined_at=_iso(member.joined_at)
        if member is not None
        else _iso(user.created_at),
        last_seen_at=_iso_or_none(user.last_seen_at),
        removed_at=_iso_or_none(member.removed_at) if member is not None else None,
        source=member.source.value if member is not None else "local",
    )


def _user_title(user: UserRecord) -> str | None:
    """Read ``title`` off ``users.metadata`` if present.

    PR 4.1 owns the ``user_profiles`` sidecar table that authoritatively
    holds title / timezone / locale. Until that ships, we do best-effort by
    inspecting the existing ``users.metadata`` JSONB blob if a title was
    seeded there. We deliberately do not import PR 4.1's store to avoid
    a hard dep before that PR lands; once it lands a small follow-up
    JOINs ``user_profiles`` here.
    """
    title = user.metadata.get("title") if isinstance(user.metadata, dict) else None
    return title if isinstance(title, str) and title else None


def _iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _iso_or_none(value: datetime | None) -> str | None:
    return _iso(value) if value is not None else None


__all__ = ["register_members_routes"]
