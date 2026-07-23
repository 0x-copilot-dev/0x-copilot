"""HTTP route for the cross-run pending-work queue (PRD-E2).

One read endpoint mounted on ``/v1/agent`` and registered ONLY when
``SURFACES_V2`` is on (flag off ⇒ the route does not exist ⇒ 404 — the cleanest
byte-identical guarantee):

- ``GET /pending-work`` → :class:`PendingWorkResponse`

Identity comes from the verified session (the ``Identity`` dependency), so the
queue can only ever return the caller's own pending work — a caller-supplied
``org_id`` / ``user_id`` is never read. The handler is a thin shim over
:class:`~agent_runtime.surfaces_v2.pending_work.PendingWorkService`; the fold is
pure and the service degrades one bad run to zero items rather than 500ing.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status

from copilot_service_contracts.scopes import RUNTIME_USE

from agent_runtime.surfaces_v2.pending_work import (
    PendingWorkResponse,
    PendingWorkService,
)
from runtime_api.identity import Identity
from runtime_api.rbac import RequireScopes


class PendingWorkRoutes:
    """Route handler for ``GET /v1/agent/pending-work``."""

    @classmethod
    async def list_pending_work(
        cls,
        request: Request,
        identity: Identity,
    ) -> PendingWorkResponse:
        """Return the caller's cross-run pending gates + held drafts + row-sets."""

        service = cls._service(request)
        return await service.list_pending(
            org_id=identity.org_id, user_id=identity.user_id
        )

    @staticmethod
    def _service(request: Request) -> PendingWorkService:
        service = getattr(request.app.state, "pending_work_service", None)
        if service is None:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "Pending-work service is not configured.",
            )
        return service


def register_pending_work_routes(router: APIRouter) -> None:
    """Attach the pending-work endpoint (flag-gated by the caller) to ``/v1/agent``."""

    router.add_api_route(
        "/pending-work",
        PendingWorkRoutes.list_pending_work,
        methods=["GET"],
        response_model=PendingWorkResponse,
        name="list_pending_work",
        dependencies=[Depends(RequireScopes(RUNTIME_USE))],
    )


__all__ = ["PendingWorkRoutes", "register_pending_work_routes"]
