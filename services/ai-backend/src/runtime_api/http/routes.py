"""FastAPI routes for the runtime API."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse

from agent_runtime.api.constants import Keys
from agent_runtime.api.service import RuntimeApiService
from runtime_api.auth import RuntimeServiceAuthenticator
from runtime_api.schemas import (
    ApprovalDecisionRequest,
    ApprovalDecisionResponse,
    CancelRunRequest,
    CancelRunResponse,
    ConversationListResponse,
    ConversationResponse,
    CreateConversationRequest,
    CreateRunRequest,
    CreateRunResponse,
    HistoryDeletionResponse,
    MessageListResponse,
    RuntimeRequestContext,
    RuntimeEventReplayResponse,
    RunStatusResponse,
)
from runtime_api.sse.adapter import RuntimeSseAdapter


class RuntimeApiRoutes:
    """Route handlers for the v1 agent runtime API."""

    @classmethod
    def create_conversation(
        cls,
        request: Request,
        payload: CreateConversationRequest,
    ) -> ConversationResponse:
        identity = RuntimeServiceAuthenticator.trusted_identity_from_request(request)
        if identity is not None:
            payload = payload.model_copy(
                update={"org_id": identity.org_id, "user_id": identity.user_id}
            )
        return cls.service(request).create_conversation(payload)

    @classmethod
    def list_conversations(
        cls,
        request: Request,
        org_id: str | None = Query(None, min_length=1),
        user_id: str | None = Query(None, min_length=1),
        limit: int = Query(30, ge=1, le=200),
        include_archived: bool = False,
    ) -> ConversationListResponse:
        org_id, user_id = cls.scoped_identity(request, org_id=org_id, user_id=user_id)
        return cls.service(request).list_conversations(
            org_id=org_id,
            user_id=user_id,
            limit=limit,
            include_archived=include_archived,
        )

    @classmethod
    def get_conversation(
        cls,
        request: Request,
        conversation_id: str,
        org_id: str | None = Query(None, min_length=1),
        user_id: str | None = Query(None, min_length=1),
    ) -> ConversationResponse:
        org_id, user_id = cls.scoped_identity(request, org_id=org_id, user_id=user_id)
        return cls.service(request).get_conversation(
            org_id=org_id,
            user_id=user_id,
            conversation_id=conversation_id,
        )

    @classmethod
    def get_messages(
        cls,
        request: Request,
        conversation_id: str,
        org_id: str | None = Query(None, min_length=1),
        user_id: str | None = Query(None, min_length=1),
        limit: int = Query(50, ge=1, le=200),
        include_deleted: bool = False,
    ) -> MessageListResponse:
        org_id, user_id = cls.scoped_identity(request, org_id=org_id, user_id=user_id)
        return cls.service(request).list_messages(
            org_id=org_id,
            user_id=user_id,
            conversation_id=conversation_id,
            limit=limit,
            include_deleted=include_deleted,
        )

    @classmethod
    def create_run(
        cls, request: Request, payload: CreateRunRequest
    ) -> CreateRunResponse:
        identity = RuntimeServiceAuthenticator.trusted_identity_from_request(request)
        if identity is not None:
            if payload.runtime_context is not None:
                raise HTTPException(
                    status.HTTP_403_FORBIDDEN, "runtime_context is server-owned"
                )
            payload = payload.model_copy(
                update={
                    "org_id": identity.org_id,
                    "user_id": identity.user_id,
                    "request_context": RuntimeRequestContext(
                        roles=identity.roles,
                        permission_scopes=identity.permission_scopes,
                        connector_scopes=identity.connector_scopes or {},
                    ),
                }
            )
        return cls.service(request).create_run(payload)

    @classmethod
    def get_run(
        cls,
        request: Request,
        run_id: str,
        org_id: str | None = Query(None, min_length=1),
        user_id: str | None = Query(None, min_length=1),
    ) -> RunStatusResponse:
        org_id, user_id = cls.scoped_identity(request, org_id=org_id, user_id=user_id)
        return cls.service(request).get_run(
            org_id=org_id, user_id=user_id, run_id=run_id
        )

    @classmethod
    def get_events(
        cls,
        request: Request,
        run_id: str,
        org_id: str | None = Query(None, min_length=1),
        user_id: str | None = Query(None, min_length=1),
        after_sequence: int = Query(0, ge=0),
        follow: bool = Query(True),
    ) -> RuntimeEventReplayResponse:
        org_id, user_id = cls.scoped_identity(request, org_id=org_id, user_id=user_id)
        return cls.service(request).replay_events(
            org_id=org_id,
            user_id=user_id,
            run_id=run_id,
            after_sequence=after_sequence,
        )

    @classmethod
    def stream_run(
        cls,
        request: Request,
        run_id: str,
        org_id: str | None = Query(None, min_length=1),
        user_id: str | None = Query(None, min_length=1),
        after_sequence: int = Query(0, ge=0),
        follow: bool = Query(True),
    ) -> StreamingResponse:
        org_id, user_id = cls.scoped_identity(request, org_id=org_id, user_id=user_id)
        return StreamingResponse(
            RuntimeSseAdapter.stream(
                service=cls.service(request),
                org_id=org_id,
                user_id=user_id,
                run_id=run_id,
                after_sequence=after_sequence,
                follow=follow,
            ),
            media_type=RuntimeSseAdapter.MEDIA_TYPE,
        )

    @classmethod
    def cancel_run(
        cls,
        request: Request,
        run_id: str,
        payload: CancelRunRequest,
        org_id: str | None = Query(None, min_length=1),
        user_id: str | None = Query(None, min_length=1),
    ) -> CancelRunResponse:
        org_id, user_id = cls.scoped_identity(request, org_id=org_id, user_id=user_id)
        payload = payload.model_copy(update={"requested_by_user_id": user_id})
        return cls.service(request).cancel_run(
            org_id=org_id,
            user_id=user_id,
            run_id=run_id,
            request=payload,
        )

    @classmethod
    def approval_decision(
        cls,
        request: Request,
        approval_id: str,
        payload: ApprovalDecisionRequest,
        org_id: str | None = Query(None, min_length=1),
    ) -> ApprovalDecisionResponse:
        identity = RuntimeServiceAuthenticator.trusted_identity_from_request(request)
        if identity is not None:
            org_id = identity.org_id
            payload = payload.model_copy(
                update={"decided_by_user_id": identity.user_id}
            )
        if org_id is None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "org_id is required")
        return cls.service(request).record_approval_decision(
            org_id=org_id,
            approval_id=approval_id,
            request=payload,
        )

    @classmethod
    def delete_user_history(
        cls,
        request: Request,
        org_id: str | None = Query(None, min_length=1),
        user_id: str | None = Query(None, min_length=1),
        reason: str | None = Query(None),
    ) -> HistoryDeletionResponse:
        org_id, user_id = cls.scoped_identity(request, org_id=org_id, user_id=user_id)
        return cls.service(request).delete_user_history(
            org_id=org_id, user_id=user_id, reason=reason
        )

    @classmethod
    def service(cls, request: Request) -> RuntimeApiService:
        """Return the configured application service."""

        return request.app.state.runtime_api_service

    @classmethod
    def scoped_identity(
        cls,
        request: Request,
        *,
        org_id: str | None,
        user_id: str | None,
    ) -> tuple[str, str]:
        identity = RuntimeServiceAuthenticator.trusted_identity_from_request(request)
        if identity is not None:
            return identity.org_id, identity.user_id
        if org_id is None or user_id is None:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, "org_id and user_id are required"
            )
        return org_id, user_id


class RuntimeApiRouter:
    """Build the v1 agent runtime router."""

    @classmethod
    def create_router(cls) -> APIRouter:
        router = APIRouter(prefix="/v1/agent", tags=["agent-runtime"])
        router.add_api_route(
            "/conversations",
            RuntimeApiRoutes.create_conversation,
            methods=["POST"],
            response_model=ConversationResponse,
            name=Keys.RouteName.CREATE_CONVERSATION,
        )
        router.add_api_route(
            "/conversations",
            RuntimeApiRoutes.list_conversations,
            methods=["GET"],
            response_model=ConversationListResponse,
            name=Keys.RouteName.LIST_CONVERSATIONS,
        )
        router.add_api_route(
            "/conversations/{conversation_id}",
            RuntimeApiRoutes.get_conversation,
            methods=["GET"],
            response_model=ConversationResponse,
            name=Keys.RouteName.GET_CONVERSATION,
        )
        router.add_api_route(
            "/conversations/{conversation_id}/messages",
            RuntimeApiRoutes.get_messages,
            methods=["GET"],
            response_model=MessageListResponse,
            name=Keys.RouteName.GET_MESSAGES,
        )
        router.add_api_route(
            "/runs",
            RuntimeApiRoutes.create_run,
            methods=["POST"],
            response_model=CreateRunResponse,
            name=Keys.RouteName.CREATE_RUN,
        )
        router.add_api_route(
            "/runs/{run_id}",
            RuntimeApiRoutes.get_run,
            methods=["GET"],
            response_model=RunStatusResponse,
            name=Keys.RouteName.GET_RUN,
        )
        router.add_api_route(
            "/runs/{run_id}/events",
            RuntimeApiRoutes.get_events,
            methods=["GET"],
            response_model=RuntimeEventReplayResponse,
            name=Keys.RouteName.GET_EVENTS,
        )
        router.add_api_route(
            "/runs/{run_id}/stream",
            RuntimeApiRoutes.stream_run,
            methods=["GET"],
            name=Keys.RouteName.STREAM_RUN,
        )
        router.add_api_route(
            "/runs/{run_id}/cancel",
            RuntimeApiRoutes.cancel_run,
            methods=["POST"],
            response_model=CancelRunResponse,
            name=Keys.RouteName.CANCEL_RUN,
        )
        router.add_api_route(
            "/approvals/{approval_id}/decision",
            RuntimeApiRoutes.approval_decision,
            methods=["POST"],
            response_model=ApprovalDecisionResponse,
            name=Keys.RouteName.APPROVAL_DECISION,
        )
        router.add_api_route(
            "/history",
            RuntimeApiRoutes.delete_user_history,
            methods=["DELETE"],
            response_model=HistoryDeletionResponse,
            name="delete_user_history",
        )
        return router
