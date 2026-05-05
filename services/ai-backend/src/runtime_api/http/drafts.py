"""HTTP routes for the Workspace-pane draft artifact (PR 1.3).

Five endpoints, all under ``/v1/agent``:

- ``GET    /conversations/{cid}/drafts``
- ``GET    /drafts/{draft_id}`` (optional ``?version=N``)
- ``PATCH  /drafts/{draft_id}`` (edit-in-place)
- ``POST   /drafts/{draft_id}/send`` (request approval-gated send)
- ``POST   /drafts/{draft_id}/discard``

The handlers are thin shims around :class:`DraftService`. Identity comes from
the standard ``RuntimeServiceAuthenticator`` headers; non-identity-bearing
requests fall back to query params for parity with the rest of the runtime
router (per ``RuntimeApiRoutes.scoped_identity``).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from enterprise_service_contracts.scopes import RUNTIME_USE

from agent_runtime.api.constants import Keys
from agent_runtime.api.draft_service import DraftService
from runtime_api.auth import RuntimeServiceAuthenticator
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
    """Route handlers for ``/v1/agent`` draft endpoints."""

    @classmethod
    async def list_drafts(
        cls,
        request: Request,
        conversation_id: str,
    ) -> DraftListResponse:
        org_id, _user_id = cls._scoped_identity(request)
        return await cls._service(request).list_for_conversation(
            org_id=org_id, conversation_id=conversation_id
        )

    @classmethod
    async def get_draft(
        cls,
        request: Request,
        draft_id: str,
        version: int | None = Query(None, ge=1),
    ) -> Draft:
        org_id, _user_id = cls._scoped_identity(request)
        return await cls._service(request).get(
            org_id=org_id, draft_id=draft_id, version=version
        )

    @classmethod
    async def patch_draft(
        cls,
        request: Request,
        draft_id: str,
        payload: DraftPatchRequest,
    ) -> Draft:
        org_id, user_id = cls._scoped_identity(request)
        return await cls._service(request).patch(
            org_id=org_id,
            user_id=user_id,
            draft_id=draft_id,
            request=payload,
        )

    @classmethod
    async def send_draft(
        cls,
        request: Request,
        draft_id: str,
        payload: DraftSendRequest,
    ) -> DraftSendResponse:
        org_id, user_id = cls._scoped_identity(request)
        return await cls._service(request).send(
            org_id=org_id,
            user_id=user_id,
            draft_id=draft_id,
            request=payload,
        )

    @classmethod
    async def discard_draft(
        cls,
        request: Request,
        draft_id: str,
        payload: DraftDiscardRequest,
    ) -> Draft:
        org_id, user_id = cls._scoped_identity(request)
        return await cls._service(request).discard(
            org_id=org_id,
            user_id=user_id,
            draft_id=draft_id,
            request=payload,
        )

    # -- helpers -------------------------------------------------------------

    @staticmethod
    def _service(request: Request) -> DraftService:
        service = getattr(request.app.state, "draft_service", None)
        if service is None:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "Draft service is not configured.",
            )
        return service

    @staticmethod
    def _scoped_identity(request: Request) -> tuple[str, str]:
        identity = RuntimeServiceAuthenticator.trusted_identity_from_request(request)
        if identity is None:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "org_id and user_id are required.",
            )
        return identity.org_id, identity.user_id


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
