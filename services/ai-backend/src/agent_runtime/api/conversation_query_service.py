"""Read-only projection over conversations, messages, runs, and events (P22 / PR 1).

Owns: ``list_models``, ``get_conversation``, ``list_conversations``,
``list_messages``, ``get_conversation_context``, ``get_run``, ``replay_events``.

Called by HTTP routes and the SSE adapter (``replay_events``). Never mutates.
Returns typed Pydantic responses.

PR 1 of the P22 split (see ``docs/refactor/19-runtime-api-service-split.md``)
ships this as a thin forwarder onto :class:`RuntimeApiService`. PR 4 will move
the method bodies here and reduce the legacy class to a 1-line delegator. PR 5
deletes the legacy class.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from agent_runtime.api.constants import Values
from runtime_api.schemas import (
    AgentRunStatus,
    ConversationContextResponse,
    ConversationListResponse,
    ConversationResponse,
    MessageListResponse,
    ModelCatalogResponse,
    RunStatusResponse,
    RuntimeEventReplayResponse,
)

if TYPE_CHECKING:
    from agent_runtime.api.service import RuntimeApiService


class ConversationQueryService:
    """Read-only projection across conversation, message, run, and event records.

    Public surface tracked by PRD §3.3. Methods forward to the legacy
    :class:`RuntimeApiService` during PR 1; implementation moves here in PR 4.
    """

    TERMINAL_RUN_STATUSES = frozenset(
        {
            AgentRunStatus.CANCELLED,
            AgentRunStatus.COMPLETED,
            AgentRunStatus.FAILED,
            AgentRunStatus.TIMED_OUT,
        }
    )

    def __init__(self, *, legacy: "RuntimeApiService") -> None:
        self._legacy = legacy

    def list_models(self) -> ModelCatalogResponse:
        return self._legacy.list_models()

    async def get_conversation(
        self,
        *,
        org_id: str,
        user_id: str,
        conversation_id: str,
    ) -> ConversationResponse:
        return await self._legacy.get_conversation(
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
        return await self._legacy.list_conversations(
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
        return await self._legacy.list_messages(
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
        return await self._legacy.get_conversation_context(
            org_id=org_id,
            user_id=user_id,
            conversation_id=conversation_id,
        )

    async def get_run(
        self,
        *,
        org_id: str,
        user_id: str,
        run_id: str,
    ) -> RunStatusResponse:
        return await self._legacy.get_run(
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
        return await self._legacy.replay_events(
            org_id=org_id,
            user_id=user_id,
            run_id=run_id,
            after_sequence=after_sequence,
        )
