"""Thin application service for the FastAPI runtime API."""

from __future__ import annotations

import asyncio
import base64
from collections.abc import Callable
from datetime import datetime, timedelta, timezone

from starlette import status

from agent_runtime.execution.contracts import (
    AgentRuntimeContext,
    CatalogSuggestionCard,
    RuntimeErrorCode,
    StreamEventSource,
)
from agent_runtime.api.constants import Keys, Messages, Values
from agent_runtime.api.membership import (
    InMemoryWorkspaceMembershipResolver,
    MembershipResolverUnavailable,
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
from agent_runtime.api.usage_service import ConversationContextBuilder
from agent_runtime.observability.approval_metrics import (
    ApprovalMetrics,
    ForwardInvalidReason,
)
from agent_runtime.pricing import ModelPricingCatalog
from runtime_api.schemas import (
    AgentRunStatus,
    ApprovalDecision,
    ApprovalDecisionRequest,
    ApprovalDecisionResponse,
    ApprovalDecisionRecord,
    ApprovalForwardTarget,
    ApprovalRequestRecord,
    ApprovalStatus,
    ApprovalUndoResponse,
    AssignedApproval,
    AssignedApprovalsResponse,
    UNDO_WINDOW_SECONDS,
    CancelRunRequest,
    CancelRunResponse,
    ConversationConnectorScopesResponse,
    ConversationContextResponse,
    ConversationListResponse,
    ConversationRecord,
    ConversationResponse,
    ConversationStatus,
    CreateConversationRequest,
    CreateRunRequest,
    CreateRunResponse,
    HistoryDeletionResponse,
    MessageListResponse,
    MessageRecord,
    ModelCatalogItem,
    ModelCatalogResponse,
    RuntimeApiEventType,
    RuntimeApprovalResolvedCommand,
    RuntimeCancelCommand,
    RuntimeEventReplayResponse,
    RuntimeRunCommand,
    RunRecord,
    RunStatusResponse,
    UpdateConversationConnectorsRequest,
    UpdateConversationRequest,
    UpdateWorkspaceDefaultsRequest,
    WorkspaceDefaultsResponse,
)
from runtime_api.http.errors import RuntimeApiError
from agent_runtime.api.events import RuntimeEventProducer
from agent_runtime.api.ports import (
    EventStorePort,
    PersistencePort,
    RuntimeQueuePort,
)
from agent_runtime.execution.errors import AgentRuntimeError
from agent_runtime.execution.models import ModelConfigResolver, ModelSelection
from agent_runtime.settings import RuntimeSettings


class RuntimeApiService:
    """Coordinate API requests across persistence, event store, and queue ports."""

    TERMINAL_RUN_STATUSES = frozenset(
        {
            AgentRunStatus.CANCELLED,
            AgentRunStatus.COMPLETED,
            AgentRunStatus.FAILED,
            AgentRunStatus.TIMED_OUT,
        }
    )

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
        # The service is uniformly async; ports are async-native, so every
        # call site below uses `await self.persistence.*` regardless of
        # which backend is configured.
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
        # Pricing lookups for the /context endpoint (B5). Cache lives on the
        # service so repeated panel opens hit the in-process LRU rather than
        # re-querying ``model_pricing`` per render.
        self._pricing_catalog = ModelPricingCatalog(self.persistence)
        # PR 1.4.1 — membership resolver + notification dispatcher are
        # injected dependencies so production can swap impls without
        # touching the service. Defaults are harmless: the in-memory
        # resolver treats every (org, user) as inactive (forces explicit
        # test wiring), and the logging dispatcher just logs structured
        # events — matches dev behaviour.
        self._membership_resolver: WorkspaceMembershipResolver = (
            membership_resolver or InMemoryWorkspaceMembershipResolver()
        )
        self._notifications: NotificationDispatcher = (
            notification_dispatcher or LoggingNotificationDispatcher()
        )
        # PR 8.0.5 — fall back to the no-op resolver so unit tests that
        # don't exercise the runtime-policy path keep their existing
        # wiring; production wires ``HttpUserPoliciesResolver`` via the
        # app factory.
        self._user_policies_resolver: UserPoliciesResolver = (
            user_policies_resolver or NullUserPoliciesResolver()
        )
        self._suggestible_connectors_resolver: SuggestibleConnectorsResolver = (
            suggestible_connectors_resolver or NullSuggestibleConnectorsResolver()
        )
        # PR 1.4.1 Gap #9 — three OTel signals (forward_total,
        # forward_invalid_total, chain_resolution_seconds). Best-effort:
        # the meter facade no-ops if OTel isn't importable.
        self._approval_metrics = ApprovalMetrics()

    def list_models(self) -> ModelCatalogResponse:
        """Return selectable chat models and credential availability."""

        default = self.settings.default_model
        configured = {
            "openai": self.settings.openai.is_configured,
            "anthropic": self.settings.anthropic.is_configured,
            "gemini": self.settings.gemini.is_configured,
        }
        models = [
            ModelCatalogItem(
                id=default.model_name,
                provider=default.provider,
                model_name=default.model_name,
                name=_display_model_name(default.model_name),
                description="Runtime default model",
                configured=configured.get(default.provider, False),
                supports_streaming=default.supports_streaming,
                supports_reasoning=default.reasoning is not None,
                reasoning=default.reasoning.model_dump(mode="json")
                if default.reasoning is not None
                else None,
            ),
            ModelCatalogItem(
                id="gpt-5.4-mini",
                provider="openai",
                model_name="gpt-5.4-mini",
                name="GPT-5.4 Mini",
                description="Compact OpenAI model",
                configured=configured["openai"],
                supports_streaming=True,
                supports_attachments=True,
                supports_reasoning=True,
                reasoning={"enabled": True, "effort": "medium", "summary": "auto"},
            ),
            ModelCatalogItem(
                id="claude-opus-4-7",
                provider="anthropic",
                model_name="claude-opus-4-7",
                name="Claude Opus 4.7",
                description="Anthropic reasoning model",
                configured=configured["anthropic"],
                supports_streaming=True,
                supports_reasoning=True,
            ),
            ModelCatalogItem(
                id="gemini-2.5-pro",
                provider="gemini",
                model_name="gemini-2.5-pro",
                name="Gemini 2.5 Pro",
                description="Google long-context model",
                configured=configured["gemini"],
                supports_streaming=True,
                supports_attachments=True,
            ),
        ]
        unique_models = {model.id: model for model in models}
        return ModelCatalogResponse(
            default_model_id=default.model_name,
            models=tuple(unique_models.values()),
        )

    async def create_conversation(
        self, request: CreateConversationRequest
    ) -> ConversationResponse:
        """Create or idempotently return a conversation.

        PR 1.6: when the request omits per-chat connector scopes (the
        normal browser-driven path), seed ``enabled_connectors`` from
        the workspace defaults row. The header-driven service-to-service
        path stays unchanged.
        """

        conversation = await self.persistence.create_conversation(request)
        seeded = await self._seed_default_connectors_if_needed(
            conversation=conversation
        )
        await self.persistence.write_audit_log(
            event_type="conversation_created",
            record={
                "org_id": seeded.org_id,
                "user_id": seeded.user_id,
                "resource_type": "conversation",
                "resource_id": seeded.conversation_id,
                "outcome": "success",
            },
        )
        return seeded.to_response()

    async def _apply_workspace_default_model(
        self, *, request: CreateRunRequest
    ) -> CreateRunRequest:
        """Fall back to workspace defaults when the request omits model.

        ``ModelConfigResolver`` already handles a fully-empty selection
        by walking to ``settings.default_model``. We slot workspace
        defaults in *between* the request and the deployment fallback
        so an admin's "default everyone to Atlas Reasoning" sticks.

        Idempotent: a request that already pins a provider+model_name
        passes through unchanged.
        """

        if request.org_id is None:
            return request
        if request.model is not None and (
            request.model.provider is not None and request.model.model_name is not None
        ):
            return request
        defaults = await self._workspace_defaults().get_record(org_id=request.org_id)
        if defaults is None or defaults.default_model is None:
            return request
        from runtime_api.schemas import ModelSelectionRequest

        existing = request.model
        merged = ModelSelectionRequest(
            provider=(
                existing.provider
                if existing is not None and existing.provider is not None
                else defaults.default_model.provider
            ),
            model_name=(
                existing.model_name
                if existing is not None and existing.model_name is not None
                else defaults.default_model.model_name
            ),
            temperature=existing.temperature if existing is not None else None,
            timeout_seconds=(
                existing.timeout_seconds if existing is not None else None
            ),
            max_input_tokens=(
                existing.max_input_tokens if existing is not None else None
            ),
            supports_streaming=(
                existing.supports_streaming if existing is not None else None
            ),
            reasoning=existing.reasoning if existing is not None else None,
        )
        return request.model_copy(update={"model": merged})

    async def _seed_default_connectors_if_needed(
        self, *, conversation: ConversationRecord
    ) -> ConversationRecord:
        """Materialise workspace default_connectors onto a fresh row.

        Only fires when the row carries an empty ``enabled_connectors``
        map (the create-conversation path leaves it ``{}`` until a
        PATCH from the client). Idempotent — calling this on a row
        that already has overrides is a no-op.
        """

        if conversation.enabled_connectors:
            return conversation
        defaults = await self._workspace_defaults().get_record(
            org_id=conversation.org_id
        )
        if defaults is None or not defaults.default_connectors:
            return conversation
        # Reuse the existing connector PATCH path — same audit trail,
        # same merge semantics, no duplicated UPDATE SQL.
        now = datetime.now(timezone.utc)
        updated = await self.persistence.update_conversation_connectors(
            org_id=conversation.org_id,
            user_id=conversation.user_id,
            conversation_id=conversation.conversation_id,
            scopes_patch=defaults.default_connectors,
            now=now,
        )
        return updated or conversation

    async def get_conversation(
        self,
        *,
        org_id: str,
        user_id: str,
        conversation_id: str,
    ) -> ConversationResponse:
        """Return conversation metadata for the caller scope.

        PR 2.2.1 — overlays the most-recent non-terminal run (if any)
        onto the response so the sidebar / topbar can paint live state
        on a fresh navigation without opening a stream first.
        """

        conversation = await self._conversation_for_scope(
            org_id=org_id,
            user_id=user_id,
            conversation_id=conversation_id,
        )
        return await self._with_latest_run(conversation.to_response(), org_id=org_id)

    async def list_conversations(
        self,
        *,
        org_id: str,
        user_id: str,
        limit: int = Values.DEFAULT_CONVERSATION_LIMIT,
        include_archived: bool = False,
        include_deleted: bool = False,
    ) -> ConversationListResponse:
        """Return scoped conversation metadata newest first.

        ``include_deleted`` (PR 1.6) flips between the default sidebar
        view (active rows only) and the "Show deleted" filter that
        powers Restore. Deleted rows are still inside the retention
        window — the C8 sweeper reaps them on TTL.

        PR 2.2.1 — every row is overlaid with the conversation's
        most-recent non-terminal run via ``_with_latest_run`` so the
        sidebar live-set is correct on cold reload. The lookup uses
        the existing ``get_active_run_for_conversation`` port
        (terminal runs return ``None``, leaving both projection fields
        ``None`` on the response — the FE then doesn't paint live).
        """

        bounded_limit = min(max(1, limit), Values.MAX_MESSAGE_LIMIT)
        records = await self.persistence.list_conversations(
            org_id=org_id,
            user_id=user_id,
            limit=bounded_limit,
            include_archived=include_archived,
            include_deleted=include_deleted,
        )
        responses: list[ConversationResponse] = []
        for record in records:
            responses.append(
                await self._with_latest_run(record.to_response(), org_id=org_id)
            )
        return ConversationListResponse(
            conversations=tuple(responses),
            has_more=len(records) == bounded_limit,
        )

    async def _with_latest_run(
        self,
        response: ConversationResponse,
        *,
        org_id: str,
    ) -> ConversationResponse:
        """Overlay the most-recent non-terminal run onto a conversation.

        Returns the response unchanged when there is no live run for
        this conversation. The non-terminal filter mirrors what the
        sidebar's live-pill cares about; terminal runs do not paint a
        pulse so we do not project them.
        """

        active = await self.persistence.get_active_run_for_conversation(
            org_id=org_id,
            conversation_id=response.conversation_id,
        )
        if active is None:
            return response
        return response.with_latest_run(
            status=active.status.value
            if hasattr(active.status, "value")
            else str(active.status),
            run_id=active.run_id,
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
        """Return ordered conversation history after validating caller scope."""

        await self._conversation_for_scope(
            org_id=org_id,
            user_id=user_id,
            conversation_id=conversation_id,
        )
        bounded_limit = min(max(1, limit), Values.MAX_MESSAGE_LIMIT)
        records = await self.persistence.list_messages(
            org_id=org_id,
            conversation_id=conversation_id,
            limit=bounded_limit,
            include_deleted=include_deleted,
        )
        return MessageListResponse(
            conversation_id=conversation_id,
            messages=tuple(record.to_response() for record in records),
            has_more=len(records) == bounded_limit,
        )

    async def get_conversation_context(
        self,
        *,
        org_id: str,
        user_id: str,
        conversation_id: str,
    ) -> ConversationContextResponse:
        """Return the per-conversation context-window view (B5).

        404s for foreign-tenant conversations (does not leak existence).
        Returns zero/None totals when the conversation has no completed
        runs yet — the panel renders the "no data" state.
        """

        await self._conversation_for_scope(
            org_id=org_id,
            user_id=user_id,
            conversation_id=conversation_id,
        )
        latest_run = await self.persistence.query_latest_run_usage_for_conversation(
            org_id=org_id,
            user_id=user_id,
            conversation_id=conversation_id,
        )
        if latest_run is None:
            default_model = self.settings.default_model
            return ConversationContextBuilder.build(
                provider=default_model.provider,
                model_name=default_model.model_name,
                latest_run=None,
                per_call_rows=(),
                compression_events=(),
                pricing=None,
            )

        per_call_rows = await self.persistence.query_model_call_usage_for_run(
            org_id=org_id, run_id=latest_run.run_id
        )
        compression_events = await self.persistence.query_compression_events_for_run(
            org_id=org_id, run_id=latest_run.run_id
        )
        pricing = await self._pricing_catalog.lookup(
            provider=latest_run.model_provider,
            model_name=latest_run.model_name,
            region="global",
            at=latest_run.completed_at,
        )
        return ConversationContextBuilder.build(
            provider=latest_run.model_provider,
            model_name=latest_run.model_name,
            latest_run=latest_run,
            per_call_rows=per_call_rows,
            compression_events=compression_events,
            pricing=pricing,
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
        """Merge-patch the chat's connector scope override + emit an audit row.

        ``allow_admin_override`` is set by the route handler when the
        caller's permission_scopes contain :data:`ADMIN_USERS` (PR 1.2.1
        admin-override path). When True, a non-owner caller in the same
        tenant is permitted to PATCH; the audit row records
        ``override_by_admin=True`` plus the owner's user_id so SIEM can
        reconstruct who acted on whose data.

        404s for foreign-tenant conversations (does not leak existence).
        Audit metadata captures ``before`` / ``after`` / ``diff_keys`` for
        forensic reconstruction; the row is also append-only via the
        existing ``runtime_audit_log`` HMAC chain.
        """

        before, is_admin_override = await self._conversation_for_owner_or_admin(
            org_id=org_id,
            actor_user_id=user_id,
            conversation_id=conversation_id,
            allow_admin_override=allow_admin_override,
        )
        now = datetime.now(timezone.utc)
        # The persistence UPDATE filters by owner user_id — for admin
        # overrides we use the owner's id (from the loaded record), not
        # the actor's. The actor is recorded separately in the audit row.
        updated = await self.persistence.update_conversation_connectors(
            org_id=org_id,
            user_id=before.user_id,
            conversation_id=conversation_id,
            scopes_patch=request.scopes,
            now=now,
        )
        if updated is None:
            # Race: row vanished between the scope check and the UPDATE.
            raise RuntimeApiError(
                RuntimeErrorCode.CAPABILITY_NOT_FOUND,
                Messages.Error.CONVERSATION_NOT_FOUND,
                http_status=status.HTTP_404_NOT_FOUND,
                retryable=False,
            )
        audit_metadata = _connector_scope_audit_metadata(
            before=before.enabled_connectors,
            patch=request.scopes,
            after=updated.enabled_connectors,
        )
        if is_admin_override:
            audit_metadata["override_by_admin"] = True
            audit_metadata["conversation_owner_user_id"] = before.user_id
        await self.persistence.write_audit_log(
            event_type=Messages.Audit.CONVERSATION_CONNECTORS_UPDATE,
            record={
                "org_id": org_id,
                "user_id": user_id,
                "resource_type": "conversation",
                "resource_id": conversation_id,
                "outcome": "success",
                "metadata": audit_metadata,
            },
        )
        return ConversationConnectorScopesResponse(
            conversation_id=updated.conversation_id,
            scopes=updated.enabled_connectors,
            updated_at=updated.connectors_updated_at,
        )

    async def create_run(self, request: CreateRunRequest) -> CreateRunResponse:
        """Persist a queued run and enqueue worker execution without invoking runtime inline."""

        # Resolve the conversation up front so we can apply the per-chat
        # connector scope fallback before sealing the runtime context. The
        # inbound header (already merged into request_context.connector_scopes
        # by the route handler) wins when present; an empty dict falls back
        # to the conversation's stored override.
        conversation_for_scope = await self._conversation_for_scope_when_known(
            request=request
        )
        if conversation_for_scope is not None:
            request = self._apply_conversation_scope_fallback(
                request=request, conversation=conversation_for_scope
            )

        # PR 1.6: if the request did not pin a model, fall back to the
        # workspace default before model resolution. Existing chain:
        #   request.model → assistant.model → settings.default_model
        # New chain:
        #   request.model → assistant.model → workspace_defaults.default_model
        #                                    → settings.default_model
        request = await self._apply_workspace_default_model(request=request)

        # P3 (refactor 03-parallel-bootstrap.md) — three independent
        # run-start resolvers run concurrently. All three only read
        # ``request.org_id`` / ``request.user_id`` /
        # ``request.request_context.paused_connectors``; none consumes
        # another's output. ``request`` is not mutated between them.
        # Adding a 4th resolver here? It must be independent of the
        # other three — see docs/refactor/03-parallel-bootstrap.md.
        #
        # Per-coroutine context:
        #   PR 4.3 — workspace-policy knobs (system prompt override,
        #     temperature default, citation density, refusal behavior,
        #     reasoning effort, training opt-out). Frozen onto the run's
        #     runtime_context for the lifetime of the run; mid-run admin
        #     toggles do not affect in-flight runs.
        #   PR 8.0.5 — single fetch for (tool-use × privacy) per-(org,
        #     user) policy. Same freeze lifecycle as workspace overrides.
        #   PR 4.4.7 Phase 2 (Slice B) — per-(org, user) suggestible-
        #     catalog snapshot, fetched after the per-chat fallback has
        #     applied so paused connectors are filtered out before the
        #     backend joins them. Failure modes are swallowed inside the
        #     resolver — empty tuple is the "no suggestions" no-op.
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
        request = self._request_with_runtime_context(
            request,
            workspace_behavior_overrides=workspace_overrides,
            user_policies_json=user_policies_json,
            suggested_connectors=suggested_connectors,
        )
        context = request.runtime_context
        if context is None:
            raise RuntimeApiError(
                RuntimeErrorCode.VALIDATION_ERROR,
                "Runtime context could not be created.",
                http_status=status.HTTP_400_BAD_REQUEST,
                retryable=False,
            )
        conversation = conversation_for_scope or await self._conversation_for_scope(
            org_id=context.org_id,
            user_id=context.user_id,
            conversation_id=request.conversation_id,
        )
        (
            run,
            user_message,
            created,
        ) = await self.persistence.create_run_with_user_message(
            request=request,
            conversation=conversation,
        )
        if created:
            await self.persistence.write_audit_log(
                event_type="run_created",
                record={
                    "org_id": run.org_id,
                    "user_id": run.user_id,
                    "resource_type": "run",
                    "resource_id": run.run_id,
                    "run_id": run.run_id,
                    "trace_id": run.trace_id,
                    "outcome": "success",
                },
            )
            await self.event_producer.append_api_event(
                run=run,
                source=StreamEventSource.RUNTIME,
                event_type=RuntimeApiEventType.RUN_QUEUED,
                payload={Keys.Payload.MESSAGE: Messages.Event.RUN_QUEUED},
            )
            await self.queue.enqueue_run(
                RuntimeRunCommand(
                    run_id=run.run_id,
                    conversation_id=run.conversation_id,
                    org_id=run.org_id,
                    user_id=run.user_id,
                    trace_id=run.trace_id,
                    runtime_context=run.runtime_context,
                )
            )
        prior_run_ids = await self._prior_run_ids_for_chain(
            org_id=run.org_id,
            conversation_id=run.conversation_id,
            current_run_id=run.run_id,
            user_message=user_message,
        )
        return self._create_run_response(
            run=run,
            user_message_id=user_message.message_id,
            prior_run_ids=prior_run_ids,
        )

    async def delete_user_history(
        self,
        *,
        org_id: str,
        user_id: str,
        reason: str | None = None,
    ) -> HistoryDeletionResponse:
        """Delete user-visible conversation history and persist deletion evidence."""

        result = await self.persistence.delete_user_history(
            org_id=org_id, user_id=user_id, reason=reason
        )
        await self.persistence.write_audit_log(
            event_type="user_history_deleted",
            record={
                "org_id": org_id,
                "user_id": user_id,
                "resource_type": "user_history",
                "resource_id": user_id,
                "outcome": "success",
                "metadata": {
                    "reason": reason,
                    "conversations_archived": result.conversations_archived,
                    "messages_tombstoned": result.messages_tombstoned,
                    "runs_cancelled": result.runs_cancelled,
                    "events_retained": result.events_retained,
                },
            },
        )
        return result

    async def get_run(
        self, *, org_id: str, user_id: str, run_id: str
    ) -> RunStatusResponse:
        """Return current run state."""

        run = await self._run_for_scope(org_id=org_id, user_id=user_id, run_id=run_id)
        return run.to_response()

    async def replay_events(
        self,
        *,
        org_id: str,
        user_id: str,
        run_id: str,
        after_sequence: int,
    ) -> RuntimeEventReplayResponse:
        """Return persisted events after a client sequence checkpoint."""

        run = await self._run_for_scope(org_id=org_id, user_id=user_id, run_id=run_id)
        events = tuple(
            await self.event_store.list_events_after(
                org_id=org_id,
                run_id=run_id,
                after_sequence=after_sequence,
            )
        )
        latest_sequence_no = max(
            (event.sequence_no for event in events),
            default=await self.event_store.get_latest_sequence(run_id=run_id),
        )
        return RuntimeEventReplayResponse(
            run_id=run_id,
            events=events,
            latest_sequence_no=latest_sequence_no,
            run_status=run.status,
            has_more=False,
        )

    async def cancel_run(
        self,
        *,
        org_id: str,
        user_id: str,
        run_id: str,
        request: CancelRunRequest,
    ) -> CancelRunResponse:
        """Persist a best-effort cancellation request and enqueue a worker command."""

        run = await self._run_for_scope(org_id=org_id, user_id=user_id, run_id=run_id)
        if request.requested_by_user_id != user_id:
            raise RuntimeApiError(
                RuntimeErrorCode.PERMISSION_DENIED,
                "Cancellation requester does not match run user.",
                http_status=status.HTTP_403_FORBIDDEN,
                retryable=False,
                correlation_id=run.trace_id,
            )
        if run.status in self.TERMINAL_RUN_STATUSES:
            return CancelRunResponse(
                run_id=run.run_id,
                status=run.status,
                cancel_requested_at=run.cancelled_at,
                latest_sequence_no=run.latest_sequence_no,
            )
        if run.status != AgentRunStatus.CANCELLING:
            run = await self.persistence.update_run_status(
                run_id=run.run_id,
                status=AgentRunStatus.CANCELLING,
            )
            await self.event_producer.append_api_event(
                run=run,
                source=StreamEventSource.RUNTIME,
                event_type=RuntimeApiEventType.RUN_CANCELLING,
                payload={
                    Keys.Payload.MESSAGE: Messages.Event.RUN_CANCELLING,
                    Keys.Payload.REASON: request.reason,
                },
            )
            refreshed = await self.persistence.get_run(org_id=org_id, run_id=run.run_id)
            run = refreshed or run
            await self.queue.enqueue_cancel(
                RuntimeCancelCommand(
                    run_id=run.run_id,
                    org_id=run.org_id,
                    requested_by_user_id=request.requested_by_user_id,
                    reason=request.reason,
                )
            )
            await self.persistence.write_audit_log(
                event_type="run_cancel_requested",
                record={
                    "org_id": run.org_id,
                    "user_id": run.user_id,
                    "resource_type": "run",
                    "resource_id": run.run_id,
                    "run_id": run.run_id,
                    "trace_id": run.trace_id,
                    "outcome": "success",
                    "metadata": {"reason": request.reason},
                },
            )
        return CancelRunResponse(
            run_id=run.run_id,
            status=run.status,
            cancel_requested_at=datetime.now(timezone.utc),
            latest_sequence_no=run.latest_sequence_no,
        )

    # PR 1.4 — chain depth cap. Schema permits an arbitrary chain; the
    # service refuses to extend it past this depth so a misconfigured
    # workflow (or runaway script) can't build a thousand-link approval
    # tree before someone notices.
    APPROVAL_FORWARD_MAX_CHAIN_DEPTH = 3

    # PR 1.4 — kinds that may be forwarded. ``ask_a_question`` is a
    # clarification to the original requester, never a sensitive action;
    # forwarding it makes no semantic sense.
    APPROVAL_FORWARDABLE_KINDS = frozenset(
        {
            Values.ApprovalKind.ACTION,
            Values.ApprovalKind.MCP_TOOL,
            # PR 1.4.1 Gap #4 — mcp_auth was previously listed but the
            # OAuth flow binds tokens to whoever completes it. Forwarding
            # would either silently rebind to the recipient's identity
            # (footgun) or require the requester to come back and re-auth
            # (defeats the point). We narrow the contract instead. The FE
            # already hides the forward button for mcp_auth approvals;
            # contract is now consistent.
        }
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
        """Return the recipient inbox view (PR 1.4.1 Gap #6).

        Filters to ``requested_by_user_id == user_id`` AND ``status == status_filter``.
        Newest-first; cursor is opaque base64 of ``f"{created_at_iso}|{approval_id}"``.
        """

        bounded = min(
            max(1, limit),
            Values.MAX_ASSIGNED_APPROVAL_LIMIT,
        )
        decoded_cursor = self._decode_assigned_cursor(cursor)
        records = await self.persistence.list_assigned_approvals(
            org_id=org_id,
            requested_by_user_id=user_id,
            status=status_filter.value,
            limit=bounded,
            cursor=decoded_cursor,
        )
        approvals = tuple(self._record_to_assigned(record) for record in records)
        next_cursor = (
            self._encode_assigned_cursor(
                records[-1].created_at, records[-1].approval_id
            )
            if len(records) == bounded and records
            else None
        )
        return AssignedApprovalsResponse(
            approvals=approvals,
            next_cursor=next_cursor,
        )

    @classmethod
    def _record_to_assigned(cls, record: ApprovalRequestRecord) -> AssignedApproval:
        approval_kind = record.metadata.get(Keys.Field.APPROVAL_KIND)
        action_summary = record.metadata.get(Keys.Field.ACTION_SUMMARY)
        risk_class = record.metadata.get("risk_level") or record.metadata.get(
            "risk_class"
        )
        forwarded_by = record.metadata.get(Keys.Field.FORWARDED_BY_USER_ID)
        return AssignedApproval(
            approval_id=record.approval_id,
            conversation_id=record.conversation_id,
            run_id=record.run_id,
            approval_kind=approval_kind if isinstance(approval_kind, str) else "action",
            status=record.status,
            chain_parent_approval_id=record.chain_parent_approval_id,
            forwarded_by_user_id=forwarded_by
            if isinstance(forwarded_by, str)
            else None,
            forwarded_at=record.forwarded_at,
            action_summary=action_summary if isinstance(action_summary, str) else "",
            risk_class=risk_class if isinstance(risk_class, str) else None,
            expires_at=record.expires_at,
            created_at=record.created_at,
        )

    @staticmethod
    def _encode_assigned_cursor(created_at: datetime, approval_id: str) -> str:
        # Stable, opaque, replay-safe. The pipe is escape-free because
        # ``approval_id`` matches Patterns.ID (no pipe).
        raw = f"{created_at.isoformat()}|{approval_id}".encode()
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")

    @staticmethod
    def _decode_assigned_cursor(cursor: str | None) -> tuple[datetime, str] | None:
        if cursor is None:
            return None
        padding = "=" * (-len(cursor) % 4)
        try:
            raw = base64.urlsafe_b64decode(cursor + padding).decode()
            iso, approval_id = raw.split("|", 1)
            return datetime.fromisoformat(iso), approval_id
        except (ValueError, UnicodeDecodeError):
            # Treat malformed cursors as "no cursor" rather than 400 — the
            # FE may have a stale cursor across deployments.
            return None

    async def record_approval_decision(
        self,
        *,
        org_id: str,
        approval_id: str,
        request: ApprovalDecisionRequest,
    ) -> ApprovalDecisionResponse:
        """Persist an approval decision and enqueue the worker resume command.

        PR 1.4 — when ``request.decision`` is ``FORWARDED`` this dispatches
        to ``_decide_forwarded`` instead of resolving the approval. The
        forwarded path *does not* enqueue a worker resume command: the run
        stays ``WAITING_FOR_APPROVAL`` until the leaf approver decides on
        the child row, which flows through the existing approve/reject
        path on a different ``approval_id``.
        """

        approval = await self.persistence.get_approval_request(
            org_id=org_id, approval_id=approval_id
        )
        if approval is None:
            raise RuntimeApiError(
                RuntimeErrorCode.CAPABILITY_NOT_FOUND,
                Messages.Error.APPROVAL_NOT_FOUND,
                http_status=status.HTTP_404_NOT_FOUND,
                retryable=False,
            )
        if approval.user_id != request.decided_by_user_id:
            raise RuntimeApiError(
                RuntimeErrorCode.PERMISSION_DENIED,
                "Approval decision user does not match approval scope.",
                http_status=status.HTTP_403_FORBIDDEN,
                retryable=False,
            )
        if request.decision is ApprovalDecision.FORWARDED:
            return await self._decide_forwarded(
                approval=approval,
                request=request,
            )
        status_value = (
            ApprovalStatus.APPROVED
            if request.decision.value == ApprovalStatus.APPROVED.value
            else ApprovalStatus.REJECTED
        )
        record = await self.persistence.record_approval_decision(
            record=ApprovalDecisionRecord(
                approval_id=approval.approval_id,
                run_id=approval.run_id,
                conversation_id=approval.conversation_id,
                org_id=approval.org_id,
                user_id=approval.user_id,
                status=status_value,
                decided_by_user_id=request.decided_by_user_id,
                reason=request.reason,
                answer=request.answer,
            )
        )
        run = await self._run_for_scope(
            org_id=record.org_id,
            user_id=record.user_id,
            run_id=record.run_id,
        )
        approval_kind = approval.metadata.get(Keys.Field.APPROVAL_KIND)
        await self.event_producer.append_api_event(
            run=run,
            source=StreamEventSource.RUNTIME,
            event_type=RuntimeApiEventType.APPROVAL_RESOLVED,
            payload={
                Keys.Field.APPROVAL_ID: record.approval_id,
                Keys.Field.APPROVAL_KIND: approval_kind,
                # ask_a_question is a question-to-user, not a permission gate.
                # Emit a vocabulary that matches the user-facing semantics so
                # the frontend can render "Answered"/"Skipped" rather than
                # leaning on approve/reject copy.
                Keys.Field.STATUS: self._wire_status_for(
                    approval_kind=approval_kind,
                    record_status=record.status.value,
                ),
                Keys.Payload.MESSAGE: Messages.Event.APPROVAL_RESOLVED,
                Keys.Field.DECISION: record.status.value,
            },
        )
        await self.queue.enqueue_approval_resolved(
            RuntimeApprovalResolvedCommand(
                approval_id=record.approval_id,
                run_id=record.run_id,
                org_id=record.org_id,
                decision=request.decision,
                answer=request.answer,
            )
        )
        await self.persistence.write_audit_log(
            event_type="approval_decision_recorded",
            record={
                "org_id": record.org_id,
                "user_id": record.user_id,
                "resource_type": "approval",
                "resource_id": record.approval_id,
                "run_id": record.run_id,
                "outcome": "success",
                "metadata": {"status": record.status.value},
            },
        )
        return ApprovalDecisionResponse(
            approval_id=record.approval_id,
            run_id=record.run_id,
            status=record.status,
            decided_at=record.decided_at,
            undo_expires_at=self._undo_expires_at_for(approval=approval, record=record),
        )

    @staticmethod
    def _undo_expires_at_for(
        *,
        approval: ApprovalRequestRecord,
        record: ApprovalDecisionRecord,
    ) -> datetime | None:
        """Compute the undo deadline for an approved + reversible decision.

        PR 4.4.6.4 — non-null only when status is APPROVED and the
        original request was tagged ``reversible="yes"`` (set by the
        worker via ``McpApprovalMetadata`` in PR 4.4.6.2). Computed
        from ``decided_at + UNDO_WINDOW_SECONDS``; not persisted.
        """

        if record.status is not ApprovalStatus.APPROVED:
            return None
        if approval.metadata.get("reversible") != "yes":
            return None
        return record.decided_at + timedelta(seconds=UNDO_WINDOW_SECONDS)

    async def request_approval_undo(
        self,
        *,
        org_id: str,
        approval_id: str,
        decided_by_user_id: str,
    ) -> ApprovalUndoResponse:
        """Record the user's intent to undo an approved + reversible action.

        PR 4.4.6.4 — protocol layer only. The audit row + stream event
        capture the user's request inside the 60s window; actual MCP-
        level revert (e.g., calling Slack's ``chat.delete``) is
        per-vendor follow-up territory.
        """

        approval = await self.persistence.get_approval_request(
            org_id=org_id, approval_id=approval_id
        )
        if approval is None:
            raise RuntimeApiError(
                RuntimeErrorCode.CAPABILITY_NOT_FOUND,
                Messages.Error.APPROVAL_NOT_FOUND,
                http_status=status.HTTP_404_NOT_FOUND,
                retryable=False,
            )
        if approval.user_id != decided_by_user_id:
            raise RuntimeApiError(
                RuntimeErrorCode.PERMISSION_DENIED,
                "Approval decision user does not match approval scope.",
                http_status=status.HTTP_403_FORBIDDEN,
                retryable=False,
            )
        if approval.status is not ApprovalStatus.APPROVED:
            raise RuntimeApiError(
                RuntimeErrorCode.VALIDATION_ERROR,
                "Only approved decisions are reversible.",
                http_status=status.HTTP_422_UNPROCESSABLE_ENTITY,
                retryable=False,
            )
        if approval.metadata.get("reversible") != "yes":
            raise RuntimeApiError(
                RuntimeErrorCode.VALIDATION_ERROR,
                "This approval was not flagged reversible.",
                http_status=status.HTTP_422_UNPROCESSABLE_ENTITY,
                retryable=False,
            )
        decided_at = self._decision_decided_at(approval=approval)
        if decided_at is None:
            raise RuntimeApiError(
                RuntimeErrorCode.VALIDATION_ERROR,
                "Approval has no decision yet.",
                http_status=status.HTTP_422_UNPROCESSABLE_ENTITY,
                retryable=False,
            )
        undo_expires_at = decided_at + timedelta(seconds=UNDO_WINDOW_SECONDS)
        now = datetime.now(timezone.utc)
        if now >= undo_expires_at:
            raise RuntimeApiError(
                RuntimeErrorCode.VALIDATION_ERROR,
                "Undo window expired.",
                http_status=status.HTTP_410_GONE,
                retryable=False,
            )
        run = await self._run_for_scope(
            org_id=approval.org_id,
            user_id=approval.user_id,
            run_id=approval.run_id,
        )
        approval_kind = approval.metadata.get(Keys.Field.APPROVAL_KIND)
        await self.event_producer.append_api_event(
            run=run,
            source=StreamEventSource.RUNTIME,
            event_type=RuntimeApiEventType.APPROVAL_UNDO_REQUESTED,
            payload={
                Keys.Field.APPROVAL_ID: approval.approval_id,
                Keys.Field.APPROVAL_KIND: approval_kind,
                "decided_by_user_id": decided_by_user_id,
                "undo_requested_at": now.isoformat(),
                "undo_expires_at": undo_expires_at.isoformat(),
            },
        )
        await self.persistence.write_audit_log(
            event_type="approval_undo_requested",
            record={
                "org_id": approval.org_id,
                "user_id": approval.user_id,
                "resource_type": "approval",
                "resource_id": approval.approval_id,
                "run_id": approval.run_id,
                "outcome": "success",
                "metadata": {
                    "approval_kind": approval_kind,
                    "vendor": approval.metadata.get("vendor"),
                    "tool_name": approval.metadata.get("tool_name"),
                    "undo_expires_at": undo_expires_at.isoformat(),
                    "undo_requested_at": now.isoformat(),
                },
            },
        )
        return ApprovalUndoResponse(
            approval_id=approval.approval_id,
            run_id=approval.run_id,
            undo_requested_at=now,
            undo_expires_at=undo_expires_at,
        )

    @staticmethod
    def _decision_decided_at(*, approval: ApprovalRequestRecord) -> datetime | None:
        """Read ``decided_at`` round-tripped into the request metadata.

        PR 4.4.6.4 — both adapters merge ``decided_at`` into the metadata
        blob when ``record_approval_decision`` runs. This avoids a
        separate ``get_approval_decision`` persistence call. Returns
        ``None`` when the decision hasn't landed yet (race) or the
        adapter pre-dates this PR.
        """

        raw = approval.metadata.get("decided_at")
        if not isinstance(raw, str):
            return None
        try:
            return datetime.fromisoformat(raw)
        except ValueError:
            return None

    async def _decide_forwarded(
        self,
        *,
        approval: ApprovalRequestRecord,
        request: ApprovalDecisionRequest,
    ) -> ApprovalDecisionResponse:
        """Forward a pending approval to a second workspace user (PR 1.4).

        Forwarding is bookkeeping: the LangChain HumanInTheLoopMiddleware
        and LangGraph interrupt/resume contract are byte-identical. The
        graph stays paused; the API merely
            (1) resolves the parent row to ``status=FORWARDED``,
            (2) inserts a child row addressed to the recipient,
            (3) emits ``approval_resolved`` (status=forwarded) for the parent,
            (4) emits ``approval_forwarded`` so the FE can transform the
                inline card into "Waiting on @marcus",
            (5) emits ``approval_requested`` for the child,
            (6) writes a ``approval.forward`` audit row.
        Resume of the run hangs off the child's eventual approve/reject.
        """

        target = request.forward_to
        if target is None:
            # Defensive: model_validator should have already raised.
            raise RuntimeApiError(
                RuntimeErrorCode.VALIDATION_ERROR,
                Messages.Error.APPROVAL_FORWARD_INVALID_TARGET,
                http_status=status.HTTP_400_BAD_REQUEST,
                retryable=False,
            )
        await self._guard_forwardable(approval=approval, target=target)
        # PR 1.4.1 — at depth ≥ 2 the approval owner is the previous
        # forwarder, not the run's original requester. Look up the run
        # by id alone here (org-scoped via the persistence port's RLS);
        # the legitimacy gate is already enforced by the
        # approval.user_id == decided_by_user_id check upstream.
        run = await self.persistence.get_run(
            org_id=approval.org_id,
            run_id=approval.run_id,
        )
        if run is None:
            raise RuntimeApiError(
                RuntimeErrorCode.CAPABILITY_NOT_FOUND,
                Messages.Error.RUN_NOT_FOUND,
                http_status=status.HTTP_404_NOT_FOUND,
                retryable=False,
            )
        now = datetime.now(timezone.utc)
        # Build the child row. Inherit metadata byte-for-byte so the LangGraph
        # native_interrupt_id, tool_invocation_id, and approval_kind survive
        # the forward — the leaf decision must produce the same Command(resume)
        # payload the original would have.
        child_metadata = dict(approval.metadata)
        child_metadata[Keys.Field.CHAIN_PARENT_APPROVAL_ID] = approval.approval_id
        child_metadata[Keys.Field.FORWARDED_BY_USER_ID] = request.decided_by_user_id
        child = ApprovalRequestRecord(
            run_id=approval.run_id,
            conversation_id=approval.conversation_id,
            org_id=approval.org_id,
            user_id=target.user_id,
            status=ApprovalStatus.PENDING,
            created_at=now,
            expires_at=approval.expires_at,
            metadata=child_metadata,
            chain_parent_approval_id=approval.approval_id,
            chain_depth=approval.chain_depth + 1,
        )
        try:
            updated_parent, child = await self.persistence.forward_approval_request(
                parent_approval_id=approval.approval_id,
                org_id=approval.org_id,
                decided_by_user_id=request.decided_by_user_id,
                forwarded_to_user_id=target.user_id,
                decision_reason=request.reason,
                child=child,
                now=now,
            )
        except RuntimeError as exc:
            # PR 1.4 race guard: postgres adapter raises with
            # ``approval_forward_parent_no_longer_pending`` when its
            # ``WHERE status='pending'`` UPDATE finds nothing. PR 1.4.1
            # in-memory adapter raises the same message for parity. The
            # service translates either substring to the user-facing 409.
            message = str(exc)
            if "no_longer_pending" in message or "not_pending" in message:
                raise RuntimeApiError(
                    RuntimeErrorCode.VALIDATION_ERROR,
                    Messages.Error.APPROVAL_FORWARD_NOT_PENDING,
                    http_status=status.HTTP_409_CONFLICT,
                    retryable=False,
                ) from exc
            raise
        approval_kind = approval.metadata.get(Keys.Field.APPROVAL_KIND)
        action_summary = approval.metadata.get(Keys.Field.ACTION_SUMMARY)
        # Emit the three events in stream order. They land on the same
        # SSE channel as every other runtime event — the FE reducer keys
        # on chain_parent_approval_id to transform the inline card.
        await self.event_producer.append_api_event(
            run=run,
            source=StreamEventSource.RUNTIME,
            event_type=RuntimeApiEventType.APPROVAL_RESOLVED,
            payload={
                Keys.Field.APPROVAL_ID: approval.approval_id,
                Keys.Field.APPROVAL_KIND: approval_kind,
                Keys.Field.STATUS: Values.Status.FORWARDED,
                Keys.Field.DECISION: ApprovalStatus.FORWARDED.value,
                Keys.Payload.MESSAGE: Messages.Event.APPROVAL_RESOLVED,
            },
        )
        await self.event_producer.append_api_event(
            run=run,
            source=StreamEventSource.RUNTIME,
            event_type=RuntimeApiEventType.APPROVAL_FORWARDED,
            payload={
                Keys.Field.APPROVAL_ID: child.approval_id,
                Keys.Field.CHAIN_PARENT_APPROVAL_ID: approval.approval_id,
                Keys.Field.APPROVAL_KIND: approval_kind,
                Keys.Field.FORWARDED_BY_USER_ID: request.decided_by_user_id,
                Keys.Field.FORWARDED_TO_USER_ID: target.user_id,
                Keys.Field.FORWARDED_AT: now.isoformat(),
                Keys.Field.ACTION_SUMMARY: action_summary,
                Keys.Field.STATUS: Values.Status.WAITING,
                Keys.Payload.MESSAGE: Messages.Event.APPROVAL_FORWARDED,
            },
        )
        await self.event_producer.append_api_event(
            run=run,
            source=StreamEventSource.RUNTIME,
            event_type=RuntimeApiEventType.APPROVAL_REQUESTED,
            payload={
                Keys.Field.APPROVAL_ID: child.approval_id,
                Keys.Field.CHAIN_PARENT_APPROVAL_ID: approval.approval_id,
                Keys.Field.APPROVAL_KIND: approval_kind,
                Keys.Field.REQUESTED_BY_USER_ID: target.user_id,
                **{
                    key: value
                    for key, value in approval.metadata.items()
                    if isinstance(key, str)
                    and key
                    in (
                        Keys.Field.SERVER_ID,
                        Keys.Field.SERVER_NAME,
                        "display_name",
                        Keys.Field.TOOL_NAME,
                        "risk_level",
                        Keys.Field.SOURCE_TOOL_CALL_ID,
                    )
                },
                Keys.Payload.MESSAGE: action_summary
                if isinstance(action_summary, str)
                else "",
            },
        )
        await self.persistence.write_audit_log(
            event_type=Messages.Audit.APPROVAL_FORWARD,
            record={
                "org_id": approval.org_id,
                "user_id": request.decided_by_user_id,
                "resource_type": "approval",
                "resource_id": approval.approval_id,
                "run_id": approval.run_id,
                "outcome": "success",
                "metadata": {
                    "chain_parent_approval_id": approval.approval_id,
                    "child_approval_id": child.approval_id,
                    "forwarded_to_user_id": target.user_id,
                    "approval_kind": approval_kind,
                    "reason": request.reason,
                },
            },
        )
        # PR 1.4.1 Gap #5 — fire-and-forget recipient notification *after*
        # the persistence transaction has committed and the audit row has
        # landed. The dispatcher is contractually swallow-and-log on
        # failure; we use ``asyncio.create_task`` to keep the request
        # latency bound to the writes we own. Failure to notify never
        # rolls back the forward — the chain re-converges either at the
        # recipient's next page-load or at the next sweeper tick.
        asyncio.create_task(
            self._notifications.notify_approval_assigned(
                approval=child,
                forwarded_by_user_id=request.decided_by_user_id,
            )
        )
        # PR 1.4.1 Gap #9 — emit the success counter. Labels are
        # constrained (depth is a small int, decision_kind is enumerated)
        # so cardinality is bounded.
        self._approval_metrics.record_forward_success(
            approval_kind=approval_kind if isinstance(approval_kind, str) else None,
            depth=child.chain_depth,
        )
        return ApprovalDecisionResponse(
            approval_id=approval.approval_id,
            run_id=approval.run_id,
            status=ApprovalStatus.FORWARDED,
            decided_at=now,
            forwarded_to_user_id=target.user_id,
            child_approval_id=child.approval_id,
        )

    async def _guard_forwardable(
        self,
        *,
        approval: ApprovalRequestRecord,
        target: ApprovalForwardTarget,
    ) -> None:
        """Pre-flight checks on a forward request before any write.

        Self-forward and decision/forward_to coherence are caught by the
        ``ApprovalDecisionRequest`` validator. This guard covers state
        invariants (parent must be pending; kind must be forwardable;
        chain depth cap) plus the membership lookup against the identity
        backend (PR 1.4.1 Gap #1). Membership resolution comes last so
        cheap rejections (kind, depth) short-circuit before we hit the
        network.
        """

        if approval.status is not ApprovalStatus.PENDING:
            self._approval_metrics.record_forward_invalid(
                reason=ForwardInvalidReason.NOT_PENDING
            )
            raise RuntimeApiError(
                RuntimeErrorCode.VALIDATION_ERROR,
                Messages.Error.APPROVAL_FORWARD_NOT_PENDING,
                http_status=status.HTTP_409_CONFLICT,
                retryable=False,
            )
        approval_kind = approval.metadata.get(Keys.Field.APPROVAL_KIND)
        if approval_kind not in self.APPROVAL_FORWARDABLE_KINDS:
            self._approval_metrics.record_forward_invalid(
                reason=ForwardInvalidReason.KIND_NOT_SUPPORTED
            )
            raise RuntimeApiError(
                RuntimeErrorCode.VALIDATION_ERROR,
                Messages.Error.APPROVAL_FORWARD_KIND_NOT_SUPPORTED,
                http_status=status.HTTP_422_UNPROCESSABLE_CONTENT,
                retryable=False,
            )
        # Walk the chain depth via the persisted column (set on insert).
        depth = self._chain_depth(approval=approval)
        if depth >= self.APPROVAL_FORWARD_MAX_CHAIN_DEPTH:
            self._approval_metrics.record_forward_invalid(
                reason=ForwardInvalidReason.CHAIN_TOO_DEEP
            )
            raise RuntimeApiError(
                RuntimeErrorCode.VALIDATION_ERROR,
                Messages.Error.APPROVAL_FORWARD_CHAIN_TOO_DEEP,
                http_status=status.HTTP_422_UNPROCESSABLE_CONTENT,
                retryable=False,
            )
        # PR 1.4.1 Gap #1 — verify the forward target is a real, active
        # member of this org *before* any DB write. Resolver failures
        # (5xx from identity backend) surface as 503 retryable; definitive
        # negatives surface as 422.
        try:
            is_active = await self._membership_resolver.is_active_member(
                org_id=approval.org_id, user_id=target.user_id
            )
        except MembershipResolverUnavailable as exc:
            self._approval_metrics.record_forward_invalid(
                reason=ForwardInvalidReason.RESOLVER_UNAVAILABLE
            )
            raise RuntimeApiError(
                RuntimeErrorCode.DEPENDENCY_ERROR,
                Messages.Error.SAFE_FALLBACK,
                http_status=status.HTTP_503_SERVICE_UNAVAILABLE,
                retryable=True,
            ) from exc
        if not is_active:
            self._approval_metrics.record_forward_invalid(
                reason=ForwardInvalidReason.TARGET_INVALID
            )
            raise RuntimeApiError(
                RuntimeErrorCode.VALIDATION_ERROR,
                Messages.Error.APPROVAL_FORWARD_INVALID_TARGET,
                http_status=status.HTTP_422_UNPROCESSABLE_CONTENT,
                retryable=False,
            )

    @classmethod
    def _chain_depth(cls, *, approval: ApprovalRequestRecord) -> int:
        """Return the row's persisted chain depth (PR 1.4.1 Gap #7).

        Migration 0018 adds the column with a CHECK that mirrors
        APPROVAL_FORWARD_MAX_CHAIN_DEPTH; the value is set on every
        insert (root rows = 0, forward children = parent.chain_depth + 1).
        Reading the column makes the cap honour exactly 3 hops without a
        recursive CTE on the hot path.
        """

        return approval.chain_depth

    @classmethod
    def _wire_status_for(
        cls,
        *,
        approval_kind: object,
        record_status: str,
    ) -> str:
        """Translate the persisted ApprovalStatus into a wire-level status string.

        For ``ask_a_question`` approvals the persisted "approved"/"rejected"
        record is a question-answer event in disguise. Surface the user-facing
        vocabulary so the chat UI does not have to render a question card with
        an "Approved"/"Rejected" status badge."""

        if approval_kind == Values.ApprovalKind.ASK_A_QUESTION:
            if record_status == ApprovalStatus.APPROVED.value:
                return Values.Status.ANSWERED
            return Values.Status.SKIPPED
        return record_status

    @classmethod
    def _create_run_response(
        cls,
        *,
        run: RunRecord,
        user_message_id: str,
        prior_run_ids: tuple[str, ...] = (),
    ) -> CreateRunResponse:
        return CreateRunResponse(
            run_id=run.run_id,
            conversation_id=run.conversation_id,
            user_message_id=user_message_id,
            trace_id=run.trace_id,
            status=run.status,
            stream_url=f"/v1/agent/runs/{run.run_id}/stream?after_sequence=0",
            events_url=f"/v1/agent/runs/{run.run_id}/events?after_sequence=0",
            created_at=run.created_at,
            prior_run_ids=prior_run_ids,
        )

    async def _prior_run_ids_for_chain(
        self,
        *,
        org_id: str,
        conversation_id: str,
        current_run_id: str,
        user_message: MessageRecord,
    ) -> tuple[str, ...]:
        """Return distinct prior run ids reachable through the parent chain.

        The chain mirrors ``RuntimeRunHandler._selected_message_chain`` so the
        ids surfaced here match the runs whose events feed the next turn's
        prompt context. Surfacing them keeps debugging local — on-call can
        replay just the runs that shaped a given turn instead of scanning the
        whole conversation.
        """

        records = await self.persistence.list_messages(
            org_id=org_id,
            conversation_id=conversation_id,
            limit=200,
        )
        records_by_id = {record.message_id: record for record in records}
        seen: set[str] = set()
        ordered: list[str] = []
        cursor: str | None = user_message.parent_message_id
        while cursor is not None:
            record = records_by_id.get(cursor)
            if record is None:
                break
            run_id = record.run_id
            if run_id is not None and run_id != current_run_id and run_id not in seen:
                seen.add(run_id)
                ordered.append(run_id)
            cursor = record.parent_message_id
        return tuple(reversed(ordered))

    async def _resolve_user_policies(
        self, *, org_id: str, user_id: str
    ) -> dict[str, object]:
        """PR 8.0.5 — fetch the per-(org, user) policy snapshot once.

        Returns ``{"tool_use": {...}, "privacy": {...}}`` or ``{}`` —
        the resolver itself maps every "not configured" / "fetch
        failed" case to the empty dict so the runtime never refuses
        a run on a missing snapshot. Consumers downcast to typed
        snapshots via the ``ToolUsePolicySnapshot.from_response`` /
        ``PrivacySettingsSnapshot.from_response`` factories.
        """

        return await self._user_policies_resolver.resolve(
            org_id=org_id, user_id=user_id
        )

    async def _resolve_suggested_connectors(
        self,
        *,
        org_id: str,
        user_id: str,
        paused_connectors: tuple[str, ...],
    ) -> tuple[CatalogSuggestionCard, ...]:
        """PR 4.4.7 Phase 2 (Slice B) — fetch the catalog suggestions
        the agent may surface this run.

        Returns the catalog entries the user could be progressively
        introduced to (paused / installed / muted entries already
        filtered server-side). Empty tuple means "no suggestions" — the
        runtime's system prompt section is skipped, so there is no
        token cost on runs that have nothing to suggest.
        """

        return await self._suggestible_connectors_resolver.resolve(
            org_id=org_id,
            user_id=user_id,
            exclude_paused=paused_connectors,
        )

    async def _resolve_workspace_behavior_overrides(
        self, *, org_id: str
    ) -> dict[str, object]:
        """Return the workspace-policy knobs as a JSON-serialisable dict.

        Returns ``{}`` when no row exists or the overrides are at their
        defaults — keeps ``runtime_context_json`` compact for the most
        common case (org has not customised anything yet).
        """

        record = await self._workspace_defaults().get_record(org_id=org_id)
        if record is None:
            return {}
        # ``model_dump(exclude_none=True)`` strips absent overrides so
        # consumers can safely ``.get(key)`` and treat ``None`` as
        # "fall through to deployment default".
        blob = record.behavior_overrides.model_dump(mode="json", exclude_none=True)
        # ``training_data_opt_out=False`` is the default; only carry it
        # forward when explicitly opted out (keeps the blob empty for
        # the common case).
        if blob.get("training_data_opt_out") is False:
            blob.pop("training_data_opt_out", None)
        return blob

    def _request_with_runtime_context(  # noqa: C901 — additive params keep the call site small
        self,
        request: CreateRunRequest,
        *,
        workspace_behavior_overrides: dict[str, object] | None = None,
        user_policies_json: dict[str, object] | None = None,
        suggested_connectors: tuple[CatalogSuggestionCard, ...] = (),
    ) -> CreateRunRequest:
        try:
            model = request.model
            model_config = self.model_resolver.resolve(
                ModelSelection(
                    provider=model.provider if model is not None else None,
                    model_name=model.model_name if model is not None else None,
                    temperature=model.temperature if model is not None else None,
                    timeout_seconds=model.timeout_seconds
                    if model is not None
                    else None,
                    max_input_tokens=model.max_input_tokens
                    if model is not None
                    else None,
                    supports_streaming=model.supports_streaming
                    if model is not None
                    else None,
                    reasoning=model.reasoning if model is not None else None,
                )
            )
        except AgentRuntimeError as exc:
            raise RuntimeApiError(
                exc.code,
                exc.safe_message,
                http_status=status.HTTP_400_BAD_REQUEST,
                retryable=exc.retryable,
                correlation_id=exc.correlation_id,
            ) from exc
        context = request.request_context
        trace_metadata = dict(context.trace_metadata)
        if context.context:
            trace_metadata["request_context"] = context.context
        quote = request.quote_payload()
        if quote is not None:
            trace_metadata["quote"] = quote
        if request.attachments:
            trace_metadata["attachments"] = [
                attachment.model_dump(
                    mode="json",
                    exclude_none=True,
                    exclude_defaults=True,
                )
                for attachment in request.attachments
            ]
        if request.content:
            trace_metadata["content_parts"] = [
                part.model_dump(
                    mode="json",
                    exclude_none=True,
                    exclude_defaults=True,
                )
                for part in request.content
            ]
        if request.regenerate_from_message_id is not None:
            trace_metadata["regenerate_from_message_id"] = (
                request.regenerate_from_message_id
            )
        if request.source_message_id is not None:
            trace_metadata["source_message_id"] = request.source_message_id
        if request.parent_message_id is not None:
            trace_metadata["parent_message_id"] = request.parent_message_id
        if request.branch_id is not None:
            trace_metadata["branch_id"] = request.branch_id
        branch = request.branch_payload()
        if branch is not None:
            trace_metadata["branch"] = branch
        runtime_context = AgentRuntimeContext(
            user_id=request.user_id,
            org_id=request.org_id,
            roles=context.roles,
            permission_scopes=context.permission_scopes,
            connector_scopes=context.connector_scopes,
            paused_connectors=context.paused_connectors,
            suggested_connectors=suggested_connectors,
            model_profile=model_config,
            max_parallel_tasks=self.settings.execution.max_parallel_tasks,
            trace_metadata=trace_metadata,
            feature_flags=context.feature_flags,
            workspace_behavior_overrides=workspace_behavior_overrides or {},
            user_policies_json=user_policies_json or {},
        )
        return request.model_copy(update={"runtime_context": runtime_context})

    async def _conversation_for_scope(
        self,
        *,
        org_id: str,
        user_id: str,
        conversation_id: str,
    ):
        conversation = await self.persistence.get_conversation(
            org_id=org_id,
            user_id=user_id,
            conversation_id=conversation_id,
        )
        if conversation is None:
            raise RuntimeApiError(
                RuntimeErrorCode.CAPABILITY_NOT_FOUND,
                Messages.Error.CONVERSATION_NOT_FOUND,
                http_status=status.HTTP_404_NOT_FOUND,
                retryable=False,
            )
        return conversation

    async def _conversation_for_owner_or_admin(
        self,
        *,
        org_id: str,
        actor_user_id: str,
        conversation_id: str,
        allow_admin_override: bool,
    ) -> tuple[ConversationRecord, bool]:
        """Owner-first lookup with optional admin-override fallback (PR 1.2.1).

        Returns ``(record, is_admin_override)``. Owner self-access wins
        (no override), avoiding an extra query in the common case.
        Non-owner callers without ``allow_admin_override=True`` get 404 —
        same opacity as the strict ``_conversation_for_scope`` path so
        existence is never leaked. Admin path returns ``(record, True)``
        only when the caller is provably acting on someone else's data.
        """

        conversation = await self.persistence.get_conversation(
            org_id=org_id,
            user_id=actor_user_id,
            conversation_id=conversation_id,
        )
        if conversation is not None:
            return conversation, False
        if not allow_admin_override:
            raise RuntimeApiError(
                RuntimeErrorCode.CAPABILITY_NOT_FOUND,
                Messages.Error.CONVERSATION_NOT_FOUND,
                http_status=status.HTTP_404_NOT_FOUND,
                retryable=False,
            )
        admin_view = await self.persistence.get_conversation_for_org(
            org_id=org_id,
            conversation_id=conversation_id,
        )
        if admin_view is None:
            # Foreign tenant — same 404 opacity as the owner path.
            raise RuntimeApiError(
                RuntimeErrorCode.CAPABILITY_NOT_FOUND,
                Messages.Error.CONVERSATION_NOT_FOUND,
                http_status=status.HTTP_404_NOT_FOUND,
                retryable=False,
            )
        return admin_view, True

    async def _conversation_for_scope_when_known(
        self, *, request: CreateRunRequest
    ) -> ConversationRecord | None:
        """Resolve the conversation row when both org and user are present.

        Returns ``None`` when ``org_id`` / ``user_id`` aren't yet populated
        on the request — in that case the existing path handles it. Returns
        the row when found, raises 404 otherwise (caller will be inside
        ``create_run`` and we want the same behaviour as before).
        """

        if request.org_id is None or request.user_id is None:
            return None
        return await self._conversation_for_scope(
            org_id=request.org_id,
            user_id=request.user_id,
            conversation_id=request.conversation_id,
        )

    @staticmethod
    def _apply_conversation_scope_fallback(
        *, request: CreateRunRequest, conversation: ConversationRecord
    ) -> CreateRunRequest:
        """Fall back to the stored per-chat scope when the inbound dict is empty.

        Header (already merged into ``request_context.connector_scopes`` by
        the route) wins when non-empty so service-to-service callers stay
        in control. When empty, the chat's stored override (filtered to
        active connectors only) materialises into the runtime context.

        PR 4.4.6.2 — also lifts the conversation's *paused* connector ids
        onto ``request_context.paused_connectors`` whenever the runtime
        context is being driven by the conversation column, so MCP gates
        downstream see an explicit "deny" for connectors the user
        toggled off in the popover. ``connector_scopes`` alone can't
        carry that signal (its empty-set is ambiguous between "no
        override" and "all paused"). Header-driven flows skip this so
        service-to-service callers retain full control.
        """

        paused = conversation.paused_connectors()
        if request.request_context.connector_scopes:
            if not paused:
                return request
            new_context = request.request_context.model_copy(
                update={"paused_connectors": paused}
            )
            return request.model_copy(update={"request_context": new_context})
        fallback = conversation.runtime_connector_scopes()
        if not fallback and not paused:
            return request
        update: dict[str, object] = {}
        if fallback:
            update["connector_scopes"] = fallback
        if paused:
            update["paused_connectors"] = paused
        new_context = request.request_context.model_copy(update=update)
        return request.model_copy(update={"request_context": new_context})

    async def _run_for_scope(
        self, *, org_id: str, user_id: str, run_id: str
    ) -> RunRecord:
        run = await self.persistence.get_run(org_id=org_id, run_id=run_id)
        if run is None or run.user_id != user_id:
            raise RuntimeApiError(
                RuntimeErrorCode.CAPABILITY_NOT_FOUND,
                Messages.Error.RUN_NOT_FOUND,
                http_status=status.HTTP_404_NOT_FOUND,
                retryable=False,
            )
        return run

    # ==================================================================
    # PR 1.6 — workspace defaults + conversation lifecycle
    # ==================================================================

    def _workspace_defaults(self):
        """Build a ``WorkspaceDefaultsService`` over our async persistence.

        Lazy import keeps the module-level dependency graph one-way
        (``workspace_defaults_service`` imports from
        ``runtime_api.schemas``; we don't want this file to be loaded
        while the schemas package is still resolving).
        """

        from agent_runtime.api.workspace_defaults_service import (
            WorkspaceDefaultsService,
        )

        return WorkspaceDefaultsService(
            persistence=self.persistence,
            settings=self.settings,
        )

    async def get_workspace_defaults(self, *, org_id: str) -> WorkspaceDefaultsResponse:
        """Public ``GET /v1/agent/workspace/defaults`` (PR 1.6)."""

        return await self._workspace_defaults().get(org_id=org_id)

    async def update_workspace_defaults(
        self,
        *,
        org_id: str,
        actor_user_id: str,
        request: UpdateWorkspaceDefaultsRequest,
    ) -> WorkspaceDefaultsResponse:
        """Public ``PUT /v1/agent/workspace/defaults`` (PR 1.6 + PR 4.3).

        Composes the workspace_defaults upsert + retention policy
        upserts and writes one or more audit rows. Validates the
        requested default model against the same catalog ``list_models``
        exposes — bouncing typos as 422 instead of letting them silently
        corrupt every future ``create_run`` for the org.

        Audit emission (PR 4.3):
          * Always: ``workspace.defaults.update`` (PR 1.6 row, carries
            the full diff including ``behavior_overrides``).
          * Always: ``workspace.behavior_overrides.update`` when the
            ``behavior_overrides`` block changed (the diff is part of
            ``audit_metadata['diff_keys']``).
          * Always: ``workspace.training_opt_out.update`` when the
            boolean flag transitioned (compliance auditors search by
            this dedicated action).
        """

        self._validate_workspace_default_model(request)
        # Snapshot the prior overrides BEFORE the upsert so the
        # dedicated training-opt-out diff row reflects the actual
        # transition (and not the post-write state).
        before_record = await self._workspace_defaults().get_record(org_id=org_id)
        response, audit_metadata = await self._workspace_defaults().update(
            org_id=org_id,
            actor_user_id=actor_user_id,
            request=request,
        )
        await self.persistence.write_audit_log(
            event_type=Messages.Audit.WORKSPACE_DEFAULTS_UPDATE,
            record={
                "org_id": org_id,
                "user_id": actor_user_id,
                "resource_type": "workspace_defaults",
                "resource_id": org_id,
                "outcome": "success",
                "metadata": audit_metadata,
            },
        )
        # PR 4.3 — when the behavior_overrides block changed, emit a
        # dedicated audit row so SIEM searches by action name find them
        # without parsing the broader defaults diff.
        if "behavior_overrides" in audit_metadata.get("diff_keys", []):
            await self.persistence.write_audit_log(
                event_type=Messages.Audit.WORKSPACE_BEHAVIOR_OVERRIDES_UPDATE,
                record={
                    "org_id": org_id,
                    "user_id": actor_user_id,
                    "resource_type": "workspace_defaults",
                    "resource_id": org_id,
                    "outcome": "success",
                    "metadata": {
                        "before": audit_metadata["before"]["behavior_overrides"],
                        "after": audit_metadata["after"]["behavior_overrides"],
                    },
                },
            )
        # PR 4.3 — dedicated row for the training opt-out boolean
        # transition (compliance / DPA audit signal). Fires only when
        # the value flipped.
        from agent_runtime.api.workspace_defaults_service import (
            WorkspaceDefaultsService,
        )

        before_overrides = (
            before_record.behavior_overrides if before_record is not None else None
        )
        opt_out_diff = WorkspaceDefaultsService.training_opt_out_diff(
            before=before_overrides,
            after=request.behavior_overrides,
        )
        if opt_out_diff is not None:
            previous, current = opt_out_diff
            await self.persistence.write_audit_log(
                event_type=Messages.Audit.WORKSPACE_TRAINING_OPT_OUT_UPDATE,
                record={
                    "org_id": org_id,
                    "user_id": actor_user_id,
                    "resource_type": "workspace_defaults",
                    "resource_id": org_id,
                    "outcome": "success",
                    "metadata": {"before": previous, "after": current},
                },
            )
        return response

    # ==================================================================
    # PR 4.3 — workspace data export + delete-all stubs
    # ==================================================================

    async def request_workspace_export(
        self,
        *,
        org_id: str,
        actor_user_id: str,
    ) -> dict[str, str]:
        """Audit a queued workspace export (v1 stub).

        Real export pipeline lives in a follow-up PR. The stub returns
        ``{export_id, status}`` and emits one audit row so a forensic
        reader knows when a member asked, which org, by whom.
        """

        from uuid import uuid4

        export_id = f"exp_{uuid4().hex[:24]}"
        await self.persistence.write_audit_log(
            event_type=Messages.Audit.WORKSPACE_EXPORT_REQUEST,
            record={
                "org_id": org_id,
                "user_id": actor_user_id,
                "resource_type": "workspace_export",
                "resource_id": export_id,
                "outcome": "queued",
                "metadata": {
                    "export_id": export_id,
                    "scope": "workspace",
                    "status": "queued",
                },
            },
        )
        return {"export_id": export_id, "status": "queued"}

    async def record_workspace_delete_attempt(
        self,
        *,
        org_id: str,
        actor_user_id: str,
        typed_confirmation_correct: bool,
    ) -> None:
        """Audit a delete-all-data attempt (v1 stub; route returns 501).

        We record both correct and incorrect typed-confirmation answers
        so an attacker can't accidentally fly under audit by giving a
        wrong slug — every attempt leaves a row.
        """

        await self.persistence.write_audit_log(
            event_type=Messages.Audit.WORKSPACE_DELETE_ATTEMPT,
            record={
                "org_id": org_id,
                "user_id": actor_user_id,
                "resource_type": "workspace_data",
                "resource_id": org_id,
                "outcome": "blocked",
                "metadata": {
                    "typed_confirmation_correct": typed_confirmation_correct,
                },
            },
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
        """Public ``PATCH /v1/agent/conversations/{id}`` (PR 1.6).

        Lifecycle PATCH: title/folder/archived. Each field is optional;
        we use ``model_fields_set`` to honour RFC 7396 merge-patch
        semantics (omitted = no-op, explicit null = clear).

        ``allow_admin_override`` mirrors PR 1.2.1: an actor with the
        ``ADMIN_USERS`` permission scope can PATCH another member's
        chat in the same tenant; the audit row records
        ``override_by_admin=True`` plus the owner's user_id.
        """

        before, is_admin_override = await self._conversation_for_owner_or_admin(
            org_id=org_id,
            actor_user_id=user_id,
            conversation_id=conversation_id,
            allow_admin_override=allow_admin_override,
        )
        fields_set = request.model_fields_set
        title_changed = "title" in fields_set
        folder_changed = "folder" in fields_set
        archived_changed = "archived" in fields_set
        now = datetime.now(timezone.utc)
        # Persistence UPDATE filters by owner user_id; for admin
        # overrides we use the owner's id (from the loaded record), not
        # the actor's. The actor is captured separately in audit metadata.
        updated = await self.persistence.update_conversation(
            org_id=org_id,
            user_id=before.user_id,
            conversation_id=conversation_id,
            title=request.title,
            title_changed=title_changed,
            folder=request.folder,
            folder_changed=folder_changed,
            archived=request.archived,
            archived_changed=archived_changed,
            now=now,
        )
        if updated is None:
            # Race: row vanished between the scope read and the UPDATE.
            raise RuntimeApiError(
                RuntimeErrorCode.CAPABILITY_NOT_FOUND,
                Messages.Error.CONVERSATION_NOT_FOUND,
                http_status=status.HTTP_404_NOT_FOUND,
                retryable=False,
            )
        audit_metadata = _conversation_lifecycle_audit_metadata(
            before=before,
            after=updated,
            fields_set=fields_set,
        )
        if is_admin_override:
            audit_metadata["override_by_admin"] = True
            audit_metadata["conversation_owner_user_id"] = before.user_id
        await self.persistence.write_audit_log(
            event_type=Messages.Audit.CONVERSATION_UPDATE,
            record={
                "org_id": org_id,
                "user_id": user_id,
                "resource_type": "conversation",
                "resource_id": conversation_id,
                "outcome": "success",
                "metadata": audit_metadata,
            },
        )
        return updated.to_response()

    async def delete_conversation(
        self,
        *,
        org_id: str,
        user_id: str,
        conversation_id: str,
        allow_admin_override: bool = False,
    ) -> None:
        """Public ``DELETE /v1/agent/conversations/{id}`` (PR 1.6).

        Soft-deletes the row (stamps ``deleted_at``). When an active
        run exists for this conversation, it is cancelled first via
        the existing ``cancel_run`` path so the SSE stream emits
        ``run_cancelled`` and clients close cleanly.

        ``allow_admin_override`` mirrors PR 1.2.1 / connector PATCH:
        an actor with ``ADMIN_USERS`` can soft-delete another member's
        chat in the same tenant; the audit row records the override
        and the owner's user_id for SIEM reconstruction.
        """

        conversation, is_admin_override = await self._conversation_for_owner_or_admin(
            org_id=org_id,
            actor_user_id=user_id,
            conversation_id=conversation_id,
            allow_admin_override=allow_admin_override,
        )
        await self._cancel_active_run_for_conversation(
            org_id=org_id,
            user_id=conversation.user_id,
            conversation_id=conversation_id,
        )
        now = datetime.now(timezone.utc)
        await self.persistence.soft_delete_conversation(
            org_id=org_id,
            user_id=conversation.user_id,
            conversation_id=conversation_id,
            now=now,
        )
        retention_until = await self._resolve_conversation_retention_until(
            org_id=org_id,
            user_id=conversation.user_id,
            conversation_id=conversation_id,
            assistant_id=conversation.assistant_id,
            deleted_at=now,
        )
        audit_metadata: dict[str, object] = {
            "conversation_id": conversation_id,
            "folder": conversation.folder,
            "retention_until": (
                retention_until.isoformat() if retention_until is not None else None
            ),
        }
        if is_admin_override:
            audit_metadata["override_by_admin"] = True
            audit_metadata["conversation_owner_user_id"] = conversation.user_id
        await self.persistence.write_audit_log(
            event_type=Messages.Audit.CONVERSATION_DELETE,
            record={
                "org_id": org_id,
                "user_id": user_id,
                "resource_type": "conversation",
                "resource_id": conversation_id,
                "outcome": "success",
                "metadata": audit_metadata,
            },
        )

    async def restore_conversation(
        self,
        *,
        org_id: str,
        user_id: str,
        conversation_id: str,
        allow_admin_override: bool = False,
    ) -> ConversationResponse:
        """Public ``POST /v1/agent/conversations/{id}/restore`` (PR 1.6).

        ``allow_admin_override`` mirrors PR 1.2.1: an actor with
        ``ADMIN_USERS`` can restore another member's chat in the same
        tenant.
        """

        # Soft-deleted rows are still visible to the owner via
        # ``get_conversation`` (the adapter doesn't filter by
        # ``deleted_at``). For admin overrides we fall through to
        # ``get_conversation_for_org`` which is also deleted-row-aware.
        now = datetime.now(timezone.utc)
        owner_user_id = user_id
        is_admin_override = False
        if allow_admin_override:
            owner_lookup = await self.persistence.get_conversation(
                org_id=org_id,
                user_id=user_id,
                conversation_id=conversation_id,
            )
            if owner_lookup is None:
                admin_view = await self.persistence.get_conversation_for_org(
                    org_id=org_id, conversation_id=conversation_id
                )
                if admin_view is None:
                    raise RuntimeApiError(
                        RuntimeErrorCode.CAPABILITY_NOT_FOUND,
                        Messages.Error.CONVERSATION_NOT_FOUND,
                        http_status=status.HTTP_404_NOT_FOUND,
                        retryable=False,
                    )
                owner_user_id = admin_view.user_id
                is_admin_override = True
        restored = await self.persistence.restore_conversation(
            org_id=org_id,
            user_id=owner_user_id,
            conversation_id=conversation_id,
            now=now,
        )
        if restored is None:
            raise RuntimeApiError(
                RuntimeErrorCode.CAPABILITY_NOT_FOUND,
                Messages.Error.CONVERSATION_NOT_FOUND,
                http_status=status.HTTP_404_NOT_FOUND,
                retryable=False,
            )
        audit_metadata: dict[str, object] = {"conversation_id": conversation_id}
        if is_admin_override:
            audit_metadata["override_by_admin"] = True
            audit_metadata["conversation_owner_user_id"] = owner_user_id
        await self.persistence.write_audit_log(
            event_type=Messages.Audit.CONVERSATION_RESTORE,
            record={
                "org_id": org_id,
                "user_id": user_id,
                "resource_type": "conversation",
                "resource_id": conversation_id,
                "outcome": "success",
                "metadata": audit_metadata,
            },
        )
        return restored.to_response()

    async def _cancel_active_run_for_conversation(
        self,
        *,
        org_id: str,
        user_id: str,
        conversation_id: str,
    ) -> None:
        """Cancel any non-terminal run on the conversation.

        Reuses the existing ``cancel_run`` path so the SSE handshake
        and audit chain stay identical to a user-initiated cancel.
        We tolerate "no active run" silently — the typical case.
        """

        active_run = await self.persistence.get_active_run_for_conversation(
            org_id=org_id, conversation_id=conversation_id
        )
        if active_run is None:
            return
        await self.cancel_run(
            org_id=org_id,
            user_id=user_id,
            run_id=active_run.run_id,
            request=CancelRunRequest(
                requested_by_user_id=user_id,
                reason="conversation_deleted",
            ),
        )

    def _validate_workspace_default_model(
        self, request: UpdateWorkspaceDefaultsRequest
    ) -> None:
        """Reject unknown providers / model names with typed 422s.

        Provider validation reuses ``ModelConfigResolver._normalize_provider``
        (the same alias table the run path enforces), so the ground
        truth of "what providers exist" lives in one place.
        Model-name validation reuses ``list_models().models``: the
        catalog the FE picker reads from is the same set the admin
        is allowed to nominate as a default. No second source of
        truth, no manual list to drift.
        """

        try:
            self.model_resolver._normalize_provider(request.default_model.provider)
        except AgentRuntimeError as exc:
            raise RuntimeApiError(
                exc.code,
                Messages.Error.UNKNOWN_MODEL_PROVIDER,
                http_status=status.HTTP_422_UNPROCESSABLE_CONTENT,
                retryable=False,
            ) from exc
        catalog_ids = {model.id for model in self.list_models().models}
        catalog_names = {model.model_name for model in self.list_models().models}
        if (
            request.default_model.model_name not in catalog_ids
            and request.default_model.model_name not in catalog_names
        ):
            raise RuntimeApiError(
                RuntimeErrorCode.VALIDATION_ERROR,
                Messages.Error.UNKNOWN_MODEL_NAME,
                http_status=status.HTTP_422_UNPROCESSABLE_CONTENT,
                retryable=False,
            )

    async def _resolve_conversation_retention_until(
        self,
        *,
        org_id: str,
        user_id: str,
        conversation_id: str,
        assistant_id: str,
        deleted_at: datetime,
    ) -> datetime | None:
        """Resolve the moment the C8 sweeper would reap this conversation.

        Walks the same most-specific policy precedence the sweeper
        uses (``conversation > assistant > user > org > deployment``)
        for ``RetentionKind.MESSAGES``. Returns ``None`` when no TTL
        applies (single-tenant deploys without seeded policies). The
        forensic value lives in the audit row so SIEM can answer "when
        will this become unrecoverable?" without re-walking policy at
        read time.
        """

        from agent_runtime.persistence.records.retention import RetentionKind
        from agent_runtime.retention import (
            DEPLOYMENT_DEFAULT_TTL_SECONDS,
            RetentionPolicyResolver,
        )

        policies = await self.persistence.list_retention_policies(org_id=org_id)
        resolver = RetentionPolicyResolver(
            org_id=org_id,
            policies=policies,
            deployment_defaults=DEPLOYMENT_DEFAULT_TTL_SECONDS,
        )
        resolved = resolver.resolve(
            kind=RetentionKind.MESSAGES,
            conversation_id=conversation_id,
            user_id=user_id,
            assistant_id=assistant_id,
        )
        if resolved.ttl_seconds is None:
            return None
        return deleted_at + timedelta(seconds=resolved.ttl_seconds)


def _conversation_lifecycle_audit_metadata(
    *,
    before: ConversationRecord,
    after: ConversationRecord,
    fields_set: frozenset[str] | set[str],
) -> dict[str, object]:
    """Build before/after/diff metadata for a lifecycle PATCH (PR 1.6)."""

    diff_keys: list[str] = []
    before_blob: dict[str, object] = {}
    after_blob: dict[str, object] = {}
    if "title" in fields_set:
        before_blob["title"] = before.title
        after_blob["title"] = after.title
        if before.title != after.title:
            diff_keys.append("title")
    if "folder" in fields_set:
        before_blob["folder"] = before.folder
        after_blob["folder"] = after.folder
        if before.folder != after.folder:
            diff_keys.append("folder")
    if "archived" in fields_set:
        before_blob["archived"] = before.status == ConversationStatus.ARCHIVED
        after_blob["archived"] = after.status == ConversationStatus.ARCHIVED
        if before_blob["archived"] != after_blob["archived"]:
            diff_keys.append("archived")
    return {
        "before": before_blob,
        "after": after_blob,
        "diff_keys": diff_keys,
    }


def _display_model_name(model_name: str) -> str:
    parts = model_name.replace("_", "-").split("-")
    return " ".join(
        part.upper() if part in {"gpt"} else part.capitalize() for part in parts
    )


def _connector_scope_audit_metadata(
    *,
    before: dict[str, tuple[str, ...] | None],
    patch: dict[str, tuple[str, ...] | None],
    after: dict[str, tuple[str, ...] | None],
) -> dict[str, object]:
    """Build the audit metadata blob for a per-chat connector scope change.

    Captures the keys touched by the patch plus the before/after value of
    each — enough for forensic reconstruction without leaking unrelated
    state. Tuples are serialised as lists for JSON portability; ``None``
    survives as JSON null and signals the paused state.
    """

    def _to_json(
        value: dict[str, tuple[str, ...] | None],
    ) -> dict[str, list[str] | None]:
        return {
            connector_id: (list(scopes) if scopes is not None else None)
            for connector_id, scopes in value.items()
        }

    diff_keys = sorted(patch.keys())
    return {
        "before": _to_json({k: before.get(k) for k in diff_keys}),
        "after": _to_json({k: after.get(k) for k in diff_keys}),
        "diff_keys": diff_keys,
    }
