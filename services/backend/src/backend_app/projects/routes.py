"""Public ``/v1/projects`` routes — Phase 6 P6-A1 CRUD + members + transfer.

Routes are presentation-only; ACL + audit + state-machine + invariants
live in :class:`ProjectsService`. The route layer is responsible for:

1. Identity scoping via :class:`BackendServiceAuthenticator.scoped_identity`.
2. Translating the service layer's exceptions to HTTP status codes:
   * :class:`ProjectNotFound`        → 404 (cross-audit §1.3: 404-not-403)
   * :class:`ProjectForbidden`       → 403
   * :class:`ProjectInvalidRequest`  → 400
   * :class:`ProjectConflict`        → 409 (state + duplicate-name + owner-cap)
3. Marshalling request / response bodies to / from the wire shapes
   declared in ``packages/api-types/src/projects.ts``.
4. Enforcing the membership-graph guard on
   ``filter[member_user_id]``: non-admin callers can filter only to
   themselves (projects-prd §4.4 — prevents harvesting other users'
   memberships).

The route layer uses the same ``filter[<axis>]=<value>`` repeatable
query pattern as inbox / todos / routines (cross-audit §1.5,
multi-value OR by default).
"""

from __future__ import annotations

from typing import Any

from enterprise_service_contracts.scopes import RUNTIME_USE
from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, ConfigDict

from backend_app.auth import BackendServiceAuthenticator
from backend_app.identity.rbac import RequireScopes
from backend_app.projects.service import (
    ProjectConflict,
    ProjectForbidden,
    ProjectInvalidRequest,
    ProjectNotFound,
    ProjectsService,
)
from backend_app.projects.store import (
    ProjectActivityCounts,
    ProjectMembershipRecord,
    ProjectRecord,
)

try:
    from backend_app.liveness.service import LivenessService  # noqa: F401
except Exception:  # pragma: no cover — circular guard
    LivenessService = None  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Request / response models (Python mirrors of api-types/src/projects.ts)
# ---------------------------------------------------------------------------


class CreateProjectRequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    description: str | None = None
    icon_emoji: str
    color_hue: int
    # Phase 6.5 §5 — optional on create.
    default_connector_allowlist: list[str] | None = None


class UpdateProjectRequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    description: str | None = None
    icon_emoji: str | None = None
    color_hue: int | None = None
    status: str | None = None
    # Phase 6.5 §5.3 — owner-only edit; null clears (= inherit owner default),
    # [] = explicit deny, [...] = allowlist of connector kinds.
    default_connector_allowlist: list[str] | None = None


class AddMemberRequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: str
    role: str


class ChangeRoleRequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: str


class TransferOwnershipRequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    new_owner_user_id: str
    previous_owner_new_role: str | None = None


class ForceTransferOwnershipRequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    new_owner_user_id: str
    previous_owner_new_role: str | None = None
    reason: str | None = None


class ProjectCountsModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    chats: int = 0
    todos_open: int = 0
    todos_done: int = 0
    inbox_items: int = 0
    library_items: int = 0
    routines_active: int = 0
    members: int = 0


class ProjectResponseModel(BaseModel):
    """Wire mirror of ``Project`` (packages/api-types/src/projects.ts)."""

    model_config = ConfigDict(extra="forbid")

    id: str
    tenant_id: str
    owner_user_id: str
    name: str
    description: str
    icon_emoji: str
    color_hue: int
    status: str
    archived_at: str | None = None
    created_at: str
    updated_at: str
    last_activity_at: str | None = None
    counts: ProjectCountsModel
    viewer_role: str | None = None
    viewer_starred: bool = False
    # Phase 6.5 §5 — connector allowlist (null = inherit owner default).
    default_connector_allowlist: list[str] | None = None


class ProjectListResponseModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    items: list[ProjectResponseModel]
    next_cursor: str | None = None


class ProjectMembershipResponseModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project_id: str
    user_id: str
    role: str
    added_at: str
    added_by: str


class ProjectMembershipListResponseModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    items: list[ProjectMembershipResponseModel]
    next_cursor: str | None = None


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def register_projects_routes(
    app: FastAPI,
    *,
    service: ProjectsService,
    liveness_service: "LivenessService | None" = None,
) -> None:
    """Attach ``/v1/projects`` routes to ``app``.

    ``liveness_service`` is the optional Phase 6.5 §3 aggregator used by
    the archive endpoint (§6.1). When provided, ``DELETE /v1/projects/{id}``
    pre-checks liveness and returns 409 with the full ``LivenessReport``
    body if the project has live work. When omitted (legacy tests), the
    archive endpoint behaves as Phase 6 shipped (soft-delete + 204).
    """

    @app.get(
        "/v1/projects",
        response_model=ProjectListResponseModel,
        dependencies=[Depends(RequireScopes(RUNTIME_USE))],
    )
    def list_projects(
        request: Request,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
        q: str | None = Query(default=None, max_length=200),
        cursor: str | None = Query(default=None),
        limit: int = Query(default=50, ge=1, le=200),
        sort: str = Query(default="updated_at:desc"),
    ) -> ProjectListResponseModel:
        identity = BackendServiceAuthenticator.scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        statuses = _parse_repeatable_filter(request, "status") or None
        owner_filter = _parse_repeatable_filter(request, "owner_user_id")
        member_filter = _parse_repeatable_filter(request, "member_user_id")
        starred_filter = (
            request.query_params.get("filter[starred]", "").lower() == "true"
        )

        # Membership-graph guard (projects-prd §4.4).
        admin = any(role in {"admin", "owner"} for role in identity.roles)
        scoped_member_filter: str | None = None
        if member_filter:
            # Multi-value member filter not supported in P6-A1 — collapse
            # to the first value; the wire shape accepts repeats per
            # cross-audit §1.5 but a non-admin can only legitimately
            # filter to themselves.
            target = member_filter[0]
            if target == "me":
                target = identity.user_id
            if not admin and target != identity.user_id:
                raise HTTPException(
                    status.HTTP_403_FORBIDDEN,
                    "filter_member_user_id_admin_only",
                )
            scoped_member_filter = target

        scoped_owner_filter: str | None = None
        if owner_filter:
            scoped_owner_filter = (
                identity.user_id if owner_filter[0] == "me" else owner_filter[0]
            )

        try:
            enriched, next_cursor = service.list_projects(
                tenant_id=identity.org_id,
                caller_user_id=identity.user_id,
                caller_roles=identity.roles,
                statuses=statuses,
                owner_user_id=scoped_owner_filter,
                member_user_id=scoped_member_filter,
                q=q,
                starred=starred_filter,
                cursor=cursor,
                limit=limit,
                sort=sort,
            )
        except ProjectForbidden as exc:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN, str(exc) or "forbidden"
            ) from exc

        return ProjectListResponseModel(
            items=[_to_wire(*row) for row in enriched],
            next_cursor=next_cursor,
        )

    @app.get(
        "/v1/projects/{project_id}",
        response_model=ProjectResponseModel,
        dependencies=[Depends(RequireScopes(RUNTIME_USE))],
    )
    def get_project(
        request: Request,
        project_id: str,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> ProjectResponseModel:
        identity = BackendServiceAuthenticator.scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        try:
            record, viewer_role, starred, counts = service.get_project(
                tenant_id=identity.org_id,
                caller_user_id=identity.user_id,
                caller_roles=identity.roles,
                project_id=project_id,
            )
        except ProjectNotFound as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "project_not_found") from exc
        return _to_wire(record, viewer_role, starred, counts)

    @app.post(
        "/v1/projects",
        response_model=ProjectResponseModel,
        status_code=status.HTTP_201_CREATED,
        dependencies=[Depends(RequireScopes(RUNTIME_USE))],
    )
    def create_project(
        request: Request,
        payload: CreateProjectRequestModel,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> ProjectResponseModel:
        identity = BackendServiceAuthenticator.scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        try:
            record, viewer_role, starred, counts = service.create_project(
                tenant_id=identity.org_id,
                caller_user_id=identity.user_id,
                payload=payload.model_dump(exclude_none=True),
            )
        except ProjectConflict as exc:
            raise HTTPException(
                status.HTTP_409_CONFLICT, exc.code or "conflict"
            ) from exc
        except ProjectInvalidRequest as exc:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, str(exc) or "invalid_request"
            ) from exc
        return _to_wire(record, viewer_role, starred, counts)

    @app.patch(
        "/v1/projects/{project_id}",
        response_model=ProjectResponseModel,
        dependencies=[Depends(RequireScopes(RUNTIME_USE))],
    )
    def update_project(
        request: Request,
        project_id: str,
        payload: UpdateProjectRequestModel,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> ProjectResponseModel:
        identity = BackendServiceAuthenticator.scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        patch_dict = payload.model_dump(exclude_unset=True)
        try:
            record = service.update_project(
                tenant_id=identity.org_id,
                caller_user_id=identity.user_id,
                caller_roles=identity.roles,
                project_id=project_id,
                patch=patch_dict,
            )
        except ProjectNotFound as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "project_not_found") from exc
        except ProjectForbidden as exc:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "owner_only_writes") from exc
        except ProjectConflict as exc:
            raise HTTPException(
                status.HTTP_409_CONFLICT, exc.code or "conflict"
            ) from exc
        except ProjectInvalidRequest as exc:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, str(exc) or "invalid_request"
            ) from exc
        # Re-fetch the caller-relative view (viewer_role hasn't changed
        # since the caller is the owner; counts may have moved if a
        # background task ran, but in this CRUD path we keep the cheap
        # synthesized counts).
        viewer_role = "owner" if record.owner_user_id == identity.user_id else None
        starred = False  # PATCH doesn't return star; FE refetches if needed.
        counts = ProjectActivityCounts(tenant_id=identity.org_id, project_id=record.id)
        return _to_wire(record, viewer_role, starred, counts)

    @app.delete(
        "/v1/projects/{project_id}",
        dependencies=[Depends(RequireScopes(RUNTIME_USE))],
    )
    async def delete_project(
        request: Request,
        project_id: str,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> Response:
        identity = BackendServiceAuthenticator.scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        # Phase 6.5 §6.1 — pre-check liveness. If any source reports the
        # project as alive, return 409 with the full LivenessReport body
        # so the FE archive modal can render the inline detail.
        if liveness_service is not None:
            report = await liveness_service.is_project_alive(
                tenant_id=identity.org_id,
                project_id=project_id,
            )
            if report.is_alive:
                raise HTTPException(
                    status.HTTP_409_CONFLICT,
                    {
                        "error": "project_archive_blocked_live_work",
                        "message": (
                            "Cannot archive project with active runs / "
                            "routines / approvals / inbox items."
                        ),
                        "liveness": report.model_dump(),
                    },
                )
        try:
            service.delete_project(
                tenant_id=identity.org_id,
                caller_user_id=identity.user_id,
                caller_roles=identity.roles,
                project_id=project_id,
            )
        except ProjectNotFound as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "project_not_found") from exc
        except ProjectForbidden as exc:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "owner_only_writes") from exc
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @app.post(
        "/v1/projects/{project_id}/restore",
        response_model=ProjectResponseModel,
        dependencies=[Depends(RequireScopes(RUNTIME_USE))],
    )
    def restore_project(
        request: Request,
        project_id: str,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> ProjectResponseModel:
        identity = BackendServiceAuthenticator.scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        try:
            record = service.restore_project(
                tenant_id=identity.org_id,
                caller_user_id=identity.user_id,
                project_id=project_id,
            )
        except ProjectNotFound as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "project_not_found") from exc
        viewer_role = "owner" if record.owner_user_id == identity.user_id else None
        counts = ProjectActivityCounts(tenant_id=identity.org_id, project_id=record.id)
        return _to_wire(record, viewer_role, False, counts)

    # -- members ------------------------------------------------------

    @app.get(
        "/v1/projects/{project_id}/members",
        response_model=ProjectMembershipListResponseModel,
        dependencies=[Depends(RequireScopes(RUNTIME_USE))],
    )
    def list_members(
        request: Request,
        project_id: str,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
        cursor: str | None = Query(default=None),
        limit: int = Query(default=50, ge=1, le=500),
    ) -> ProjectMembershipListResponseModel:
        identity = BackendServiceAuthenticator.scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        try:
            rows, next_cursor = service.list_members(
                tenant_id=identity.org_id,
                caller_user_id=identity.user_id,
                caller_roles=identity.roles,
                project_id=project_id,
                cursor=cursor,
                limit=limit,
            )
        except ProjectNotFound as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "project_not_found") from exc
        return ProjectMembershipListResponseModel(
            items=[_membership_to_wire(r) for r in rows],
            next_cursor=next_cursor,
        )

    @app.post(
        "/v1/projects/{project_id}/members",
        response_model=ProjectMembershipResponseModel,
        status_code=status.HTTP_201_CREATED,
        dependencies=[Depends(RequireScopes(RUNTIME_USE))],
    )
    def add_member(
        request: Request,
        project_id: str,
        payload: AddMemberRequestModel,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> ProjectMembershipResponseModel:
        identity = BackendServiceAuthenticator.scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        try:
            row = service.add_member(
                tenant_id=identity.org_id,
                caller_user_id=identity.user_id,
                caller_roles=identity.roles,
                project_id=project_id,
                target_user_id=payload.user_id,
                role=payload.role,
            )
        except ProjectNotFound as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "project_not_found") from exc
        except ProjectForbidden as exc:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "owner_only_writes") from exc
        except ProjectConflict as exc:
            raise HTTPException(
                status.HTTP_409_CONFLICT, exc.code or "conflict"
            ) from exc
        except ProjectInvalidRequest as exc:
            # cross-tenant user / role-invalid → 422 per projects-prd §3.5.2
            code = str(exc) or "invalid_request"
            http_code = (
                status.HTTP_422_UNPROCESSABLE_ENTITY
                if code == "cross_tenant_user"
                else status.HTTP_400_BAD_REQUEST
            )
            raise HTTPException(http_code, code) from exc
        return _membership_to_wire(row)

    @app.patch(
        "/v1/projects/{project_id}/members/{member_user_id}",
        response_model=ProjectMembershipResponseModel,
        dependencies=[Depends(RequireScopes(RUNTIME_USE))],
    )
    def change_member_role(
        request: Request,
        project_id: str,
        member_user_id: str,
        payload: ChangeRoleRequestModel,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> ProjectMembershipResponseModel:
        identity = BackendServiceAuthenticator.scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        try:
            row = service.change_member_role(
                tenant_id=identity.org_id,
                caller_user_id=identity.user_id,
                caller_roles=identity.roles,
                project_id=project_id,
                target_user_id=member_user_id,
                role=payload.role,
            )
        except ProjectNotFound as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "project_not_found") from exc
        except ProjectForbidden as exc:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "owner_only_writes") from exc
        except ProjectConflict as exc:
            raise HTTPException(
                status.HTTP_409_CONFLICT, exc.code or "conflict"
            ) from exc
        except ProjectInvalidRequest as exc:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, str(exc) or "invalid_request"
            ) from exc
        return _membership_to_wire(row)

    @app.delete(
        "/v1/projects/{project_id}/members/{member_user_id}",
        status_code=status.HTTP_204_NO_CONTENT,
        dependencies=[Depends(RequireScopes(RUNTIME_USE))],
    )
    def remove_member(
        request: Request,
        project_id: str,
        member_user_id: str,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> Response:
        identity = BackendServiceAuthenticator.scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        target = member_user_id
        if target == "me":
            target = identity.user_id
        try:
            service.remove_member(
                tenant_id=identity.org_id,
                caller_user_id=identity.user_id,
                caller_roles=identity.roles,
                project_id=project_id,
                target_user_id=target,
            )
        except ProjectNotFound as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "project_not_found") from exc
        except ProjectForbidden as exc:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "owner_only_writes") from exc
        except ProjectConflict as exc:
            raise HTTPException(
                status.HTTP_409_CONFLICT, exc.code or "conflict"
            ) from exc
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    # -- transfer -----------------------------------------------------

    @app.post(
        "/v1/projects/{project_id}/transfer",
        response_model=ProjectResponseModel,
        dependencies=[Depends(RequireScopes(RUNTIME_USE))],
    )
    def transfer_ownership(
        request: Request,
        project_id: str,
        payload: TransferOwnershipRequestModel,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> ProjectResponseModel:
        identity = BackendServiceAuthenticator.scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        try:
            record = service.transfer_ownership(
                tenant_id=identity.org_id,
                caller_user_id=identity.user_id,
                caller_roles=identity.roles,
                project_id=project_id,
                new_owner_user_id=payload.new_owner_user_id,
                previous_owner_new_role=(payload.previous_owner_new_role or "editor"),
            )
        except ProjectNotFound as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "project_not_found") from exc
        except ProjectForbidden as exc:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "owner_only_writes") from exc
        except ProjectConflict as exc:
            raise HTTPException(
                status.HTTP_409_CONFLICT, exc.code or "conflict"
            ) from exc
        except ProjectInvalidRequest as exc:
            code = str(exc) or "invalid_request"
            http_code = (
                status.HTTP_422_UNPROCESSABLE_ENTITY
                if code in {"new_owner_not_member", "new_owner_is_current_owner"}
                else status.HTTP_400_BAD_REQUEST
            )
            raise HTTPException(http_code, code) from exc
        viewer_role = "owner" if record.owner_user_id == identity.user_id else "editor"
        counts = ProjectActivityCounts(tenant_id=identity.org_id, project_id=record.id)
        return _to_wire(record, viewer_role, False, counts)

    # Phase 6 product decision (user override 2026-05-18): admin force-transfer
    # is DEFERRED — flagged as a security hazard. Route registration commented
    # out below; service layer keeps `force_transfer_ownership` for future use
    # (e.g., a per-tenant SAML-claim-based admin path). To re-enable, uncomment
    # the @app.post decorator. Calls to `/v1/admin/projects/{id}/force-transfer`
    # return 404 today.
    #
    # @app.post(
    #     "/v1/admin/projects/{project_id}/force-transfer",
    #     response_model=ProjectResponseModel,
    #     dependencies=[Depends(RequireScopes(RUNTIME_USE))],
    # )
    def force_transfer_ownership(
        request: Request,
        project_id: str,
        payload: ForceTransferOwnershipRequestModel,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> ProjectResponseModel:
        """Admin force-transfer (DEFERRED 2026-05-18; route unregistered).
        Caller MUST be a tenant admin; non-admins 403."""

        identity = BackendServiceAuthenticator.scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        try:
            record = service.force_transfer_ownership(
                tenant_id=identity.org_id,
                caller_user_id=identity.user_id,
                caller_roles=identity.roles,
                project_id=project_id,
                new_owner_user_id=payload.new_owner_user_id,
                previous_owner_new_role=(payload.previous_owner_new_role or "editor"),
                reason=payload.reason,
            )
        except ProjectForbidden as exc:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN, str(exc) or "admin_required"
            ) from exc
        except ProjectNotFound as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "project_not_found") from exc
        except ProjectConflict as exc:
            raise HTTPException(
                status.HTTP_409_CONFLICT, exc.code or "conflict"
            ) from exc
        except ProjectInvalidRequest as exc:
            code = str(exc) or "invalid_request"
            http_code = (
                status.HTTP_422_UNPROCESSABLE_ENTITY
                if code in {"new_owner_not_member", "new_owner_is_current_owner"}
                else status.HTTP_400_BAD_REQUEST
            )
            raise HTTPException(http_code, code) from exc
        viewer_role = "owner" if record.owner_user_id == identity.user_id else None
        counts = ProjectActivityCounts(tenant_id=identity.org_id, project_id=record.id)
        return _to_wire(record, viewer_role, False, counts)

    # -- stars --------------------------------------------------------

    @app.post(
        "/v1/projects/{project_id}/star",
        status_code=status.HTTP_204_NO_CONTENT,
        dependencies=[Depends(RequireScopes(RUNTIME_USE))],
    )
    def star_project(
        request: Request,
        project_id: str,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> Response:
        identity = BackendServiceAuthenticator.scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        try:
            service.star(
                tenant_id=identity.org_id,
                caller_user_id=identity.user_id,
                caller_roles=identity.roles,
                project_id=project_id,
            )
        except ProjectNotFound as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "project_not_found") from exc
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @app.post(
        "/v1/projects/{project_id}/unstar",
        status_code=status.HTTP_204_NO_CONTENT,
        dependencies=[Depends(RequireScopes(RUNTIME_USE))],
    )
    def unstar_project(
        request: Request,
        project_id: str,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> Response:
        identity = BackendServiceAuthenticator.scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        try:
            service.unstar(
                tenant_id=identity.org_id,
                caller_user_id=identity.user_id,
                caller_roles=identity.roles,
                project_id=project_id,
            )
        except ProjectNotFound as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "project_not_found") from exc
        return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_repeatable_filter(request: Request, axis: str) -> tuple[str, ...]:
    """Extract the OR-multi-value ``filter[<axis>]`` query params."""

    key = f"filter[{axis}]"
    return tuple(v for v in request.query_params.getlist(key) if v)


