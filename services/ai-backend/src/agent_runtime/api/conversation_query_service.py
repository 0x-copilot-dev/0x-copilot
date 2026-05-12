"""Read-only projection over conversations, messages, runs, and events (P22 / PR 4).

Owns: ``list_models``, ``get_conversation``, ``list_conversations``,
``list_messages``, ``get_conversation_context``, ``get_run``, ``replay_events``.

Called by HTTP routes and the SSE adapter (``replay_events``). Never mutates.
Returns typed Pydantic responses.
"""

from __future__ import annotations

from agent_runtime.api.constants import Messages, Values
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
    MessageListResponse,
    ModelCatalogItem,
    ModelCatalogResponse,
    RunStatusResponse,
    RuntimeEventReplayResponse,
)
from starlette import status


def _display_model_name(model_name: str) -> str:
    parts = model_name.replace("_", "-").split("-")
    return " ".join(
        part.upper() if part in {"gpt"} else part.capitalize() for part in parts
    )


class ConversationQueryService:
    """Read-only projection across conversation, message, run, and event records."""

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
        self._pricing_catalog = ModelPricingCatalog(persistence)

    def list_models(self) -> ModelCatalogResponse:
        """Return selectable chat models and credential availability."""

        default = self._settings.default_model
        configured = {
            "openai": self._settings.openai.is_configured,
            "anthropic": self._settings.anthropic.is_configured,
            "gemini": self._settings.gemini.is_configured,
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
        """Return scoped conversation metadata newest first."""

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
            responses.append(
                await self._with_latest_run(record.to_response(), org_id=org_id)
            )
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
        """Return ordered conversation history after validating caller scope."""

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
        """Return the per-conversation context-window view (B5)."""

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
        """Return persisted events after a client sequence checkpoint."""

        run = await self._run_for_scope(org_id=org_id, user_id=user_id, run_id=run_id)
        events = tuple(
            await self._event_store.list_events_after(
                org_id=org_id,
                run_id=run_id,
                after_sequence=after_sequence,
            )
        )
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
        active = await self._persistence.get_active_run_for_conversation(
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

    async def _run_for_scope(self, *, org_id: str, user_id: str, run_id: str):
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
        from agent_runtime.api.workspace_defaults_service import (
            WorkspaceDefaultsService,
        )

        return WorkspaceDefaultsService(
            persistence=self._persistence,
            settings=self._settings,
        )
