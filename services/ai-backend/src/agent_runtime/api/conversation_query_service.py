"""Read-only projection over conversations, messages, runs, and events.

Provides the query side of the CQRS-lite split: ``list_models``,
``get_conversation``, ``list_conversations``, ``list_messages``,
``get_conversation_context``, ``get_run``, and ``replay_events``. Never mutates
state; returns typed Pydantic responses for HTTP routes and the SSE adapter.
"""

from __future__ import annotations

from agent_runtime.api.constants import Messages, Values
from agent_runtime.api.model_catalog import ModelCatalog
from agent_runtime.api.model_enablement import ModelEnablementResolver
from agent_runtime.api.ports import EventStorePort, PersistencePort
from agent_runtime.api.usage_service import ConversationContextBuilder
from agent_runtime.execution.contracts import RuntimeErrorCode
from agent_runtime.execution.models import ModelConfigResolver
from agent_runtime.pricing import ModelPricingCatalog
from agent_runtime.settings import RuntimeSettings
from runtime_api.http.errors import RuntimeApiError
from runtime_api.schemas import (
    AgentRunStatus,
    ConversationContextResponse,
    ConversationListResponse,
    ConversationResponse,
    DefaultModelSelection,
    MessageListResponse,
    ModelCatalogResponse,
    RunStatusResponse,
    RuntimeEventReplayResponse,
)
from starlette import status


