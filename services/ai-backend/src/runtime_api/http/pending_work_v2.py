"""Identity-scoped HTTP endpoint for canonical v2.1 pending work.

This is deliberately additive beside the legacy ``/pending-work`` queue.  It
is mounted only for the existing enforced workspace cohort, so default-on
surface rendering never makes canonical effect state broadly queryable before
the v2.1 migration flag says it is safe.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from copilot_service_contracts.scopes import RUNTIME_USE

from agent_runtime.api.pending_work_v2_service import (
    PendingWorkV2InvalidCursor,
    PendingWorkV2QueryService,
    PendingWorkV2Response,
    PendingWorkV2Unavailable,
    PendingWorkV2Values,
)
from runtime_api.identity import Identity
from runtime_api.rbac import RequireScopes


class PendingWorkV2Routes:
    """Thin identity boundary for ``GET /v1/agent/pending-work-v2``."""

    @classmethod
    async def list_pending_work(
        cls,
        request: Request,
        identity: Identity,
        limit: int = Query(
            PendingWorkV2Values.DEFAULT_RUN_LIMIT,
            ge=1,
            le=PendingWorkV2Values.MAX_RUN_LIMIT,
        ),
        cursor: str | None = Query(
            None,
            max_length=PendingWorkV2Values.CURSOR_MAX_LENGTH,
        ),
    ) -> PendingWorkV2Response:
        """Return a bounded page for the verified identity only.

        There are intentionally no ``org_id`` / ``user_id`` query parameters:
        the service receives them exclusively from ``Identity``.
        """

        service = cls._service(request)
        try:
            return await service.list_pending(
                org_id=identity.org_id,
                user_id=identity.user_id,
                limit=limit,
                cursor=cursor,
            )
        except PendingWorkV2InvalidCursor as exc:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, "Invalid pending-work cursor."
            ) from exc
        except PendingWorkV2Unavailable as exc:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "Pending-work service is unavailable.",
            ) from exc

    @staticmethod
    def _service(request: Request) -> PendingWorkV2QueryService:
        service = getattr(request.app.state, "pending_work_v2_service", None)
        if service is None:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "Pending-work service is unavailable.",
            )
        return service


def register_pending_work_v2_routes(router: APIRouter) -> None:
    """Attach the canonical queue only when the caller's cohort is enabled."""

    router.add_api_route(
        "/pending-work-v2",
        PendingWorkV2Routes.list_pending_work,
        methods=["GET"],
        response_model=PendingWorkV2Response,
        name="list_pending_work_v2",
        dependencies=[Depends(RequireScopes(RUNTIME_USE))],
    )


__all__ = ["PendingWorkV2Routes", "register_pending_work_v2_routes"]
