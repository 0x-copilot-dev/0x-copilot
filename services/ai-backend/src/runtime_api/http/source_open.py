"""Owner-routed ``POST /runs/{run_id}/sources/{source_id}/open`` endpoint.

The route accepts only the opaque source id.  It never accepts a path, ref,
arguments, result body, cookie, provider token, or caller-provided identity.
``SourceOpenService`` rechecks both run ownership and the owning artifact
repository before it returns a navigation target.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Path, Request, status

from copilot_service_contracts.scopes import RUNTIME_USE

from agent_runtime.api.constants import Keys, Messages
from agent_runtime.api.source_open_service import (
    SourceOpenNotFoundError,
    SourceOpenResultV2,
    SourceOpenService,
)
from runtime_api.identity import Identity
from runtime_api.rbac import RequireScopes


class SourceOpenRoutes:
    """Thin identity boundary for one safe Sources v2 opener."""

    @classmethod
    async def open_source(
        cls,
        request: Request,
        run_id: str,
        identity: Identity,
        source_id: str = Path(min_length=1, max_length=256),
    ) -> SourceOpenResultV2:
        """Resolve the source through its owner using the verified identity only."""

        try:
            return await cls._service(request).open_source(
                org_id=identity.org_id,
                user_id=identity.user_id,
                run_id=run_id,
                source_id=source_id,
            )
        except SourceOpenNotFoundError as exc:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND,
                Messages.Error.SOURCE_NOT_AVAILABLE,
            ) from exc

    @staticmethod
    def _service(request: Request) -> SourceOpenService:
        service = getattr(request.app.state, "source_open_service", None)
        if service is None:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                Messages.Error.SOURCE_OPEN_UNAVAILABLE,
            )
        return service


def register_source_open_routes(router: APIRouter) -> None:
    """Attach the flag-gated v2 source opener to the agent router."""

    router.add_api_route(
        "/runs/{run_id}/sources/{source_id}/open",
        SourceOpenRoutes.open_source,
        methods=["POST"],
        response_model=SourceOpenResultV2,
        # D8's deferred inventory reserves this exact name/method/scope:
        # ``source_open`` / POST / artifact_revision.  The handler enforces
        # that scope by rechecking the run's parent conversation and the
        # owner-authorized immutable artifact revision before returning a
        # target.
        name=Keys.RouteName.SOURCE_OPEN,
        dependencies=[Depends(RequireScopes(RUNTIME_USE))],
    )


__all__ = ("SourceOpenRoutes", "register_source_open_routes")
