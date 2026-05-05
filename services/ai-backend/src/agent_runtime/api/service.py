"""Thin application service for the FastAPI runtime API."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone

from starlette import status

from agent_runtime.execution.contracts import (
    AgentRuntimeContext,
    RuntimeErrorCode,
    StreamEventSource,
)
from agent_runtime.api.constants import Keys, Messages, Values
from agent_runtime.api.usage_service import ConversationContextBuilder
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
    CancelRunRequest,
    CancelRunResponse,
    ConversationConnectorScopesResponse,
    ConversationContextResponse,
    ConversationListResponse,
    ConversationRecord,
    ConversationResponse,
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
)
from runtime_api.http.errors import RuntimeApiError
from agent_runtime.api.events import RuntimeEventProducer
from agent_runtime.api.async_ports import (
    AsyncEventStorePort,
    AsyncPersistencePort,
    AsyncRuntimeQueuePort,
)
from agent_runtime.api.ports import EventStorePort, PersistencePort, RuntimeQueuePort
from agent_runtime.execution.errors import AgentRuntimeError
from agent_runtime.execution.models import ModelConfigResolver, ModelSelection
from agent_runtime.settings import RuntimeSettings
from runtime_adapters.async_wrappers import (
    adapt_event_store_to_async,
    adapt_persistence_to_async,
    adapt_queue_to_async,
)


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
        persistence: PersistencePort | AsyncPersistencePort,
        event_store: EventStorePort | AsyncEventStorePort,
        queue: RuntimeQueuePort | AsyncRuntimeQueuePort,
        settings: RuntimeSettings | None = None,
        model_resolver: ModelConfigResolver | None = None,
        on_event_appended: Callable[[str], None] | None = None,
    ) -> None:
        # The service is uniformly async on the inside. Sync ports get
        # wrapped via to_thread; async ports pass through. This way every
        # call site below uses `await self.persistence.*` and we never have
        # to ask which kind of backend is configured.
        self.persistence: AsyncPersistencePort = adapt_persistence_to_async(persistence)
        self.event_store: AsyncEventStorePort = adapt_event_store_to_async(event_store)
        self.queue: AsyncRuntimeQueuePort = adapt_queue_to_async(queue)
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
        """Create or idempotently return a conversation."""

        conversation = await self.persistence.create_conversation(request)
        await self.persistence.write_audit_log(
            event_type="conversation_created",
            record={
                "org_id": conversation.org_id,
                "user_id": conversation.user_id,
                "resource_type": "conversation",
                "resource_id": conversation.conversation_id,
                "outcome": "success",
            },
        )
        return conversation.to_response()

    async def get_conversation(
        self,
        *,
        org_id: str,
        user_id: str,
        conversation_id: str,
    ) -> ConversationResponse:
        """Return conversation metadata for the caller scope."""

        conversation = await self._conversation_for_scope(
            org_id=org_id,
            user_id=user_id,
            conversation_id=conversation_id,
        )
        return conversation.to_response()

    async def list_conversations(
        self,
        *,
        org_id: str,
        user_id: str,
        limit: int = Values.DEFAULT_CONVERSATION_LIMIT,
        include_archived: bool = False,
    ) -> ConversationListResponse:
        """Return scoped conversation metadata newest first."""

        bounded_limit = min(max(1, limit), Values.MAX_MESSAGE_LIMIT)
        records = await self.persistence.list_conversations(
            org_id=org_id,
            user_id=user_id,
            limit=bounded_limit,
            include_archived=include_archived,
        )
        return ConversationListResponse(
            conversations=tuple(record.to_response() for record in records),
            has_more=len(records) == bounded_limit,
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
    ) -> ConversationConnectorScopesResponse:
        """Merge-patch the chat's connector scope override + emit an audit row.

        404s for foreign-tenant conversations (does not leak existence).
        Audit metadata captures ``before`` / ``after`` / ``diff_keys`` for
        forensic reconstruction; the row is also append-only via the
        existing ``runtime_audit_log`` HMAC chain.
        """

        before = await self._conversation_for_scope(
            org_id=org_id,
            user_id=user_id,
            conversation_id=conversation_id,
        )
        now = datetime.now(timezone.utc)
        updated = await self.persistence.update_conversation_connectors(
            org_id=org_id,
            user_id=user_id,
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
        await self.persistence.write_audit_log(
            event_type=Messages.Audit.CONVERSATION_CONNECTORS_UPDATE,
            record={
                "org_id": org_id,
                "user_id": user_id,
                "resource_type": "conversation",
                "resource_id": conversation_id,
                "outcome": "success",
                "metadata": _connector_scope_audit_metadata(
                    before=before.enabled_connectors,
                    patch=request.scopes,
                    after=updated.enabled_connectors,
                ),
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

        request = self._request_with_runtime_context(request)
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
            Values.ApprovalKind.MCP_AUTH,
            Values.ApprovalKind.MCP_TOOL,
        }
    )

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
        )

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
        self._guard_forwardable(approval=approval, target=target)
        run = await self._run_for_scope(
            org_id=approval.org_id,
            user_id=approval.user_id,
            run_id=approval.run_id,
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
            if "not_pending" in str(exc):
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
        return ApprovalDecisionResponse(
            approval_id=approval.approval_id,
            run_id=approval.run_id,
            status=ApprovalStatus.FORWARDED,
            decided_at=now,
            forwarded_to_user_id=target.user_id,
            child_approval_id=child.approval_id,
        )

    def _guard_forwardable(
        self,
        *,
        approval: ApprovalRequestRecord,
        target: ApprovalForwardTarget,
    ) -> None:
        """Pre-flight checks on a forward request before any write.

        Self-forward and decision/forward_to coherence are caught by the
        ``ApprovalDecisionRequest`` validator. This guard covers state
        invariants (parent must be pending; kind must be forwardable;
        chain depth cap) plus a defensive cross-tenant check on the
        target user_id format. The "is the target a real workspace user?"
        check belongs in services/backend's identity layer; for v1 we
        rely on the audit + notification dispatch to surface a missing
        user. Inactive members will simply never see the inbox row.
        """

        if approval.status is not ApprovalStatus.PENDING:
            raise RuntimeApiError(
                RuntimeErrorCode.VALIDATION_ERROR,
                Messages.Error.APPROVAL_FORWARD_NOT_PENDING,
                http_status=status.HTTP_409_CONFLICT,
                retryable=False,
            )
        approval_kind = approval.metadata.get(Keys.Field.APPROVAL_KIND)
        if approval_kind not in self.APPROVAL_FORWARDABLE_KINDS:
            raise RuntimeApiError(
                RuntimeErrorCode.VALIDATION_ERROR,
                Messages.Error.APPROVAL_FORWARD_KIND_NOT_SUPPORTED,
                http_status=status.HTTP_422_UNPROCESSABLE_CONTENT,
                retryable=False,
            )
        # Walk the chain depth via metadata (cheap; no extra fetches).
        depth = self._chain_depth(approval=approval)
        if depth >= self.APPROVAL_FORWARD_MAX_CHAIN_DEPTH:
            raise RuntimeApiError(
                RuntimeErrorCode.VALIDATION_ERROR,
                Messages.Error.APPROVAL_FORWARD_CHAIN_TOO_DEEP,
                http_status=status.HTTP_422_UNPROCESSABLE_CONTENT,
                retryable=False,
            )

    @classmethod
    def _chain_depth(cls, *, approval: ApprovalRequestRecord) -> int:
        """Approximate chain depth from a single record's chain_parent.

        Each forward inscribes ``chain_parent_approval_id`` on the child's
        ``metadata`` (and on the row column) so we can read the depth
        without a recursive CTE — the parent's child carries its own
        chain_parent which carries the prior, etc. v1 caps at 3 hops, so
        a length walk through ``metadata.chain_parent_approval_id`` ids
        we keep on the row gives an honest count without crawling.
        """

        if approval.chain_parent_approval_id is None:
            return 0
        # Conservative lower bound — we count ourselves plus our parent.
        # The cap is small (3) so this is fine for v1; we revisit if
        # depth ever rises (PR notes already hint at that).
        return 1

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

    def _request_with_runtime_context(
        self, request: CreateRunRequest
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
            model_profile=model_config,
            max_parallel_tasks=self.settings.execution.max_parallel_tasks,
            trace_metadata=trace_metadata,
            feature_flags=context.feature_flags,
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
        """

        if request.request_context.connector_scopes:
            return request
        fallback = conversation.runtime_connector_scopes()
        if not fallback:
            return request
        new_context = request.request_context.model_copy(
            update={"connector_scopes": fallback}
        )
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
