"""HTTP routes for the Workspace-pane draft artifact.

Five endpoints mounted on ``/v1/agent``:

- ``GET    /conversations/{cid}/drafts``
- ``GET    /drafts/{draft_id}`` (optional ``?version=N``)
- ``PATCH  /drafts/{draft_id}`` (edit-in-place)
- ``POST   /drafts/{draft_id}/send`` (approval-gated send)
- ``POST   /drafts/{draft_id}/discard``

Every handler is a thin shim over :class:`~agent_runtime.api.draft_service.DraftService`.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from copilot_service_contracts.scopes import RUNTIME_USE

from agent_runtime.api.constants import Keys
from agent_runtime.api.draft_service import DraftService
from runtime_api.identity import Identity
from runtime_api.rbac import RequireScopes
from runtime_api.schemas import (
    Draft,
    DraftDiscardRequest,
    DraftListResponse,
    DraftPatchRequest,
    DraftSendRequest,
    DraftSendResponse,
)


class DraftRoutes:
    """Route handlers for the ``/v1/agent`` draft endpoints."""

    @classmethod
    async def list_drafts(
        cls,
        request: Request,
        conversation_id: str,
        identity: Identity,
    ) -> DraftListResponse:
        """Return the latest version of every draft for one conversation."""
        return await cls._service(request).list_for_conversation(
            org_id=identity.org_id, conversation_id=conversation_id
        )

    @classmethod
    async def get_draft(
        cls,
        request: Request,
        draft_id: str,
        identity: Identity,
        version: int | None = Query(None, ge=1),
    ) -> Draft:
        """Return a specific draft version, or the latest version when omitted."""
        return await cls._service(request).get(
            org_id=identity.org_id, draft_id=draft_id, version=version
        )

    @classmethod
    async def patch_draft(
        cls,
        request: Request,
        draft_id: str,
        payload: DraftPatchRequest,
        identity: Identity,
    ) -> Draft:
        """Replace a draft's content in-place and return the updated version."""
        return await cls._service(request).patch(
            org_id=identity.org_id,
            user_id=identity.user_id,
            draft_id=draft_id,
            request=payload,
        )

    @classmethod
    async def send_draft(
        cls,
        request: Request,
        draft_id: str,
        payload: DraftSendRequest,
        identity: Identity,
    ) -> DraftSendResponse:
        """Request an approval-gated send of a draft through a connector."""
        return await cls._service(request).send(
            org_id=identity.org_id,
            user_id=identity.user_id,
            draft_id=draft_id,
            request=payload,
        )

    @classmethod
    async def discard_draft(
        cls,
        request: Request,
        draft_id: str,
        payload: DraftDiscardRequest,
        identity: Identity,
    ) -> Draft:
        """Mark a draft as discarded (soft-delete, terminal state)."""
        return await cls._service(request).discard(
            org_id=identity.org_id,
            user_id=identity.user_id,
            draft_id=draft_id,
            request=payload,
        )

    # -- helpers -------------------------------------------------------------

    @staticmethod
    def _service(request: Request) -> DraftService:
        """Return the wired DraftService or raise 503 if absent."""
        service = getattr(request.app.state, "draft_service", None)
        if service is None:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "Draft service is not configured.",
            )
        return service


def register_draft_routes(router: APIRouter) -> None:
    """Attach the draft endpoints to the ``/v1/agent`` router."""

    router.add_api_route(
        "/conversations/{conversation_id}/drafts",
        DraftRoutes.list_drafts,
        methods=["GET"],
        response_model=DraftListResponse,
        name=Keys.RouteName.LIST_DRAFTS,
        dependencies=[Depends(RequireScopes(RUNTIME_USE))],
    )
    router.add_api_route(
        "/drafts/{draft_id}",
        DraftRoutes.get_draft,
        methods=["GET"],
        response_model=Draft,
        name=Keys.RouteName.GET_DRAFT,
        dependencies=[Depends(RequireScopes(RUNTIME_USE))],
    )
    router.add_api_route(
        "/drafts/{draft_id}",
        DraftRoutes.patch_draft,
        methods=["PATCH"],
        response_model=Draft,
        name=Keys.RouteName.PATCH_DRAFT,
        dependencies=[Depends(RequireScopes(RUNTIME_USE))],
    )
    router.add_api_route(
        "/drafts/{draft_id}/send",
        DraftRoutes.send_draft,
        methods=["POST"],
        response_model=DraftSendResponse,
        name=Keys.RouteName.SEND_DRAFT,
        dependencies=[Depends(RequireScopes(RUNTIME_USE))],
    )
    router.add_api_route(
        "/drafts/{draft_id}/discard",
        DraftRoutes.discard_draft,
        methods=["POST"],
        response_model=Draft,
        name=Keys.RouteName.DISCARD_DRAFT,
        dependencies=[Depends(RequireScopes(RUNTIME_USE))],
    )
