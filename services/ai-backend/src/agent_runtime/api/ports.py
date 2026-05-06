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
    WorkspaceDefaultsRecord,
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

    def get_conversation_for_org(
        self,
        *,
        org_id: str,
        conversation_id: str,
    ) -> ConversationRecord | None:
        """Return a conversation for the tenant scope, ignoring user ownership.

        Used by admin-override paths (PR 1.2.1) where the caller holds an
        admin scope and acts on a member's data. Authorization layering
        is the caller's responsibility — this port only enforces tenant
        isolation. Returns ``None`` for cross-tenant access.
        """

    def list_conversations(
        self,
        *,
        org_id: str,
        user_id: str,
        limit: int,
        include_archived: bool = False,
        include_deleted: bool = False,
    ) -> Sequence[ConversationRecord]:
        """Return conversations for the tenant/user scope, newest first.

        ``include_deleted`` (PR 1.6) filters by ``deleted_at IS NULL``
        when False (the default sidebar query). Setting it to True
        returns soft-deleted rows still inside the retention window
        (the C8 sweeper reaps them on TTL).
        """

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

    def insert_forked_conversation(
        self, conversation: ConversationRecord
    ) -> ConversationRecord:
        """Insert a fork-authored conversation row verbatim (PR 6.2).

        Bypasses the idempotency check the standard ``create_conversation``
        path runs and writes every column the caller has populated —
        including ``parent_conversation_id``, ``forked_from_share_id``,
        ``folder``, ``enabled_connectors``, and ``deleted_at``.
        """

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

    def get_active_run_for_conversation(
        self,
        *,
        org_id: str,
        conversation_id: str,
    ) -> RunRecord | None:
        """Return the most recent non-terminal run for one conversation.

        Used by the soft-delete path (PR 1.6) so deleting a chat with a
        running agent cancels the run via the existing cancel pipeline
        (no new event family). Implementations filter rows whose
        ``status`` is one of ``QUEUED / RUNNING / WAITING_FOR_APPROVAL
        / CANCELLING``; returns ``None`` when nothing is in flight.
        """

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

    def list_assigned_approvals(
        self,
        *,
        org_id: str,
        requested_by_user_id: str,
        status: str,
        limit: int,
        cursor: tuple[datetime, str] | None,
    ) -> Sequence[ApprovalRequestRecord]:
        """Return approvals addressed to ``requested_by_user_id`` (PR 1.4.1).

        Used by the recipient inbox endpoint
        ``GET /v1/agent/approvals?assigned_to_me=true``. Filters to a
        single status (typically ``"pending"``); cursor is ``(created_at,
        approval_id)`` for stable keyset pagination across replays.
        Implementations honor RLS — the ``org_id`` filter narrows further
        within the trusted tenant scope set by the caller.
        """

    def list_pending_expired_approvals(
        self,
        *,
        now: datetime,
        limit: int,
    ) -> Sequence[ApprovalRequestRecord]:
        """Return pending approvals whose ``expires_at`` is in the past.

        Used by the expiry sweeper (PR 1.4.1 Phase B). Implementations
        SHOULD use ``FOR UPDATE SKIP LOCKED`` semantics so multiple
        sweeper replicas process disjoint batches; the in-memory
        adapter approximates with a simple atomic snapshot.
        """

    def list_pending_approvals_for_membership_audit(
        self,
        *,
        limit: int,
    ) -> Sequence[ApprovalRequestRecord]:
        """Return pending approvals for the membership-cascade pass.

        The sweeper calls this after the time-expiry pass to verify each
        recipient is still an active workspace member. The set is
        bounded; orgs with large pending backlogs naturally cap at
        ``limit`` per tick.
        """

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

    # ----- PR 1.6: workspace defaults + conversation lifecycle ----- #

    def get_workspace_defaults(
        self,
        *,
        org_id: str,
    ) -> WorkspaceDefaultsRecord | None:
        """Return the persisted workspace defaults row, or ``None`` when absent.

        ``retention_days`` on the returned record is **not** read from
        ``workspace_defaults`` — that's the service's job (it composes
        from ``retention_policies``). The adapter only fills in the
        columns it owns: default_model, default_connectors, updated_*.
        """

    def upsert_workspace_defaults(
        self,
        *,
        record: WorkspaceDefaultsRecord,
    ) -> WorkspaceDefaultsRecord:
        """Insert-or-update the workspace defaults row for ``record.org_id``.

        Single-row upsert (org_id PK). The service is responsible for
        any audit row + retention upserts that go alongside.
        """

    def update_conversation(
        self,
        *,
        org_id: str,
        user_id: str,
        conversation_id: str,
        title: str | None,
        title_changed: bool,
        folder: str | None,
        folder_changed: bool,
        archived: bool | None,
        archived_changed: bool,
        now: datetime,
    ) -> ConversationRecord | None:
        """Apply a lifecycle PATCH to one conversation row.

        ``*_changed`` flags signal "the caller wants this column
        rewritten" — distinguishing "field omitted" (leave alone) from
        "field set to null" (clear). Returns the post-update row, or
        ``None`` when no row matches the (org, user, conversation)
        scope. Caller computes the audit diff from before/after.
        """

    def soft_delete_conversation(
        self,
        *,
        org_id: str,
        user_id: str,
        conversation_id: str,
        now: datetime,
    ) -> ConversationRecord | None:
        """Stamp ``deleted_at`` (idempotent: no-op when already deleted)."""

    def restore_conversation(
        self,
        *,
        org_id: str,
        user_id: str,
        conversation_id: str,
        now: datetime,
    ) -> ConversationRecord | None:
        """Clear ``deleted_at``. Returns ``None`` when the row was already
        reaped by the retention sweeper (vs. simply not deleted).
        """


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
