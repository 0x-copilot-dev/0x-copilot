"""Self-fork service (PR A3 / 8.0.3c).

The owner of a conversation forks from a specific message in their own
thread (the "Retry from here" affordance). Mechanically the operation
mirrors :class:`ConversationForkService` (PR 6.2) — read source row,
slice messages, build a new conversation row, copy messages with
:class:`MessageCopyPlanner`, audit, return — minus everything that's
share-specific (share-token resolution, recipient gate, cross-org
opacity 404, share-snapshot bound, share-forked notification).

Sequencing is deliberately simple: the source-side validation just
requires the caller to own the source conversation in the same tenant.
The agent harness, capabilities middleware, and SSE pipeline are not
touched. Nothing here is share-aware.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

from fastapi import status

from agent_runtime.api.ports import PersistencePort
from agent_runtime.execution.contracts import RuntimeErrorCode
from agent_runtime.persistence.message_copy import MessageCopyPlanner
from runtime_api.http.errors import RuntimeApiError
from runtime_api.schemas import (
    ConversationRecord,
    ForkResponse,
    RUNTIME_FORK_MAX_MESSAGES_DEFAULT,
    SelfForkRequest,
)
from runtime_worker.audit import WorkerAuditEmitter

logger = logging.getLogger(__name__)


class _Errors:
    """Stable error messages surfaced by the self-fork endpoint."""

    SOURCE_NOT_FOUND = "Conversation was not found."
    MESSAGE_NOT_FOUND = (
        "Message could not be found in this conversation. It may have been deleted."
    )
    FORK_TOO_LARGE = (
        "Too many messages above this point to fork. Try forking from a later message."
    )


class _Env:
    """Operator-tunable knobs."""

    MAX_MESSAGES = "RUNTIME_FORK_MAX_MESSAGES"


class SelfForkService:
    """Owner-driven conversation fork from an explicit message."""

    def __init__(
        self,
        *,
        persistence: PersistencePort,
        audit: WorkerAuditEmitter,
        max_messages: int | None = None,
    ) -> None:
        self._persistence = persistence
        self._audit = audit
        self._max_messages = self._resolve_max_messages(max_messages)

    @staticmethod
    def _resolve_max_messages(explicit: int | None) -> int:
        if explicit is not None and explicit > 0:
            return explicit
        raw = os.environ.get(_Env.MAX_MESSAGES, "").strip()
        if not raw:
            return RUNTIME_FORK_MAX_MESSAGES_DEFAULT
        try:
            value = int(raw)
        except ValueError:
            return RUNTIME_FORK_MAX_MESSAGES_DEFAULT
        return value if value > 0 else RUNTIME_FORK_MAX_MESSAGES_DEFAULT

    async def fork(
        self,
        *,
        conversation_id: str,
        actor_org_id: str,
        actor_user_id: str,
        request: SelfForkRequest,
    ) -> ForkResponse:
        # 1. Source row. The owner-scoped read enforces both tenant
        #    isolation (RLS) and ownership in one call. Cross-tenant or
        #    non-owner reads return None — uniformly mapped to 404 so
        #    existence is never leaked across boundaries.
        source = await self._persistence.get_conversation(
            org_id=actor_org_id,
            user_id=actor_user_id,
            conversation_id=conversation_id,
        )
        if source is None or source.deleted_at is not None:
            raise self._not_found(_Errors.SOURCE_NOT_FOUND)

        # 2. Bounded read. ``+1`` lets us detect overflow cleanly without
        #    pre-counting; the cap is applied AFTER slicing at the
        #    requested message_id boundary so a 50k-row chat with a
        #    fork-point near the start is still cheap.
        all_messages = await self._persistence.list_messages(
            org_id=actor_org_id,
            conversation_id=conversation_id,
            limit=self._max_messages + 1,
            include_deleted=False,
        )

        # 3. Slice up to and including ``from_message_id``. The message
        #    must belong to this conversation — anything else is a 404.
        snapshot_messages = self._slice_through(all_messages, request.from_message_id)
        if snapshot_messages is None:
            # Two cases collapse here: the message id genuinely doesn't
            # belong to this conversation (404), or the message lives
            # beyond ``max_messages + 1`` rows from the start of the
            # conversation (the bounded read couldn't see it). The
            # latter is a fork-too-large signal — same 422 the
            # share-fork raises for the same boundary. We disambiguate
            # by counting the bounded read: a full cap+1 read with the
            # message absent means "not visible in the cap window."
            if len(all_messages) > self._max_messages:
                raise RuntimeApiError(
                    RuntimeErrorCode.VALIDATION_ERROR,
                    _Errors.FORK_TOO_LARGE,
                    http_status=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    retryable=False,
                )
            raise self._not_found(_Errors.MESSAGE_NOT_FOUND)
        if len(snapshot_messages) > self._max_messages:
            raise RuntimeApiError(
                RuntimeErrorCode.VALIDATION_ERROR,
                _Errors.FORK_TOO_LARGE,
                http_status=status.HTTP_422_UNPROCESSABLE_ENTITY,
                retryable=False,
            )
        snapshot_at = snapshot_messages[-1].created_at

        # 4. Build the new conversation row + plan the message copies in
        #    memory so the persistence writes can run sequentially.
        now = datetime.now(timezone.utc)
        new_conversation = ConversationRecord(
            org_id=actor_org_id,
            user_id=actor_user_id,
            assistant_id=source.assistant_id,
            title=self._derive_title(source.title, request.title),
            metadata={"forked_from_message_id": request.from_message_id},
            folder=request.folder,
            parent_conversation_id=source.conversation_id,
            forked_from_message_id=request.from_message_id,
            created_at=now,
            updated_at=now,
        )
        copy_plan = MessageCopyPlanner.plan(
            source_messages=snapshot_messages,
            target_conversation_id=new_conversation.conversation_id,
            target_org_id=actor_org_id,
            now=now,
        )

        # 5. Persist + audit. Audit row is the immutable record of the
        #    fork; the plan's orphan_warnings count surfaces in
        #    metadata for SIEM forensics.
        await self._persistence.insert_forked_conversation(new_conversation)
        for message in copy_plan.records:
            await self._persistence.append_message(message)

        await self._audit.emit_conversation_fork(
            org_id=actor_org_id,
            actor_user_id=actor_user_id,
            source_conversation_id=conversation_id,
            target_conversation_id=new_conversation.conversation_id,
            snapshot_at=snapshot_at,
            message_count=len(copy_plan),
            from_message_id=request.from_message_id,
            orphan_warnings=len(copy_plan.orphan_warnings),
        )

        return ForkResponse(
            conversation_id=new_conversation.conversation_id,
            parent_conversation_id=source.conversation_id,
            forked_from_message_id=request.from_message_id,
            fork_message_count=len(copy_plan),
            title=new_conversation.title,
            folder=new_conversation.folder,
            created_at=new_conversation.created_at,
            user_id=actor_user_id,
        )

    @staticmethod
    def _slice_through(messages, from_message_id: str):  # type: ignore[no-untyped-def]
        """Return messages up to and including ``from_message_id``.

        ``messages`` arrives in the conversation's natural order
        (``list_messages`` returns ordered rows). Returns ``None`` if
        the message_id isn't found in the conversation snapshot — the
        caller maps that to a 404 to avoid leaking which sibling chats
        may hold the same id.
        """

        sliced = []
        for message in messages:
            sliced.append(message)
            if message.message_id == from_message_id:
                return tuple(sliced)
        return None

    @staticmethod
    def _not_found(message: str) -> RuntimeApiError:
        return RuntimeApiError(
            RuntimeErrorCode.CAPABILITY_NOT_FOUND,
            message,
            http_status=status.HTTP_404_NOT_FOUND,
            retryable=False,
        )

    @staticmethod
    def _derive_title(source_title: str | None, requested: str | None) -> str | None:
        if requested is not None:
            return requested or None
        if not source_title:
            return None
        # Mirror the share-fork prefix shape so a forked thread looks
        # immediately recognisable next to a share-fork in the sidebar.
        prefix = "Forked from "
        budget = 240 - len(prefix)
        if len(source_title) <= budget:
            return f"{prefix}{source_title}"
        return f"{prefix}{source_title[: budget - 1]}…"


__all__ = ["SelfForkService"]
