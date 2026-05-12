"""HTTP route for the share-fork endpoint.

One endpoint: ``POST /v1/agent/shares/{share_token}/fork``. The handler
is a thin shim over :class:`ConversationForkService`. Lives in its own
module so the share-lifecycle routes and the fork route stay visibly
distinct in the route table.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, status

from agent_runtime.api.constants import Keys
from agent_runtime.api.conversation_fork import ConversationForkService
from runtime_api.identity import Identity
from runtime_api.schemas import ForkRequest, ForkResponse


class ShareForkRoutes:
    """Route handlers for forking a shared conversation into the caller's workspace."""

    @classmethod
    async def fork_share(
        cls,
        request: Request,
        share_token: str,
        payload: ForkRequest,
        identity: Identity,
    ) -> ForkResponse:
        """Fork the conversation identified by ``share_token`` into the caller's workspace."""
        return await cls._service(request).fork(
            share_token=share_token,
            recipient_org_id=identity.org_id,
            recipient_user_id=identity.user_id,
            request=payload,
        )

    @staticmethod
    def _service(request: Request) -> ConversationForkService:
        """Return the wired ConversationForkService or raise 503 if not configured."""
        service = getattr(request.app.state, "conversation_fork_service", None)
        if service is None:
            # 503 is a defensive fallback for half-configured deployments
            # (e.g. the FE shipped before the service flag was toggled).
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "Conversation fork service is not configured.",
            )
        return service


def register_share_fork_routes(router: APIRouter) -> None:
    """Attach the share-fork endpoint to the ``/v1/agent`` router."""

    router.add_api_route(
        "/shares/{share_token}/fork",
        ShareForkRoutes.fork_share,
        methods=["POST"],
        response_model=ForkResponse,
        name=Keys.RouteName.FORK_SHARE,
    )


__all__ = ("ShareForkRoutes", "register_share_fork_routes")
