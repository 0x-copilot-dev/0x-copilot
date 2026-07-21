"""FastAPI router for the SurfaceSpec registry (generative-UI PRD-08).

Internal-only surface mounted under ``/internal/v1/surfaces/specs``. There are
**no facade / app-facing routes** — specs are runtime infrastructure consumed
only by the ai-backend render/generation path. Auth is the same internal
service-token + org/user header discipline as every other ``/internal/v1/*``
route: the caller identity is required and validated, never trusted from the
body.
"""

from __future__ import annotations

from copilot_service_contracts.scopes import ADMIN_AUDIT_EXPORT, RUNTIME_USE
from fastapi import Depends, FastAPI, HTTPException, Path, Query, Request, status

from backend_app.auth import BackendServiceAuthenticator
from backend_app.identity.rbac import RequireScopes
from backend_app.surface_specs.contracts import (
    SurfaceSpecResponse,
    SurfaceSpecUpsert,
    SurfaceSpecView,
)
from backend_app.surface_specs.service import SurfaceSpecService
from backend_app.surface_specs.validation import SurfaceSpecSchemaError


def register_surface_specs_routes(
    app: FastAPI,
    *,
    service: SurfaceSpecService,
) -> None:
    """Attach the SurfaceSpec registry routes to the FastAPI app."""

    app.state.surface_specs_service = service

    @app.get(
        "/internal/v1/surfaces/specs",
        response_model=SurfaceSpecResponse,
        dependencies=[Depends(RequireScopes(RUNTIME_USE))],
    )
    def get_spec(
        request: Request,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
        server: str = Query(..., min_length=1),
        tool: str = Query(..., min_length=1),
        shape_hash: str | None = Query(None, min_length=1),
        schema_version: int | None = Query(None, ge=1),
        skill_version: int | None = Query(None, ge=1),
    ) -> SurfaceSpecResponse:
        identity = BackendServiceAuthenticator.internal_scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        view = service.get_spec(
            org_id=identity.org_id,
            server=server,
            tool=tool,
            output_shape_hash=shape_hash,
            spec_schema_version=schema_version,
            skill_version=skill_version,
        )
        return SurfaceSpecResponse(spec=view)

    @app.put(
        "/internal/v1/surfaces/specs",
        response_model=SurfaceSpecView,
        status_code=status.HTTP_201_CREATED,
        dependencies=[Depends(RequireScopes(RUNTIME_USE))],
    )
    def put_spec(
        request: Request,
        payload: SurfaceSpecUpsert,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> SurfaceSpecView:
        identity = BackendServiceAuthenticator.internal_scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        try:
            return service.put_spec(
                org_id=identity.org_id,
                user_id=identity.user_id,
                upsert=payload,
            )
        except SurfaceSpecSchemaError as exc:
            # An invalid spec is a client contract error, not a server fault.
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc

    @app.delete(
        "/internal/v1/surfaces/specs/{spec_id}",
        status_code=status.HTTP_204_NO_CONTENT,
        dependencies=[Depends(RequireScopes(ADMIN_AUDIT_EXPORT))],
    )
    def delete_spec(
        request: Request,
        spec_id: str = Path(..., min_length=1, max_length=128),
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> None:
        identity = BackendServiceAuthenticator.internal_scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        removed = service.delete_spec(org_id=identity.org_id, spec_id=spec_id)
        if not removed:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "surface_spec_not_found")


__all__ = ["register_surface_specs_routes"]
