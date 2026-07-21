"""HTTP routes for workspace defaults and conversation lifecycle management.

Five endpoints under ``/v1/agent``:

  * ``GET    /v1/agent/workspace/defaults``        — read defaults
  * ``PUT    /v1/agent/workspace/defaults``        — write defaults (admin)
  * ``PATCH  /v1/agent/conversations/{id}``        — title / folder / archived
  * ``DELETE /v1/agent/conversations/{id}``        — soft-delete
  * ``POST   /v1/agent/conversations/{id}/restore``— un-soft-delete
"""

from __future__ import annotations

from copilot_service_contracts.scopes import ADMIN_USERS
from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.responses import Response

from agent_runtime.api.constants import Keys
from runtime_api.auth import RuntimeServiceAuthenticator
from runtime_api.http.routes import RuntimeApiRoutes
from runtime_api.schemas import (
    ConversationResponse,
    PinConversationRequest,
    UpdateConversationRequest,
    UpdateWorkspaceDefaultsRequest,
    WorkspaceDefaultsResponse,
)


def _is_admin(request: Request) -> bool:
    """Return True when the trusted identity carries the admin scope."""

    identity = RuntimeServiceAuthenticator.trusted_identity_from_request(request)
    return identity is not None and ADMIN_USERS in identity.permission_scopes


class WorkspaceDefaultsRoutes:
    """Route handlers for reading and updating workspace runtime defaults.

    Reads are open to any tenant member; writes require ``ADMIN_USERS``.
    """

    @classmethod
    async def get_defaults(
        cls,
        request: Request,
        org_id: str | None = Query(None, min_length=1),
        user_id: str | None = Query(None, min_length=1),
    ) -> WorkspaceDefaultsResponse:
        """Return the current workspace defaults; materialises deployment fallbacks when no row exists."""
        org_id, _ = RuntimeApiRoutes.scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        return await RuntimeApiRoutes.workspace_coordinator(
            request
        ).get_workspace_defaults(org_id=org_id)

    @classmethod
    async def put_defaults(
        cls,
        request: Request,
        payload: UpdateWorkspaceDefaultsRequest,
        org_id: str | None = Query(None, min_length=1),
        user_id: str | None = Query(None, min_length=1),
    ) -> WorkspaceDefaultsResponse:
        """Replace workspace defaults for the org; admin-only."""
        org_id, user_id = RuntimeApiRoutes.scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        identity = RuntimeServiceAuthenticator.trusted_identity_from_request(request)
        if identity is None or ADMIN_USERS not in identity.permission_scopes:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                "workspace defaults require admin scope",
            )
        return await RuntimeApiRoutes.workspace_coordinator(
            request
        ).update_workspace_defaults(
            org_id=org_id,
            actor_user_id=user_id,
            request=payload,
        )


class ConversationLifecycleRoutes:
    """Route handlers for conversation title/folder/archive, soft-delete, and restore."""

    @classmethod
    async def update_conversation(
        cls,
        request: Request,
        conversation_id: str,
        payload: UpdateConversationRequest,
        org_id: str | None = Query(None, min_length=1),
        user_id: str | None = Query(None, min_length=1),
    ) -> ConversationResponse:
        """Apply a title, folder, or archived-state patch to a conversation."""
        org_id, user_id = RuntimeApiRoutes.scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        return await RuntimeApiRoutes.conversation_coordinator(
            request
        ).update_conversation(
            org_id=org_id,
            user_id=user_id,
            conversation_id=conversation_id,
            request=payload,
            allow_admin_override=_is_admin(request),
        )

    @classmethod
    async def pin_conversation(
        cls,
        request: Request,
        conversation_id: str,
        payload: PinConversationRequest,
        org_id: str | None = Query(None, min_length=1),
        user_id: str | None = Query(None, min_length=1),
    ) -> ConversationResponse:
        """Pin or unpin a conversation (PRD-H.4); returns the updated row."""
        org_id, user_id = RuntimeApiRoutes.scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        return await RuntimeApiRoutes.conversation_coordinator(
            request
        ).set_conversation_pinned(
            org_id=org_id,
            user_id=user_id,
            conversation_id=conversation_id,
            pinned=payload.pinned,
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
        """Soft-delete a conversation; 204 on success."""
        org_id, user_id = RuntimeApiRoutes.scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        await RuntimeApiRoutes.conversation_coordinator(request).delete_conversation(
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
        """Undo a soft-delete and return the conversation to the active list."""
        org_id, user_id = RuntimeApiRoutes.scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        return await RuntimeApiRoutes.conversation_coordinator(
            request
        ).restore_conversation(
            org_id=org_id,
            user_id=user_id,
            conversation_id=conversation_id,
            allow_admin_override=_is_admin(request),
        )


def register_workspace_defaults_routes(router: APIRouter) -> None:
    """Mount workspace defaults and conversation lifecycle routes on the ``/v1/agent`` router."""

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
        "/conversations/{conversation_id}/pin",
        ConversationLifecycleRoutes.pin_conversation,
        methods=["POST"],
        response_model=ConversationResponse,
        name=Keys.RouteName.PIN_CONVERSATION,
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
