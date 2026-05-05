"""Port protocols for runtime API persistence, event replay, and queueing."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Protocol, runtime_checkable

from runtime_api.schemas import (
    AgentRunStatus,
    ApprovalDecisionRecord,
    ApprovalRequestRecord,
    ConversationRecord,
    CreateConversationRequest,
    CreateRunRequest,
    MessageRecord,
    HistoryDeletionResponse,
    RuntimeApprovalResolvedCommand,
    RuntimeCancelCommand,
    RuntimeEventDraft,
    RuntimeEventEnvelope,
    RuntimeRunCommand,
    RunRecord,
)
from agent_runtime.persistence.records import RuntimeWorkerClaim, RuntimeWorkerResult


@runtime_checkable
class PersistencePort(Protocol):
    """Conversation, message, run, approval, and audit persistence boundary."""

    def create_conversation(
        self, request: CreateConversationRequest
    ) -> ConversationRecord:
        """Create or idempotently return a conversation."""

    def get_conversation(
        self,
        *,
        org_id: str,
        user_id: str,
        conversation_id: str,
    ) -> ConversationRecord | None:
        """Return a conversation for the tenant/user scope."""

    def list_conversations(
        self,
        *,
        org_id: str,
        user_id: str,
        limit: int,
        include_archived: bool = False,
    ) -> Sequence[ConversationRecord]:
        """Return conversations for the tenant/user scope, newest first."""

    def list_messages(
        self,
        *,
        org_id: str,
        conversation_id: str,
        limit: int,
        include_deleted: bool = False,
    ) -> Sequence[MessageRecord]:
        """Return ordered conversation messages."""

    def append_message(self, message: MessageRecord) -> MessageRecord:
        """Append a message created outside the initial API run transaction."""

    def update_conversation_connectors(
        self,
        *,
        org_id: str,
        user_id: str,
        conversation_id: str,
        scopes_patch: dict[str, tuple[str, ...] | None],
        now: datetime,
    ) -> ConversationRecord | None:
        """RFC 7396 merge-patch ``enabled_connectors`` for one conversation.

        Returns ``None`` when no row matches the (org, user, conversation)
        scope. The implementation merges ``scopes_patch`` into the stored
        column: keys present in the patch overwrite the stored value
        (including ``None`` to pause); keys absent in the patch are left
        untouched. Caller computes the diff for audit before calling.
        """

    def create_run_with_user_message(
        self,
        *,
        request: CreateRunRequest,
        conversation: ConversationRecord,
    ) -> tuple[RunRecord, MessageRecord, bool]:
        """Create a user message and run, or return an idempotent existing run."""

    def get_run(self, *, org_id: str, run_id: str) -> RunRecord | None:
        """Return a run scoped by organization."""

    def update_run_status(self, *, run_id: str, status: AgentRunStatus) -> RunRecord:
        """Update mutable run status and return the new record."""

    def set_run_latest_sequence(
        self, *, run_id: str, latest_sequence_no: int
    ) -> RunRecord:
        """Persist latest event sequence for run inspection."""

    def record_approval_decision(
        self,
        *,
        record: ApprovalDecisionRecord,
    ) -> ApprovalDecisionRecord:
        """Persist an approval decision."""

    def create_approval_request(
        self,
        *,
        record: ApprovalRequestRecord,
    ) -> ApprovalRequestRecord:
        """Persist a pending approval request."""

    def forward_approval_request(
        self,
        *,
        parent_approval_id: str,
        org_id: str,
        decided_by_user_id: str,
        forwarded_to_user_id: str,
        decision_reason: str | None,
        child: ApprovalRequestRecord,
        now: datetime,
    ) -> tuple[ApprovalRequestRecord, ApprovalRequestRecord]:
        """Atomically transition a pending approval to ``FORWARDED`` and
        insert the child row addressed to the next approver (PR 1.4).

        The update + insert run in one transaction so a failure halfway
        through never leaves a chain orphan. Returns ``(updated_parent,
        inserted_child)``. The caller is responsible for emitting events
        and audit rows after the transaction commits.
        """

    def get_approval_request(
        self,
        *,
        org_id: str,
        approval_id: str,
    ) -> ApprovalRequestRecord | None:
        """Return a pending or resolved approval request."""

    def write_audit_log(self, *, event_type: str, record: object) -> None:
        """Append an audit record for security-relevant actions."""

    def delete_user_history(
        self,
        *,
        org_id: str,
        user_id: str,
        reason: str | None = None,
    ) -> HistoryDeletionResponse:
        """Tombstone user-visible history while retaining audit-safe evidence."""


@runtime_checkable
class EventStorePort(Protocol):
    """Append-only event persistence and replay boundary."""

    def append_event(self, event: RuntimeEventDraft) -> RuntimeEventEnvelope:
        """Append one event with the next per-run sequence number."""

    def list_events_after(
        self,
        *,
        org_id: str,
        run_id: str,
        after_sequence: int,
    ) -> Sequence[RuntimeEventEnvelope]:
        """Return persisted events after a sequence number."""

    def get_latest_sequence(self, *, run_id: str) -> int:
        """Return latest persisted sequence number for a run."""


@runtime_checkable
class RuntimeQueuePort(Protocol):
    """Durable command queue boundary for runtime workers."""

    def enqueue_run(self, command: RuntimeRunCommand) -> None:
        """Enqueue a run command for workers."""

    def enqueue_cancel(self, command: RuntimeCancelCommand) -> None:
        """Enqueue a cancellation command for workers."""

    def enqueue_approval_resolved(
        self, command: RuntimeApprovalResolvedCommand
    ) -> None:
        """Enqueue an approval resolution command for workers."""

    def claim_next(
        self,
        *,
        worker_id: str,
        lock_expires_at: datetime,
    ) -> RuntimeWorkerClaim | None:
        """Claim the next available runtime command for a worker."""

    def mark_complete(self, *, result: RuntimeWorkerResult) -> None:
        """Mark a claimed command complete."""

    def mark_retry(self, *, result: RuntimeWorkerResult) -> None:
        """Release a claimed command for retry after its available time."""

    def mark_dead_letter(self, *, result: RuntimeWorkerResult) -> None:
        """Mark a command permanently failed after retries are exhausted."""
