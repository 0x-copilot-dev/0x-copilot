"""Conversation lifecycle coordinator (P22 / PR 1).

Owns conversation write operations: ``create_conversation``,
``update_conversation``, ``update_conversation_connectors``,
``delete_conversation``, ``restore_conversation``, ``delete_user_history``.

Read paths live on :class:`ConversationQueryService` per the CQRS-lite split
in PRD §3.

PR 1 of the P22 split (see ``docs/refactor/19-runtime-api-service-split.md``)
ships this as a thin forwarder onto :class:`RuntimeApiService`. PR 4 will move
the method bodies here and reduce the legacy class to a 1-line delegator. PR 5
deletes the legacy class.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from runtime_api.schemas import (
    ConversationConnectorScopesResponse,
    ConversationResponse,
    CreateConversationRequest,
    HistoryDeletionResponse,
    UpdateConversationConnectorsRequest,
    UpdateConversationRequest,
)

if TYPE_CHECKING:
    from agent_runtime.api.service import RuntimeApiService


class ConversationCoordinator:
    """Coordinate conversation lifecycle write commands.

    Public surface tracked by PRD §3.3. Methods forward to the legacy
    :class:`RuntimeApiService` during PR 1; implementation moves here in PR 4.
    """

    def __init__(self, *, legacy: "RuntimeApiService") -> None:
        self._legacy = legacy

    async def create_conversation(
        self, request: CreateConversationRequest
    ) -> ConversationResponse:
        return await self._legacy.create_conversation(request)

    async def update_conversation(
        self,
        *,
        org_id: str,
        user_id: str,
        conversation_id: str,
        request: UpdateConversationRequest,
        allow_admin_override: bool = False,
    ) -> ConversationResponse:
        return await self._legacy.update_conversation(
            org_id=org_id,
            user_id=user_id,
            conversation_id=conversation_id,
            request=request,
            allow_admin_override=allow_admin_override,
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
        return await self._legacy.update_conversation_connectors(
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
        await self._legacy.delete_conversation(
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
        return await self._legacy.restore_conversation(
            org_id=org_id,
            user_id=user_id,
            conversation_id=conversation_id,
            allow_admin_override=allow_admin_override,
        )

    async def delete_user_history(
        self,
        *,
        org_id: str,
        user_id: str,
        reason: str | None = None,
    ) -> HistoryDeletionResponse:
        return await self._legacy.delete_user_history(
            org_id=org_id,
            user_id=user_id,
            reason=reason,
        )
