"""Run lifecycle coordinator — API-side source of truth for run-state transitions.

Owns ``create_run`` and ``cancel_run``. The runtime worker uses persistence ports
directly and never depends on this coordinator, keeping the worker path free of
API-layer concerns.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from starlette import status

from agent_runtime.api.constants import Keys, Messages
from agent_runtime.api.events import RuntimeEventProducer
from agent_runtime.api.ports import PersistencePort, RuntimeQueuePort
from agent_runtime.api.suggestible_connectors_resolver import (
    NullSuggestibleConnectorsResolver,
    SuggestibleConnectorsResolver,
)
from agent_runtime.api.user_policies_resolver import (
    NullUserPoliciesResolver,
    ProviderKeysParser,
    UserPoliciesResolver,
)
from agent_runtime.execution.contracts import (
    AgentRuntimeContext,
    CatalogSuggestionCard,
    RuntimeErrorCode,
    StreamEventSource,
)
from agent_runtime.execution.errors import AgentRuntimeError
from agent_runtime.execution.models import ModelConfigResolver, ModelSelection
from agent_runtime.observability.queue_propagation import QueueTracePropagator
from agent_runtime.settings import RuntimeSettings
from runtime_api.http.errors import RuntimeApiError
from runtime_api.schemas import (
    AgentRunStatus,
    CancelRunRequest,
    CancelRunResponse,
    ConversationRecord,
    CreateRunRequest,
    CreateRunResponse,
    MessageRecord,
    RuntimeApiEventType,
    RuntimeCancelCommand,
    RuntimeRunCommand,
    RunRecord,
)


class RunCoordinator:
    """Service layer that persists, enqueues, and cancels agent runs.

    Resolves workspace/user context (model, policies, connector suggestions)
    concurrently at run-create time to minimise latency. All public methods
    raise ``RuntimeApiError`` on invalid input or missing scope.
    """

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
        queue: RuntimeQueuePort,
        event_producer: RuntimeEventProducer,
        settings: RuntimeSettings,
        model_resolver: ModelConfigResolver,
        user_policies_resolver: UserPoliciesResolver | None = None,
        suggestible_connectors_resolver: SuggestibleConnectorsResolver | None = None,
    ) -> None:
        self._persistence = persistence
        self._queue = queue
        self._event_producer = event_producer
        self._settings = settings
        self._model_resolver = model_resolver
        self._user_policies_resolver: UserPoliciesResolver = (
            user_policies_resolver or NullUserPoliciesResolver()
        )
        self._suggestible_connectors_resolver: SuggestibleConnectorsResolver = (
            suggestible_connectors_resolver or NullSuggestibleConnectorsResolver()
        )

    async def create_run(self, request: CreateRunRequest) -> CreateRunResponse:
        """Create a persisted, queued run and return its stream/events URLs.

        Conversation connector scopes and the workspace default model are applied
        before the runtime context is sealed, so the worker sees a fully resolved
        context without further lookups. Workspace overrides, user policies, and
        connector suggestions are fetched concurrently to keep the create-run
        latency tight.
        """

        conversation_for_scope = await self._conversation_for_scope_when_known(
            request=request
        )
        if conversation_for_scope is not None:
            request = self._apply_conversation_scope_fallback(
                request=request, conversation=conversation_for_scope
            )

        request = await self._apply_workspace_default_model(request=request)

        (
            workspace_overrides,
            user_policies_snapshot,
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
        # BYOK keys must never reach a persisted surface: split them out of
        # the snapshot before the remainder is sealed into the (persisted)
        # ``user_policies_json`` context field.
        provider_keys, user_policies_json = ProviderKeysParser.split(
            user_policies_snapshot
        )
        return await self._persist_and_enqueue(
            request=request,
            conversation_for_scope=conversation_for_scope,
            workspace_overrides=workspace_overrides,
            user_policies_json=user_policies_json,
            provider_keys=provider_keys,
            suggested_connectors=suggested_connectors,
        )

    async def _persist_and_enqueue(
        self,
        *,
        request: CreateRunRequest,
        conversation_for_scope: ConversationRecord | None,
        workspace_overrides: dict[str, object],
        user_policies_json: dict[str, object],
        suggested_connectors: tuple[CatalogSuggestionCard, ...],
        provider_keys: dict[str, str] | None = None,
    ) -> CreateRunResponse:
        """Seal the runtime context, persist the run, and enqueue the worker command.

        Separated from ``create_run`` so tests can patch the individual
        ``_resolve_*`` methods on the service instance and then delegate the
        write + enqueue step here without re-implementing the logic.
        """

        request = self._request_with_runtime_context(
            request,
            workspace_behavior_overrides=workspace_overrides,
            user_policies_json=user_policies_json,
            provider_keys=provider_keys,
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
        ) = await self._persistence.create_run_with_user_message(
            request=request,
            conversation=conversation,
        )
        # ``created=False`` means an idempotent replay; skip re-enqueuing
        # to avoid a duplicate worker pickup for the same run_id.
        if created:
            await self._persistence.write_audit_log(
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
            await self._event_producer.append_api_event(
                run=run,
                source=StreamEventSource.RUNTIME,
                event_type=RuntimeApiEventType.RUN_QUEUED,
                payload={Keys.Payload.MESSAGE: Messages.Event.RUN_QUEUED},
            )
            await self._queue.enqueue_run(
                RuntimeRunCommand(
                    run_id=run.run_id,
                    conversation_id=run.conversation_id,
                    org_id=run.org_id,
                    user_id=run.user_id,
                    trace_id=run.trace_id,
                    runtime_context=run.runtime_context,
                    trace_propagation=QueueTracePropagator.inject(),
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

    async def cancel_run(
        self,
        *,
        org_id: str,
        user_id: str,
        run_id: str,
        request: CancelRunRequest,
    ) -> CancelRunResponse:
        """Record a cancellation request and enqueue the worker cancel command.

        Idempotent: already-terminal or already-cancelling runs return their
        current state without re-enqueuing. Cancellation is best-effort — the
        worker may have already completed by the time the command is consumed.
        """

        run = await self._run_for_scope(org_id=org_id, user_id=user_id, run_id=run_id)
        if request.requested_by_user_id != user_id:
            raise RuntimeApiError(
                RuntimeErrorCode.PERMISSION_DENIED,
                "Cancellation requester does not match run user.",
                http_status=status.HTTP_403_FORBIDDEN,
                retryable=False,
                correlation_id=run.trace_id,
            )
        # Already done — return current state; nothing to enqueue.
        if run.status in self.TERMINAL_RUN_STATUSES:
            return CancelRunResponse(
                run_id=run.run_id,
                status=run.status,
                cancel_requested_at=run.cancelled_at,
                latest_sequence_no=run.latest_sequence_no,
            )
        # Skip status update and event emission when already in CANCELLING
        # to avoid duplicate events on concurrent cancel requests.
        if run.status != AgentRunStatus.CANCELLING:
            run = await self._persistence.update_run_status(
                run_id=run.run_id,
                status=AgentRunStatus.CANCELLING,
            )
            await self._event_producer.append_api_event(
                run=run,
                source=StreamEventSource.RUNTIME,
                event_type=RuntimeApiEventType.RUN_CANCELLING,
                payload={
                    Keys.Payload.MESSAGE: Messages.Event.RUN_CANCELLING,
                    Keys.Payload.REASON: request.reason,
                },
            )
            # Re-fetch to get the updated sequence_no after the status write.
            refreshed = await self._persistence.get_run(
                org_id=org_id, run_id=run.run_id
            )
            run = refreshed or run
            await self._queue.enqueue_cancel(
                RuntimeCancelCommand(
                    run_id=run.run_id,
                    org_id=run.org_id,
                    requested_by_user_id=request.requested_by_user_id,
                    reason=request.reason,
                    trace_propagation=QueueTracePropagator.inject(),
                )
            )
            await self._persistence.write_audit_log(
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

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    async def _conversation_for_scope(
        self,
        *,
        org_id: str,
        user_id: str,
        conversation_id: str,
    ):
        """Return the conversation or raise 404 when outside the caller's scope."""
        conv = await self._persistence.get_conversation(
            org_id=org_id,
            user_id=user_id,
            conversation_id=conversation_id,
        )
        if conv is None:
            raise RuntimeApiError(
                RuntimeErrorCode.CAPABILITY_NOT_FOUND,
                Messages.Error.CONVERSATION_NOT_FOUND,
                http_status=status.HTTP_404_NOT_FOUND,
                retryable=False,
            )
        return conv

    async def _conversation_for_scope_when_known(
        self, *, request: CreateRunRequest
    ) -> ConversationRecord | None:
        """Prefetch the conversation early when org_id and user_id are available.

        Returns ``None`` for anonymous or partially-formed requests; the conversation
        is resolved later in ``_persist_and_enqueue`` in those cases.
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
        """Merge connector scopes and paused-connectors from the conversation into the request.

        When the caller provided explicit connector scopes, those win; the conversation
        may still contribute paused connectors that the caller omitted. When the caller
        provided no scopes at all, the conversation's persisted scope is used as a fallback
        so the user's per-chat customisation is honoured without re-sending it on each run.
        """
        paused = conversation.paused_connectors()
        if request.request_context.connector_scopes:
            # Caller specified scopes — only inject paused if they missed it.
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

    async def _apply_workspace_default_model(
        self, *, request: CreateRunRequest
    ) -> CreateRunRequest:
        """Fill in workspace default model when the request doesn't specify one fully.

        A fully-specified model (both provider and model_name present) always wins;
        the workspace default only applies when either field is missing. Individual
        fields like temperature and reasoning from the request are preserved.
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

    async def _resolve_user_policies(
        self, *, org_id: str, user_id: str
    ) -> dict[str, object]:
        """Fetch the per-(org, user) policy snapshot from the backend."""
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
        """Fetch connector suggestion cards, excluding explicitly paused connectors."""
        return await self._suggestible_connectors_resolver.resolve(
            org_id=org_id,
            user_id=user_id,
            exclude_paused=paused_connectors,
        )

    async def _resolve_workspace_behavior_overrides(
        self, *, org_id: str
    ) -> dict[str, object]:
        """Return workspace behavior overrides as a JSON-serialisable dict.

        ``training_data_opt_out=False`` is stripped so consumers treat an absent
        key identically to an explicit ``False`` — the default is never opt-out.
        """
        record = await self._workspace_defaults().get_record(org_id=org_id)
        if record is None:
            return {}
        blob = record.behavior_overrides.model_dump(mode="json", exclude_none=True)
        # Strip the default (False) to keep the contract minimal; consumers
        # that see no key treat it as the deployment default.
        if blob.get("training_data_opt_out") is False:
            blob.pop("training_data_opt_out", None)
        return blob

    def _request_with_runtime_context(  # noqa: C901
        self,
        request: CreateRunRequest,
        *,
        workspace_behavior_overrides: dict[str, object] | None = None,
        user_policies_json: dict[str, object] | None = None,
        provider_keys: dict[str, str] | None = None,
        suggested_connectors: tuple[CatalogSuggestionCard, ...] = (),
    ) -> CreateRunRequest:
        """Build a sealed ``AgentRuntimeContext`` and attach it to the request.

        Collects trace metadata from all enrichment sources (quotes, attachments,
        branching, regeneration) into a single dict so the worker doesn't need to
        reassemble it from the request.

        ``provider_keys`` (BYOK) rides the in-memory, serialization-excluded
        context field; only its provider slugs feed the model resolver's
        credential gate so a user key satisfies credentials without an env key.
        """
        try:
            model = request.model
            model_config = self._model_resolver.resolve(
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
                    # Top-level on the request, not nested under ``model`` —
                    # depth is a per-turn user choice, not a model attribute.
                    # The resolver folds it into ``model_profile`` via the
                    # single depth → budget mapping point.
                    reasoning_depth=request.reasoning_depth,
                ),
                user_key_providers=frozenset(provider_keys or ()),
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
            max_parallel_tasks=self._settings.execution.max_parallel_tasks,
            trace_metadata=trace_metadata,
            feature_flags=context.feature_flags,
            workspace_behavior_overrides=workspace_behavior_overrides or {},
            user_policies_json=user_policies_json or {},
            provider_keys=provider_keys or {},
        )
        return request.model_copy(update={"runtime_context": runtime_context})

    async def _prior_run_ids_for_chain(
        self,
        *,
        org_id: str,
        conversation_id: str,
        current_run_id: str,
        user_message: MessageRecord,
    ) -> tuple[str, ...]:
        """Walk the parent-message chain and collect ancestor run IDs in chronological order.

        The chain is used by the worker to load prior LangGraph checkpoints for
        context continuity. Walking stops at the first message not found in the
        local 200-record window — deep threads that exceed this limit lose context
        beyond that boundary, which is an accepted trade-off for response latency.
        """
        records = await self._persistence.list_messages(
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
        # Walk collected run IDs oldest-first so the worker can replay in order.
        return tuple(reversed(ordered))

    @classmethod
    def _create_run_response(
        cls,
        *,
        run: RunRecord,
        user_message_id: str,
        prior_run_ids: tuple[str, ...] = (),
    ) -> CreateRunResponse:
        """Assemble the HTTP response from a persisted run record."""
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

    async def _run_for_scope(
        self, *, org_id: str, user_id: str, run_id: str
    ) -> RunRecord:
        """Return the run or raise 404 when absent or owned by a different user."""
        run = await self._persistence.get_run(org_id=org_id, run_id=run_id)
        if run is None or run.user_id != user_id:
            raise RuntimeApiError(
                RuntimeErrorCode.CAPABILITY_NOT_FOUND,
                Messages.Error.RUN_NOT_FOUND,
                http_status=status.HTTP_404_NOT_FOUND,
                retryable=False,
            )
        return run

    def _workspace_defaults(self):
        """Return a ``WorkspaceDefaultsService`` bound to this coordinator's deps.

        Lazily imported to avoid a module-level circular dependency.
        """
        from agent_runtime.api.workspace_defaults_service import (
            WorkspaceDefaultsService,
        )

        return WorkspaceDefaultsService(
            persistence=self._persistence,
            settings=self._settings,
        )
