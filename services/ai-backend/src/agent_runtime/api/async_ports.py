"""Async port protocols for runtime API persistence, event replay, and queueing.

These mirror the sync ports in `agent_runtime.api.ports` exactly, with every
method declared `async def`. They exist alongside the sync ports during the
incremental migration to a fully-async I/O chain (see plan
`hazy-kindling-minsky`). Once all callers are async and the sync postgres
adapter is retired, the sync ports go away and these are renamed to the
unprefixed names.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Protocol, runtime_checkable

from agent_runtime.persistence.records import RuntimeWorkerClaim, RuntimeWorkerResult
from runtime_api.schemas import (
    AgentRunStatus,
    ApprovalDecisionRecord,
    ApprovalRequestRecord,
    ConversationRecord,
    CreateConversationRequest,
    CreateRunRequest,
    HistoryDeletionResponse,
    MessageRecord,
    RuntimeApprovalResolvedCommand,
    RuntimeCancelCommand,
    RuntimeEventDraft,
    RuntimeEventEnvelope,
    RuntimeRunCommand,
    RunRecord,
)


@runtime_checkable
class AsyncPersistencePort(Protocol):
    """Conversation, message, run, approval, and audit persistence boundary."""

    async def create_conversation(
        self, request: CreateConversationRequest
    ) -> ConversationRecord:
        """Create or idempotently return a conversation."""

    async def get_conversation(
        self,
        *,
        org_id: str,
        user_id: str,
        conversation_id: str,
    ) -> ConversationRecord | None:
        """Return a conversation for the tenant/user scope."""

    async def list_conversations(
        self,
        *,
        org_id: str,
        user_id: str,
        limit: int,
        include_archived: bool = False,
    ) -> Sequence[ConversationRecord]:
        """Return conversations for the tenant/user scope, newest first."""

    async def list_messages(
        self,
        *,
        org_id: str,
        conversation_id: str,
        limit: int,
        include_deleted: bool = False,
    ) -> Sequence[MessageRecord]:
        """Return ordered conversation messages."""

    async def append_message(self, message: MessageRecord) -> MessageRecord:
        """Append a message created outside the initial API run transaction."""

    async def create_run_with_user_message(
        self,
        *,
        request: CreateRunRequest,
        conversation: ConversationRecord,
    ) -> tuple[RunRecord, MessageRecord, bool]:
        """Create a user message and run, or return an idempotent existing run."""

    async def get_run(self, *, org_id: str, run_id: str) -> RunRecord | None:
        """Return a run scoped by organization."""

    async def update_run_status(
        self, *, run_id: str, status: AgentRunStatus
    ) -> RunRecord:
        """Update mutable run status and return the new record."""

    async def set_run_latest_sequence(
        self, *, run_id: str, latest_sequence_no: int
    ) -> RunRecord:
        """Persist latest event sequence for run inspection.

        Implementations MUST be monotonic: a write with a lower
        ``latest_sequence_no`` than the currently persisted value is a no-op.
        Returns the current record either way.
        """

    async def record_approval_decision(
        self,
        *,
        record: ApprovalDecisionRecord,
    ) -> ApprovalDecisionRecord:
        """Persist an approval decision."""

    async def create_approval_request(
        self,
        *,
        record: ApprovalRequestRecord,
    ) -> ApprovalRequestRecord:
        """Persist a pending approval request, idempotent on ``approval_id``."""

    async def get_approval_request(
        self,
        *,
        org_id: str,
        approval_id: str,
    ) -> ApprovalRequestRecord | None:
        """Return a pending or resolved approval request."""

    async def write_audit_log(self, *, event_type: str, record: object) -> None:
        """Append an audit record for security-relevant actions."""

    async def delete_user_history(
        self,
        *,
        org_id: str,
        user_id: str,
        reason: str | None = None,
    ) -> HistoryDeletionResponse:
        """Tombstone user-visible history while retaining audit-safe evidence."""


@runtime_checkable
class AsyncEventStorePort(Protocol):
    """Append-only event persistence and replay boundary."""

    async def append_event(self, event: RuntimeEventDraft) -> RuntimeEventEnvelope:
        """Append one event with the next per-run sequence number.

        Implementations MUST serialize concurrent appends per ``run_id`` so the
        returned ``sequence_no`` is monotonically increasing without gaps.
        """

    async def list_events_after(
        self,
        *,
        org_id: str,
        run_id: str,
        after_sequence: int,
    ) -> Sequence[RuntimeEventEnvelope]:
        """Return persisted events after a sequence number."""

    async def get_latest_sequence(self, *, run_id: str) -> int:
        """Return latest persisted sequence number for a run."""


@runtime_checkable
class AsyncRuntimeQueuePort(Protocol):
    """Durable command queue boundary for runtime workers."""

    async def enqueue_run(self, command: RuntimeRunCommand) -> None:
        """Enqueue a run command for workers."""

    async def enqueue_cancel(self, command: RuntimeCancelCommand) -> None:
        """Enqueue a cancellation command for workers."""

    async def enqueue_approval_resolved(
        self, command: RuntimeApprovalResolvedCommand
    ) -> None:
        """Enqueue an approval resolution command for workers."""

    async def claim_next(
        self,
        *,
        worker_id: str,
        lock_expires_at: datetime,
    ) -> RuntimeWorkerClaim | None:
        """Claim the next available runtime command for a worker."""

    async def mark_complete(self, *, result: RuntimeWorkerResult) -> None:
        """Mark a claimed command complete."""

    async def mark_retry(self, *, result: RuntimeWorkerResult) -> None:
        """Release a claimed command for retry after its available time."""

    async def mark_dead_letter(self, *, result: RuntimeWorkerResult) -> None:
        """Mark a command permanently failed after retries are exhausted."""
