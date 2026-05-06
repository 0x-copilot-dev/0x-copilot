"""HTTP route for the self-fork mechanic (PR A3 / 8.0.3c).

One endpoint, ``POST /v1/agent/conversations/{conversation_id}/fork``.
The handler is a thin shim over :class:`SelfForkService` (mounted on
``request.app.state.self_fork_service`` by the runtime API bootstrap
when the service is wired). Lives in its own module so the share-fork
route (PR 6.2's ``share_fork_routes.py``) and the self-fork route stay
visibly distinct in the route table — they share the same response
shape but have different source-side validations.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, status

from agent_runtime.api.constants import Keys
from agent_runtime.api.self_fork import SelfForkService
from runtime_api.identity import Identity
from runtime_api.schemas import ForkResponse, SelfForkRequest


class SelfForkRoutes:
    """Route handlers for the self-fork endpoint."""

    @classmethod
    async def fork_conversation(
        cls,
        request: Request,
        conversation_id: str,
        payload: SelfForkRequest,
        identity: Identity,
    ) -> ForkResponse:
        return await cls._service(request).fork(
            conversation_id=conversation_id,
            actor_org_id=identity.org_id,
            actor_user_id=identity.user_id,
            request=payload,
        )

    @staticmethod
    def _service(request: Request) -> SelfForkService:
        service = getattr(request.app.state, "self_fork_service", None)
        if service is None:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "Self-fork service is not configured.",
            )
        return service


def register_self_fork_routes(router: APIRouter) -> None:
    """Attach PR A3's self-fork endpoint to the ``/v1/agent`` router."""

    router.add_api_route(
        "/conversations/{conversation_id}/fork",
        SelfForkRoutes.fork_conversation,
        methods=["POST"],
        response_model=ForkResponse,
        name=Keys.RouteName.FORK_CONVERSATION,
    )


__all__ = ("SelfForkRoutes", "register_self_fork_routes")
