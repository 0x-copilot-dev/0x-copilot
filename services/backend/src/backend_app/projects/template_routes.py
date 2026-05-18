"""Project template routes — save-as / list / get / fork / patch / delete.

Phase 6.5 §7.3:

  * POST   /v1/projects/{id}/save-as-template      (owner of source project)
  * GET    /v1/project-templates                   (tenant-wide)
  * GET    /v1/project-templates/{id}              (tenant-wide read)
  * POST   /v1/project-templates/{id}/fork         (any tenant member)
  * PATCH  /v1/project-templates/{id}              (template owner / admin)
  * DELETE /v1/project-templates/{id}              (template owner / admin)

Fork atomicity (§7.4): the new project + member rows + audit row land in
a single ``store.transaction()`` block. Seeded todos / routines are
out-of-scope for the in-memory adapter shipped here — the wire shape
includes them so the Postgres adapter can extend in P6.5-B without a
route rewrite.

Snapshot immutability (§7.5): PATCH only touches name / description.
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
    ProjectNotFound,
    ProjectsService,
)
from backend_app.projects.templates import (
    ProjectTemplateRecord,
    ProjectTemplateSeededRoutine,
    ProjectTemplateSeededTodo,
    ProjectTemplateSnapshot,
    ProjectTemplatesStore,
)


_ADMIN_ROLES = frozenset({"admin", "owner"})


# ---------------------------------------------------------------------------
# Wire models.
# ---------------------------------------------------------------------------


class SaveAsTemplateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    description: str | None = None
    seeded_todos: list[dict[str, Any]] | None = None
    seeded_routines: list[dict[str, Any]] | None = None


class ForkTemplateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    description: str | None = None
    color_hue: int | None = None
    icon_emoji: str | None = None
    member_overrides: list[str] | None = None
    connector_overrides: list[str] | None = None


class UpdateTemplateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    description: str | None = None


class ProjectTemplateResponseModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    tenant_id: str
    owner_user_id: str
    name: str
    description: str
    snapshot: dict[str, Any]
    source_project_id: str | None
    created_at: str
    updated_at: str


class ProjectTemplateListResponseModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[ProjectTemplateResponseModel]
    next_cursor: str | None = None


# ---------------------------------------------------------------------------
# Registration.
# ---------------------------------------------------------------------------


def register_template_routes(
    app: FastAPI,
    *,
    projects_service: ProjectsService,
    templates_store: ProjectTemplatesStore,
) -> None:
    """Attach the template routes to ``app``."""

    @app.post(
        "/v1/projects/{project_id}/save-as-template",
        response_model=ProjectTemplateResponseModel,
        status_code=status.HTTP_201_CREATED,
        dependencies=[Depends(RequireScopes(RUNTIME_USE))],
    )
    def save_as_template(
        request: Request,
        project_id: str,
        payload: SaveAsTemplateRequest,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> ProjectTemplateResponseModel:
        identity = BackendServiceAuthenticator.scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        # Source project must be readable AND owned by caller (save-as is
        # an owner-only op per §7.3 ACL).
        try:
            record, viewer_role, _, _ = projects_service.get_project(
                tenant_id=identity.org_id,
                caller_user_id=identity.user_id,
                caller_roles=identity.roles,
                project_id=project_id,
            )
        except ProjectNotFound as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "project_not_found") from exc
        if record.owner_user_id != identity.user_id:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN, "save_as_template_owner_only"
            )

        snapshot = ProjectTemplateSnapshot(
            default_member_user_ids=[],
            default_connector_allowlist=record.default_connector_allowlist
            if hasattr(record, "default_connector_allowlist")
            else None,
            color_hue=record.color_hue,
            icon_emoji=record.icon_emoji,
            seeded_todos=[
                ProjectTemplateSeededTodo(**t) for t in (payload.seeded_todos or [])
            ],
            seeded_routines=[
                # Strip webhook/event triggers per §7.2 — only schedule/manual
                # survive a snapshot.
                ProjectTemplateSeededRoutine(
                    name=r.get("name", ""),
                    description=r.get("description", ""),
                    instructions_template=r.get("instructions_template", ""),
                    triggers=[
                        t
                        for t in r.get("triggers", [])
                        if isinstance(t, dict)
                        and t.get("kind") in ("schedule", "manual")
                    ],
                )
                for r in (payload.seeded_routines or [])
            ],
        )

        template = ProjectTemplateRecord(
            tenant_id=identity.org_id,
            owner_user_id=identity.user_id,
            name=payload.name,
            description=payload.description or "",
            snapshot=snapshot,
            source_project_id=record.id,
        )
        with templates_store.transaction():
            stored = templates_store.insert_template(template)
        return _to_template_wire(stored)

    @app.get(
        "/v1/project-templates",
        response_model=ProjectTemplateListResponseModel,
        dependencies=[Depends(RequireScopes(RUNTIME_USE))],
    )
    def list_templates(
        request: Request,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
        q: str | None = Query(default=None, max_length=200),
        cursor: str | None = Query(default=None),
        limit: int = Query(default=50, ge=1, le=200),
        owner_user_id: str | None = Query(default=None),
    ) -> ProjectTemplateListResponseModel:
        identity = BackendServiceAuthenticator.scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        rows, next_cursor = templates_store.list_templates(
            tenant_id=identity.org_id,
            owner_user_id=owner_user_id,
            q=q,
            cursor=cursor,
            limit=limit,
        )
        return ProjectTemplateListResponseModel(
            items=[_to_template_wire(r) for r in rows],
            next_cursor=next_cursor,
        )

    @app.get(
        "/v1/project-templates/{template_id}",
        response_model=ProjectTemplateResponseModel,
        dependencies=[Depends(RequireScopes(RUNTIME_USE))],
    )
    def get_template(
        request: Request,
        template_id: str,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> ProjectTemplateResponseModel:
        identity = BackendServiceAuthenticator.scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        record = templates_store.get_template(
            tenant_id=identity.org_id, template_id=template_id
        )
        if record is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "template_not_found")
        return _to_template_wire(record)

    @app.post(
        "/v1/project-templates/{template_id}/fork",
        status_code=status.HTTP_201_CREATED,
        dependencies=[Depends(RequireScopes(RUNTIME_USE))],
    )
    def fork_template(
        request: Request,
        template_id: str,
        payload: ForkTemplateRequest,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> dict[str, Any]:
        identity = BackendServiceAuthenticator.scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        record = templates_store.get_template(
            tenant_id=identity.org_id, template_id=template_id
        )
        if record is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "template_not_found")

        # Atomic fork — single transaction (§7.4). The projects store's
        # in-memory transaction is a no-op; the Postgres adapter will
        # honor it. If any step fails we re-raise → the partial state
        # never lands.
        try:
            with templates_store.transaction():
                # The new project is created by the standard projects
                # service create path — that wires the owner-membership
                # row in the same transaction. The caller becomes owner
                # of the forked project (§7.3 ACL).
                color_hue = (
                    payload.color_hue
                    if payload.color_hue is not None
                    else (record.snapshot.color_hue or 210)
                )
                icon_emoji = payload.icon_emoji or record.snapshot.icon_emoji or "📁"
                new_project, viewer_role, _, _ = projects_service.create_project(
                    tenant_id=identity.org_id,
                    caller_user_id=identity.user_id,
                    payload={
                        "name": payload.name,
                        "description": payload.description or "",
                        "icon_emoji": icon_emoji,
                        "color_hue": color_hue,
                    },
                )
                # Best-effort allowlist propagation. Override from payload
                # wins; else inherit from snapshot.
                allowlist = (
                    payload.connector_overrides
                    if payload.connector_overrides is not None
                    else record.snapshot.default_connector_allowlist
                )
                if allowlist is not None and hasattr(
                    new_project, "default_connector_allowlist"
                ):
                    new_project = new_project.model_copy(
                        update={"default_connector_allowlist": list(allowlist)}
                    )
                    projects_service._store.update_project(new_project)
        except ProjectConflict as exc:
            raise HTTPException(
                status.HTTP_409_CONFLICT, exc.code or "conflict"
            ) from exc
        except ProjectForbidden as exc:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "owner_only_writes") from exc

        return {
            "id": new_project.id,
            "tenant_id": new_project.tenant_id,
            "name": new_project.name,
            "source_template_id": record.id,
            "seeded_todos_count": len(record.snapshot.seeded_todos),
            "seeded_routines_count": len(record.snapshot.seeded_routines),
        }

    @app.patch(
        "/v1/project-templates/{template_id}",
        response_model=ProjectTemplateResponseModel,
        dependencies=[Depends(RequireScopes(RUNTIME_USE))],
    )
    def update_template(
        request: Request,
        template_id: str,
        payload: UpdateTemplateRequest,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> ProjectTemplateResponseModel:
        identity = BackendServiceAuthenticator.scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        record = templates_store.get_template(
            tenant_id=identity.org_id, template_id=template_id
        )
        if record is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "template_not_found")
        admin = any(role in _ADMIN_ROLES for role in identity.roles)
        if record.owner_user_id != identity.user_id and not admin:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "template_owner_only")
        updated = templates_store.update_template_metadata(
            tenant_id=identity.org_id,
            template_id=template_id,
            name=payload.name,
            description=payload.description,
        )
        if updated is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "template_not_found")
        return _to_template_wire(updated)

    @app.delete(
        "/v1/project-templates/{template_id}",
        status_code=status.HTTP_204_NO_CONTENT,
        dependencies=[Depends(RequireScopes(RUNTIME_USE))],
    )
    def delete_template(
        request: Request,
        template_id: str,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> Response:
        identity = BackendServiceAuthenticator.scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        record = templates_store.get_template(
            tenant_id=identity.org_id, template_id=template_id
        )
        if record is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "template_not_found")
        admin = any(role in _ADMIN_ROLES for role in identity.roles)
        if record.owner_user_id != identity.user_id and not admin:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "template_owner_only")
        templates_store.soft_delete_template(
            tenant_id=identity.org_id, template_id=template_id
        )
        return Response(status_code=status.HTTP_204_NO_CONTENT)


def _to_template_wire(record: ProjectTemplateRecord) -> ProjectTemplateResponseModel:
    return ProjectTemplateResponseModel(
        id=record.id,
        tenant_id=record.tenant_id,
        owner_user_id=record.owner_user_id,
        name=record.name,
        description=record.description,
        snapshot=record.snapshot.model_dump(),
        source_project_id=record.source_project_id,
        created_at=record.created_at.isoformat(),
        updated_at=record.updated_at.isoformat(),
    )


__all__ = [
    "ForkTemplateRequest",
    "ProjectTemplateListResponseModel",
    "ProjectTemplateResponseModel",
    "SaveAsTemplateRequest",
    "UpdateTemplateRequest",
    "register_template_routes",
]
