"""Thin delegating shell that assembles the runtime API coordinators into one service object.

``RuntimeApiService`` retains its original constructor signature so existing test
fixtures and the app factory continue to work unchanged. All implementation lives
in the coordinators; this class delegates every public method via 1-line forwarders.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable

from agent_runtime.api.constants import Values
from agent_runtime.api.membership import (
    InMemoryWorkspaceMembershipResolver,
    WorkspaceMembershipResolver,
)
from agent_runtime.api.notifications import (
    LoggingNotificationDispatcher,
    NotificationDispatcher,
)
from agent_runtime.api.user_policies_resolver import (
    NullUserPoliciesResolver,
    UserPoliciesResolver,
)
from agent_runtime.api.suggestible_connectors_resolver import (
    NullSuggestibleConnectorsResolver,
    SuggestibleConnectorsResolver,
)
from agent_runtime.observability.approval_metrics import ApprovalMetrics
from agent_runtime.pricing import ModelPricingCatalog
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
    RuntimeEventReplayResponse,
    RunStatusResponse,
    UpdateConversationConnectorsRequest,
    UpdateConversationRequest,
    UpdateWorkspaceDefaultsRequest,
    WorkspaceDefaultsResponse,
)
from agent_runtime.api.events import RuntimeEventProducer
from agent_runtime.api.ports import (
    EventStorePort,
    PersistencePort,
    RuntimeQueuePort,
)
from agent_runtime.execution.models import ModelConfigResolver
from agent_runtime.settings import RuntimeSettings


class RuntimeApiService:
    """Coordinate API requests across persistence, event store, and queue ports."""

    def __init__(
        self,
        *,
        persistence: PersistencePort,
        event_store: EventStorePort,
        queue: RuntimeQueuePort,
        settings: RuntimeSettings | None = None,
        model_resolver: ModelConfigResolver | None = None,
        on_event_appended: Callable[[str], None] | None = None,
        # PR 1.4.1 — production wires real impls; tests pass fakes. Both
        # default to the harmless dev impl so unit tests that only
        # exercise non-forwarding paths don't have to wire them.
        membership_resolver: "WorkspaceMembershipResolver | None" = None,
        notification_dispatcher: "NotificationDispatcher | None" = None,
        # PR 8.0.5 — per-(org, user) policy resolver. Optional so unit
        # tests that don't exercise the runtime-policy path keep their
        # existing wiring; the default ``NullUserPoliciesResolver``
        # returns ``{}`` (= deployment defaults) so the runtime never
        # refuses on a missing resolver.
        user_policies_resolver: "UserPoliciesResolver | None" = None,
        # PR 4.4.7 Phase 2 (Slice B) — catalog suggestions resolver.
        # Same lifecycle as the policy resolver; default falls back to
        # an empty tuple so tests + dev runs don't surface suggestions
        # until the trusted-backend lane is configured.
        suggestible_connectors_resolver: "SuggestibleConnectorsResolver | None" = None,
    ) -> None:
        # Public attrs kept for backwards compat (app.py and tests access them).
        self.persistence: PersistencePort = persistence
        self.event_store: EventStorePort = event_store
        self.queue: RuntimeQueuePort = queue
        self.settings = settings or RuntimeSettings.load()
        self.model_resolver = model_resolver or ModelConfigResolver(self.settings)
        self.event_producer = RuntimeEventProducer(
            persistence=self.persistence,
            event_store=self.event_store,
            on_event_appended=on_event_appended,
        )
        self._pricing_catalog = ModelPricingCatalog(self.persistence)

        # Resolve optional deps for coordinator construction.
        _membership_resolver: WorkspaceMembershipResolver = (
            membership_resolver or InMemoryWorkspaceMembershipResolver()
        )
        _notifications: NotificationDispatcher = (
            notification_dispatcher or LoggingNotificationDispatcher()
        )
        _user_policies_resolver: UserPoliciesResolver = (
            user_policies_resolver or NullUserPoliciesResolver()
        )
        _suggestible_connectors_resolver: SuggestibleConnectorsResolver = (
            suggestible_connectors_resolver or NullSuggestibleConnectorsResolver()
        )
        # Keep these as attributes for code that accesses them directly
        # (e.g. app.py's default_share_service reads self._notifications).
        self._membership_resolver = _membership_resolver
        self._notifications = _notifications
        self._user_policies_resolver = _user_policies_resolver
        self._suggestible_connectors_resolver = _suggestible_connectors_resolver
        self._approval_metrics = ApprovalMetrics()

        # Construct the five coordinators.
        from agent_runtime.api.run_coordinator import RunCoordinator
        from agent_runtime.api.approval_coordinator import ApprovalCoordinator
        from agent_runtime.api.conversation_coordinator import ConversationCoordinator
        from agent_runtime.api.conversation_query_service import (
            ConversationQueryService,
        )
        from agent_runtime.api.workspace_coordinator import WorkspaceCoordinator

        self._run = RunCoordinator(
            persistence=self.persistence,
            queue=self.queue,
            event_producer=self.event_producer,
            settings=self.settings,
            model_resolver=self.model_resolver,
            user_policies_resolver=_user_policies_resolver,
            suggestible_connectors_resolver=_suggestible_connectors_resolver,
        )
        self._approval = ApprovalCoordinator(
            persistence=self.persistence,
            queue=self.queue,
            event_producer=self.event_producer,
            membership_resolver=_membership_resolver,
            notification_dispatcher=_notifications,
        )
        self._conv = ConversationCoordinator(
            persistence=self.persistence,
            settings=self.settings,
            run_coordinator=self._run,
        )
        self._cqs = ConversationQueryService(
            persistence=self.persistence,
            event_store=self.event_store,
            settings=self.settings,
            model_resolver=self.model_resolver,
        )
        self._ws = WorkspaceCoordinator(
            persistence=self.persistence,
            settings=self.settings,
            model_resolver=self.model_resolver,
        )

    # ------------------------------------------------------------------
    # Delegating public methods
    # ------------------------------------------------------------------

    def list_models(self) -> ModelCatalogResponse:
        return self._cqs.list_models()

    async def create_conversation(
        self, request: CreateConversationRequest
    ) -> ConversationResponse:
        return await self._conv.create_conversation(request)

    async def get_conversation(
        self,
        *,
        org_id: str,
        user_id: str,
        conversation_id: str,
    ) -> ConversationResponse:
        return await self._cqs.get_conversation(
            org_id=org_id,
            user_id=user_id,
            conversation_id=conversation_id,
        )

    async def list_conversations(
        self,
        *,
        org_id: str,
        user_id: str,
        limit: int = Values.DEFAULT_CONVERSATION_LIMIT,
        include_archived: bool = False,
        include_deleted: bool = False,
    ) -> ConversationListResponse:
        return await self._cqs.list_conversations(
            org_id=org_id,
            user_id=user_id,
            limit=limit,
            include_archived=include_archived,
            include_deleted=include_deleted,
        )

    async def list_messages(
        self,
        *,
        org_id: str,
        user_id: str,
        conversation_id: str,
        limit: int = Values.DEFAULT_MESSAGE_LIMIT,
        include_deleted: bool = False,
    ) -> MessageListResponse:
        return await self._cqs.list_messages(
            org_id=org_id,
            user_id=user_id,
            conversation_id=conversation_id,
            limit=limit,
            include_deleted=include_deleted,
        )

    async def get_conversation_context(
        self,
        *,
        org_id: str,
        user_id: str,
        conversation_id: str,
    ) -> ConversationContextResponse:
        return await self._cqs.get_conversation_context(
            org_id=org_id,
            user_id=user_id,
            conversation_id=conversation_id,
        )

    async def update_conversation_connectors(
        self,
        *,
        org_id: str,
        user_id: str,
        conversation_id: str,
        request: UpdateConversationConnectorsRequest,
        allow_admin_override: bool = False,
    ) -> ConversationConnectorScopesResponse:
        return await self._conv.update_conversation_connectors(
            org_id=org_id,
            user_id=user_id,
            conversation_id=conversation_id,
            request=request,
            allow_admin_override=allow_admin_override,
        )

    async def create_run(self, request: CreateRunRequest) -> CreateRunResponse:
        """Orchestrate the parallel resolvers then delegate persistence to _run.

        The ``asyncio.gather`` runs via ``self._resolve_*`` so that
        ``patch.object(service, "_resolve_*", ...)`` in tests correctly
        intercepts the calls. ``_run._persist_and_enqueue`` handles the
        rest of the pipeline once the resolved values are in hand.
        """

        conversation_for_scope = await self._run._conversation_for_scope_when_known(
            request=request
        )
        if conversation_for_scope is not None:
            request = self._run._apply_conversation_scope_fallback(
                request=request, conversation=conversation_for_scope
            )
        request = await self._apply_workspace_default_model(request=request)

        (
            workspace_overrides,
            user_policies_json,
            suggested_connectors,
        ) = await asyncio.gather(
            self._resolve_workspace_behavior_overrides(org_id=request.org_id),
            self._resolve_user_policies(org_id=request.org_id, user_id=request.user_id),
            self._resolve_suggested_connectors(
                org_id=request.org_id,
                user_id=request.user_id,
                paused_connectors=request.request_context.paused_connectors,
            ),
        )
        return await self._run._persist_and_enqueue(
            request=request,
            conversation_for_scope=conversation_for_scope,
            workspace_overrides=workspace_overrides,
            user_policies_json=user_policies_json,
            suggested_connectors=suggested_connectors,
        )

    async def delete_user_history(
        self,
        *,
        org_id: str,
        user_id: str,
        reason: str | None = None,
    ) -> HistoryDeletionResponse:
        return await self._conv.delete_user_history(
            org_id=org_id,
            user_id=user_id,
            reason=reason,
        )

    async def get_run(
        self, *, org_id: str, user_id: str, run_id: str
    ) -> RunStatusResponse:
        return await self._cqs.get_run(
            org_id=org_id,
            user_id=user_id,
            run_id=run_id,
        )

    async def replay_events(
        self,
        *,
        org_id: str,
        user_id: str,
        run_id: str,
        after_sequence: int,
    ) -> RuntimeEventReplayResponse:
        return await self._cqs.replay_events(
            org_id=org_id,
            user_id=user_id,
            run_id=run_id,
            after_sequence=after_sequence,
        )

    async def cancel_run(
        self,
        *,
        org_id: str,
        user_id: str,
        run_id: str,
        request: CancelRunRequest,
    ) -> CancelRunResponse:
        return await self._run.cancel_run(
            org_id=org_id,
            user_id=user_id,
            run_id=run_id,
            request=request,
        )

    async def list_assigned_approvals(
        self,
        *,
        org_id: str,
        user_id: str,
        status_filter: ApprovalStatus,
        limit: int,
        cursor: str | None,
    ) -> AssignedApprovalsResponse:
        return await self._approval.list_assigned_approvals(
            org_id=org_id,
            user_id=user_id,
            status_filter=status_filter,
            limit=limit,
            cursor=cursor,
        )

    async def record_approval_decision(
        self,
        *,
        org_id: str,
        approval_id: str,
        request: ApprovalDecisionRequest,
    ) -> ApprovalDecisionResponse:
        return await self._approval.record_approval_decision(
            org_id=org_id,
            approval_id=approval_id,
            request=request,
        )

    async def request_approval_undo(
        self,
        *,
        org_id: str,
        approval_id: str,
        decided_by_user_id: str,
    ) -> ApprovalUndoResponse:
        return await self._approval.request_approval_undo(
            org_id=org_id,
            approval_id=approval_id,
            decided_by_user_id=decided_by_user_id,
        )

    async def get_workspace_defaults(self, *, org_id: str) -> WorkspaceDefaultsResponse:
        return await self._ws.get_workspace_defaults(org_id=org_id)

    async def update_workspace_defaults(
        self,
        *,
        org_id: str,
        actor_user_id: str,
        request: UpdateWorkspaceDefaultsRequest,
    ) -> WorkspaceDefaultsResponse:
        return await self._ws.update_workspace_defaults(
            org_id=org_id,
            actor_user_id=actor_user_id,
            request=request,
        )

    async def request_workspace_export(
        self,
        *,
        org_id: str,
        actor_user_id: str,
    ) -> dict[str, str]:
        return await self._ws.request_workspace_export(
            org_id=org_id,
            actor_user_id=actor_user_id,
        )

    async def record_workspace_delete_attempt(
        self,
        *,
        org_id: str,
        actor_user_id: str,
        typed_confirmation_correct: bool,
    ) -> None:
        await self._ws.record_workspace_delete_attempt(
            org_id=org_id,
            actor_user_id=actor_user_id,
            typed_confirmation_correct=typed_confirmation_correct,
        )

    async def update_conversation(
        self,
        *,
        org_id: str,
        user_id: str,
        conversation_id: str,
        request: UpdateConversationRequest,
        allow_admin_override: bool = False,
    ) -> ConversationResponse:
        return await self._conv.update_conversation(
            org_id=org_id,
            user_id=user_id,
            conversation_id=conversation_id,
            request=request,
            allow_admin_override=allow_admin_override,
        )

    async def delete_conversation(
        self,
        *,
        org_id: str,
        user_id: str,
        conversation_id: str,
        allow_admin_override: bool = False,
    ) -> None:
        await self._conv.delete_conversation(
            org_id=org_id,
            user_id=user_id,
            conversation_id=conversation_id,
            allow_admin_override=allow_admin_override,
        )

    async def restore_conversation(
        self,
        *,
        org_id: str,
        user_id: str,
        conversation_id: str,
        allow_admin_override: bool = False,
    ) -> ConversationResponse:
        return await self._conv.restore_conversation(
            org_id=org_id,
            user_id=user_id,
            conversation_id=conversation_id,
            allow_admin_override=allow_admin_override,
        )

    # ------------------------------------------------------------------
    # Private helper forwarders — kept so tests that patch these on the
    # service object continue to work. Implementation lives in the
    # coordinators; these delegates make ``patch.object(service, ...)``
    # intercept the right call path (PR 5 removes these once tests are
    # updated to patch on the coordinator directly).
    # ------------------------------------------------------------------

    async def _resolve_workspace_behavior_overrides(
        self, *, org_id: str
    ) -> dict[str, object]:
        return await self._run._resolve_workspace_behavior_overrides(org_id=org_id)

    async def _resolve_user_policies(
        self, *, org_id: str, user_id: str
    ) -> dict[str, object]:
        return await self._run._resolve_user_policies(org_id=org_id, user_id=user_id)

    async def _resolve_suggested_connectors(
        self,
        *,
        org_id: str,
        user_id: str,
        paused_connectors: tuple[str, ...],
    ):
        return await self._run._resolve_suggested_connectors(
            org_id=org_id,
            user_id=user_id,
            paused_connectors=paused_connectors,
        )

    async def _apply_workspace_default_model(
        self, *, request: CreateRunRequest
    ) -> CreateRunRequest:
        return await self._run._apply_workspace_default_model(request=request)
