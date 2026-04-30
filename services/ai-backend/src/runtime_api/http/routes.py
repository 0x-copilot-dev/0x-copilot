"""FastAPI routes for the runtime API."""

from __future__ import annotations

from fastapi import APIRouter, Query, Request
from fastapi.responses import StreamingResponse

from agent_runtime.api.constants import Keys
from agent_runtime.api.service import RuntimeApiService
from runtime_api.schemas import (
    ApprovalDecisionRequest,
    ApprovalDecisionResponse,
    CancelRunRequest,
    CancelRunResponse,
    ConversationResponse,
    CreateConversationRequest,
    CreateRunRequest,
    CreateRunResponse,
    MessageListResponse,
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
        return cls.service(request).create_conversation(payload)

    @classmethod
    def get_conversation(
        cls,
        request: Request,
        conversation_id: str,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> ConversationResponse:
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
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
        limit: int = Query(50, ge=1, le=200),
        include_deleted: bool = False,
    ) -> MessageListResponse:
        return cls.service(request).list_messages(
            org_id=org_id,
            user_id=user_id,
            conversation_id=conversation_id,
            limit=limit,
            include_deleted=include_deleted,
        )

    @classmethod
    def create_run(cls, request: Request, payload: CreateRunRequest) -> CreateRunResponse:
        return cls.service(request).create_run(payload)

    @classmethod
    def get_run(
        cls,
        request: Request,
        run_id: str,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> RunStatusResponse:
        return cls.service(request).get_run(org_id=org_id, user_id=user_id, run_id=run_id)

    @classmethod
    def get_events(
        cls,
        request: Request,
        run_id: str,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
        after_sequence: int = Query(0, ge=0),
    ) -> RuntimeEventReplayResponse:
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
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
        after_sequence: int = Query(0, ge=0),
    ) -> StreamingResponse:
        return StreamingResponse(
            RuntimeSseAdapter.stream(
                service=cls.service(request),
                org_id=org_id,
                user_id=user_id,
                run_id=run_id,
                after_sequence=after_sequence,
            ),
            media_type=RuntimeSseAdapter.MEDIA_TYPE,
        )

    @classmethod
    def cancel_run(
        cls,
        request: Request,
        run_id: str,
        payload: CancelRunRequest,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> CancelRunResponse:
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
        org_id: str = Query(..., min_length=1),
    ) -> ApprovalDecisionResponse:
        return cls.service(request).record_approval_decision(
            org_id=org_id,
            approval_id=approval_id,
            request=payload,
        )

    @classmethod
    def service(cls, request: Request) -> RuntimeApiService:
        """Return the configured application service."""

        return request.app.state.runtime_api_service


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
        return router
