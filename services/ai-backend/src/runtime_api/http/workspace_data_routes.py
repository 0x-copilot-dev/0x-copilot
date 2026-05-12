"""HTTP stub routes for the workspace export and bulk-delete data lifecycle.

Two endpoints mounted on ``/v1/agent``:

  * ``POST   /v1/agent/workspace/export``  — admin-only; queues an export job
    and returns ``{export_id, status: 'queued'}``; writes one audit row.
    The actual NDJSON dump pipeline is a planned follow-up.
  * ``DELETE /v1/agent/workspace/data``    — admin-only; always 501 in v1.
    Audits the ``confirm_slug`` correctness so even a failed attempt is
    traceable; correct confirmation is a prerequisite for future v2.

Both require the ``ADMIN_USERS`` permission scope.
"""

from __future__ import annotations

from enterprise_service_contracts.scopes import ADMIN_USERS
from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import Field

from agent_runtime.api.constants import Keys
from agent_runtime.execution.contracts import RuntimeContract
from runtime_api.auth import RuntimeServiceAuthenticator
from runtime_api.http.routes import RuntimeApiRoutes


class WorkspaceExportRequest(RuntimeContract):
    """Body for ``POST /v1/agent/workspace/export``.

    ``scope`` is a forward-compatibility hook; v1 only accepts
    ``"workspace"`` and rejects everything else as 422. Future scopes
    (``"user"``, ``"conversation"``) ride this same surface when the
    real export pipeline lands.
    """

    scope: str = Field(default="workspace")


class WorkspaceExportResponse(RuntimeContract):
    """Response for the export queue (v1 stub)."""

    export_id: str
    status: str  # always "queued" in v1


class WorkspaceDeleteAllRequest(RuntimeContract):
    """Body for ``DELETE /v1/agent/workspace/data``.

    The admin must type the org's slug into ``confirm_slug`` for the
    audit row to record the typed confirmation as correct. The route
    still returns 501 either way — but the boolean value lives on the
    audit row for forensic review.
    """

    confirm_slug: str = Field(min_length=1, max_length=64)


def _require_admin(request: Request) -> None:
    """Raise 403 when the caller's identity does not carry ``ADMIN_USERS``."""
    identity = RuntimeServiceAuthenticator.trusted_identity_from_request(request)
    if identity is None or ADMIN_USERS not in identity.permission_scopes:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "workspace data lifecycle requires admin scope",
        )


class WorkspaceDataRoutes:
    """Route handlers for the workspace export and bulk-delete stub endpoints."""

    @classmethod
    async def request_export(
        cls,
        request: Request,
        payload: WorkspaceExportRequest,
        org_id: str | None = Query(None, min_length=1),
        user_id: str | None = Query(None, min_length=1),
    ) -> WorkspaceExportResponse:
        """Queue a workspace data export and return the new export-job id."""
        org_id, user_id = RuntimeApiRoutes.scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        _require_admin(request)
        if payload.scope != "workspace":
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                "only scope='workspace' is supported in this version",
            )
        result = await RuntimeApiRoutes.workspace_coordinator(
            request
        ).request_workspace_export(org_id=org_id, actor_user_id=user_id)
        return WorkspaceExportResponse(
            export_id=str(result["export_id"]),
            status=str(result["status"]),
        )

    @classmethod
    async def delete_all(
        cls,
        request: Request,
        confirm_slug: str = Query(..., min_length=1, max_length=64),
        org_id: str | None = Query(None, min_length=1),
        user_id: str | None = Query(None, min_length=1),
    ) -> None:
        """Audit the bulk-delete intent and always return 501 (pipeline not yet implemented)."""
        org_id, user_id = RuntimeApiRoutes.scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        _require_admin(request)
        # v1 stub — we record whether ``confirm_slug`` matches the org's
        # slug so the forensic audit row is honest about caller intent.
        # The org's display slug lives in backend (not ai-backend), so
        # we treat ``confirm_slug == org_id`` as the correctness check;
        # that's the same id the FE uses to disambiguate which workspace
        # the user is operating on.
        #
        # ``confirm_slug`` rides as a query parameter rather than a
        # request body — DELETE-with-body is not idiomatic and middle
        # boxes occasionally strip the body. Query param keeps the
        # surface portable across CDNs and proxies.
        typed_correctly = confirm_slug.strip() == org_id
        await RuntimeApiRoutes.workspace_coordinator(
            request
        ).record_workspace_delete_attempt(
            org_id=org_id,
            actor_user_id=user_id,
            typed_confirmation_correct=typed_correctly,
        )
        # Always 501 — the cascade-delete pipeline lives in a follow-up.
        raise HTTPException(
            status.HTTP_501_NOT_IMPLEMENTED,
            "Workspace deletion is gated. Contact support.",
        )


def register_workspace_data_routes(router: APIRouter) -> None:
    """Mount the PR 4.3 export + delete-all routes on ``/v1/agent``."""

    router.add_api_route(
        "/workspace/export",
        WorkspaceDataRoutes.request_export,
        methods=["POST"],
        status_code=status.HTTP_202_ACCEPTED,
        response_model=WorkspaceExportResponse,
        name=Keys.RouteName.REQUEST_WORKSPACE_EXPORT,
    )
    router.add_api_route(
        "/workspace/data",
        WorkspaceDataRoutes.delete_all,
        methods=["DELETE"],
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        name=Keys.RouteName.DELETE_WORKSPACE_DATA,
    )


__all__ = (
    "WorkspaceDataRoutes",
    "WorkspaceDeleteAllRequest",
    "WorkspaceExportRequest",
    "WorkspaceExportResponse",
    "register_workspace_data_routes",
)
