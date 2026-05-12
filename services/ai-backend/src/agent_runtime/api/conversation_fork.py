"""Transactional share-token-to-recipient-conversation fork service."""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone

from fastapi import status

from agent_runtime.api.ports import PersistencePort
from agent_runtime.api.notifications import NotificationDispatcher
from agent_runtime.execution.contracts import RuntimeErrorCode
from agent_runtime.persistence.message_copy import MessageCopyPlanner
from runtime_api.http.errors import RuntimeApiError
from runtime_api.schemas import (
    ConversationRecord,
    ForkRequest,
    ForkResponse,
    RUNTIME_FORK_MAX_MESSAGES_DEFAULT,
    ShareSnapshot,
    ShareSnapshotPort,
)
from runtime_worker.audit import WorkerAuditEmitter

logger = logging.getLogger(__name__)


class _Errors:
    """Stable error messages surfaced by the fork endpoint."""

    SHARE_NOT_FOUND = "Share was not found, has been revoked, or has expired."
    SHARE_NOT_FOR_RECIPIENT = "This share isn't available to your account."
    FORK_TOO_LARGE = (
        "This chat is too long to open in your own chat. "
        "Continue from the source instead."
    )


class _Env:
    """Operator-tunable knobs."""

    MAX_MESSAGES = "RUNTIME_FORK_MAX_MESSAGES"


