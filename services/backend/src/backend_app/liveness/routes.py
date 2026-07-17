"""Internal liveness route — ``GET /internal/v1/liveness/project/{project_id}``.

Service-token + tenant headers gated. There is intentionally NO app-facing
``/v1/liveness/*`` endpoint on the backend; the facade exposes its own
proxy under ``/v1/liveness/...`` for the FE's archive-409 modal. The
internal route is the only direct surface.

Per §3.5: callers send ``ENTERPRISE_SERVICE_TOKEN`` + ``x-enterprise-org-id``
+ ``x-enterprise-user-id``. Tenant is taken from the verified header,
NOT from the path — caller-supplied identity is untrusted (master rule).
"""

from __future__ import annotations

from copilot_service_contracts.scopes import RUNTIME_USE
from fastapi import Depends, FastAPI, Query, Request

from backend_app.auth import BackendServiceAuthenticator
from backend_app.identity.rbac import RequireScopes
from backend_app.liveness.service import LivenessReport, LivenessService


def register_liveness_routes(app: FastAPI, *, service: LivenessService) -> None:
    """Attach the internal liveness endpoint to ``app``."""

    @app.get(
        "/internal/v1/liveness/project/{project_id}",
        response_model=LivenessReport,
        dependencies=[Depends(RequireScopes(RUNTIME_USE))],
    )
    async def get_project_liveness(
        request: Request,
        project_id: str,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
        force_refresh: bool = Query(default=False),
    ) -> LivenessReport:
        # Tenant binding from the verified headers (or query-fallback in dev).
        identity = BackendServiceAuthenticator.internal_scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        return await service.is_project_alive(
            tenant_id=identity.org_id,
            project_id=project_id,
            force_refresh=force_refresh,
        )


__all__ = ["register_liveness_routes"]
