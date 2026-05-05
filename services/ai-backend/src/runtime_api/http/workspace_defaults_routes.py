"""HTTP routes for PR 1.6 — workspace defaults + conversation lifecycle.

Lives outside ``routes.py`` so the parallel approval-forwarding work
(PR 1.4.1) can land alongside without merge friction. Both route
groups are mounted on the same ``/v1/agent`` router.

  * ``GET    /v1/agent/workspace/defaults``        — read defaults
  * ``PUT    /v1/agent/workspace/defaults``        — write defaults (admin)
  * ``PATCH  /v1/agent/conversations/{id}``        — title / folder / archived
  * ``DELETE /v1/agent/conversations/{id}``        — soft-delete
  * ``POST   /v1/agent/conversations/{id}/restore``— un-soft-delete
"""

from __future__ import annotations

from enterprise_service_contracts.scopes import ADMIN_USERS
from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.responses import Response

from agent_runtime.api.constants import Keys
from runtime_api.auth import RuntimeServiceAuthenticator
from runtime_api.http.routes import RuntimeApiRoutes
from runtime_api.schemas import (
    ConversationResponse,
    UpdateConversationRequest,
    UpdateWorkspaceDefaultsRequest,
    WorkspaceDefaultsResponse,
)


def _is_admin(request: Request) -> bool:
    """Return True iff the trusted identity carries the admin scope.

    Mirrors the gate used by the connector PATCH route (PR 1.2.1) so
    audit semantics line up across every conversation-mutating
    endpoint in the runtime API.
    """

    identity = RuntimeServiceAuthenticator.trusted_identity_from_request(request)
    return identity is not None and ADMIN_USERS in identity.permission_scopes


class WorkspaceDefaultsRoutes:
    """Route handlers for workspace defaults (PR 1.6).

    Reads are public to any tenant member (the FE's Settings panel
    populates from this); writes are gated by the existing
    ``ADMIN_USERS`` permission scope.
    """

    @classmethod
    async def get_defaults(
        cls,
        request: Request,
        org_id: str | None = Query(None, min_length=1),
        user_id: str | None = Query(None, min_length=1),
    ) -> WorkspaceDefaultsResponse:
        org_id, _ = RuntimeApiRoutes.scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        return await RuntimeApiRoutes.service(request).get_workspace_defaults(
            org_id=org_id
        )

    @classmethod
    async def put_defaults(
        cls,
        request: Request,
        payload: UpdateWorkspaceDefaultsRequest,
        org_id: str | None = Query(None, min_length=1),
        user_id: str | None = Query(None, min_length=1),
    ) -> WorkspaceDefaultsResponse:
        org_id, user_id = RuntimeApiRoutes.scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        identity = RuntimeServiceAuthenticator.trusted_identity_from_request(request)
        if identity is None or ADMIN_USERS not in identity.permission_scopes:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                "workspace defaults require admin scope",
            )
        return await RuntimeApiRoutes.service(request).update_workspace_defaults(
            org_id=org_id,
            actor_user_id=user_id,
            request=payload,
        )


class ConversationLifecycleRoutes:
    """Route handlers for the lifecycle PATCH/DELETE/restore surface."""

    @classmethod
    async def update_conversation(
        cls,
        request: Request,
        conversation_id: str,
        payload: UpdateConversationRequest,
        org_id: str | None = Query(None, min_length=1),
        user_id: str | None = Query(None, min_length=1),
    ) -> ConversationResponse:
        org_id, user_id = RuntimeApiRoutes.scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        return await RuntimeApiRoutes.service(request).update_conversation(
            org_id=org_id,
            user_id=user_id,
            conversation_id=conversation_id,
            request=payload,
            allow_admin_override=_is_admin(request),
        )

    @classmethod
    async def delete_conversation(
        cls,
        request: Request,
        conversation_id: str,
        org_id: str | None = Query(None, min_length=1),
        user_id: str | None = Query(None, min_length=1),
    ) -> Response:
        org_id, user_id = RuntimeApiRoutes.scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        await RuntimeApiRoutes.service(request).delete_conversation(
            org_id=org_id,
            user_id=user_id,
            conversation_id=conversation_id,
            allow_admin_override=_is_admin(request),
        )
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @classmethod
    async def restore_conversation(
        cls,
        request: Request,
        conversation_id: str,
        org_id: str | None = Query(None, min_length=1),
        user_id: str | None = Query(None, min_length=1),
    ) -> ConversationResponse:
        org_id, user_id = RuntimeApiRoutes.scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        return await RuntimeApiRoutes.service(request).restore_conversation(
            org_id=org_id,
            user_id=user_id,
            conversation_id=conversation_id,
            allow_admin_override=_is_admin(request),
        )


def register_workspace_defaults_routes(router: APIRouter) -> None:
    """Add PR 1.6 routes to the ``/v1/agent`` router.

    Called once from ``RuntimeApiRouter.create_router`` alongside the
    other ``register_*`` helpers (drafts, workspace feeds). Keeping
    registration centralised in a single function makes the route
    table greppable.
    """

    router.add_api_route(
        "/workspace/defaults",
        WorkspaceDefaultsRoutes.get_defaults,
        methods=["GET"],
        response_model=WorkspaceDefaultsResponse,
        name=Keys.RouteName.GET_WORKSPACE_DEFAULTS,
    )
    router.add_api_route(
        "/workspace/defaults",
        WorkspaceDefaultsRoutes.put_defaults,
        methods=["PUT"],
        response_model=WorkspaceDefaultsResponse,
        name=Keys.RouteName.UPDATE_WORKSPACE_DEFAULTS,
    )
    router.add_api_route(
        "/conversations/{conversation_id}",
        ConversationLifecycleRoutes.update_conversation,
        methods=["PATCH"],
        response_model=ConversationResponse,
        name=Keys.RouteName.UPDATE_CONVERSATION,
    )
    router.add_api_route(
        "/conversations/{conversation_id}",
        ConversationLifecycleRoutes.delete_conversation,
        methods=["DELETE"],
        status_code=status.HTTP_204_NO_CONTENT,
        name=Keys.RouteName.DELETE_CONVERSATION,
    )
    router.add_api_route(
        "/conversations/{conversation_id}/restore",
        ConversationLifecycleRoutes.restore_conversation,
        methods=["POST"],
        response_model=ConversationResponse,
        name=Keys.RouteName.RESTORE_CONVERSATION,
    )


__all__ = (
    "ConversationLifecycleRoutes",
    "WorkspaceDefaultsRoutes",
    "register_workspace_defaults_routes",
)
