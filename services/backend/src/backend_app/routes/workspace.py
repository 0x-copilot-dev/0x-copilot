"""``/internal/v1/workspace`` — workspace branding read + write (PR 4.2).

Two endpoints:

  - ``GET  /internal/v1/workspace`` — any authenticated member can read.
  - ``PATCH /internal/v1/workspace`` — admin only; merge-patch for
    ``display_name``, ``slug`` (with uniqueness check), ``metadata.logo_url``.

The workspace-deletion danger-zone surface is intentionally a 501 stub in
v1 (cascade scope is large and gated). The audit row is still written so
operators can see who's asking.

Default model / connectors / retention controls come from PR 1.6's
``/v1/agent/workspace/defaults`` — this PR does not duplicate them.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from copilot_service_contracts.scopes import ADMIN_USERS, RUNTIME_USE
from fastapi import Depends, FastAPI, HTTPException, Query, Request, status
from pydantic import BaseModel, Field

from backend_app.auth import BackendServiceAuthenticator
from backend_app.contracts import (
    IdentityAuditEventRecord,
    OrganizationRecord,
)
from backend_app.identity.rbac import RequireScopes
from backend_app.identity.store import IdentityStore


_SLUG_PATTERN = re.compile(r"^[a-z0-9]([a-z0-9-]{1,38}[a-z0-9])$")


class WorkspaceResponse(BaseModel):
    """Public workspace read shape. Mirrors
    ``packages/api-types/src/index.ts::Workspace``."""

    org_id: str
    display_name: str
    slug: str
    deployment_kind: str
    status: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: str


class UpdateWorkspaceRequest(BaseModel):
    """RFC 7396 merge-patch — fields supplied are written, fields omitted
    are untouched. ``metadata`` is treated as a deep-merge per its key
    (we currently model ``logo_url`` only)."""

    display_name: str | None = None
    slug: str | None = None
    metadata: dict[str, Any] | None = None


def register_workspace_routes(app: FastAPI) -> None:
    @app.get(
        "/internal/v1/workspace",
        response_model=WorkspaceResponse,
        dependencies=[Depends(RequireScopes(RUNTIME_USE))],
    )
    def get_workspace(
        request: Request,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> WorkspaceResponse:
        identity = BackendServiceAuthenticator.internal_scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        store: IdentityStore = app.state.identity_store
        org = store.get_organization(org_id=identity.org_id)
        if org is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "workspace_not_found")
        return _project(org)

    @app.patch(
        "/internal/v1/workspace",
        response_model=WorkspaceResponse,
        dependencies=[Depends(RequireScopes(ADMIN_USERS, RUNTIME_USE))],
    )
    def patch_workspace(
        request: Request,
        body: UpdateWorkspaceRequest,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> WorkspaceResponse:
        identity = BackendServiceAuthenticator.internal_scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        store: IdentityStore = app.state.identity_store
        org = store.get_organization(org_id=identity.org_id)
        if org is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "workspace_not_found")

        diff_keys: list[str] = []
        update: dict[str, Any] = {}

        if body.display_name is not None:
            normalized = body.display_name.strip()
            if not normalized:
                raise HTTPException(
                    status.HTTP_422_UNPROCESSABLE_ENTITY, "invalid_display_name"
                )
            if normalized != org.display_name:
                update["display_name"] = normalized
                diff_keys.append("display_name")

        if body.slug is not None:
            normalized_slug = body.slug.strip().lower()
            if not _SLUG_PATTERN.fullmatch(normalized_slug):
                raise HTTPException(
                    status.HTTP_422_UNPROCESSABLE_ENTITY, "invalid_slug"
                )
            if normalized_slug != org.slug:
                conflict = store.get_organization_by_slug(slug=normalized_slug)
                if conflict is not None and conflict.org_id != org.org_id:
                    raise HTTPException(
                        status.HTTP_422_UNPROCESSABLE_ENTITY, "slug_taken"
                    )
                update["slug"] = normalized_slug
                diff_keys.append("slug")

        if body.metadata is not None:
            merged = dict(org.metadata)
            for key, value in body.metadata.items():
                if value is None:
                    merged.pop(key, None)
                else:
                    merged[key] = value
            if merged != org.metadata:
                update["metadata"] = merged
                diff_keys.append("metadata")

        if not update:
            return _project(org)

        before = {
            "display_name": org.display_name,
            "slug": org.slug,
            "metadata": org.metadata,
        }
        with store.transaction():
            updated = store.update_organization(org.model_copy(update=update))
            store.append_identity_audit(
                IdentityAuditEventRecord(
                    org_id=identity.org_id,
                    actor_user_id=identity.user_id,
                    action="workspace.update",
                    metadata={
                        "before": before,
                        "after": {
                            "display_name": updated.display_name,
                            "slug": updated.slug,
                            "metadata": updated.metadata,
                        },
                        "diff_keys": diff_keys,
                    },
                )
            )
        return _project(updated)

    @app.delete(
        "/internal/v1/workspace",
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        dependencies=[Depends(RequireScopes(ADMIN_USERS, RUNTIME_USE))],
    )
    def delete_workspace(
        request: Request,
        confirm_slug: str = Query("", alias="confirm_slug"),
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> dict[str, Any]:
        identity = BackendServiceAuthenticator.internal_scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        store: IdentityStore = app.state.identity_store
        org = store.get_organization(org_id=identity.org_id)
        if org is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "workspace_not_found")
        # Audit even rejected attempts so an operator sees who's asking.
        store.append_identity_audit(
            IdentityAuditEventRecord(
                org_id=identity.org_id,
                actor_user_id=identity.user_id,
                action="workspace.delete_attempt",
                metadata={
                    "attempting_user_id": identity.user_id,
                    "typed_confirmation_correct": confirm_slug == org.slug,
                },
            )
        )
        raise HTTPException(
            status.HTTP_501_NOT_IMPLEMENTED,
            "Workspace deletion is gated. Contact support.",
        )


def _project(org: OrganizationRecord) -> WorkspaceResponse:
    return WorkspaceResponse(
        org_id=org.org_id,
        display_name=org.display_name,
        slug=org.slug,
        deployment_kind=org.deployment_kind.value,
        status=org.status.value,
        metadata=dict(org.metadata),
        created_at=_iso(org.created_at),
    )


def _iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


__all__ = ["register_workspace_routes"]