class ConversationForkService:
    """Atomic share-token → recipient-owned conversation fork."""

    _ENABLED_VIEW_ACCESSES = frozenset({"workspace", "specific"})

    def __init__(
        self,
        *,
        persistence: PersistencePort,
        share_snapshots: ShareSnapshotPort,
        audit: WorkerAuditEmitter,
        notifications: NotificationDispatcher,
        max_messages: int | None = None,
    ) -> None:
        self._persistence = persistence
        self._share_snapshots = share_snapshots
        self._audit = audit
        self._notifications = notifications
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
        share_token: str,
        recipient_org_id: str,
        recipient_user_id: str,
        request: ForkRequest,
    ) -> ForkResponse:
        share = await self._share_snapshots.resolve_by_token(share_token)
        if share is None:
            raise self._not_found()

        # Cross-org refusal is the same opacity shape used everywhere
        # else in the runtime API: 404, never 403, so existence is not
        # leaked across tenant boundaries.
        if share.org_id != recipient_org_id:
            raise self._not_found()

        # Recipient gate. Workspace-scoped shares accept any same-org
        # user; specific-people shares require an explicit allow-list
        # entry. Membership of the org is enforced by the cross-org
        # check above + the persistence layer's RLS.
        if share.view_access not in self._ENABLED_VIEW_ACCESSES:
            raise self._not_found()
        if share.view_access == "specific":
            if recipient_user_id not in share.recipient_user_ids:
                raise RuntimeApiError(
                    RuntimeErrorCode.PERMISSION_DENIED,
                    _Errors.SHARE_NOT_FOR_RECIPIENT,
                    http_status=status.HTTP_403_FORBIDDEN,
                    retryable=False,
                )

        # Read the source conversation row through the org-scoped
        # admin-override path so the fork works even if the recipient
        # isn't the source's owner. RLS still gates the row to the
        # share's org_id (which we already verified matches the caller).
        source = await self._persistence.get_conversation_for_org(
            org_id=share.org_id,
            conversation_id=share.conversation_id,
        )
        if source is None or source.deleted_at is not None:
            raise self._not_found()

        # Snapshot read — bounded by the share's snapshot_at and the
        # operator-configured cap. ``+1`` lets us detect overflow
        # cleanly without pre-counting.
        all_messages = await self._persistence.list_messages(
            org_id=share.org_id,
            conversation_id=share.conversation_id,
            limit=self._max_messages + 1,
            include_deleted=False,
        )
        snapshot_messages = tuple(
            message
            for message in all_messages
            if message.created_at <= share.snapshot_at
        )
        if len(snapshot_messages) > self._max_messages:
            raise RuntimeApiError(
                RuntimeErrorCode.VALIDATION_ERROR,
                _Errors.FORK_TOO_LARGE,
                http_status=status.HTTP_422_UNPROCESSABLE_ENTITY,
                retryable=False,
            )

        # Build the new conversation row + the message copies (all in
        # memory) so the persistence writes can run sequentially without
        # any compute-bound work between them.
        now = datetime.now(timezone.utc)
        new_conversation = ConversationRecord(
            org_id=share.org_id,
            user_id=recipient_user_id,
            assistant_id=source.assistant_id,
            title=self._derive_title(source.title, request.title),
            metadata={"forked_from_share_id": share.share_id},
            folder=request.folder,
            parent_conversation_id=source.conversation_id,
            forked_from_share_id=share.share_id,
            created_at=now,
            updated_at=now,
        )
        copy_plan = MessageCopyPlanner.plan(
            source_messages=snapshot_messages,
            target_conversation_id=new_conversation.conversation_id,
            target_org_id=share.org_id,
            now=now,
        )

        # Persist. Each adapter wraps its writes in a transaction (the
        # postgres adapter does so via ``_tenant_connection`` +
        # ``conn.transaction()``); the in-memory adapter is naturally
        # atomic because it runs in a single coroutine.
        await self._persistence.insert_forked_conversation(new_conversation)
        for message in copy_plan.records:
            await self._persistence.append_message(message)

        await self._audit.emit_conversation_fork(
            org_id=share.org_id,
            actor_user_id=recipient_user_id,
            source_conversation_id=share.conversation_id,
            target_conversation_id=new_conversation.conversation_id,
            share_id=share.share_id,
            snapshot_at=share.snapshot_at,
            message_count=len(copy_plan),
            orphan_warnings=len(copy_plan.orphan_warnings),
        )

        # Best-effort notification fan-out off the request thread. A
        # failure here must not roll back the fork; the audit row is
        # the immutable record of what happened.
        asyncio.create_task(
            self._safe_notify(
                share, recipient_user_id, new_conversation.conversation_id
            )
        )

        return ForkResponse(
            conversation_id=new_conversation.conversation_id,
            parent_conversation_id=source.conversation_id,
            forked_from_share_id=share.share_id,
            fork_message_count=len(copy_plan),
            title=new_conversation.title,
            folder=new_conversation.folder,
            created_at=new_conversation.created_at,
            user_id=recipient_user_id,
        )

    async def _safe_notify(
        self,
        share: ShareSnapshot,
        forked_by_user_id: str,
        new_conversation_id: str,
    ) -> None:
        try:
            await self._notifications.notify_share_forked(
                share=share,
                forked_by_user_id=forked_by_user_id,
                new_conversation_id=new_conversation_id,
            )
        except Exception:  # pragma: no cover - best-effort
            logger.warning(
                "share.notify.forked.failed",
                extra={
                    "metadata": {
                        "share_id": share.share_id,
                        "new_conversation_id": new_conversation_id,
                    }
                },
                exc_info=True,
            )

    @staticmethod
    def _not_found() -> RuntimeApiError:
        return RuntimeApiError(
            RuntimeErrorCode.CAPABILITY_NOT_FOUND,
            _Errors.SHARE_NOT_FOUND,
            http_status=status.HTTP_404_NOT_FOUND,
            retryable=False,
        )

    @staticmethod
    def _derive_title(source_title: str | None, requested: str | None) -> str | None:
        if requested is not None:
            return requested or None
        if not source_title:
            return None
        prefix = "Forked from "
        # Truncation budget mirrors the conversation schema's ``TITLE_MAX_LENGTH``; avoid
        # importing the constant directly to keep cross-module coupling loose.
        budget = 240 - len(prefix)
        if len(source_title) <= budget:
            return f"{prefix}{source_title}"
        return f"{prefix}{source_title[: budget - 1]}…"


__all__ = ["ConversationForkService"]