def _to_wire(
    record: ProjectRecord,
    viewer_role: Any,
    viewer_starred: bool,
    counts: ProjectActivityCounts,
) -> ProjectResponseModel:
    """Marshal a :class:`ProjectRecord` plus caller-relative bundle into
    the wire response shape."""

    return ProjectResponseModel(
        id=record.id,
        tenant_id=record.tenant_id,
        owner_user_id=record.owner_user_id,
        name=record.name,
        description=record.description,
        icon_emoji=record.icon_emoji,
        color_hue=record.color_hue,
        status=record.status,
        archived_at=record.archived_at.isoformat() if record.archived_at else None,
        created_at=record.created_at.isoformat(),
        updated_at=record.updated_at.isoformat(),
        last_activity_at=(
            record.last_activity_at.isoformat() if record.last_activity_at else None
        ),
        counts=ProjectCountsModel(
            chats=counts.chats,
            todos_open=counts.todos_open,
            todos_done=counts.todos_done,
            inbox_items=counts.inbox_items,
            library_items=counts.library_items,
            routines_active=counts.routines_active,
            members=counts.members,
        ),
        viewer_role=viewer_role,
        viewer_starred=viewer_starred,
        default_connector_allowlist=getattr(
            record, "default_connector_allowlist", None
        ),
    )


def _membership_to_wire(
    record: ProjectMembershipRecord,
) -> ProjectMembershipResponseModel:
    return ProjectMembershipResponseModel(
        project_id=record.project_id,
        user_id=record.user_id,
        role=record.role,
        added_at=record.added_at.isoformat(),
        added_by=record.added_by,
    )


__all__ = [
    "AddMemberRequestModel",
    "ChangeRoleRequestModel",
    "CreateProjectRequestModel",
    "ForceTransferOwnershipRequestModel",
    "ProjectCountsModel",
    "ProjectListResponseModel",
    "ProjectMembershipListResponseModel",
    "ProjectMembershipResponseModel",
    "ProjectResponseModel",
    "TransferOwnershipRequestModel",
    "UpdateProjectRequestModel",
    "register_projects_routes",
]