class ConversationQueryService:
    """Read-only projection that assembles typed responses from persistence and event stores.

    Scope enforcement is enforced on every public method: records outside the
    caller's (org_id, user_id) scope raise a 404 rather than leaking data.
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
        event_store: EventStorePort,
        settings: RuntimeSettings,
        model_resolver: ModelConfigResolver,
    ) -> None:
        self._persistence = persistence
        self._event_store = event_store
        self._settings = settings
        self._model_resolver = model_resolver
        self._pricing_catalog = ModelPricingCatalog.from_litellm()

    async def list_models(self, *, org_id: str | None = None) -> ModelCatalogResponse:
        """Return the model catalog with per-provider credential + enablement flags.

        The catalog is assembled in-process from ``RuntimeSettings`` (the
        ``configured`` flags reflect provider-key presence at startup); each
        item's ``enabled`` flag is then resolved from the org's workspace
        ``enabled_models`` curation (PR-2C) — an explicit selection, or the
        newest-per-provider default when the workspace hasn't curated.
        ``ModelCatalog.build`` is the single source of truth and already
        returns an id-unique tuple with the runtime default present exactly
        once, so no further deduplication happens here.
        """

        unique_models = ModelCatalog.build(self._settings)
        defaults = (
            await self._persistence.get_workspace_defaults(org_id=org_id)
            if org_id is not None
            else None
        )
        # The always-enabled default is the workspace default when set, else the
        # runtime settings default — the model every run falls back to, so it
        # must always be selectable regardless of curation.
        effective_default = (
            defaults.default_model
            if defaults is not None and defaults.default_model is not None
            else DefaultModelSelection(
                provider=self._settings.default_model.provider,
                model_name=self._settings.default_model.model_name,
            )
        )
        enabled = ModelEnablementResolver.apply(
            unique_models,
            enabled_models=defaults.enabled_models if defaults is not None else None,
            default_model=effective_default,
        )
        return ModelCatalogResponse(
            default_model_id=self._settings.default_model.model_name,
            models=enabled,
        )

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
        # desktop-run-identity §D2 — GET /conversations/{id} carries the SAME
        # projection as the list path (active-run overlay + preview/model +
        # ``latest_run_id_any_status``), so a client reopening a conversation
        # resolves its head run from either endpoint with an identical shape.
        projected = await self._with_latest_run(
            conversation.to_response(), org_id=org_id
        )
        return await self._with_list_fields(projected, org_id=org_id)

    async def list_conversations(
        self,
        *,
        org_id: str,
        user_id: str,
        limit: int = Values.DEFAULT_CONVERSATION_LIMIT,
        include_archived: bool = False,
        include_deleted: bool = False,
    ) -> ConversationListResponse:
        """Return scoped conversations newest-first, enriched with each one's active run.

        ``has_more`` is derived from whether the store returned a full page, so
        callers must re-request with a cursor (not implemented yet) when it is True.
        """

        bounded_limit = min(max(1, limit), Values.MAX_MESSAGE_LIMIT)
        records = await self._persistence.list_conversations(
            org_id=org_id,
            user_id=user_id,
            limit=bounded_limit,
            include_archived=include_archived,
            include_deleted=include_deleted,
        )
        responses: list[ConversationResponse] = []
        for record in records:
            projected = await self._with_latest_run(record.to_response(), org_id=org_id)
            projected = await self._with_list_fields(projected, org_id=org_id)
            responses.append(projected)
        return ConversationListResponse(
            conversations=tuple(responses),
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
        """Return ordered message history, gated on a successful conversation scope check."""

        await self._conversation_for_scope(
            org_id=org_id,
            user_id=user_id,
            conversation_id=conversation_id,
        )
        bounded_limit = min(max(1, limit), Values.MAX_MESSAGE_LIMIT)
        records = await self._persistence.list_messages(
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
        """Return a context-window summary for the conversation's most recent run.

        When no run exists yet, returns a default-model placeholder so the UI
        can render context-budget progress even before the first message.
        """

        await self._conversation_for_scope(
            org_id=org_id,
            user_id=user_id,
            conversation_id=conversation_id,
        )
        latest_run = await self._persistence.query_latest_run_usage_for_conversation(
            org_id=org_id,
            user_id=user_id,
            conversation_id=conversation_id,
        )
        if latest_run is None:
            default_model = self._settings.default_model
            return ConversationContextBuilder.build(
                provider=default_model.provider,
                model_name=default_model.model_name,
                latest_run=None,
                per_call_rows=(),
                compression_events=(),
                pricing=None,
            )

        per_call_rows = await self._persistence.query_model_call_usage_for_run(
            org_id=org_id, run_id=latest_run.run_id
        )
        compression_events = await self._persistence.query_compression_events_for_run(
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

    async def get_run(
        self,
        *,
        org_id: str,
        user_id: str,
        run_id: str,
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
        """Return events persisted after ``after_sequence`` for SSE reconnect replay.

        ``latest_sequence_no`` is derived from the fetched batch when possible,
        otherwise from a dedicated store query — keeping the field accurate even
        when the batch is empty.
        """

        run = await self._run_for_scope(org_id=org_id, user_id=user_id, run_id=run_id)
        events = tuple(
            await self._event_store.list_events_after(
                org_id=org_id,
                run_id=run_id,
                after_sequence=after_sequence,
            )
        )
        # Prefer the max from the fetched slice; fall back to the store query
        # only when the batch is empty so we avoid a second round-trip on the hot path.
        latest_sequence_no = max(
            (event.sequence_no for event in events),
            default=await self._event_store.get_latest_sequence(run_id=run_id),
        )
        return RuntimeEventReplayResponse(
            run_id=run_id,
            events=events,
            latest_sequence_no=latest_sequence_no,
            run_status=run.status,
            has_more=False,
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
        """Return the conversation or raise 404 if it falls outside the caller's scope."""
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

    async def _with_latest_run(
        self,
        response: ConversationResponse,
        *,
        org_id: str,
    ) -> ConversationResponse:
        """Attach the active run status to a conversation response, if one exists."""
        active = await self._persistence.get_active_run_for_conversation(
            org_id=org_id,
            conversation_id=response.conversation_id,
        )
        if active is None:
            return response
        return response.with_latest_run(
            # Guard against enum vs. string representation in older store adapters.
            status=active.status.value
            if hasattr(active.status, "value")
            else str(active.status),
            run_id=active.run_id,
        )

    async def _with_list_fields(
        self,
        response: ConversationResponse,
        *,
        org_id: str,
    ) -> ConversationResponse:
        """Attach the Chats-list ``preview`` + ``model`` projections (PRD-H.4).

        ``preview`` is the last visible message's text, trimmed to a short
        snippet; ``model`` is the latest run's model name (any status), so
        even a fully-completed conversation shows the model it last used.
        Both stay ``None`` when the conversation has no messages / runs.
        ``pinned`` needs no overlay — it rides along on the record.
        """

        latest_message = await self._persistence.get_latest_message_for_conversation(
            org_id=org_id,
            conversation_id=response.conversation_id,
        )
        latest_run = await self._persistence.get_latest_run_for_conversation(
            org_id=org_id,
            conversation_id=response.conversation_id,
        )
        preview = (
            self._snippet(latest_message.content_text)
            if latest_message is not None
            else None
        )
        model = latest_run.model_name if latest_run is not None else None
        # desktop-run-identity §D2 — surface the head run's id (any status) from
        # the SAME run row we already fetched for ``model``; previously discarded.
        latest_run_id_any_status = latest_run.run_id if latest_run is not None else None
        return response.with_list_fields(
            preview=preview,
            model=model,
            latest_run_id_any_status=latest_run_id_any_status,
        )

    @staticmethod
    def _snippet(text: str) -> str | None:
        """Collapse whitespace and trim a message body to a one-line preview.

        Returns ``None`` for empty/whitespace-only content so the row
        hides the preview rather than rendering a blank line.
        """

        collapsed = " ".join(text.split())
        if not collapsed:
            return None
        limit = Values.CONVERSATION_PREVIEW_MAX_LENGTH
        if len(collapsed) <= limit:
            return collapsed
        return collapsed[: limit - 1].rstrip() + "…"

    async def _run_for_scope(self, *, org_id: str, user_id: str, run_id: str):
        """Return the run or raise 404 when it is absent or belongs to another user."""
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
        """Return a ``WorkspaceDefaultsService`` bound to this service's deps.

        Lazily imported to avoid a circular dependency at module load time.
        """
        from agent_runtime.api.workspace_defaults_service import (
            WorkspaceDefaultsService,
        )

        return WorkspaceDefaultsService(
            persistence=self._persistence,
            settings=self._settings,
        )
