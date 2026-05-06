"""HTTP route for the conversation fork mechanic (PR 6.2).

One endpoint, ``POST /v1/agent/shares/{share_token}/fork``. The handler
is a thin shim over :class:`ConversationForkService` (mounted on
``request.app.state.conversation_fork_service`` by the runtime API
bootstrap when the service is wired).

Lives in its own module so the in-flight PR 6.1 (``share_routes.py``
for the share lifecycle) can land alongside without merge friction —
both groups attach to the same ``/v1/agent`` router via
``register_share_fork_routes``.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, status

from agent_runtime.api.constants import Keys
from agent_runtime.api.conversation_fork import ConversationForkService
from runtime_api.identity import Identity
from runtime_api.schemas import ForkRequest, ForkResponse


class ShareForkRoutes:
    """Route handlers for the fork endpoint."""

    @classmethod
    async def fork_share(
        cls,
        request: Request,
        share_token: str,
        payload: ForkRequest,
        identity: Identity,
    ) -> ForkResponse:
        return await cls._service(request).fork(
            share_token=share_token,
            recipient_org_id=identity.org_id,
            recipient_user_id=identity.user_id,
            request=payload,
        )

    @staticmethod
    def _service(request: Request) -> ConversationForkService:
        service = getattr(request.app.state, "conversation_fork_service", None)
        if service is None:
            # Surfaces through the standard runtime API error handler.
            # Wiring the service is part of the runtime API bootstrap;
            # the 503 here is a defensive fallback for half-configured
            # deployments (e.g. the FE shipped before the service flag
            # was set).
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "Conversation fork service is not configured.",
            )
        return service


def register_share_fork_routes(router: APIRouter) -> None:
    """Attach PR 6.2's fork endpoint to the ``/v1/agent`` router.

    Called once from ``RuntimeApiRouter.create_router`` alongside the
    other ``register_*`` helpers (drafts, workspace feeds, workspace
    defaults). Keeping registration centralised makes the route table
    greppable from one file.
    """

    router.add_api_route(
        "/shares/{share_token}/fork",
        ShareForkRoutes.fork_share,
        methods=["POST"],
        response_model=ForkResponse,
        name=Keys.RouteName.FORK_SHARE,
    )


__all__ = ("ShareForkRoutes", "register_share_fork_routes")
