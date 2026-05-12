"""FastAPI routes for the runtime API."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import Literal

from enterprise_service_contracts.scopes import (
    ADMIN_AUDIT_EXPORT,
    ADMIN_BUDGETS,
    ADMIN_USERS,
    AUDIT_READ,
    RUNTIME_USE,
)
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse

from agent_runtime.api.approval_coordinator import ApprovalCoordinator
from agent_runtime.api.constants import Keys
from agent_runtime.api.conversation_coordinator import ConversationCoordinator
from agent_runtime.api.conversation_query_service import ConversationQueryService
from agent_runtime.api.run_coordinator import RunCoordinator
from agent_runtime.api.usage_service import UsageQueryService
from agent_runtime.api.workspace_coordinator import WorkspaceCoordinator
from runtime_api.auth import RuntimeServiceAuthenticator
from runtime_api.rbac import RequireAnyScope, RequireScopes
from runtime_api.schemas import (
    ApprovalDecisionRequest,
    ApprovalDecisionResponse,
    ApprovalStatus,
    ApprovalUndoResponse,
    AssignedApprovalsResponse,
    CancelRunRequest,
    CancelRunResponse,
    ConversationConnectorScopesResponse,
    ConversationContextResponse,
    ConversationListResponse,
    ConversationResponse,
    CreateConversationRequest,
    CreateRunRequest,
    CreateRunResponse,
    HistoryDeletionResponse,
    MessageListResponse,
    ModelCatalogResponse,
    RuntimeRequestContext,
    RuntimeEventReplayResponse,
    RunStatusResponse,
    UpdateConversationConnectorsRequest,
)
from runtime_api.schemas.budgets import (
    BudgetCreateRequest,
    BudgetListResponse,
    BudgetMeResponse,
    BudgetMeRow,
    BudgetUpdateRequest,
    BudgetView,
)
from runtime_api.schemas.usage import (
    ConversationUsageResponse,
    RunUsageBreakdown,
    RunUsageCallRow,
    UsageConnectorRow,
    UsageConversationRow,
    UsageDailyRow,
    UsageMeResponse,
    UsageModelRow,
    UsageOrgPurposeResponse,
    UsageOrgResponse,
    UsageOrgSubagentsResponse,
    UsagePeriodWindow,
    UsagePurposeRow,
    UsageRunRow,
    UsageSubagentRow,
    UsageTotals,
)
from agent_runtime.budgets.period import BudgetPeriodCalculator
from agent_runtime.persistence.records import BudgetRecord, BudgetStatus
from runtime_api.http.workspace import register_workspace_feed_routes
from runtime_api.sse.adapter import RuntimeSseAdapter
from runtime_api.sse.event_bus import RuntimeEventBus
from runtime_api.sse.inbox_adapter import InboxSseAdapter
from runtime_api.sse.inbox_bus import InboxEventBus
from runtime_api.system_skills import (
    SystemSkillListResponse,
    SystemSkillsProjector,
)


class RuntimeApiRoutes:
    """Route handlers for the v1 agent runtime API."""

    @classmethod
    async def create_conversation(
        cls,
        request: Request,
        payload: CreateConversationRequest,
    ) -> ConversationResponse:
        identity = RuntimeServiceAuthenticator.trusted_identity_from_request(request)
        if identity is not None:
            payload = payload.model_copy(
                update={"org_id": identity.org_id, "user_id": identity.user_id}
            )
        return await cls.conversation_coordinator(request).create_conversation(payload)

    @classmethod
    async def list_conversations(
        cls,
        request: Request,
        org_id: str | None = Query(None, min_length=1),
        user_id: str | None = Query(None, min_length=1),
        limit: int = Query(30, ge=1, le=200),
        include_archived: bool = False,
        include_deleted: bool = False,
    ) -> ConversationListResponse:
        org_id, user_id = cls.scoped_identity(request, org_id=org_id, user_id=user_id)
        return await cls.cqs(request).list_conversations(
            org_id=org_id,
            user_id=user_id,
            limit=limit,
            include_archived=include_archived,
            include_deleted=include_deleted,
        )

    @classmethod
    async def get_conversation(
        cls,
        request: Request,
        conversation_id: str,
        org_id: str | None = Query(None, min_length=1),
        user_id: str | None = Query(None, min_length=1),
    ) -> ConversationResponse:
        org_id, user_id = cls.scoped_identity(request, org_id=org_id, user_id=user_id)
        return await cls.cqs(request).get_conversation(
            org_id=org_id,
            user_id=user_id,
            conversation_id=conversation_id,
        )

    @classmethod
    async def get_messages(
        cls,
        request: Request,
        conversation_id: str,
        org_id: str | None = Query(None, min_length=1),
        user_id: str | None = Query(None, min_length=1),
        limit: int = Query(50, ge=1, le=200),
        include_deleted: bool = False,
    ) -> MessageListResponse:
        org_id, user_id = cls.scoped_identity(request, org_id=org_id, user_id=user_id)
        return await cls.cqs(request).list_messages(
            org_id=org_id,
            user_id=user_id,
            conversation_id=conversation_id,
            limit=limit,
            include_deleted=include_deleted,
        )

    @classmethod
    async def get_conversation_context(
        cls,
        request: Request,
        conversation_id: str,
        org_id: str | None = Query(None, min_length=1),
        user_id: str | None = Query(None, min_length=1),
    ) -> ConversationContextResponse:
        org_id, user_id = cls.scoped_identity(request, org_id=org_id, user_id=user_id)
        return await cls.cqs(request).get_conversation_context(
            org_id=org_id,
            user_id=user_id,
            conversation_id=conversation_id,
        )

    @classmethod
    async def update_conversation_connectors(
        cls,
        request: Request,
        conversation_id: str,
        payload: UpdateConversationConnectorsRequest,
        org_id: str | None = Query(None, min_length=1),
        user_id: str | None = Query(None, min_length=1),
    ) -> ConversationConnectorScopesResponse:
        org_id, user_id = cls.scoped_identity(request, org_id=org_id, user_id=user_id)
        # PR 1.2.1 — workspace admin can override on a member's chat. The
        # ``ADMIN_USERS`` scope is the existing user-admin role; the audit
        # row distinguishes admin overrides from owner self-PATCHes via
        # the ``override_by_admin`` metadata flag.
        identity = RuntimeServiceAuthenticator.trusted_identity_from_request(request)
        allow_admin_override = (
            identity is not None and ADMIN_USERS in identity.permission_scopes
        )
        return await cls.conversation_coordinator(
            request
        ).update_conversation_connectors(
            org_id=org_id,
            user_id=user_id,
            conversation_id=conversation_id,
            request=payload,
            allow_admin_override=allow_admin_override,
        )

    @classmethod
    def list_models(
        cls,
        request: Request,
        org_id: str | None = Query(None, min_length=1),
        user_id: str | None = Query(None, min_length=1),
    ) -> ModelCatalogResponse:
        # list_models is pure in-memory (no port calls) — keep sync.
        cls.scoped_identity(request, org_id=org_id, user_id=user_id)
        return cls.cqs(request).list_models()

    @classmethod
    async def create_run(
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
        return await cls.run_coordinator(request).create_run(payload)

    @classmethod
    async def get_run(
        cls,
        request: Request,
        run_id: str,
        org_id: str | None = Query(None, min_length=1),
        user_id: str | None = Query(None, min_length=1),
    ) -> RunStatusResponse:
        org_id, user_id = cls.scoped_identity(request, org_id=org_id, user_id=user_id)
        return await cls.cqs(request).get_run(
            org_id=org_id, user_id=user_id, run_id=run_id
        )

    @classmethod
    async def get_events(
        cls,
        request: Request,
        run_id: str,
        org_id: str | None = Query(None, min_length=1),
        user_id: str | None = Query(None, min_length=1),
        after_sequence: int = Query(0, ge=0),
    ) -> RuntimeEventReplayResponse:
        org_id, user_id = cls.scoped_identity(request, org_id=org_id, user_id=user_id)
        return await cls.cqs(request).replay_events(
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
        event_bus: RuntimeEventBus | None = getattr(
            request.app.state, "runtime_event_bus", None
        )
        return StreamingResponse(
            RuntimeSseAdapter.stream(
                service=cls.cqs(request),
                org_id=org_id,
                user_id=user_id,
                run_id=run_id,
                after_sequence=after_sequence,
                follow=follow,
                event_bus=event_bus,
            ),
            media_type=RuntimeSseAdapter.MEDIA_TYPE,
        )

    @classmethod
    async def cancel_run(
        cls,
        request: Request,
        run_id: str,
        payload: CancelRunRequest,
        org_id: str | None = Query(None, min_length=1),
        user_id: str | None = Query(None, min_length=1),
    ) -> CancelRunResponse:
        org_id, user_id = cls.scoped_identity(request, org_id=org_id, user_id=user_id)
        payload = payload.model_copy(update={"requested_by_user_id": user_id})
        return await cls.run_coordinator(request).cancel_run(
            org_id=org_id,
            user_id=user_id,
            run_id=run_id,
            request=payload,
        )

    @classmethod
    async def list_approvals(
        cls,
        request: Request,
        assigned_to_me: bool = Query(False),
        status_filter: str = Query("pending", alias="status", min_length=1),
        limit: int = Query(50, ge=1, le=200),
        cursor: str | None = Query(None, min_length=1),
        org_id: str | None = Query(None, min_length=1),
        user_id: str | None = Query(None, min_length=1),
    ) -> AssignedApprovalsResponse:
        """Recipient inbox endpoint (PR 1.4.1 Gap #6).

        Today only ``assigned_to_me=true`` is supported; without it the
        route returns an empty list (open for future filters like
        ``my_team`` without changing the wire shape). Status filter
        defaults to ``pending`` since the inbox is action-oriented.
        """

        org_id, user_id = cls.scoped_identity(request, org_id=org_id, user_id=user_id)
        if not assigned_to_me:
            return AssignedApprovalsResponse(approvals=(), next_cursor=None)
        try:
            status_value = ApprovalStatus(status_filter)
        except ValueError as exc:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "Unsupported status filter for assigned approvals.",
            ) from exc
        return await cls.approval_coordinator(request).list_assigned_approvals(
            org_id=org_id,
            user_id=user_id,
            status_filter=status_value,
            limit=limit,
            cursor=cursor,
        )

    @classmethod
    async def stream_inbox(
        cls,
        request: Request,
        after_sequence: int = Query(0, ge=0),
        org_id: str | None = Query(None, min_length=1),
        user_id: str | None = Query(None, min_length=1),
    ) -> StreamingResponse:
        """Per-user inbox SSE channel (PR 1.4.1 Gap #6).

        One channel per user; carries ``approval_assigned`` and
        ``approval_resolved`` envelopes for approvals where this user is
        the requester. Mirrors the run-stream's ``?after_sequence=N``
        reconnect contract.
        """

        org_id, user_id = cls.scoped_identity(request, org_id=org_id, user_id=user_id)
        del org_id  # Inbox is per-user, not per-org; tenant isolation
        # is enforced by the bus's user-keyed subscriptions.
        bus = InboxEventBus.get_default()
        return StreamingResponse(
            InboxSseAdapter.stream(
                bus=bus,
                user_id=user_id,
                after_sequence=after_sequence,
                follow=True,
            ),
            media_type=InboxSseAdapter.MEDIA_TYPE,
        )

    @classmethod
    async def approval_decision(
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
        return await cls.approval_coordinator(request).record_approval_decision(
            org_id=org_id,
            approval_id=approval_id,
            request=payload,
        )

    @classmethod
    async def approval_undo(
        cls,
        request: Request,
        approval_id: str,
        org_id: str | None = Query(None, min_length=1),
        user_id: str | None = Query(None, min_length=1),
    ) -> ApprovalUndoResponse:
        """PR 4.4.6.4 — record an undo request within the 60s window."""

        org_id, user_id = cls.scoped_identity(request, org_id=org_id, user_id=user_id)
        return await cls.approval_coordinator(request).request_approval_undo(
            org_id=org_id,
            approval_id=approval_id,
            decided_by_user_id=user_id,
        )

    @classmethod
    async def delete_user_history(
        cls,
        request: Request,
        org_id: str | None = Query(None, min_length=1),
        user_id: str | None = Query(None, min_length=1),
        reason: str | None = Query(None),
    ) -> HistoryDeletionResponse:
        org_id, user_id = cls.scoped_identity(request, org_id=org_id, user_id=user_id)
        return await cls.conversation_coordinator(request).delete_user_history(
            org_id=org_id, user_id=user_id, reason=reason
        )

    @classmethod
    def run_coordinator(cls, request: Request) -> RunCoordinator:
        return request.app.state.run_coordinator

    @classmethod
    def approval_coordinator(cls, request: Request) -> ApprovalCoordinator:
        return request.app.state.approval_coordinator

    @classmethod
    def conversation_coordinator(cls, request: Request) -> ConversationCoordinator:
        return request.app.state.conversation_coordinator

    @classmethod
    def cqs(cls, request: Request) -> ConversationQueryService:
        return request.app.state.conversation_query_service

    @classmethod
    def workspace_coordinator(cls, request: Request) -> WorkspaceCoordinator:
        return request.app.state.workspace_coordinator

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
        # A10: every /v1/agent/* route requires the runtime:use scope.
        # Router-level dependency applies to every route registered
        # below — admins, employees, and service accounts all carry
        # runtime:use per the seeded role catalog (0004b).
        router = APIRouter(
            prefix="/v1/agent",
            tags=["agent-runtime"],
            dependencies=[Depends(RequireScopes(RUNTIME_USE))],
        )
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
            "/conversations/{conversation_id}/context",
            RuntimeApiRoutes.get_conversation_context,
            methods=["GET"],
            response_model=ConversationContextResponse,
            name=Keys.RouteName.GET_CONVERSATION_CONTEXT,
        )
        router.add_api_route(
            "/conversations/{conversation_id}/connectors",
            RuntimeApiRoutes.update_conversation_connectors,
            methods=["PATCH"],
            response_model=ConversationConnectorScopesResponse,
            name=Keys.RouteName.UPDATE_CONVERSATION_CONNECTORS,
        )
        router.add_api_route(
            "/models",
            RuntimeApiRoutes.list_models,
            methods=["GET"],
            response_model=ModelCatalogResponse,
            name=Keys.RouteName.LIST_MODELS,
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
            "/approvals/{approval_id}/undo",
            RuntimeApiRoutes.approval_undo,
            methods=["POST"],
            response_model=ApprovalUndoResponse,
            name=Keys.RouteName.APPROVAL_UNDO,
        )
        # PR 1.4.1 — recipient inbox endpoint + per-user SSE channel.
        router.add_api_route(
            "/approvals",
            RuntimeApiRoutes.list_approvals,
            methods=["GET"],
            response_model=AssignedApprovalsResponse,
            name=Keys.RouteName.LIST_APPROVALS,
        )
        router.add_api_route(
            "/me/inbox/stream",
            RuntimeApiRoutes.stream_inbox,
            methods=["GET"],
            name=Keys.RouteName.STREAM_INBOX,
        )
        router.add_api_route(
            "/history",
            RuntimeApiRoutes.delete_user_history,
            methods=["DELETE"],
            response_model=HistoryDeletionResponse,
            name=Keys.RouteName.DELETE_USER_HISTORY,
        )
        # PR 1.3 — Workspace-pane draft artifacts.
        from runtime_api.http.drafts import register_draft_routes

        register_draft_routes(router)
        # PR 1.5 — Workspace-pane data feeds (subagents + sources).
        register_workspace_feed_routes(router)
        # PR 1.6 — Workspace defaults + conversation lifecycle
        # (PATCH / DELETE / restore). Lives in its own module to
        # minimise merge friction with PR 1.4.1 (approval forwarding).
        from runtime_api.http.workspace_defaults_routes import (
            register_workspace_defaults_routes,
        )

        register_workspace_defaults_routes(router)
        # PR 4.3 — Workspace data lifecycle stubs (export queue +
        # delete-all-attempt audit). Same router, separate module.
        from runtime_api.http.workspace_data_routes import (
            register_workspace_data_routes,
        )

        register_workspace_data_routes(router)
        # PR 6.1 — conversation sharing (create / list / update / revoke
        # + recipient view). Mounted before the fork routes below so the
        # ``/v1/agent/shares/{share_token}`` GET registers ahead of the
        # ``/v1/agent/shares/{share_token}/fork`` POST that PR 6.2 added.
        from runtime_api.http.share_routes import register_share_routes

        register_share_routes(router)
        # PR 6.2 — recipient forks a shared conversation into their own
        # workspace. The handler is a thin shim over
        # ``ConversationForkService`` mounted on ``app.state``; if the
        # service isn't wired the route returns 503 (same opacity as
        # other optional services).
        from runtime_api.http.share_fork_routes import register_share_fork_routes

        register_share_fork_routes(router)
        # PR A3 / 8.0.3c — owner forks their own conversation from a
        # specific message (Retry from here). Mounted alongside the
        # share-fork POST so the route table groups the two fork
        # pathways. Same 503-when-not-configured opacity.
        from runtime_api.http.self_fork_routes import register_self_fork_routes

        register_self_fork_routes(router)
        return router


class UsageApiRoutes:
    """Read endpoints for token usage + cost (B4).

    Backed by ``runtime_usage_daily_user`` / ``runtime_usage_daily_org``
    rollup tables when warm; fall back to a 30-day-capped scan of
    ``runtime_run_usage`` otherwise. The cold-start fallback is a stop-gap
    until the rollup loop has finished its first pass — reads are bounded
    to 30 days so an accidental cold-start can't trigger a full-table
    scan.
    """

    _COLD_START_CAP_DAYS = 30

    @classmethod
    async def usage_me(
        cls,
        request: Request,
        period: Literal["today", "7d", "30d", "month"] = Query("7d"),
        org_id: str | None = Query(None, min_length=1),
        user_id: str | None = Query(None, min_length=1),
    ) -> UsageMeResponse:
        org_id, user_id = RuntimeApiRoutes.scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        start, end = UsageQueryService.parse_period(period)
        persistence = cls._persistence(request)
        rows = await persistence.query_user_daily_usage(
            org_id=org_id,
            user_id=user_id,
            start_day=start,
            end_day=end,
        )
        cold_start = False
        if not rows:
            cold_start = True
            rows = cls._cold_start_user_rollup(
                await persistence.query_run_usage_for_range(
                    org_id=org_id,
                    user_id=user_id,
                    start=cls._cap_cold_start(start, end),
                    end=end,
                )
            )
        total = cls._totals_from_rows(rows)
        by_connector = await cls._user_connector_breakdown(
            persistence,
            org_id=org_id,
            user_id=user_id,
            start=cls._cap_cold_start(start, end),
            end=end,
        )
        return UsageMeResponse(
            period=UsagePeriodWindow(start=start, end=end),
            total=total,
            by_day=cls._rows_by_day(rows),
            by_model=cls._rows_by_model(rows),
            by_connector=by_connector,
            cold_start_fallback=cold_start,
        )

    @classmethod
    async def usage_me_conversations(
        cls,
        request: Request,
        period: Literal["today", "7d", "30d", "month"] = Query("7d"),
        limit: int = Query(10, ge=1, le=100),
        org_id: str | None = Query(None, min_length=1),
        user_id: str | None = Query(None, min_length=1),
    ) -> tuple[UsageConversationRow, ...]:
        org_id, user_id = RuntimeApiRoutes.scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        start, end = UsageQueryService.parse_period(period)
        persistence = cls._persistence(request)
        rows = await persistence.query_top_conversations(
            org_id=org_id,
            user_id=user_id,
            start=start,
            end=end,
            limit=limit,
        )
        return tuple(
            UsageConversationRow(
                conversation_id=r.conversation_id,
                title=getattr(r, "title", None),
                input=int(getattr(r, "input_tokens", 0) or 0),
                output=int(getattr(r, "output_tokens", 0) or 0),
                cached_input=int(getattr(r, "cached_input_tokens", 0) or 0),
                total=int(getattr(r, "total_tokens", 0) or 0),
                runs_count=int(getattr(r, "runs_count", 0) or 0),
                cost_micro_usd=getattr(r, "cost_micro_usd", None),
            )
            for r in rows
        )

    @classmethod
    async def usage_run(
        cls,
        request: Request,
        run_id: str,
        org_id: str | None = Query(None, min_length=1),
        user_id: str | None = Query(None, min_length=1),
    ) -> RunUsageBreakdown:
        org_id, _ = RuntimeApiRoutes.scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        persistence = cls._persistence(request)
        run_row = await persistence.query_run_usage(org_id=org_id, run_id=run_id)
        if run_row is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "run not found")
        call_rows = await persistence.query_model_call_usage_for_run(
            org_id=org_id, run_id=run_id
        )
        return RunUsageBreakdown(
            run_id=run_row.run_id,
            org_id=run_row.org_id,
            user_id=run_row.user_id,
            conversation_id=run_row.conversation_id,
            model_provider=run_row.model_provider,
            model_name=run_row.model_name,
            started_at=run_row.started_at,
            completed_at=run_row.completed_at,
            duration_ms=run_row.duration_ms,
            chunk_count=run_row.chunk_count,
            status=run_row.status,
            total=UsageTotals(
                input=run_row.input_tokens,
                output=run_row.output_tokens,
                cached_input=run_row.cached_input_tokens,
                total=run_row.total_tokens,
                runs_count=1,
                cost_micro_usd=run_row.cost_micro_usd,
            ),
            by_call=tuple(
                RunUsageCallRow(
                    id=row.id,
                    parent_event_id=row.parent_event_id,
                    task_id=row.task_id,
                    subagent_id=row.subagent_id,
                    model_provider=row.model_provider,
                    model_name=row.model_name,
                    input=row.input_tokens,
                    output=row.output_tokens,
                    cached_input=row.cached_input_tokens,
                    total=row.total_tokens,
                    duration_ms=row.duration_ms,
                    cost_micro_usd=row.cost_micro_usd,
                    created_at=row.created_at,
                )
                for row in call_rows
            ),
        )

    @classmethod
    async def usage_conversation(
        cls,
        request: Request,
        conversation_id: str,
        period: Literal["today", "7d", "30d", "month"] = Query("30d"),
        org_id: str | None = Query(None, min_length=1),
        user_id: str | None = Query(None, min_length=1),
    ) -> ConversationUsageResponse:
        org_id, user_id = RuntimeApiRoutes.scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        start, end = UsageQueryService.parse_period(period)
        persistence = cls._persistence(request)
        # Reuse query_run_usage_for_range and filter by conversation_id —
        # this avoids a second port method while remaining bounded by
        # the period window.
        rows = [
            r
            for r in await persistence.query_run_usage_for_range(
                org_id=org_id,
                user_id=user_id,
                start=start,
                end=end,
            )
            if r.conversation_id == conversation_id
        ]
        total = UsageTotals(
            input=sum(r.input_tokens for r in rows),
            output=sum(r.output_tokens for r in rows),
            cached_input=sum(r.cached_input_tokens for r in rows),
            total=sum(r.total_tokens for r in rows),
            runs_count=len(rows),
            cost_micro_usd=cls._sum_costs(rows),
        )
        # Per-connector breakdown for this conversation: scan per-call
        # rows (no rollup table for per-conversation; live computation).
        call_rows = [
            row
            for row in await persistence.query_model_call_usage_for_range(
                org_id=org_id, start=start, end=end
            )
            if row.conversation_id == conversation_id
        ]
        by_connector = cls._connector_rows_from_calls(call_rows)
        return ConversationUsageResponse(
            conversation_id=conversation_id,
            period=UsagePeriodWindow(start=start, end=end),
            total=total,
            by_run=tuple(
                UsageRunRow(
                    run_id=r.run_id,
                    started_at=r.started_at,
                    completed_at=r.completed_at,
                    status=r.status,
                    total=UsageTotals(
                        input=r.input_tokens,
                        output=r.output_tokens,
                        cached_input=r.cached_input_tokens,
                        total=r.total_tokens,
                        runs_count=1,
                        cost_micro_usd=r.cost_micro_usd,
                    ),
                )
                for r in rows
            ),
            by_connector=by_connector,
        )

    @classmethod
    async def usage_org(
        cls,
        request: Request,
        period: Literal["today", "7d", "30d", "month"] = Query("month"),
        org_id: str | None = Query(None, min_length=1),
    ) -> UsageOrgResponse:
        # ``user_id`` is unused for the admin org view but the standard
        # scoped_identity helper requires it; pass a placeholder so the
        # facade-derived identity can override.
        org_id, _ = RuntimeApiRoutes.scoped_identity(
            request, org_id=org_id, user_id="__org_admin__"
        )
        start, end = UsageQueryService.parse_period(period)
        persistence = cls._persistence(request)
        rows = await persistence.query_org_daily_usage(
            org_id=org_id, start_day=start, end_day=end
        )
        cold_start = False
        if not rows:
            cold_start = True
            run_rows = await persistence.query_run_usage_for_range(
                org_id=org_id,
                user_id=None,
                start=cls._cap_cold_start(start, end),
                end=end,
            )
            rows = UsageQueryService.rollup_org_rows(
                run_rows, refreshed_at=datetime.now(timezone.utc)
            )
        total = cls._totals_from_rows(rows)
        by_connector = await cls._org_connector_breakdown(
            persistence,
            org_id=org_id,
            start=cls._cap_cold_start(start, end),
            end=end,
        )
        return UsageOrgResponse(
            period=UsagePeriodWindow(start=start, end=end),
            total=total,
            by_day=cls._rows_by_day(rows),
            by_model=cls._rows_by_model(rows),
            by_connector=by_connector,
            cold_start_fallback=cold_start,
        )

    # --- helpers -----------------------------------------------------------

    @classmethod
    def _persistence(cls, request: Request):  # type: ignore[no-untyped-def]
        return request.app.state.runtime_persistence

    @classmethod
    def _cap_cold_start(cls, start: datetime, end: datetime) -> datetime:
        """Clamp the cold-start scan window to ``_COLD_START_CAP_DAYS``."""

        from datetime import timedelta

        capped = end - timedelta(days=cls._COLD_START_CAP_DAYS)
        return max(start, capped)

    @classmethod
    def _cold_start_user_rollup(cls, run_rows):  # type: ignore[no-untyped-def]
        return UsageQueryService.rollup_user_rows(
            run_rows, refreshed_at=datetime.now(timezone.utc)
        )

    @classmethod
    def _totals_from_rows(cls, rows) -> UsageTotals:  # type: ignore[no-untyped-def]
        input_tokens = sum(r.input_tokens for r in rows)
        output_tokens = sum(r.output_tokens for r in rows)
        cached_input_tokens = sum(r.cached_input_tokens for r in rows)
        total_tokens = sum(r.total_tokens for r in rows)
        runs_count = sum(r.runs_count for r in rows)
        cost = cls._sum_costs(rows)
        return UsageTotals(
            input=input_tokens,
            output=output_tokens,
            cached_input=cached_input_tokens,
            total=total_tokens,
            runs_count=runs_count,
            cost_micro_usd=cost,
        )

    @classmethod
    def _rows_by_day(cls, rows) -> tuple[UsageDailyRow, ...]:  # type: ignore[no-untyped-def]
        per_day: dict[str, dict[str, int | None]] = defaultdict(
            lambda: {
                "input": 0,
                "output": 0,
                "cached_input": 0,
                "total": 0,
                "runs_count": 0,
                "cost_micro_usd": None,
            }
        )
        for r in rows:
            day = r.day.date().isoformat() if hasattr(r.day, "date") else str(r.day)
            bucket = per_day[day]
            bucket["input"] = (bucket["input"] or 0) + int(r.input_tokens)
            bucket["output"] = (bucket["output"] or 0) + int(r.output_tokens)
            bucket["cached_input"] = (bucket["cached_input"] or 0) + int(
                r.cached_input_tokens
            )
            bucket["total"] = (bucket["total"] or 0) + int(r.total_tokens)
            bucket["runs_count"] = (bucket["runs_count"] or 0) + int(r.runs_count)
            if r.cost_micro_usd is not None:
                bucket["cost_micro_usd"] = (
                    bucket["cost_micro_usd"] or 0
                ) + r.cost_micro_usd
        return tuple(
            UsageDailyRow(day=day, **bucket)  # type: ignore[arg-type]
            for day, bucket in sorted(per_day.items())
        )

    @classmethod
    def _rows_by_model(cls, rows) -> tuple[UsageModelRow, ...]:  # type: ignore[no-untyped-def]
        per_model: dict[tuple[str, str], dict[str, int | None]] = defaultdict(
            lambda: {
                "input": 0,
                "output": 0,
                "cached_input": 0,
                "total": 0,
                "runs_count": 0,
                "cost_micro_usd": None,
            }
        )
        for r in rows:
            key = (r.model_provider, r.model_name)
            bucket = per_model[key]
            bucket["input"] = (bucket["input"] or 0) + int(r.input_tokens)
            bucket["output"] = (bucket["output"] or 0) + int(r.output_tokens)
            bucket["cached_input"] = (bucket["cached_input"] or 0) + int(
                r.cached_input_tokens
            )
            bucket["total"] = (bucket["total"] or 0) + int(r.total_tokens)
            bucket["runs_count"] = (bucket["runs_count"] or 0) + int(r.runs_count)
            if r.cost_micro_usd is not None:
                bucket["cost_micro_usd"] = (
                    bucket["cost_micro_usd"] or 0
                ) + r.cost_micro_usd
        return tuple(
            UsageModelRow(provider=provider, model=model, **bucket)  # type: ignore[arg-type]
            for (provider, model), bucket in sorted(per_model.items())
        )

    @staticmethod
    def _sum_costs(rows) -> int | None:  # type: ignore[no-untyped-def]
        total: int | None = None
        for r in rows:
            cost = getattr(r, "cost_micro_usd", None)
            if cost is None:
                continue
            total = (total or 0) + cost
        return total

    @classmethod
    async def _user_connector_breakdown(
        cls,
        persistence,  # type: ignore[no-untyped-def]
        *,
        org_id: str,
        user_id: str,
        start: datetime,
        end: datetime,
    ) -> tuple[UsageConnectorRow, ...]:
        """PR 7.2 — per-connector breakdown for ``/v1/usage/me``.

        No per-(org, user, connector) rollup exists. We compute live
        from per-call rows filtered to this user. The window is bounded
        by the same cold-start cap the rest of /usage/me uses, so this
        is at most a 30-day per-call scan.
        """

        runs = await persistence.query_run_usage_for_range(
            org_id=org_id, user_id=user_id, start=start, end=end
        )
        if not runs:
            return ()
        run_ids = {row.run_id for row in runs}
        call_rows = [
            row
            for row in await persistence.query_model_call_usage_for_range(
                org_id=org_id, start=start, end=end
            )
            if row.run_id in run_ids
        ]
        return cls._connector_rows_from_calls(call_rows)

    @classmethod
    async def _org_connector_breakdown(
        cls,
        persistence,  # type: ignore[no-untyped-def]
        *,
        org_id: str,
        start: datetime,
        end: datetime,
    ) -> tuple[UsageConnectorRow, ...]:
        """PR 7.2 — per-connector breakdown for ``/v1/usage/org``.

        Tries the rollup table first; falls back to a live scan of
        per-call rows when empty (cold-start). Same posture as the
        existing ``cold_start_fallback`` for the by-day / by-model
        axes — by_connector is silently rebuilt; the response's
        single ``cold_start_fallback`` flag covers both.
        """

        rollup_rows = await persistence.query_connector_daily_usage(
            org_id=org_id, start_day=start, end_day=end
        )
        if rollup_rows:
            return cls._connector_rows_from_rollup(rollup_rows)
        call_rows = await persistence.query_model_call_usage_for_range(
            org_id=org_id, start=start, end=end
        )
        return cls._connector_rows_from_calls(call_rows)

    @classmethod
    def _connector_rows_from_calls(cls, call_rows) -> tuple[UsageConnectorRow, ...]:  # type: ignore[no-untyped-def]
        per_connector: dict[str, dict[str, int | None | set[str]]] = defaultdict(
            lambda: {
                "input": 0,
                "output": 0,
                "cached_input": 0,
                "total": 0,
                "run_ids": set(),
                "cost_micro_usd": None,
            }
        )
        for row in call_rows:
            slug = row.connector_slug or ""
            bucket = per_connector[slug]
            bucket["input"] = (bucket["input"] or 0) + int(row.input_tokens)
            bucket["output"] = (bucket["output"] or 0) + int(row.output_tokens)
            bucket["cached_input"] = (bucket["cached_input"] or 0) + int(
                row.cached_input_tokens
            )
            bucket["total"] = (bucket["total"] or 0) + int(row.total_tokens)
            run_ids = bucket["run_ids"]
            assert isinstance(run_ids, set)
            run_ids.add(row.run_id)
            if row.cost_micro_usd is not None:
                bucket["cost_micro_usd"] = (
                    bucket["cost_micro_usd"] or 0
                ) + row.cost_micro_usd
        return tuple(
            UsageConnectorRow(
                connector_slug=slug,
                input=int(bucket["input"] or 0),
                output=int(bucket["output"] or 0),
                cached_input=int(bucket["cached_input"] or 0),
                total=int(bucket["total"] or 0),
                runs_count=len(bucket["run_ids"])
                if isinstance(bucket["run_ids"], set)
                else 0,
                cost_micro_usd=bucket["cost_micro_usd"],
            )
            for slug, bucket in sorted(per_connector.items())
        )

    @classmethod
    def _connector_rows_from_rollup(cls, rollup_rows) -> tuple[UsageConnectorRow, ...]:  # type: ignore[no-untyped-def]
        # The rollup table is keyed on (org, day, slug, model_name) after
        # 01d. The /org by_connector axis collapses model_name so the
        # existing FE contract is unchanged; the new /org/subagents and
        # /org/purpose endpoints expose the model dimension instead.
        per_connector: dict[str, dict[str, int | None]] = defaultdict(
            lambda: {
                "input": 0,
                "output": 0,
                "cached_input": 0,
                "total": 0,
                "runs_count": 0,
                "cost_micro_usd": None,
            }
        )
        for row in rollup_rows:
            bucket = per_connector[row.connector_slug]
            bucket["input"] = (bucket["input"] or 0) + int(row.input_tokens)
            bucket["output"] = (bucket["output"] or 0) + int(row.output_tokens)
            bucket["cached_input"] = (bucket["cached_input"] or 0) + int(
                row.cached_input_tokens
            )
            bucket["total"] = (bucket["total"] or 0) + int(row.total_tokens)
            bucket["runs_count"] = (bucket["runs_count"] or 0) + int(row.runs_count)
            if row.cost_micro_usd is not None:
                bucket["cost_micro_usd"] = (
                    bucket["cost_micro_usd"] or 0
                ) + row.cost_micro_usd
        return tuple(
            UsageConnectorRow(connector_slug=slug, **bucket)  # type: ignore[arg-type]
            for slug, bucket in sorted(per_connector.items())
        )

    @classmethod
    async def usage_org_subagents(
        cls,
        request: Request,
        period: Literal["today", "7d", "30d", "month"] = Query("month"),
        org_id: str | None = Query(None, min_length=1),
    ) -> UsageOrgSubagentsResponse:
        """Sub-PRD 01d — org-scoped per-subagent breakdown.

        Reads from the rollup table first; falls back to live
        per-call scan + ``rollup_subagent_rows`` within the
        cold-start cap when the rollup is empty.
        """

        org_id, _ = RuntimeApiRoutes.scoped_identity(
            request, org_id=org_id, user_id="__org_admin__"
        )
        start, end = UsageQueryService.parse_period(period)
        persistence = cls._persistence(request)
        rollup_rows = await persistence.query_subagent_daily_usage(
            org_id=org_id, start_day=start, end_day=end
        )
        cold_start = False
        if rollup_rows:
            rows = cls._subagent_rows_from_rollup(rollup_rows)
        else:
            cold_start = True
            call_rows = await persistence.query_model_call_usage_for_range(
                org_id=org_id,
                start=cls._cap_cold_start(start, end),
                end=end,
            )
            synthesized = UsageQueryService.rollup_subagent_rows(
                call_rows, refreshed_at=datetime.now(timezone.utc)
            )
            rows = cls._subagent_rows_from_rollup(synthesized)
        return UsageOrgSubagentsResponse(
            period=UsagePeriodWindow(start=start, end=end),
            rows=rows,
            cold_start_fallback=cold_start,
        )

    @classmethod
    async def usage_org_purpose(
        cls,
        request: Request,
        period: Literal["today", "7d", "30d", "month"] = Query("month"),
        org_id: str | None = Query(None, min_length=1),
    ) -> UsageOrgPurposeResponse:
        """Sub-PRD 01d — org-scoped per-purpose breakdown.

        Same posture as ``usage_org_subagents``: rollup-first with a
        live-scan cold-start fallback bounded by the standard
        ``_COLD_START_CAP_DAYS`` window.
        """

        org_id, _ = RuntimeApiRoutes.scoped_identity(
            request, org_id=org_id, user_id="__org_admin__"
        )
        start, end = UsageQueryService.parse_period(period)
        persistence = cls._persistence(request)
        rollup_rows = await persistence.query_purpose_daily_usage(
            org_id=org_id, start_day=start, end_day=end
        )
        cold_start = False
        if rollup_rows:
            rows = cls._purpose_rows_from_rollup(rollup_rows)
        else:
            cold_start = True
            call_rows = await persistence.query_model_call_usage_for_range(
                org_id=org_id,
                start=cls._cap_cold_start(start, end),
                end=end,
            )
            synthesized = UsageQueryService.rollup_purpose_rows(
                call_rows, refreshed_at=datetime.now(timezone.utc)
            )
            rows = cls._purpose_rows_from_rollup(synthesized)
        return UsageOrgPurposeResponse(
            period=UsagePeriodWindow(start=start, end=end),
            rows=rows,
            cold_start_fallback=cold_start,
        )

    @classmethod
    def _subagent_rows_from_rollup(cls, rollup_rows) -> tuple[UsageSubagentRow, ...]:  # type: ignore[no-untyped-def]
        """Project rollup records to wire rows, sorted by cost desc."""

        wire_rows = tuple(
            UsageSubagentRow(
                subagent_slug=row.subagent_slug,
                model_provider=row.model_provider,
                model_name=row.model_name,
                call_count=int(row.call_count),
                input=int(row.input_tokens),
                output=int(row.output_tokens),
                cached_input=int(row.cached_input_tokens),
                cache_creation_input=int(row.cache_creation_input_tokens),
                reasoning=int(row.reasoning_tokens),
                audio_input=int(row.audio_input_tokens),
                audio_output=int(row.audio_output_tokens),
                total=int(row.total_tokens),
                cost_micro_usd=row.cost_micro_usd,
            )
            for row in rollup_rows
        )
        return tuple(
            sorted(
                wire_rows,
                key=lambda r: (
                    -(r.cost_micro_usd if r.cost_micro_usd is not None else 0),
                    -r.total,
                    r.subagent_slug,
                ),
            )
        )

    @classmethod
    def _purpose_rows_from_rollup(cls, rollup_rows) -> tuple[UsagePurposeRow, ...]:  # type: ignore[no-untyped-def]
        wire_rows = tuple(
            UsagePurposeRow(
                purpose=row.purpose,
                model_provider=row.model_provider,
                model_name=row.model_name,
                call_count=int(row.call_count),
                input=int(row.input_tokens),
                output=int(row.output_tokens),
                cached_input=int(row.cached_input_tokens),
                cache_creation_input=int(row.cache_creation_input_tokens),
                reasoning=int(row.reasoning_tokens),
                audio_input=int(row.audio_input_tokens),
                audio_output=int(row.audio_output_tokens),
                total=int(row.total_tokens),
                cost_micro_usd=row.cost_micro_usd,
            )
            for row in rollup_rows
        )
        return tuple(
            sorted(
                wire_rows,
                key=lambda r: (
                    -(r.cost_micro_usd if r.cost_micro_usd is not None else 0),
                    -r.total,
                    r.purpose,
                ),
            )
        )


class UsageApiRouter:
    """Build the ``/v1/usage`` router."""

    @classmethod
    def create_router(cls) -> APIRouter:
        # A10: most /v1/usage/* routes only require runtime:use; the
        # ``/org`` route adds an additional admin-or-auditor check
        # below via per-route ``dependencies=``.
        router = APIRouter(
            prefix="/v1/usage",
            tags=["usage"],
            dependencies=[Depends(RequireScopes(RUNTIME_USE))],
        )
        router.add_api_route(
            "/me",
            UsageApiRoutes.usage_me,
            methods=["GET"],
            response_model=UsageMeResponse,
            name=Keys.RouteName.USAGE_ME,
        )
        router.add_api_route(
            "/me/conversations",
            UsageApiRoutes.usage_me_conversations,
            methods=["GET"],
            response_model=tuple[UsageConversationRow, ...],
            name=Keys.RouteName.USAGE_ME_CONVERSATIONS,
        )
        router.add_api_route(
            "/runs/{run_id}",
            UsageApiRoutes.usage_run,
            methods=["GET"],
            response_model=RunUsageBreakdown,
            name=Keys.RouteName.USAGE_RUN,
        )
        router.add_api_route(
            "/conversations/{conversation_id}",
            UsageApiRoutes.usage_conversation,
            methods=["GET"],
            response_model=ConversationUsageResponse,
            name=Keys.RouteName.USAGE_CONVERSATION,
        )
        router.add_api_route(
            "/org",
            UsageApiRoutes.usage_org,
            methods=["GET"],
            response_model=UsageOrgResponse,
            name=Keys.RouteName.USAGE_ORG,
            # Org-wide usage = audit:read OR admin:users (auditors and
            # admins both legitimately query this; employees never do).
            # The router-level RUNTIME_USE check above also applies.
            dependencies=[Depends(RequireAnyScope(AUDIT_READ, "admin:users"))],
        )
        # Sub-PRD 01d — org-scoped subagent + purpose breakdowns.
        # Same auth scope as ``/org``: auditors and admins only.
        router.add_api_route(
            "/org/subagents",
            UsageApiRoutes.usage_org_subagents,
            methods=["GET"],
            response_model=UsageOrgSubagentsResponse,
            name=Keys.RouteName.USAGE_ORG_SUBAGENTS,
            dependencies=[Depends(RequireAnyScope(AUDIT_READ, "admin:users"))],
        )
        router.add_api_route(
            "/org/purpose",
            UsageApiRoutes.usage_org_purpose,
            methods=["GET"],
            response_model=UsageOrgPurposeResponse,
            name=Keys.RouteName.USAGE_ORG_PURPOSE,
            dependencies=[Depends(RequireAnyScope(AUDIT_READ, "admin:users"))],
        )
        return router


class BudgetApiRoutes:
    """Admin CRUD + per-user remaining-headroom endpoints for B7 budgets.

    Admin endpoints (``GET/POST/PATCH/DELETE /v1/budgets``) are gated by
    the same `RuntimeServiceAuthenticator` flow used by other v1/agent
    routes; the actual ``admin:budgets`` scope check will land in A10
    when the scope catalog ships. ``/v1/budgets/me`` is open to any
    authenticated user — it only returns budgets that match their
    ``(org_id, user_id)``.
    """

    @classmethod
    async def list_budgets(
        cls,
        request: Request,
        org_id: str | None = Query(None, min_length=1),
        user_id: str | None = Query(None, min_length=1),
    ) -> BudgetListResponse:
        org_id, _ = RuntimeApiRoutes.scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        persistence = request.app.state.runtime_persistence
        rows = await persistence.list_budgets(org_id=org_id)
        return BudgetListResponse(
            budgets=tuple(cls._to_view(record) for record in rows)
        )

    @classmethod
    async def create_budget(
        cls,
        request: Request,
        payload: BudgetCreateRequest,
        org_id: str | None = Query(None, min_length=1),
        user_id: str | None = Query(None, min_length=1),
    ) -> BudgetView:
        org_id, user_id = RuntimeApiRoutes.scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        persistence = request.app.state.runtime_persistence
        record = BudgetRecord(
            org_id=org_id,
            user_id=payload.user_id,
            scope=payload.scope,
            period=payload.period,
            enforcement=payload.enforcement,
            limit_micro_usd=payload.limit_micro_usd,
            limit_tokens=payload.limit_tokens,
            status=BudgetStatus.ACTIVE,
            created_by_user_id=user_id,
        )
        try:
            persisted = await persistence.create_budget(record)
        except ValueError as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
        return cls._to_view(persisted)

    @classmethod
    async def update_budget(
        cls,
        request: Request,
        budget_id: str,
        payload: BudgetUpdateRequest,
        org_id: str | None = Query(None, min_length=1),
        user_id: str | None = Query(None, min_length=1),
    ) -> BudgetView:
        org_id, _ = RuntimeApiRoutes.scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        persistence = request.app.state.runtime_persistence
        existing = await persistence.get_budget(org_id=org_id, budget_id=budget_id)
        if existing is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "budget not found")
        update: dict[str, object] = {"updated_at": datetime.now(timezone.utc)}
        if payload.enforcement is not None:
            update["enforcement"] = payload.enforcement
        if payload.limit_micro_usd is not None:
            update["limit_micro_usd"] = payload.limit_micro_usd
        if payload.limit_tokens is not None:
            update["limit_tokens"] = payload.limit_tokens
        if payload.status is not None:
            update["status"] = payload.status
        merged = existing.model_copy(update=update)
        persisted = await persistence.update_budget(merged)
        return cls._to_view(persisted)

    @classmethod
    async def delete_budget(
        cls,
        request: Request,
        budget_id: str,
        org_id: str | None = Query(None, min_length=1),
        user_id: str | None = Query(None, min_length=1),
    ) -> dict[str, str]:
        org_id, _ = RuntimeApiRoutes.scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        persistence = request.app.state.runtime_persistence
        await persistence.delete_budget(org_id=org_id, budget_id=budget_id)
        return {"status": "deleted"}

    @classmethod
    async def my_budgets(
        cls,
        request: Request,
        org_id: str | None = Query(None, min_length=1),
        user_id: str | None = Query(None, min_length=1),
    ) -> BudgetMeResponse:
        org_id, user_id = RuntimeApiRoutes.scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        persistence = request.app.state.runtime_persistence
        entries = await persistence.lookup_budgets_for_run(
            org_id=org_id, user_id=user_id
        )
        rows: list[BudgetMeRow] = []
        for entry in entries:
            window = BudgetPeriodCalculator.window(entry.budget.period)
            current_micro = (
                entry.state.current_spend_micro_usd if entry.state is not None else 0
            )
            current_tokens = (
                entry.state.current_spend_tokens if entry.state is not None else 0
            )
            remaining_micro = (
                max(0, entry.budget.limit_micro_usd - current_micro)
                if entry.budget.limit_micro_usd is not None
                else None
            )
            remaining_tokens = (
                max(0, entry.budget.limit_tokens - current_tokens)
                if entry.budget.limit_tokens is not None
                else None
            )
            rows.append(
                BudgetMeRow(
                    id=entry.budget.id,
                    scope=entry.budget.scope,
                    period=entry.budget.period,
                    enforcement=entry.budget.enforcement,
                    status=entry.budget.status,
                    limit_micro_usd=entry.budget.limit_micro_usd,
                    limit_tokens=entry.budget.limit_tokens,
                    current_micro_usd=current_micro,
                    current_tokens=current_tokens,
                    remaining_micro_usd=remaining_micro,
                    remaining_tokens=remaining_tokens,
                    period_start=window.period_start,
                    period_end=window.period_end,
                )
            )
        return BudgetMeResponse(budgets=tuple(rows))

    @staticmethod
    def _to_view(record: BudgetRecord) -> BudgetView:
        return BudgetView(
            id=record.id,
            org_id=record.org_id,
            user_id=record.user_id,
            scope=record.scope,
            period=record.period,
            enforcement=record.enforcement,
            limit_micro_usd=record.limit_micro_usd,
            limit_tokens=record.limit_tokens,
            status=record.status,
            created_at=record.created_at,
            updated_at=record.updated_at,
            created_by_user_id=record.created_by_user_id,
        )


class BudgetApiRouter:
    """Build the ``/v1/budgets/*`` router."""

    @classmethod
    def create_router(cls) -> APIRouter:
        # A10: ``runtime:use`` covers the /me self-service route. Admin
        # CRUD adds ``admin:budgets`` per-route.
        router = APIRouter(
            prefix="/v1/budgets",
            tags=["budgets"],
            dependencies=[Depends(RequireScopes(RUNTIME_USE))],
        )
        router.add_api_route(
            "",
            BudgetApiRoutes.list_budgets,
            methods=["GET"],
            response_model=BudgetListResponse,
            name=Keys.RouteName.BUDGETS_LIST,
            dependencies=[Depends(RequireScopes(ADMIN_BUDGETS))],
        )
        router.add_api_route(
            "",
            BudgetApiRoutes.create_budget,
            methods=["POST"],
            response_model=BudgetView,
            name=Keys.RouteName.BUDGETS_CREATE,
            dependencies=[Depends(RequireScopes(ADMIN_BUDGETS))],
        )
        router.add_api_route(
            "/me",
            BudgetApiRoutes.my_budgets,
            methods=["GET"],
            response_model=BudgetMeResponse,
            name=Keys.RouteName.BUDGETS_ME,
            # /me only needs the router-level runtime:use; no admin scope.
        )
        router.add_api_route(
            "/{budget_id}",
            BudgetApiRoutes.update_budget,
            methods=["PATCH"],
            response_model=BudgetView,
            name=Keys.RouteName.BUDGETS_UPDATE,
            dependencies=[Depends(RequireScopes(ADMIN_BUDGETS))],
        )
        router.add_api_route(
            "/{budget_id}",
            BudgetApiRoutes.delete_budget,
            methods=["DELETE"],
            name=Keys.RouteName.BUDGETS_DELETE,
            dependencies=[Depends(RequireScopes(ADMIN_BUDGETS))],
        )
        return router


class InternalRuntimeApiRoutes:
    """Internal-only routes consumed by the facade, never by browsers.

    Lives off `/internal/v1/*`, mirroring backend's existing internal-only
    namespace. Service-token auth is enforced when configured (production)
    via the same `RuntimeServiceAuthenticator` used by the v1/agent routes.
    """

    @classmethod
    def list_system_skills(cls, request: Request) -> SystemSkillListResponse:
        # Force the service-token check in production. In development this
        # returns None and the route remains open, mirroring how other v1/agent
        # routes degrade for local dev without ENTERPRISE_SERVICE_TOKEN.
        RuntimeServiceAuthenticator.trusted_identity_from_request(request)
        return SystemSkillsProjector().list_skills()

    @classmethod
    async def audit_cursor(
        cls,
        request: Request,
        after_id: str | None = Query(None, min_length=1),
        limit: int = Query(100, ge=1, le=1000),
    ) -> dict[str, object]:
        """C9 SIEM cursor — paginated runtime_audit_log read.

        Service-token only. Returns rows ordered by ``(created_at, id)``
        ascending so the SIEM pump's cursor is monotonic.
        """

        RuntimeServiceAuthenticator.trusted_identity_from_request(request)
        persistence = request.app.state.runtime_persistence
        rows = await persistence.list_audit_log_for_export(
            after_id=after_id, limit=limit
        )
        next_cursor = rows[-1]["id"] if rows else after_id
        # Coerce datetimes to ISO strings so the JSON response is stable
        # for the SIEM pump and downstream tooling.
        events: list[dict[str, object]] = []
        for row in rows:
            events.append(
                {
                    key: (value.isoformat() if hasattr(value, "isoformat") else value)
                    for key, value in row.items()
                }
            )
        return {
            "events": events,
            "next_cursor": next_cursor,
        }


class InternalRuntimeApiRouter:
    """Build the `/internal/v1/*` runtime router.

    Kept separate from the public `/v1/agent` router so middleware, OpenAPI
    grouping, and future internal-only auth changes can target one prefix
    cleanly.
    """

    @classmethod
    def create_router(cls) -> APIRouter:
        router = APIRouter(prefix="/internal/v1", tags=["runtime-internal"])
        router.add_api_route(
            "/skills/system",
            InternalRuntimeApiRoutes.list_system_skills,
            methods=["GET"],
            response_model=SystemSkillListResponse,
            name="internal_list_system_skills",
            # Service-to-service: ai-backend's runtime worker reads
            # the system-skills catalog. runtime:use is the bare-min.
            dependencies=[Depends(RequireScopes(RUNTIME_USE))],
        )
        router.add_api_route(
            "/audit/cursor",
            InternalRuntimeApiRoutes.audit_cursor,
            methods=["GET"],
            name="internal_audit_cursor",
            # SIEM pump only.
            dependencies=[Depends(RequireScopes(ADMIN_AUDIT_EXPORT))],
        )
        # PR 7.1 — paginated audit list for the in-product Settings →
        # Members → Audit log surface. The facade composes this with
        # the backend's four audit chains to produce the unified
        # /v1/audit endpoint.
        from runtime_api.http.audit_list_routes import register_audit_list_routes

        register_audit_list_routes(router)
        return router
