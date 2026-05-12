"""HTTP routes for the conversation sharing lifecycle.

Six endpoints under ``/v1/agent``:

- ``POST   /conversations/{conversation_id}/share``  — create
- ``GET    /conversations/{conversation_id}/shares`` — list
- ``PATCH  /shares/{share_id}``                       — update (RFC 7396)
- ``DELETE /shares/{share_id}``                       — revoke (idempotent)
- ``GET    /shares/{share_token}``                    — recipient snapshot view
- ``GET    /shares/{share_token}/preview``            — lightweight recipient gate

Recipient paths still require ``Identity`` — a token grants access to the
share row but not to an unauthenticated viewer.
"""

from __future__ import annotations

from enterprise_service_contracts.scopes import RUNTIME_USE
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from agent_runtime.api.share_service import ShareService
from runtime_api.identity import Identity
from runtime_api.rbac import RequireScopes
from runtime_api.schemas import (
    ConversationShare,
    CreateShareRequest,
    CreateShareResponse,
    ListSharesResponse,
    RecipientPreview,
    SharedConversationView,
    UpdateShareRequest,
)


class _RouteName:
    """Route name constants for the sharing surface (for ``url_for`` lookups)."""

    CREATE_SHARE = "create_share"
    LIST_SHARES = "list_shares"
    UPDATE_SHARE = "update_share"
    REVOKE_SHARE = "revoke_share"
    GET_SHARED_CONVERSATION = "get_shared_conversation"
    PREVIEW_SHARED_CONVERSATION = "preview_shared_conversation"


class ShareRoutes:
    """Route handlers for the conversation sharing lifecycle endpoints."""

    @classmethod
    async def create_share(
        cls,
        request: Request,
        conversation_id: str,
        payload: CreateShareRequest,
        identity: Identity,
    ) -> CreateShareResponse:
        """Create a share for a conversation; token is returned once and not stored in plain text."""
        return await cls._service(request).create_share(
            org_id=identity.org_id,
            user_id=identity.user_id,
            permission_scopes=identity.permission_scopes,
            conversation_id=conversation_id,
            request=payload,
        )

    @classmethod
    async def list_shares(
        cls,
        request: Request,
        conversation_id: str,
        identity: Identity,
    ) -> ListSharesResponse:
        """List all non-revoked shares for a conversation, visible to the owner."""
        return await cls._service(request).list_shares(
            org_id=identity.org_id,
            user_id=identity.user_id,
            permission_scopes=identity.permission_scopes,
            conversation_id=conversation_id,
        )

    @classmethod
    async def update_share(
        cls,
        request: Request,
        share_id: str,
        payload: UpdateShareRequest,
        identity: Identity,
    ) -> ConversationShare:
        """Apply an RFC 7396 merge-patch to an existing share row."""
        return await cls._service(request).update_share(
            org_id=identity.org_id,
            user_id=identity.user_id,
            permission_scopes=identity.permission_scopes,
            share_id=share_id,
            request=payload,
        )

    @classmethod
    async def revoke_share(
        cls,
        request: Request,
        share_id: str,
        identity: Identity,
    ) -> Response:
        """Revoke a share; idempotent — 204 whether or not it was already revoked."""
        await cls._service(request).revoke_share(
            org_id=identity.org_id,
            user_id=identity.user_id,
            permission_scopes=identity.permission_scopes,
            share_id=share_id,
        )
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @classmethod
    async def get_shared_conversation(
        cls,
        request: Request,
        share_token: str,
        identity: Identity,
    ) -> SharedConversationView:
        """Return the full snapshot view for a recipient who holds the share token."""
        return await cls._service(request).get_recipient_view(
            share_token=share_token,
            viewer_org_id=identity.org_id,
            viewer_user_id=identity.user_id,
        )

    @classmethod
    async def preview_shared_conversation(
        cls,
        request: Request,
        share_token: str,
        identity: Identity,
    ) -> RecipientPreview:
        """Return a lightweight access-check view before the full snapshot read."""
        return await cls._service(request).preview_share(
            share_token=share_token,
            viewer_org_id=identity.org_id,
            viewer_user_id=identity.user_id,
        )

    @staticmethod
    def _service(request: Request) -> ShareService:
        """Return the wired ShareService or raise 503 if not configured."""
        service = getattr(request.app.state, "share_service", None)
        if service is None:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "Share service is not configured.",
            )
        return service


def register_share_routes(router: APIRouter) -> None:
    """Attach the conversation share endpoints to the ``/v1/agent`` router."""

    router.add_api_route(
        "/conversations/{conversation_id}/share",
        ShareRoutes.create_share,
        methods=["POST"],
        response_model=CreateShareResponse,
        name=_RouteName.CREATE_SHARE,
        dependencies=[Depends(RequireScopes(RUNTIME_USE))],
    )
    router.add_api_route(
        "/conversations/{conversation_id}/shares",
        ShareRoutes.list_shares,
        methods=["GET"],
        response_model=ListSharesResponse,
        name=_RouteName.LIST_SHARES,
        dependencies=[Depends(RequireScopes(RUNTIME_USE))],
    )
    router.add_api_route(
        "/shares/{share_id}",
        ShareRoutes.update_share,
        methods=["PATCH"],
        response_model=ConversationShare,
        name=_RouteName.UPDATE_SHARE,
        dependencies=[Depends(RequireScopes(RUNTIME_USE))],
    )
    router.add_api_route(
        "/shares/{share_id}",
        ShareRoutes.revoke_share,
        methods=["DELETE"],
        status_code=status.HTTP_204_NO_CONTENT,
        name=_RouteName.REVOKE_SHARE,
        dependencies=[Depends(RequireScopes(RUNTIME_USE))],
    )
    # The token-based recipient routes still require ``RUNTIME_USE`` —
    # the caller must be a logged-in workspace member; the token grants
    # access to the share row, not the user identity.
    router.add_api_route(
        "/shares/{share_token}",
        ShareRoutes.get_shared_conversation,
        methods=["GET"],
        response_model=SharedConversationView,
        name=_RouteName.GET_SHARED_CONVERSATION,
        dependencies=[Depends(RequireScopes(RUNTIME_USE))],
    )
    router.add_api_route(
        "/shares/{share_token}/preview",
        ShareRoutes.preview_shared_conversation,
        methods=["GET"],
        response_model=RecipientPreview,
        name=_RouteName.PREVIEW_SHARED_CONVERSATION,
        dependencies=[Depends(RequireScopes(RUNTIME_USE))],
    )
