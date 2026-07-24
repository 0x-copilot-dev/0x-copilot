"""Deterministic in-memory runtime API ports for local tests and development."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from typing import Any

from starlette import status

from agent_runtime.api.constants import Messages
from agent_runtime.execution.contracts import RuntimeErrorCode
from agent_runtime.persistence.constants import Values as PersistenceValues
from agent_runtime.persistence.ports import RuntimeEventIdempotencyConflict
from copilot_audit_chain import AuditChainSigner
from agent_runtime.persistence.records import (
    ApprovalBatchItemRecord,
    ApprovalBatchRecord,
    ApprovalBatchSpec,
    ApprovalBatchStatus,
    BatchItemDecision,
    BatchOutcomeStatus,
    BatchTransitionOutcome,
    BudgetEnforcement,
    BudgetRecord,
    BudgetReservationRecord,
    BudgetStateRecord,
    BudgetStatus,
    BudgetWithState,
    ChargeOutcome,
    CompressionEventRecord,
    ModelPricingRecord,
    OutboxStatus,
    RuntimeModelCallUsageRecord,
    RuntimeRunUsageRecord,
    RuntimeWorkerClaim,
    RuntimeWorkerResult,
    ToolBudgetEnforcement,
    ToolBudgetRecord,
    ToolInvocationRecord,
    UsageConversationAggregateRecord,
    UsageDailyConnectorRow,
    UsageDailyOrgRow,
    UsageDailyPurposeRow,
    UsageDailySubagentRow,
    UsageDailyUserRow,
)
from runtime_adapters.base import (
    RuntimeAdapterHelpers,
    StatusTransition,
    _Fields,
)
from runtime_api.http.errors import RuntimeApiError
from runtime_api.schemas import (
    ACTIVE_RUN_STATUSES,
    AgentRunStatus,
    ApprovalDecision,
    ApprovalDecisionRecord,
    ApprovalRequestRecord,
    ConversationBucket,
    ConversationStatus,
    ConversationRecord,
    CreateConversationRequest,
    CreateRunRequest,
    HistoryDeletionResponse,
    matches_conversation_bucket,
    MessageRecord,
    MessageRole,
    MessageStatus,
    RuntimeApprovalResolvedCommand,
    RuntimeCancelCommand,
    RuntimeEventDraft,
    RuntimeEventEnvelope,
    RuntimeEventPresentationProjector,
    RuntimeRunCommand,
    RuntimeStageCommitCommand,
    RunHistoryEntry,
    RunRecord,
    WorkspaceDefaultsRecord,
)


class InMemoryRuntimeApiStore:
    """In-memory implementation of persistence, event store, and queue ports."""

    async def open(self) -> None:
        """Lifecycle parity with the Postgres adapter — no pool to open."""

    async def close(self) -> None:
        """Lifecycle parity with the Postgres adapter — no pool to close."""

    async def migrate(self) -> None:
        """Lifecycle parity with the Postgres adapter — no schema to migrate."""

    def __init__(self) -> None:
        self.conversations: dict[str, ConversationRecord] = {}
        self.messages: dict[str, MessageRecord] = {}
        self.runs: dict[str, RunRecord] = {}
        self.approval_requests: dict[str, ApprovalRequestRecord] = {}
        self.approval_decisions: dict[str, ApprovalDecisionRecord] = {}
        # ApprovalBatch storage (PR #43). Keyed by batch_id; items are keyed
        # by item_id but include batch_id so the per-batch view is one filter.
        # Each batch has its own ``asyncio.Lock`` so the atomic
        # read-modify-write inside ``record_item_decision_and_maybe_lock_batch``
        # cannot interleave with concurrent decisions on the same batch.
        self.approval_batches: dict[str, ApprovalBatchRecord] = {}
        self.approval_batch_items: dict[str, ApprovalBatchItemRecord] = {}
        self._approval_batch_locks: dict[str, asyncio.Lock] = {}
        self.events_by_run: dict[str, list[RuntimeEventEnvelope]] = {}
        self.run_commands: list[RuntimeRunCommand] = []
        self.cancel_commands: list[RuntimeCancelCommand] = []
        self.approval_commands: list[RuntimeApprovalResolvedCommand] = []
        self.stage_commit_commands: list[RuntimeStageCommitCommand] = []
        self._queue_order: list[str] = []
        self._queue_payloads: dict[str, dict[str, object]] = {}
        self._queue_statuses: dict[str, OutboxStatus] = {}
        self._queue_attempts: dict[str, int] = {}
        self._queue_available_at: dict[str, datetime] = {}
        self._queue_claims: dict[str, RuntimeWorkerClaim] = {}
        self.audit_log: list[tuple[str, dict[str, object]]] = []
        self._audit_chain_signer = AuditChainSigner.from_env(
            environment_env_var="RUNTIME_ENVIRONMENT"
        )
        self._audit_chain_heads_by_org: dict[str, bytes] = {}
        self._audit_chain_counts_by_org: dict[str, int] = {}
        self._conversation_idempotency: dict[tuple[str, str, str], str] = {}
        self._run_idempotency: dict[tuple[str, str, str], str] = {}
        self._run_idempotency_fingerprint: dict[
            tuple[str, str, str], tuple[str, str]
        ] = {}
        # Usage tracking — tests assert against these dicts directly.
        self.run_usage: dict[str, RuntimeRunUsageRecord] = {}
        self.model_call_usage: list[RuntimeModelCallUsageRecord] = []
        self.pricing_rows: list[ModelPricingRecord] = []
        self.user_daily_usage: dict[
            tuple[str, str, str, str, str], UsageDailyUserRow
        ] = {}
        self.org_daily_usage: dict[tuple[str, str, str, str], UsageDailyOrgRow] = {}
        # Keyed by (org_id, day, connector_slug, model_name) so a connector
        # can split costs across multiple models within the same day.
        self.connector_daily_usage: dict[
            tuple[str, str, str, str], UsageDailyConnectorRow
        ] = {}
        # Org-scoped subagent + purpose daily rollups.
        self.subagent_daily_usage: dict[
            tuple[str, str, str, str, str], UsageDailySubagentRow
        ] = {}
        self.purpose_daily_usage: dict[
            tuple[str, str, str, str, str], UsageDailyPurposeRow
        ] = {}
        # Minimal test seed for the connector attribution lookup.
        # Production writes the real table via runtime_events projection;
        # tests append tuples directly so the lookup can find them.
        # Each entry: (org_id, run_id, connector_slug, completed_at).
        self.tool_invocation_completions: list[tuple[str, str, str, datetime]] = []
        # PRD-08 D1b — the per-run tool-invocation ledger (Activity meta counters),
        # keyed by ``invocation_id`` so start→settle upserts collapse to one row.
        self.tool_invocations: dict[str, ToolInvocationRecord] = {}
        # Compression events (read-only; no writer wired in-memory yet).
        self.compression_events: list[CompressionEventRecord] = []
        self.budgets: dict[str, BudgetRecord] = {}
        # Keyed by (budget_id, period_start_isoformat) so the same budget
        # can have one state row per period without overwriting prior periods.
        self.budget_states: dict[tuple[str, str], BudgetStateRecord] = {}
        self.budget_reservations: dict[str, BudgetReservationRecord] = {}
        # Retention policies keyed by org_id; per-(scope, resource_id, kind)
        # uniqueness is enforced at upsert time.
        self.retention_policies: dict[str, tuple] = {}
        # Ordered list so tests can assert on insertion order.
        self.deletion_evidence: list = []
        # Workspace defaults keyed by org_id; one row per org.
        self.workspace_defaults: dict[str, WorkspaceDefaultsRecord] = {}
        # Per-tool call-count + input-token budgets, mirroring the
        # ``runtime_tool_budgets`` table. The seed row matches the migration's
        # global default so in-memory and Postgres deploys behave identically.
        # Tests can override by adding rows for a specific (org_id, tool_name).
        self.tool_budgets: dict[str, ToolBudgetRecord] = {
            "seed_default": ToolBudgetRecord(
                id="seed_default",
                org_id=None,
                tool_name="*",
                max_calls_per_run=6,
                enforcement=ToolBudgetEnforcement.HARD,
            ),
        }

    async def create_conversation(
        self, request: CreateConversationRequest
    ) -> ConversationRecord:
        """Create or idempotently return a scoped conversation."""

        if request.idempotency_key is not None:
            key = (request.org_id, request.user_id, request.idempotency_key)
            existing_id = self._conversation_idempotency.get(key)
            if existing_id is not None:
                return self.conversations[existing_id]

        conversation = ConversationRecord(
            org_id=request.org_id,
            user_id=request.user_id,
            assistant_id=request.assistant_id,
            title=request.title,
            metadata=request.metadata,
            idempotency_key=request.idempotency_key,
            project_id=request.project_id,
        )
        self.conversations[conversation.conversation_id] = conversation
        if request.idempotency_key is not None:
            self._conversation_idempotency[
                (request.org_id, request.user_id, request.idempotency_key)
            ] = conversation.conversation_id
        return conversation

    async def get_conversation(
        self,
        *,
        org_id: str,
        user_id: str,
        conversation_id: str,
    ) -> ConversationRecord | None:
        """Return a conversation only when org and user scope match."""

        conversation = self.conversations.get(conversation_id)
        if conversation is None:
            return None
        if conversation.org_id != org_id or conversation.user_id != user_id:
            return None
        return conversation

    async def get_conversation_for_org(
        self,
        *,
        org_id: str,
        conversation_id: str,
    ) -> ConversationRecord | None:
        """Return a conversation scoped by org only, bypassing the user filter."""

        conversation = self.conversations.get(conversation_id)
        if conversation is None or conversation.org_id != org_id:
            return None
        return conversation

    async def list_conversations(
        self,
        *,
        org_id: str,
        user_id: str,
        limit: int,
        include_archived: bool = False,
        include_deleted: bool = False,
        project_id: str | None = None,
        bucket: ConversationBucket | None = None,
        before_updated_at: datetime | None = None,
        before_conversation_id: str | None = None,
    ) -> Sequence[ConversationRecord]:
        """Return scoped conversations ordered ``(updated_at DESC, id DESC)``.

        Soft-deleted rows (``deleted_at IS NOT NULL``) are excluded by default;
        pass ``include_deleted=True`` to include them. When ``project_id`` is
        set, only conversations filed under that project are returned (PRD-07).

        ``bucket`` (PRD-09 D3) scopes to one Chats section server-side; when set,
        ``include_archived`` is ignored and soft-deleted rows always excluded. The
        ``(before_updated_at, before_conversation_id)`` keyset returns only rows
        strictly older than it under the sort order.
        """

        records = [
            conversation
            for conversation in self.conversations.values()
            if conversation.org_id == org_id and conversation.user_id == user_id
        ]
        if project_id is not None:
            records = [
                conversation
                for conversation in records
                if conversation.project_id == project_id
            ]
        if bucket is not None:
            records = [
                conversation
                for conversation in records
                if conversation.deleted_at is None
                and matches_conversation_bucket(conversation, bucket)
            ]
        else:
            if not include_archived:
                records = [
                    conversation
                    for conversation in records
                    if conversation.status != ConversationStatus.ARCHIVED
                ]
            if not include_deleted:
                records = [
                    conversation
                    for conversation in records
                    if conversation.deleted_at is None
                ]
        ordered = sorted(
            records,
            key=lambda conversation: (
                conversation.updated_at,
                conversation.conversation_id,
            ),
            reverse=True,
        )
        if before_updated_at is not None and before_conversation_id is not None:
            boundary = (before_updated_at, before_conversation_id)
            ordered = [
                conversation
                for conversation in ordered
                if (conversation.updated_at, conversation.conversation_id) < boundary
            ]
        return tuple(ordered[:limit])

    async def count_conversations_by_project(
        self,
        *,
        org_id: str,
        user_id: str,
        project_ids: Sequence[str],
    ) -> Mapping[str, int]:
        """Group the caller's non-deleted conversations by project (PRD-07)."""

        wanted = set(project_ids)
        counts: dict[str, int] = {}
        for conversation in self.conversations.values():
            if conversation.org_id != org_id or conversation.user_id != user_id:
                continue
            if conversation.deleted_at is not None:
                continue
            pid = conversation.project_id
            if pid is None or pid not in wanted:
                continue
            counts[pid] = counts.get(pid, 0) + 1
        return counts

    async def list_messages(
        self,
        *,
        org_id: str,
        conversation_id: str,
        limit: int,
        before_created_at: datetime | None = None,
        before_message_id: str | None = None,
        include_deleted: bool = False,
    ) -> Sequence[MessageRecord]:
        """Return the most-recent ``limit`` messages older than the keyset, ASC.

        Filters ``self.messages`` on the composite ``(created_at, message_id)``
        keyset, takes the newest ``limit`` (DESC), then reverses to ascending.
        """

        records = [
            message
            for message in self.messages.values()
            if message.org_id == org_id and message.conversation_id == conversation_id
        ]
        if not include_deleted:
            records = [message for message in records if message.deleted_at is None]
        if before_created_at is not None and before_message_id is not None:
            keyset = (before_created_at, before_message_id)
            records = [
                message
                for message in records
                if (message.created_at, message.message_id) < keyset
            ]
        newest_first = sorted(
            records,
            key=lambda message: (message.created_at, message.message_id),
            reverse=True,
        )[:limit]
        return tuple(reversed(newest_first))

    async def append_message(self, message: MessageRecord) -> MessageRecord:
        """Append a runtime-created message."""

        self.messages[message.message_id] = message
        conversation = self.conversations.get(message.conversation_id)
        if conversation is not None:
            self.conversations[message.conversation_id] = conversation.model_copy(
                update={"updated_at": message.created_at}
            )
        return message

    async def insert_forked_conversation(
        self, conversation: ConversationRecord
    ) -> ConversationRecord:
        """Insert a fork-authored conversation row verbatim.

        Bypasses idempotency (forks always mint a new row) and stores every
        column the caller has set — including lineage pointers
        ``parent_conversation_id`` and ``forked_from_share_id`` that the
        standard ``create_conversation`` path drops.
        """

        self.conversations[conversation.conversation_id] = conversation
        return conversation

    async def update_conversation_connectors(
        self,
        *,
        org_id: str,
        user_id: str,
        conversation_id: str,
        scopes_patch: dict[str, tuple[str, ...] | None],
        now: datetime,
    ) -> ConversationRecord | None:
        """RFC 7396 merge-patch enabled_connectors and stamp the timestamp."""

        conversation = await self.get_conversation(
            org_id=org_id, user_id=user_id, conversation_id=conversation_id
        )
        if conversation is None:
            return None
        merged: dict[str, tuple[str, ...] | None] = dict(
            conversation.enabled_connectors
        )
        merged.update(scopes_patch)
        updated = conversation.model_copy(
            update={
                "enabled_connectors": merged,
                "connectors_updated_at": now,
                "updated_at": now,
            }
        )
        self.conversations[conversation_id] = updated
        return updated

    async def get_workspace_defaults(
        self, *, org_id: str
    ) -> WorkspaceDefaultsRecord | None:
        """Return the workspace defaults row for an org, or ``None`` if absent."""
        return self.workspace_defaults.get(org_id)

    async def upsert_workspace_defaults(
        self, *, record: WorkspaceDefaultsRecord
    ) -> WorkspaceDefaultsRecord:
        """Persist workspace defaults, stripping the derived ``retention_days`` field.

        ``retention_days`` is composed at read time from ``retention_policies``; the
        stored record never carries it so adapters don't diverge from the Postgres path.
        """
        # Strip retention_days so the in-memory snapshot mirrors what Postgres stores.
        persisted = record.model_copy(update={"retention_days": None})
        self.workspace_defaults[record.org_id] = persisted
        return persisted

    async def update_conversation(
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
        project_id: str | None,
        project_id_changed: bool,
        now: datetime,
    ) -> ConversationRecord | None:
        """Apply a lifecycle PATCH idempotently.

        ``*_changed`` distinguishes "field omitted" (leave alone) from
        "field set to null" (clear/un-archive/unfile). When no flag is True we
        still bump ``updated_at`` and return the row so callers can
        round-trip an idempotent no-op.
        """

        conversation = await self.get_conversation(
            org_id=org_id, user_id=user_id, conversation_id=conversation_id
        )
        if conversation is None:
            return None
        update: dict[str, object] = {"updated_at": now}
        if title_changed:
            update["title"] = title
        if folder_changed:
            update["folder"] = folder
        if project_id_changed:
            update["project_id"] = project_id
        if archived_changed:
            if archived:
                update["status"] = ConversationStatus.ARCHIVED
                update["archived_at"] = now
            else:
                update["status"] = ConversationStatus.ACTIVE
                update["archived_at"] = None
        updated = conversation.model_copy(update=update)
        self.conversations[conversation_id] = updated
        return updated

    async def soft_delete_conversation(
        self,
        *,
        org_id: str,
        user_id: str,
        conversation_id: str,
        now: datetime,
    ) -> ConversationRecord | None:
        """Stamp ``deleted_at`` (idempotent on re-call).

        Idempotent: a row already deleted returns its existing record
        unchanged. Callers above (the service) cancel any active run
        before invoking this method.
        """

        conversation = await self.get_conversation(
            org_id=org_id, user_id=user_id, conversation_id=conversation_id
        )
        if conversation is None:
            return None
        if conversation.deleted_at is not None:
            return conversation
        updated = conversation.model_copy(update={"deleted_at": now, "updated_at": now})
        self.conversations[conversation_id] = updated
        return updated

    async def restore_conversation(
        self,
        *,
        org_id: str,
        user_id: str,
        conversation_id: str,
        now: datetime,
    ) -> ConversationRecord | None:
        """Clear ``deleted_at``. Returns ``None`` if the row was reaped."""

        conversation = await self.get_conversation(
            org_id=org_id, user_id=user_id, conversation_id=conversation_id
        )
        if conversation is None:
            return None
        if conversation.deleted_at is None:
            # Nothing to restore — callers can treat this as a 204.
            return conversation
        updated = conversation.model_copy(
            update={"deleted_at": None, "updated_at": now}
        )
        self.conversations[conversation_id] = updated
        return updated

    async def set_conversation_pinned(
        self,
        *,
        org_id: str,
        user_id: str,
        conversation_id: str,
        pinned: bool,
        now: datetime,
    ) -> ConversationRecord | None:
        """Set the first-class ``pinned`` flag (PRD-H.4), idempotent on re-call.

        ``updated_at`` is bumped only when the flag actually changes so a
        redundant pin does not reshuffle the newest-first sidebar order.
        """

        conversation = await self.get_conversation(
            org_id=org_id, user_id=user_id, conversation_id=conversation_id
        )
        if conversation is None:
            return None
        if conversation.pinned == pinned:
            return conversation
        updated = conversation.model_copy(update={"pinned": pinned, "updated_at": now})
        self.conversations[conversation_id] = updated
        return updated

    async def get_latest_message_for_conversation(
        self,
        *,
        org_id: str,
        conversation_id: str,
        prefer_roles: tuple[str, ...] = ("assistant",),
    ) -> MessageRecord | None:
        """Return the newest non-deleted message for the Chats-list preview (PRD-H.4).

        Prefers the newest message whose role is in ``prefer_roles`` (PRD-09 D6),
        falling back to the newest of any role when none matches.
        """

        candidates = [
            message
            for message in self.messages.values()
            if message.org_id == org_id
            and message.conversation_id == conversation_id
            and message.deleted_at is None
        ]
        if not candidates:
            return None
        preferred = [
            message for message in candidates if str(message.role) in prefer_roles
        ]
        pool = preferred if preferred else candidates
        return max(pool, key=lambda message: message.created_at)

    async def get_latest_run_for_conversation(
        self,
        *,
        org_id: str,
        conversation_id: str,
    ) -> RunRecord | None:
        """Return the newest run for a conversation regardless of status (PRD-H.4)."""

        candidates = [
            run
            for run in self.runs.values()
            if run.org_id == org_id and run.conversation_id == conversation_id
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda run: run.created_at)

    async def list_runs_for_conversation(
        self,
        *,
        org_id: str,
        conversation_id: str,
        limit: int,
    ) -> tuple[RunRecord, ...]:
        """Return the conversation's runs newest-first (any status), capped at ``limit``."""

        candidates = [
            run
            for run in self.runs.values()
            if run.org_id == org_id and run.conversation_id == conversation_id
        ]
        candidates.sort(key=lambda run: run.created_at, reverse=True)
        return tuple(candidates[: max(0, limit)])

    async def list_runs_for_org(
        self,
        *,
        org_id: str,
        user_id: str,
        limit: int,
        before_created_at: datetime | None = None,
        before_run_id: str | None = None,
    ) -> tuple[RunHistoryEntry, ...]:
        """Return the caller's runs newest-first across conversations (PRD-05).

        Scans ``self.runs`` filtered on ``(org_id, user_id)``, joins
        ``self.conversations`` for the title, excludes runs whose conversation
        is soft-deleted or absent, orders by ``(created_at, run_id)`` DESC,
        applies the keyset, and slices ``limit``.
        """

        entries: list[RunHistoryEntry] = []
        for run in self.runs.values():
            if run.org_id != org_id or run.user_id != user_id:
                continue
            conversation = self.conversations.get(run.conversation_id)
            # A run without a live, non-deleted conversation is hidden — the
            # deleted-conversation predicate is load-bearing (soft-delete /
            # delete-my-history), not defensive.
            if conversation is None or conversation.deleted_at is not None:
                continue
            entries.append(
                RunHistoryEntry(
                    run_id=run.run_id,
                    conversation_id=run.conversation_id,
                    conversation_title=conversation.title,
                    status=run.status,
                    model_name=run.model_name,
                    created_at=run.created_at,
                    started_at=run.started_at,
                    completed_at=run.completed_at,
                    cancelled_at=run.cancelled_at,
                )
            )
        entries.sort(key=lambda e: (e.created_at, e.run_id), reverse=True)
        if before_created_at is not None and before_run_id is not None:
            keyset = (before_created_at, before_run_id)
            entries = [e for e in entries if (e.created_at, e.run_id) < keyset]
        return tuple(entries[: max(0, limit)])

    # ------------------------------------------------------------------
    # Tool-invocation ledger (PRD-08 D1b) + Activity meta aggregates.
    # ------------------------------------------------------------------

    async def record_tool_invocation(self, record: ToolInvocationRecord) -> None:
        """Upsert a tool-invocation row keyed by ``invocation_id`` (idempotent)."""

        self.tool_invocations[record.invocation_id] = record

    async def count_tool_invocations_for_runs(
        self, *, org_id: str, run_ids: Sequence[str]
    ) -> Mapping[str, tuple[int, int]]:
        """Return ``run_id → (step_count, connector_count)`` (runs with rows only)."""

        wanted = set(run_ids)
        steps: dict[str, int] = {}
        connectors: dict[str, set[str]] = {}
        for record in self.tool_invocations.values():
            if record.org_id != org_id or record.run_id not in wanted:
                continue
            steps[record.run_id] = steps.get(record.run_id, 0) + 1
            if record.connector_slug is not None:
                connectors.setdefault(record.run_id, set()).add(record.connector_slug)
        return {
            run_id: (count, len(connectors.get(run_id, set())))
            for run_id, count in steps.items()
        }

    async def count_pending_approvals_for_runs(
        self, *, org_id: str, run_ids: Sequence[str]
    ) -> Mapping[str, int]:
        """Return ``run_id → pending-approval count`` (runs with pending only)."""

        from runtime_api.schemas.common import ApprovalStatus  # local: avoid cycle

        wanted = set(run_ids)
        pending: dict[str, int] = {}
        for request in self.approval_requests.values():
            if (
                request.org_id != org_id
                or request.run_id not in wanted
                or request.status is not ApprovalStatus.PENDING
            ):
                continue
            pending[request.run_id] = pending.get(request.run_id, 0) + 1
        return pending

    async def create_run_with_user_message(
        self,
        *,
        request: CreateRunRequest,
        conversation: ConversationRecord,
    ) -> tuple[RunRecord, MessageRecord, bool]:
        """Create message/run records or return an idempotent prior run."""

        context = request.runtime_context
        if context is None:
            raise RuntimeApiError(
                RuntimeErrorCode.VALIDATION_ERROR,
                "Runtime context is required.",
                http_status=status.HTTP_400_BAD_REQUEST,
                retryable=False,
            )

        if request.idempotency_key is not None:
            key = (context.org_id, context.user_id, request.idempotency_key)
            existing_run_id = self._run_idempotency.get(key)
            if existing_run_id is not None:
                self._ensure_run_idempotency_match(
                    key=key,
                    request=request,
                )
                run = self.runs[existing_run_id]
                return run, self.messages[run.user_message_id], False

        user_message = RuntimeAdapterHelpers.message_for_run_request(
            request=request,
            conversation=conversation,
            get_message=lambda mid: self.messages.get(mid),
            get_latest_message_id=self._latest_message_id,
            find_latest_assistant_for_run=self._find_latest_assistant_for_run,
            run_id_for_message=context.run_id,
        )
        run = RunRecord(
            run_id=context.run_id,
            conversation_id=conversation.conversation_id,
            org_id=context.org_id,
            user_id=context.user_id,
            user_message_id=user_message.message_id,
            idempotency_key=request.idempotency_key,
            trace_id=context.trace_id,
            model_provider=context.model_profile.provider,
            model_name=context.model_profile.model_name,
            runtime_context=context,
            request_options=request.request_options,
        )
        if user_message.message_id not in self.messages:
            self.messages[user_message.message_id] = user_message
        self.runs[run.run_id] = run
        self.conversations[conversation.conversation_id] = conversation.model_copy(
            update={"updated_at": run.created_at}
        )
        self.events_by_run.setdefault(run.run_id, [])
        if request.idempotency_key is not None:
            key = (context.org_id, context.user_id, request.idempotency_key)
            self._run_idempotency[key] = run.run_id
            self._run_idempotency_fingerprint[key] = (
                request.conversation_id,
                request.user_input,
            )
        return run, user_message, True

    def _latest_message_id(self, org_id: str, conversation_id: str) -> str | None:
        """Return the most recent non-deleted message ID (by created_at DESC)."""

        candidates = [
            message
            for message in self.messages.values()
            if message.org_id == org_id
            and message.conversation_id == conversation_id
            and message.deleted_at is None
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda m: m.created_at).message_id

    def _find_latest_assistant_for_run(
        self, org_id: str, conversation_id: str, run_id: str
    ) -> str | None:
        """Return the latest assistant message ID for a given run."""

        matches = [
            message
            for message in self.messages.values()
            if message.org_id == org_id
            and message.conversation_id == conversation_id
            and message.run_id == run_id
            and message.role == MessageRole.ASSISTANT
            and message.deleted_at is None
        ]
        if not matches:
            return None
        return max(matches, key=lambda m: m.created_at).message_id

    async def get_run(self, *, org_id: str, run_id: str) -> RunRecord | None:
        """Return a run scoped by organization."""

        run = self.runs.get(run_id)
        if run is None or run.org_id != org_id:
            return None
        return run

    async def get_active_run_for_conversation(
        self,
        *,
        org_id: str,
        conversation_id: str,
    ) -> RunRecord | None:
        """Return the most recent non-terminal run for one conversation."""

        candidates = [
            run
            for run in self.runs.values()
            if run.org_id == org_id
            and run.conversation_id == conversation_id
            and run.status in ACTIVE_RUN_STATUSES
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda run: run.created_at)

    async def count_active_runs(self, *, org_id: str, user_id: str) -> int:
        """Count the caller's in-flight runs for the rail Run badge (PRD-12 D1).

        Scans ``self.runs`` filtered on ``(org_id, user_id)`` and
        ``ACTIVE_RUN_STATUSES``, joined to a live conversation (soft-deleted /
        absent conversations drop out — the same load-bearing predicate
        ``list_runs_for_org`` uses). Counts every in-flight run, so two runs in
        ONE conversation count as 2.
        """

        count = 0
        for run in self.runs.values():
            if run.org_id != org_id or run.user_id != user_id:
                continue
            if run.status not in ACTIVE_RUN_STATUSES:
                continue
            conversation = self.conversations.get(run.conversation_id)
            if conversation is None or conversation.deleted_at is not None:
                continue
            count += 1
        return count

    async def update_run_status(
        self, *, run_id: str, status: AgentRunStatus
    ) -> RunRecord:
        """Update run status and relevant timestamps.

        PRD-09 D4 — also bump the parent conversation's ``updated_at`` so a
        status-only transition (cancel/fail/timeout/WAITING_FOR_APPROVAL) moves
        the row the Chats live tail watches; without it the tail would miss the
        very chip flip the user is watching.
        """

        run = self.runs[run_id]
        timestamps = StatusTransition.timestamp_updates(
            status, already_started=run.started_at is not None
        )
        updated = run.model_copy(update={"status": status, **timestamps})
        self.runs[run_id] = updated
        conversation = self.conversations.get(run.conversation_id)
        if conversation is not None:
            self.conversations[run.conversation_id] = conversation.model_copy(
                update={"updated_at": datetime.now(timezone.utc)}
            )
        return updated

    async def set_run_latest_sequence(
        self, *, run_id: str, latest_sequence_no: int
    ) -> RunRecord:
        """Persist latest event sequence for run inspection.

        A smaller-numbered update arriving out of order is a no-op — the same
        monotonic guard the Postgres adapter enforces — so the in-memory path
        stays consistent under async concurrency.
        """

        current = self.runs[run_id]
        existing = current.latest_sequence_no
        if existing is not None and existing >= latest_sequence_no:
            return current
        updated = current.model_copy(update={"latest_sequence_no": latest_sequence_no})
        self.runs[run_id] = updated
        return updated

    async def record_approval_decision(
        self,
        *,
        record: ApprovalDecisionRecord,
    ) -> ApprovalDecisionRecord:
        """Persist approval decision and update the request state."""

        self.approval_decisions[record.approval_id] = record
        request = self.approval_requests[record.approval_id]
        # Round-trip ``decided_at`` on the request metadata so callers can
        # compute the undo window without an extra get_approval_decision read.
        merged_metadata = dict(request.metadata)
        merged_metadata["decided_at"] = record.decided_at.isoformat()
        self.approval_requests[record.approval_id] = request.model_copy(
            update={"status": record.status, "metadata": merged_metadata}
        )
        return record

    async def create_approval_request(
        self,
        *,
        record: ApprovalRequestRecord,
    ) -> ApprovalRequestRecord:
        """Persist a pending approval request."""

        existing = self.approval_requests.get(record.approval_id)
        if existing is not None:
            return existing
        normalized_metadata = dict(record.metadata)
        normalized_metadata[_Fields.RISK_LEVEL] = (
            RuntimeAdapterHelpers.normalize_risk_class(record.metadata)
        )
        record = record.model_copy(update={"metadata": normalized_metadata})
        self.approval_requests[record.approval_id] = record
        return record

    async def forward_approval_request(
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
        """Atomically transition the parent to FORWARDED and insert the child approval.

        Mirrors Postgres transaction semantics. Idempotent on the child's
        ``approval_id`` — a retry with the same child id returns the prior chain
        unchanged. Records a decision row for the parent so callers observing
        the chain see the transition without a separate read.
        """

        from runtime_api.schemas.common import ApprovalStatus  # local: avoid cycle

        parent = self.approval_requests.get(parent_approval_id)
        if parent is None or parent.org_id != org_id:
            raise KeyError(parent_approval_id)
        # Mirror Postgres' WHERE status='pending' guard so a concurrent forward
        # deterministically loses the race. The service maps this RuntimeError
        # to a 409 APPROVAL_FORWARD_NOT_PENDING response.
        if parent.status is not ApprovalStatus.PENDING:
            # Re-posting the exact same forward (parent already FORWARDED to the
            # same target with the same child id) is a safe idempotent no-op.
            existing_child = self.approval_requests.get(child.approval_id)
            if (
                parent.status is ApprovalStatus.FORWARDED
                and parent.forwarded_to_user_id == forwarded_to_user_id
                and existing_child is not None
                and existing_child.chain_parent_approval_id == parent_approval_id
            ):
                return parent, existing_child
            raise RuntimeError("approval_forward_parent_no_longer_pending")
        existing_child = self.approval_requests.get(child.approval_id)
        if existing_child is not None:
            return parent, existing_child
        updated_parent = parent.model_copy(
            update={
                "status": ApprovalStatus.FORWARDED,
                "forwarded_to_user_id": forwarded_to_user_id,
                "forwarded_at": now,
            }
        )
        self.approval_requests[parent_approval_id] = updated_parent
        normalized_metadata = dict(child.metadata)
        normalized_metadata[_Fields.RISK_LEVEL] = (
            RuntimeAdapterHelpers.normalize_risk_class(child.metadata)
        )
        normalized_child = child.model_copy(
            update={
                "metadata": normalized_metadata,
                "chain_parent_approval_id": parent_approval_id,
                # chain_depth is set by the service from the parent's persisted
                # column; the adapter trusts the passed value and never recomputes.
                "chain_depth": child.chain_depth or (parent.chain_depth + 1),
            }
        )
        self.approval_requests[normalized_child.approval_id] = normalized_child
        self.approval_decisions[parent_approval_id] = ApprovalDecisionRecord(
            approval_id=parent_approval_id,
            run_id=updated_parent.run_id,
            conversation_id=updated_parent.conversation_id,
            org_id=updated_parent.org_id,
            user_id=updated_parent.user_id,
            status=ApprovalStatus.FORWARDED,
            decided_by_user_id=decided_by_user_id,
            reason=decision_reason,
            decided_at=now,
            forwarded_to_user_id=forwarded_to_user_id,
        )
        return updated_parent, normalized_child

    async def get_approval_request(
        self,
        *,
        org_id: str,
        approval_id: str,
    ) -> ApprovalRequestRecord | None:
        """Return an approval request scoped by organization."""

        approval = self.approval_requests.get(approval_id)
        if approval is None or approval.org_id != org_id:
            return None
        return approval

    # ------------------------------------------------------------------
    # ApprovalBatch — first-class entity (PR #43).
    #
    # Atomic semantics are enforced by a per-batch ``asyncio.Lock`` so the
    # in-memory adapter matches the Postgres "SELECT ... FOR UPDATE on the
    # batch row" contract: only one coroutine can flip ``PENDING -> RESUMING``
    # for a given batch.
    # ------------------------------------------------------------------

    def _approval_batch_lock(self, batch_id: str) -> asyncio.Lock:
        """Lazily create and return the per-batch ``asyncio.Lock``.

        Locks live for the process lifetime — there is at most one per batch
        and the dev/test footprint is bounded by the test fixture set.
        """
        lock = self._approval_batch_locks.get(batch_id)
        if lock is None:
            lock = asyncio.Lock()
            self._approval_batch_locks[batch_id] = lock
        return lock

    async def insert_approval_batch(
        self,
        *,
        spec: ApprovalBatchSpec,
    ) -> ApprovalBatchRecord:
        """Insert one batch and its ordered items. Idempotent on ``batch_id``."""

        existing = self.approval_batches.get(spec.batch.batch_id)
        if existing is not None:
            return existing
        self.approval_batches[spec.batch.batch_id] = spec.batch
        for item in spec.items:
            self.approval_batch_items[item.item_id] = item
        return spec.batch

    async def get_approval_batch(
        self,
        *,
        org_id: str,
        batch_id: str,
    ) -> ApprovalBatchRecord | None:
        """Return a batch row scoped to org, or ``None``."""

        batch = self.approval_batches.get(batch_id)
        if batch is None or batch.org_id != org_id:
            return None
        return batch

    async def get_approval_batch_item(
        self,
        *,
        org_id: str,
        item_id: str,
    ) -> ApprovalBatchItemRecord | None:
        """Return an item row scoped to org, or ``None``."""

        item = self.approval_batch_items.get(item_id)
        if item is None:
            return None
        batch = self.approval_batches.get(item.batch_id)
        if batch is None or batch.org_id != org_id:
            return None
        return item

    async def list_items_for_batch(
        self,
        *,
        org_id: str,
        batch_id: str,
    ) -> tuple[ApprovalBatchItemRecord, ...]:
        """Return every item belonging to ``batch_id`` in ``index`` order."""

        batch = self.approval_batches.get(batch_id)
        if batch is None or batch.org_id != org_id:
            return ()
        items = [
            item
            for item in self.approval_batch_items.values()
            if item.batch_id == batch_id
        ]
        items.sort(key=lambda record: record.index)
        return tuple(items)

    async def record_item_decision_and_maybe_lock_batch(
        self,
        *,
        org_id: str,
        item_id: str,
        decision: ApprovalDecision,
    ) -> BatchTransitionOutcome:
        """Atomic: record the item decision; flip the batch if it just completed.

        Exactly-one ``PENDING -> RESUMING`` per batch is enforced by the
        per-batch ``asyncio.Lock``. Concurrent callers serialise here; the
        first to find every item resolved wins ``READY_TO_RESUME``, the others
        return ``LOST_RACE`` because the batch is no longer ``PENDING``.
        """

        item = self.approval_batch_items.get(item_id)
        if item is None:
            return BatchTransitionOutcome(status=BatchOutcomeStatus.LOST_RACE)
        batch_id = item.batch_id
        batch = self.approval_batches.get(batch_id)
        if batch is None or batch.org_id != org_id:
            return BatchTransitionOutcome(status=BatchOutcomeStatus.LOST_RACE)

        async with self._approval_batch_lock(batch_id):
            # Re-read inside the lock; the batch may have moved past PENDING
            # while we waited for the lock.
            current_batch = self.approval_batches.get(batch_id)
            if current_batch is None:
                return BatchTransitionOutcome(status=BatchOutcomeStatus.LOST_RACE)
            if current_batch.status is not ApprovalBatchStatus.PENDING:
                return BatchTransitionOutcome(status=BatchOutcomeStatus.LOST_RACE)
            # Write the decision. Idempotent: re-recording the same decision
            # produces an equivalent row. Pydantic models are frozen so we
            # rebuild the record.
            current_item = self.approval_batch_items[item_id]
            # ApprovalDecision and BatchItemDecision share string values; the
            # records layer owns its own enum to keep itself import-cycle
            # free, but the cross-layer mapping is a no-op string round-trip.
            batch_decision = BatchItemDecision(decision.value)
            updated_item = current_item.model_copy(update={"decision": batch_decision})
            self.approval_batch_items[item_id] = updated_item
            # Read every sibling in index order.
            siblings = [
                row
                for row in self.approval_batch_items.values()
                if row.batch_id == batch_id
            ]
            siblings.sort(key=lambda record: record.index)
            if any(sibling.decision is None for sibling in siblings):
                return BatchTransitionOutcome(
                    status=BatchOutcomeStatus.BATCH_INCOMPLETE
                )
            # Every item is resolved — flip PENDING -> RESUMING.
            resuming = current_batch.model_copy(
                update={"status": ApprovalBatchStatus.RESUMING}
            )
            self.approval_batches[batch_id] = resuming
            return BatchTransitionOutcome(
                status=BatchOutcomeStatus.READY_TO_RESUME,
                batch=resuming,
                items=tuple(siblings),
            )

    async def mark_approval_batch_resolved(
        self,
        *,
        org_id: str,
        batch_id: str,
    ) -> None:
        """Stamp ``RESUMING -> RESOLVED``; idempotent for terminal statuses."""

        batch = self.approval_batches.get(batch_id)
        if batch is None or batch.org_id != org_id:
            return
        if batch.status in {
            ApprovalBatchStatus.RESOLVED,
            ApprovalBatchStatus.EXPIRED,
        }:
            return
        self.approval_batches[batch_id] = batch.model_copy(
            update={"status": ApprovalBatchStatus.RESOLVED}
        )

    async def list_assigned_approvals(
        self,
        *,
        org_id: str,
        requested_by_user_id: str,
        status: str,
        limit: int,
        cursor: tuple[datetime, str] | None,
    ) -> Sequence[ApprovalRequestRecord]:
        """Return the recipient's approval inbox, newest-first with cursor pagination.

        Ordering is ``(created_at DESC, approval_id DESC)``. The cursor is exclusive —
        rows at or after it are excluded. The ``org_id`` filter approximates row-level
        security for the in-memory path.
        """

        rows: list[ApprovalRequestRecord] = []
        for approval in self.approval_requests.values():
            if approval.org_id != org_id:
                continue
            if approval.user_id != requested_by_user_id:
                continue
            if approval.status.value != status:
                continue
            if cursor is not None:
                cursor_at, cursor_id = cursor
                if (approval.created_at, approval.approval_id) >= (
                    cursor_at,
                    cursor_id,
                ):
                    continue
            rows.append(approval)
        rows.sort(
            key=lambda record: (record.created_at, record.approval_id),
            reverse=True,
        )
        return tuple(rows[:limit])

    async def list_pending_expired_approvals(
        self,
        *,
        now: datetime,
        limit: int,
    ) -> Sequence[ApprovalRequestRecord]:
        """Return PENDING approvals whose ``expires_at`` has passed, oldest first.

        Cross-org sweeper query run by the runtime worker. Returns rows oldest-first
        so a backlog drains in fair order.
        """

        from runtime_api.schemas.common import ApprovalStatus  # local: avoid cycle

        rows = [
            approval
            for approval in self.approval_requests.values()
            if approval.status is ApprovalStatus.PENDING
            and approval.expires_at is not None
            and approval.expires_at <= now
        ]
        rows.sort(key=lambda record: (record.expires_at or now, record.approval_id))
        return tuple(rows[:limit])

    async def list_pending_approvals_for_membership_audit(
        self,
        *,
        limit: int,
    ) -> Sequence[ApprovalRequestRecord]:
        """Cross-org sweeper query for the membership-cascade pass."""

        from runtime_api.schemas.common import ApprovalStatus  # local: avoid cycle

        rows = [
            approval
            for approval in self.approval_requests.values()
            if approval.status is ApprovalStatus.PENDING
        ]
        rows.sort(key=lambda record: (record.created_at, record.approval_id))
        return tuple(rows[:limit])

    async def list_audit_log_for_export(
        self,
        *,
        after_id: str | None,
        limit: int,
    ) -> Sequence[dict]:
        """Return audit log rows for SIEM export, resumable by signature cursor.

        Row order is insertion order (the in-memory store has no separate
        ``(created_at, id)`` index). ``after_id`` is matched against the chain
        ``signature`` hex string; rows at or before that position are excluded.
        """

        rows: list[dict] = [dict(record) for _event_type, record in self.audit_log]
        if after_id is not None:
            for index, row in enumerate(rows):
                if row.get("signature") == after_id:
                    rows = rows[index + 1 :]
                    break
            else:
                rows = []
        return tuple(rows[:limit])

    async def list_retention_orgs(self) -> Sequence[str]:
        """Return distinct org_ids visible to the retention sweeper.

        Worker-role only. The in-memory store sources the set from
        conversations + messages + runs, matching the production sweeper's
        cross-table view.
        """

        seen: set[str] = set()
        seen.update(c.org_id for c in self.conversations.values())
        seen.update(m.org_id for m in self.messages.values())
        seen.update(r.org_id for r in self.runs.values())
        return tuple(sorted(seen))

    async def sweep_retention_kind(
        self,
        *,
        org_id: str,
        kind,
        ttl_seconds: int,
        dry_run: bool = False,
        chunk_size: int = 0,
    ):
        """In-memory retention sweep — no-op stub for dev backends.

        The in-memory store is process-local and never carries enough
        history to need tombstoning. Returns a zero-count outcome so the
        worker's ``RetentionSweeperLoop`` can run end-to-end against the
        in-memory backend without crashing.
        """

        from agent_runtime.persistence.records import RetentionSweepOutcome

        return RetentionSweepOutcome(
            org_id=org_id,
            kind=kind,
            tombstoned=0,
            deleted=0,
            skipped_legal_hold=0,
        )

    async def insert_retention_deletion_evidence(self, record) -> None:
        """Append a deletion evidence record (test-observable via self.deletion_evidence)."""

        self.deletion_evidence.append(record)

    async def backfill_retention_until(
        self,
        *,
        org_id: str,
        kind,
        ttl_seconds: int,
        chunk_size: int,
    ) -> int:
        """No-op for in-memory store — it starts fresh, no historical rows to fill."""

        return 0

    async def recompute_retention_until_for_policy(
        self,
        *,
        org_id: str,
        kind,
        scope,
        resource_id,
        ttl_seconds,
    ) -> int:
        """No-op for in-memory store — rows are ephemeral, no recompute needed."""

        return 0

    async def write_audit_log(
        self, *, event_type: str, record: dict[str, object]
    ) -> None:
        """Append an audit record with HMAC hash-chain fields attached.

        Chain is per-(audit_log, org_id). The record dict gains ``seq``,
        ``prev_hash`` (hex or ``None``), ``signature`` (hex), and
        ``key_version`` so callers reading ``audit_log`` see exactly what a
        Postgres-backed export would emit, without coupling tests to the
        chain internals.
        """

        signed = self._sign_audit_record(event_type=event_type, record=record)
        self.audit_log.append((event_type, signed))

    def _sign_audit_record(
        self, *, event_type: str, record: dict[str, object]
    ) -> dict[str, object]:
        """Extend a record with HMAC hash-chain fields (seq, prev_hash, signature, key_version)."""
        org_id = str(record.get(_Fields.ORG_ID, "unknown"))
        prev_hash = self._audit_chain_heads_by_org.get(org_id)
        payload = self._audit_signing_payload(event_type=event_type, record=record)
        sig = self._audit_chain_signer.sign(prev_hash=prev_hash, payload=payload)
        seq = self._audit_chain_counts_by_org.get(org_id, 0) + 1
        self._audit_chain_counts_by_org[org_id] = seq
        self._audit_chain_heads_by_org[org_id] = sig.signature
        return {
            **record,
            "seq": seq,
            "prev_hash": prev_hash.hex() if prev_hash else None,
            "signature": sig.signature.hex(),
            "key_version": sig.key_version,
        }

    @staticmethod
    def _audit_signing_payload(
        *, event_type: str, record: dict[str, object]
    ) -> dict[str, Any]:
        """Build the signing payload by stripping chain fields from the record.

        Chain fields (seq, prev_hash, signature, key_version) are excluded so the
        signature is computed over the canonical domain data only, not over itself.
        """
        # Chain fields are excluded so the signature is independent of itself.
        signable = {
            k: v
            for k, v in record.items()
            if k
            not in {
                "seq",
                "prev_hash",
                "signature",
                "key_version",
            }
        }
        signable["__event_type__"] = event_type
        return signable

    async def delete_user_history(
        self,
        *,
        org_id: str,
        user_id: str,
        reason: str | None = None,
    ) -> HistoryDeletionResponse:
        """Tombstone user-visible history while preserving audit evidence."""

        now = datetime.now(timezone.utc)
        conversation_ids = {
            conversation.conversation_id
            for conversation in self.conversations.values()
            if conversation.org_id == org_id and conversation.user_id == user_id
        }
        conversations_archived = 0
        for conversation_id in conversation_ids:
            conversation = self.conversations[conversation_id]
            if conversation.status != ConversationStatus.ARCHIVED:
                conversations_archived += 1
            self.conversations[conversation_id] = conversation.model_copy(
                update={
                    "status": ConversationStatus.ARCHIVED,
                    "archived_at": now,
                    # PRD-05 — stamp ``deleted_at`` too so the run-history feed
                    # (``list_runs_for_org``, which filters on the joined
                    # conversation's ``deleted_at``) is actually cleared, and the
                    # C8 tombstone sweeper can reap the rows. COALESCE-style: keep
                    # an earlier deletion instant if one already exists.
                    "deleted_at": conversation.deleted_at or now,
                    "updated_at": now,
                }
            )

        messages_tombstoned = 0
        for message_id, message in tuple(self.messages.items()):
            if (
                message.org_id != org_id
                or message.conversation_id not in conversation_ids
            ):
                continue
            if message.deleted_at is None:
                messages_tombstoned += 1
            self.messages[message_id] = message.model_copy(
                update={
                    "status": MessageStatus.DELETED,
                    "content_text": "[deleted by user request]",
                    "deleted_at": now,
                }
            )

        runs_cancelled = 0
        for run_id, run in tuple(self.runs.items()):
            if run.org_id != org_id or run.user_id != user_id:
                continue
            if run.status not in StatusTransition.TERMINAL_STATUSES:
                runs_cancelled += 1
                self.runs[run_id] = run.model_copy(
                    update={"status": AgentRunStatus.CANCELLED, "cancelled_at": now}
                )

        events_retained = sum(
            len(events)
            for run_id, events in self.events_by_run.items()
            if self.runs.get(run_id) is not None
            and self.runs[run_id].org_id == org_id
            and self.runs[run_id].user_id == user_id
        )
        audit_event_id = f"history_delete_{org_id}_{user_id}_{int(now.timestamp())}"
        await self.write_audit_log(
            event_type="user_history_deleted",
            record={
                _Fields.AUDIT_EVENT_ID: audit_event_id,
                _Fields.ORG_ID: org_id,
                _Fields.USER_ID: user_id,
                _Fields.REASON: reason,
                _Fields.DELETED_AT: now.isoformat(),
            },
        )
        return HistoryDeletionResponse(
            org_id=org_id,
            user_id=user_id,
            conversations_archived=conversations_archived,
            messages_tombstoned=messages_tombstoned,
            runs_cancelled=runs_cancelled,
            events_retained=events_retained,
            audit_event_id=audit_event_id,
        )

    # ------------------------------------------------------------------
    # Usage + pricing
    # ------------------------------------------------------------------

    async def record_run_usage(self, record: RuntimeRunUsageRecord) -> None:
        """Idempotent on ``run_id``; second write is a no-op."""

        if record.run_id in self.run_usage:
            return
        self.run_usage[record.run_id] = record

    async def record_model_call_usage(
        self, record: RuntimeModelCallUsageRecord
    ) -> None:
        """Append a per-call usage record."""
        self.model_call_usage.append(record)

    async def update_run_usage_cost(
        self,
        *,
        run_id: str,
        cost_micro_usd: int,
        pricing_id: str,
        pricing_version: str,
    ) -> None:
        """Back-fill cost and pricing fields on an existing run usage record."""
        existing = self.run_usage.get(run_id)
        if existing is None:
            return
        self.run_usage[run_id] = existing.model_copy(
            update={
                "cost_micro_usd": cost_micro_usd,
                "pricing_id": pricing_id,
                "pricing_version": pricing_version,
            }
        )

    async def update_model_call_usage_cost(
        self,
        *,
        usage_id: str,
        cost_micro_usd: int,
        pricing_id: str,
        pricing_version: str,
    ) -> None:
        """Back-fill cost and pricing fields on an existing model-call usage record."""
        for index, row in enumerate(self.model_call_usage):
            if row.id == usage_id:
                self.model_call_usage[index] = row.model_copy(
                    update={
                        "cost_micro_usd": cost_micro_usd,
                        "pricing_id": pricing_id,
                        "pricing_version": pricing_version,
                    }
                )
                return

    async def upsert_pricing(self, record: ModelPricingRecord) -> ModelPricingRecord:
        """Insert a new pricing row, closing any active row for the same (provider, model, region).

        Preserves the partial unique-index semantics: only one open row (``effective_until IS NULL``)
        per triple. An earlier active row is closed by setting its ``effective_until``
        to the new row's ``effective_from``.
        """
        # Close the active row for the same triple if its effective_from is
        # strictly earlier; preserves the partial unique index semantics.
        for index, existing in enumerate(self.pricing_rows):
            if (
                existing.provider == record.provider
                and existing.model_name == record.model_name
                and existing.region == record.region
                and existing.effective_until is None
                and existing.effective_from < record.effective_from
            ):
                self.pricing_rows[index] = existing.model_copy(
                    update={"effective_until": record.effective_from}
                )
        self.pricing_rows.append(record)
        return record

    async def lookup_pricing(
        self,
        *,
        provider: str,
        model_name: str,
        region: str,
        at: datetime,
    ) -> ModelPricingRecord | None:
        """Return the most-recently-effective pricing row for a provider/model/region at a point in time."""
        candidates = [
            row
            for row in self.pricing_rows
            if row.provider == provider
            and row.model_name == model_name
            and row.region == region
            and row.effective_from <= at
            and (row.effective_until is None or row.effective_until > at)
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda row: row.effective_from)

    async def list_runs_missing_cost(
        self,
        *,
        limit: int,
        cursor: str | None = None,
    ) -> Sequence[RuntimeRunUsageRecord]:
        """Return run usage records where ``cost_micro_usd`` has not yet been filled in."""
        rows = sorted(
            (row for row in self.run_usage.values() if row.cost_micro_usd is None),
            key=lambda row: row.id,
        )
        if cursor is not None:
            rows = [row for row in rows if row.id > cursor]
        return tuple(rows[:limit])

    async def upsert_user_daily_usage(self, row: UsageDailyUserRow) -> None:
        """Upsert a per-user daily usage rollup row, keyed by (org, user, day, provider, model)."""
        key = (
            row.org_id,
            row.user_id,
            row.day.isoformat(),
            row.model_provider,
            row.model_name,
        )
        self.user_daily_usage[key] = row

    async def upsert_org_daily_usage(self, row: UsageDailyOrgRow) -> None:
        """Upsert a per-org daily usage rollup row, keyed by (org, day, provider, model)."""
        key = (
            row.org_id,
            row.day.isoformat(),
            row.model_provider,
            row.model_name,
        )
        self.org_daily_usage[key] = row

    async def upsert_connector_daily_usage(self, row: UsageDailyConnectorRow) -> None:
        """Upsert a per-connector daily usage rollup row, keyed by (org, day, connector, model)."""
        key = (
            row.org_id,
            row.day.isoformat(),
            row.connector_slug,
            row.model_name,
        )
        self.connector_daily_usage[key] = row

    async def upsert_subagent_daily_usage(self, row: UsageDailySubagentRow) -> None:
        """Upsert a per-subagent daily usage rollup row, keyed by (org, day, subagent, provider, model)."""
        key = (
            row.org_id,
            row.day.isoformat(),
            row.subagent_slug,
            row.model_provider,
            row.model_name,
        )
        self.subagent_daily_usage[key] = row

    async def upsert_purpose_daily_usage(self, row: UsageDailyPurposeRow) -> None:
        """Upsert a per-purpose daily usage rollup row, keyed by (org, day, purpose, provider, model)."""
        key = (
            row.org_id,
            row.day.isoformat(),
            row.purpose,
            row.model_provider,
            row.model_name,
        )
        self.purpose_daily_usage[key] = row

    async def query_user_daily_usage(
        self,
        *,
        org_id: str,
        user_id: str,
        start_day: datetime,
        end_day: datetime,
    ) -> Sequence[UsageDailyUserRow]:
        """Return per-user daily usage rows within the inclusive date window."""
        return tuple(
            sorted(
                (
                    row
                    for row in self.user_daily_usage.values()
                    if row.org_id == org_id
                    and row.user_id == user_id
                    and start_day <= row.day <= end_day
                ),
                key=lambda r: r.day,
                reverse=True,
            )
        )

    async def query_org_daily_usage(
        self,
        *,
        org_id: str,
        start_day: datetime,
        end_day: datetime,
    ) -> Sequence[UsageDailyOrgRow]:
        """Return per-org daily usage rows within the inclusive date window."""
        return tuple(
            sorted(
                (
                    row
                    for row in self.org_daily_usage.values()
                    if row.org_id == org_id and start_day <= row.day <= end_day
                ),
                key=lambda r: r.day,
                reverse=True,
            )
        )

    async def query_connector_daily_usage(
        self,
        *,
        org_id: str,
        start_day: datetime,
        end_day: datetime,
    ) -> Sequence[UsageDailyConnectorRow]:
        """Return per-connector daily usage rows within the inclusive date window."""
        return tuple(
            sorted(
                (
                    row
                    for row in self.connector_daily_usage.values()
                    if row.org_id == org_id and start_day <= row.day <= end_day
                ),
                key=lambda r: r.day,
                reverse=True,
            )
        )

    async def query_subagent_daily_usage(
        self,
        *,
        org_id: str,
        start_day: datetime,
        end_day: datetime,
    ) -> Sequence[UsageDailySubagentRow]:
        """Return per-subagent daily usage rows within the inclusive date window."""
        return tuple(
            sorted(
                (
                    row
                    for row in self.subagent_daily_usage.values()
                    if row.org_id == org_id and start_day <= row.day <= end_day
                ),
                key=lambda r: r.day,
                reverse=True,
            )
        )

    async def query_purpose_daily_usage(
        self,
        *,
        org_id: str,
        start_day: datetime,
        end_day: datetime,
    ) -> Sequence[UsageDailyPurposeRow]:
        """Return per-purpose daily usage rows within the inclusive date window."""
        return tuple(
            sorted(
                (
                    row
                    for row in self.purpose_daily_usage.values()
                    if row.org_id == org_id and start_day <= row.day <= end_day
                ),
                key=lambda r: r.day,
                reverse=True,
            )
        )

    async def query_model_call_usage_for_range(
        self,
        *,
        org_id: str | None,
        start: datetime,
        end: datetime,
    ) -> Sequence[RuntimeModelCallUsageRecord]:
        """Return model-call usage records within a time window; ``org_id=None`` is cross-org."""
        return tuple(
            sorted(
                (
                    row
                    for row in self.model_call_usage
                    if (org_id is None or row.org_id == org_id)
                    and start <= row.created_at <= end
                ),
                key=lambda r: r.created_at,
                reverse=True,
            )
        )

    async def list_run_ids_for_agent(
        self,
        *,
        org_id: str,
        agent_id: str,
        start: datetime,
        end: datetime,
    ) -> Sequence[str]:
        """Return run IDs whose runtime context attributes the run to ``agent_id``.

        Strictly tenant-scoped (``org_id`` filter applied first) and
        windowed on ``created_at``. Reads ``agent_id`` off the existing
        ``RunRecord.runtime_context.trace_metadata`` JSON field — no new
        column, no new write path.
        """

        if not agent_id:
            return ()
        matches: list[tuple[datetime, str]] = []
        for run in self.runs.values():
            if run.org_id != org_id:
                continue
            if not (start <= run.created_at <= end):
                continue
            trace_metadata = getattr(run.runtime_context, "trace_metadata", None)
            if not isinstance(trace_metadata, dict):
                continue
            run_agent_id = trace_metadata.get("agent_id")
            if run_agent_id == agent_id:
                matches.append((run.created_at, run.run_id))
        matches.sort(key=lambda entry: entry[0], reverse=True)
        return tuple(run_id for _, run_id in matches)

    async def list_audit_log_events(
        self,
        *,
        org_id: str,
        after_seq: int = 0,
        limit: int = 50,
        action_prefix: str | None = None,
        actor_user_id: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> Sequence[dict[str, object]]:
        """Return paginated audit log events for an org, filtered and ordered by seq/created_at."""

        rows: list[dict[str, object]] = []
        for _event_type, record in self.audit_log:
            if record.get("org_id") != org_id:
                continue
            seq = int(record.get("seq") or 0)
            if seq <= after_seq:
                continue
            action = str(record.get("action") or "")
            if action_prefix is not None and not action.startswith(action_prefix):
                continue
            actor = record.get("user_id")
            if actor_user_id is not None and actor != actor_user_id:
                continue
            created_at_value = record.get("created_at")
            if isinstance(created_at_value, str):
                try:
                    created_at_value = datetime.fromisoformat(created_at_value)
                except ValueError:
                    created_at_value = None
            if since is not None and (
                not isinstance(created_at_value, datetime) or created_at_value < since
            ):
                continue
            if until is not None and (
                not isinstance(created_at_value, datetime) or created_at_value >= until
            ):
                continue
            rows.append(dict(record))
        rows.sort(
            key=lambda r: (
                r.get("created_at") or "",
                int(r.get("seq") or 0),
            ),
            reverse=True,
        )
        return tuple(rows[:limit])

    async def query_run_usage(
        self,
        *,
        org_id: str,
        run_id: str,
    ) -> RuntimeRunUsageRecord | None:
        """Return the usage record for a single run, scoped by org."""
        record = self.run_usage.get(run_id)
        if record is None or record.org_id != org_id:
            return None
        return record

    async def query_run_usage_for_range(
        self,
        *,
        org_id: str | None,
        user_id: str | None,
        start: datetime,
        end: datetime,
    ) -> Sequence[RuntimeRunUsageRecord]:
        """Return run usage records within a completion-time window; ``None`` params are cross-tenant."""
        return tuple(
            sorted(
                (
                    row
                    for row in self.run_usage.values()
                    if (org_id is None or row.org_id == org_id)
                    and (user_id is None or row.user_id == user_id)
                    and start <= row.completed_at <= end
                    and (user_id is None or row.pii_purged_at is None)
                ),
                key=lambda r: r.completed_at,
                reverse=True,
            )
        )

    async def query_top_conversations(
        self,
        *,
        org_id: str,
        user_id: str,
        start: datetime,
        end: datetime,
        limit: int,
    ) -> Sequence[UsageConversationAggregateRecord]:
        """Return the top conversations by token usage within a time window, ranked descending."""
        aggregates: dict[str, UsageConversationAggregateRecord] = {}
        for row in self.run_usage.values():
            if (
                row.org_id != org_id
                or row.user_id != user_id
                or not (start <= row.completed_at <= end)
                or row.pii_purged_at is not None
            ):
                continue
            current = aggregates.get(row.conversation_id)
            cost_micro_usd = row.cost_micro_usd
            if current is None:
                conversation = self.conversations.get(row.conversation_id)
                aggregates[row.conversation_id] = UsageConversationAggregateRecord(
                    conversation_id=row.conversation_id,
                    title=conversation.title if conversation is not None else None,
                    input_tokens=row.input_tokens,
                    output_tokens=row.output_tokens,
                    cached_input_tokens=row.cached_input_tokens,
                    total_tokens=row.total_tokens,
                    runs_count=1,
                    cost_micro_usd=cost_micro_usd,
                )
                continue
            aggregates[row.conversation_id] = current.model_copy(
                update={
                    "input_tokens": current.input_tokens + row.input_tokens,
                    "output_tokens": current.output_tokens + row.output_tokens,
                    "cached_input_tokens": current.cached_input_tokens
                    + row.cached_input_tokens,
                    "total_tokens": current.total_tokens + row.total_tokens,
                    "runs_count": current.runs_count + 1,
                    "cost_micro_usd": self._sum_optional_cost(
                        current.cost_micro_usd, cost_micro_usd
                    ),
                }
            )
        ranked = sorted(
            aggregates.values(), key=lambda item: item.total_tokens, reverse=True
        )
        return tuple(ranked[:limit])

    @staticmethod
    def _sum_optional_cost(left: int | None, right: int | None) -> int | None:
        """Sum two optional cost values; ``None`` is treated as zero for the non-None operand."""
        if left is None:
            return right
        if right is None:
            return left
        return left + right

    async def query_model_call_usage_for_run(
        self,
        *,
        org_id: str,
        run_id: str,
    ) -> Sequence[RuntimeModelCallUsageRecord]:
        """Return all model-call usage records for a single run."""
        return tuple(
            row
            for row in self.model_call_usage
            if row.org_id == org_id and row.run_id == run_id
        )

    async def query_latest_run_usage_for_conversation(
        self,
        *,
        org_id: str,
        user_id: str,
        conversation_id: str,
    ) -> RuntimeRunUsageRecord | None:
        """Return the most recently completed run usage record for a conversation."""
        candidates = [
            row
            for row in self.run_usage.values()
            if row.org_id == org_id
            and row.user_id == user_id
            and row.conversation_id == conversation_id
            and row.pii_purged_at is None
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda r: r.completed_at)

    async def query_compression_events_for_run(
        self,
        *,
        org_id: str,
        run_id: str,
    ) -> Sequence[CompressionEventRecord]:
        """Return compression events for a single run in creation order."""
        return tuple(
            sorted(
                (
                    event
                    for event in self.compression_events
                    if event.org_id == org_id and event.run_id == run_id
                ),
                key=lambda e: e.created_at,
            )
        )

    # ------------------------------------------------------------------
    # Budgets.
    # ------------------------------------------------------------------

    async def lookup_budgets_for_run(
        self,
        *,
        org_id: str,
        user_id: str,
        now: datetime | None = None,
    ) -> Sequence[BudgetWithState]:
        """Return all active budgets for an org/user pair with current-period spend, including active reservations."""
        from datetime import date, datetime as _datetime, timezone

        from agent_runtime.budgets.period import BudgetPeriodCalculator

        if now is None:
            now = _datetime.now(timezone.utc)
        results: list[BudgetWithState] = []
        for budget in self.budgets.values():
            if budget.org_id != org_id:
                continue
            if budget.scope.value == "user" and budget.user_id != user_id:
                continue
            window = BudgetPeriodCalculator.window(budget.period, now=now)
            state_key = (budget.id, window.period_start.isoformat())
            state = self.budget_states.get(state_key)
            if state is not None:
                # Inflate by active (unconsumed) reservations against
                # the same period — matches the postgres query semantics.
                reserved_micro = sum(
                    r.reserved_micro_usd
                    for r in self.budget_reservations.values()
                    if r.budget_id == budget.id
                    and r.period_start == window.period_start
                    and r.consumed_at is None
                )
                reserved_tokens = sum(
                    r.reserved_tokens
                    for r in self.budget_reservations.values()
                    if r.budget_id == budget.id
                    and r.period_start == window.period_start
                    and r.consumed_at is None
                )
                state = state.model_copy(
                    update={
                        "current_spend_micro_usd": state.current_spend_micro_usd
                        + reserved_micro,
                        "current_spend_tokens": state.current_spend_tokens
                        + reserved_tokens,
                    }
                )
            else:
                # No state row yet for this period — synthesize a zero
                # so the enforcer's reservation-aware math still picks
                # up other runs' reservations against this fresh period.
                reserved_micro = sum(
                    r.reserved_micro_usd
                    for r in self.budget_reservations.values()
                    if r.budget_id == budget.id
                    and r.period_start == window.period_start
                    and r.consumed_at is None
                )
                reserved_tokens = sum(
                    r.reserved_tokens
                    for r in self.budget_reservations.values()
                    if r.budget_id == budget.id
                    and r.period_start == window.period_start
                    and r.consumed_at is None
                )
                if reserved_micro > 0 or reserved_tokens > 0:
                    state = BudgetStateRecord(
                        budget_id=budget.id,
                        period_start=window.period_start,
                        period_end=window.period_end,
                        current_spend_micro_usd=reserved_micro,
                        current_spend_tokens=reserved_tokens,
                    )
            results.append(BudgetWithState(budget=budget, state=state))
        # Sort for determinism in tests.
        results.sort(key=lambda e: e.budget.id)
        # Suppress unused warnings if branch is dead.
        _ = (date, BudgetEnforcement)
        return tuple(results)

    async def charge_budget(
        self,
        *,
        budget_id: str,
        period_start,
        period_end,
        delta_micro_usd: int,
        delta_tokens: int,
        run_id: str,
        now,
    ) -> ChargeOutcome:
        """Apply a spend delta to a budget period state; idempotent on run_id."""
        key = (budget_id, period_start.isoformat())
        state = self.budget_states.get(key)
        if state is None:
            state = BudgetStateRecord(
                budget_id=budget_id,
                period_start=period_start,
                period_end=period_end,
                current_spend_micro_usd=0,
                current_spend_tokens=0,
            )
        if state.last_charged_run_id == run_id:
            return ChargeOutcome.IDEMPOTENT_NOOP
        self.budget_states[key] = state.model_copy(
            update={
                "current_spend_micro_usd": state.current_spend_micro_usd
                + delta_micro_usd,
                "current_spend_tokens": state.current_spend_tokens + delta_tokens,
                "row_version": state.row_version + 1,
                "last_charged_run_id": run_id,
                "updated_at": now,
            }
        )
        return ChargeOutcome.APPLIED

    async def reserve_budget(
        self,
        *,
        budget_id: str,
        period_start,
        run_id: str,
        reserved_micro_usd: int,
        reserved_tokens: int,
        now,
    ) -> BudgetReservationRecord | None:
        """Create a budget reservation for a run, or return ``None`` if one already exists.

        Idempotent on (budget_id, run_id) — a second call for the same active reservation
        returns ``None`` rather than a duplicate row.
        """
        existing = next(
            (
                r
                for r in self.budget_reservations.values()
                if r.budget_id == budget_id
                and r.run_id == run_id
                and r.consumed_at is None
            ),
            None,
        )
        if existing is not None:
            return None
        from agent_runtime.budgets.reservations import BudgetReservationManager

        record = BudgetReservationRecord(
            budget_id=budget_id,
            period_start=period_start,
            run_id=run_id,
            reserved_micro_usd=reserved_micro_usd,
            reserved_tokens=reserved_tokens,
            expires_at=BudgetReservationManager.expires_at(now=now, ttl_seconds=60),
        )
        self.budget_reservations[record.reservation_id] = record
        return record

    async def consume_budget_reservation(
        self,
        *,
        reservation_id: str,
        now,
    ) -> None:
        """Mark a reservation as consumed; idempotent if already consumed or absent."""
        record = self.budget_reservations.get(reservation_id)
        if record is None or record.consumed_at is not None:
            return
        self.budget_reservations[reservation_id] = record.model_copy(
            update={"consumed_at": now}
        )

    async def reap_expired_budget_reservations(self, *, now) -> int:
        """Delete unconsumed reservations past their expiry; returns the count purged."""
        purged = 0
        for reservation_id, record in list(self.budget_reservations.items()):
            if record.consumed_at is None and record.expires_at < now:
                del self.budget_reservations[reservation_id]
                purged += 1
        return purged

    async def list_budgets(self, *, org_id: str) -> Sequence[BudgetRecord]:
        """Return all budgets for an org, ordered by creation time descending."""
        return tuple(
            sorted(
                (b for b in self.budgets.values() if b.org_id == org_id),
                key=lambda b: b.created_at,
                reverse=True,
            )
        )

    async def list_tool_budgets_for_org(
        self, *, org_id: str
    ) -> Sequence[ToolBudgetRecord]:
        """Return per-tool budgets visible to ``org_id`` (org rows + global).

        Mirrors the ``runtime_tool_budgets`` SELECT used by the postgres
        adapter (``WHERE org_id = %s OR org_id IS NULL``). The middleware
        does its own most-specific-wins resolution; this method only
        delivers the raw rows.
        """

        return tuple(
            b
            for b in self.tool_budgets.values()
            if b.org_id == org_id or b.org_id is None
        )

    async def get_budget(self, *, org_id: str, budget_id: str) -> BudgetRecord | None:
        """Return a budget scoped by org, or ``None`` if not found."""
        record = self.budgets.get(budget_id)
        if record is None or record.org_id != org_id:
            return None
        return record

    async def create_budget(self, record: BudgetRecord) -> BudgetRecord:
        """Persist a new budget, enforcing the unique (org, user, scope, period) constraint."""
        # Enforce UNIQUE (org_id, COALESCE(user_id,'<org>'), scope, period).
        for existing in self.budgets.values():
            if (
                existing.org_id == record.org_id
                and (existing.user_id or "<org>") == (record.user_id or "<org>")
                and existing.scope == record.scope
                and existing.period == record.period
            ):
                raise ValueError("budget already exists for that scope/period")
        self.budgets[record.id] = record
        return record

    async def update_budget(self, record: BudgetRecord) -> BudgetRecord:
        """Replace an existing budget record; raises ``KeyError`` if not found."""
        if record.id not in self.budgets:
            raise KeyError(record.id)
        self.budgets[record.id] = record
        return record

    async def delete_budget(self, *, org_id: str, budget_id: str) -> None:
        """Delete a budget and cascade-remove its state and reservation rows."""
        record = self.budgets.get(budget_id)
        if record is None or record.org_id != org_id:
            return
        del self.budgets[budget_id]
        # Cascade — match the FK ON DELETE CASCADE in the migration.
        self.budget_states = {
            key: state
            for key, state in self.budget_states.items()
            if state.budget_id != budget_id
        }
        self.budget_reservations = {
            rid: r
            for rid, r in self.budget_reservations.items()
            if r.budget_id != budget_id
        }
        # Suppress unused import warnings if BudgetStatus isn't used here yet.
        _ = BudgetStatus

    async def list_retention_policies(self, *, org_id: str) -> Sequence:
        """Return all retention policies for an org."""
        return tuple(self.retention_policies.get(org_id, ()))

    async def upsert_retention_policy(self, record):  # type: ignore[no-untyped-def]
        """Insert or replace the retention policy for a (scope, resource_id, kind) triple."""
        bucket = list(self.retention_policies.get(record.org_id, ()))
        bucket = [
            row
            for row in bucket
            if (row.scope, row.resource_id, row.kind)
            != (record.scope, record.resource_id, record.kind)
        ]
        bucket.append(record)
        self.retention_policies[record.org_id] = tuple(bucket)
        return record

    async def delete_retention_policy(self, *, org_id: str, policy_id: str) -> None:
        """Remove a retention policy by id."""
        bucket = self.retention_policies.get(org_id, ())
        self.retention_policies[org_id] = tuple(
            row for row in bucket if row.id != policy_id
        )

    async def append_events_batch(
        self, events: Sequence[RuntimeEventDraft]
    ) -> Sequence[RuntimeEventEnvelope]:
        """Append N events as one logical operation, returning envelopes in input order.

        Empty input returns ``()`` without side effects. All events must share the
        same ``run_id``; a mismatch raises ``ValueError`` to surface coalescer bugs early.
        """

        if not events:
            return ()
        if any(event.event_id is not None for event in events):
            raise ValueError(
                "stable event ids require append_event; batch append is reserved "
                "for newly allocated stream events"
            )
        run_ids = {event.run_id for event in events}
        if len(run_ids) > 1:
            raise ValueError(
                "append_events_batch requires all events to share one run_id; "
                f"saw {len(run_ids)}."
            )
        envelopes: list[RuntimeEventEnvelope] = []
        for event in events:
            envelopes.append(await self.append_event(event))
        return tuple(envelopes)

    async def append_event(self, event: RuntimeEventDraft) -> RuntimeEventEnvelope:
        """Append one event with a monotonically increasing run sequence number.

        The run's ``latest_sequence_no`` cursor is advanced in-line, mirroring
        the Postgres adapter. The H3 monotonic guard inside
        :meth:`set_run_latest_sequence` keeps the cursor advance safe.
        """

        events = self.events_by_run.setdefault(event.run_id, [])
        if event.event_id is not None:
            existing = next(
                (item for item in events if item.event_id == event.event_id),
                None,
            )
            if existing is not None:
                if event.matches_envelope(existing):
                    return existing
                raise RuntimeEventIdempotencyConflict(
                    run_id=event.run_id,
                    event_id=event.event_id,
                )
        envelope_kwargs: dict[str, object] = {}
        if event.event_id is not None:
            envelope_kwargs["event_id"] = event.event_id
        if event.created_at is not None:
            envelope_kwargs["created_at"] = event.created_at
        envelope = RuntimeEventEnvelope(
            run_id=event.run_id,
            conversation_id=event.conversation_id,
            sequence_no=len(events) + 1,
            source=event.source,
            event_type=event.event_type,
            trace_id=event.trace_id,
            parent_event_id=event.parent_event_id,
            span_id=event.span_id,
            parent_span_id=event.parent_span_id,
            parent_task_id=event.parent_task_id,
            task_id=event.task_id,
            subagent_id=event.subagent_id,
            display_title=event.display_title,
            summary=event.summary,
            status=event.status,
            activity_kind=event.activity_kind
            or RuntimeEventPresentationProjector.activity_kind_for(
                event_type=event.event_type,
                source=event.source,
            ),
            visibility=event.visibility,
            redaction_state=event.redaction_state,
            presentation=event.presentation,
            payload=event.payload,
            metadata=event.metadata,
            **envelope_kwargs,
        )
        events.append(envelope)
        if event.run_id in self.runs:
            await self.set_run_latest_sequence(
                run_id=event.run_id,
                latest_sequence_no=envelope.sequence_no,
            )
        return envelope

    async def list_events_after(
        self,
        *,
        org_id: str,
        run_id: str,
        after_sequence: int,
    ) -> Sequence[RuntimeEventEnvelope]:
        """Return persisted events after a sequence number."""

        run = await self.get_run(org_id=org_id, run_id=run_id)
        if run is None:
            return ()
        return tuple(
            event
            for event in self.events_by_run.get(run_id, ())
            if event.sequence_no > after_sequence
        )

    async def get_latest_sequence(self, *, run_id: str) -> int:
        """Return latest persisted sequence number for a run."""

        return len(self.events_by_run.get(run_id, ()))

    async def enqueue_run(self, command: RuntimeRunCommand) -> None:
        """Enqueue a run command for deterministic worker tests."""

        self.run_commands.append(command)
        self._register_command(
            command_id=command.command_id,
            command_type=PersistenceValues.EventType.RUN_REQUESTED,
            org_id=command.org_id,
            run_id=command.run_id,
            approval_id=None,
            payload=command.model_dump(mode="json"),
        )

    async def enqueue_cancel(self, command: RuntimeCancelCommand) -> None:
        """Enqueue a cancel command for deterministic worker tests."""

        self.cancel_commands.append(command)
        self._register_command(
            command_id=command.command_id,
            command_type=PersistenceValues.EventType.RUN_CANCEL_REQUESTED,
            org_id=command.org_id,
            run_id=command.run_id,
            approval_id=None,
            payload=command.model_dump(mode="json"),
        )

    async def enqueue_approval_resolved(
        self, command: RuntimeApprovalResolvedCommand
    ) -> None:
        """Enqueue an approval resolution command for deterministic worker tests."""

        self.approval_commands.append(command)
        self._register_command(
            command_id=command.command_id,
            command_type=PersistenceValues.EventType.APPROVAL_RESOLVED,
            org_id=command.org_id,
            run_id=command.run_id,
            approval_id=command.approval_id,
            payload=command.model_dump(mode="json"),
        )

    async def enqueue_stage_commit(self, command: RuntimeStageCommitCommand) -> None:
        """Enqueue a staged-write commit command for deterministic worker tests (PRD-D2)."""

        self.stage_commit_commands.append(command)
        self._register_command(
            command_id=command.command_id,
            command_type=PersistenceValues.EventType.STAGE_COMMIT_REQUESTED,
            org_id=command.org_id,
            run_id=command.run_id,
            approval_id=None,
            payload=command.model_dump(mode="json"),
        )

    async def claim_next(
        self,
        *,
        worker_id: str,
        lock_expires_at: datetime,
    ) -> RuntimeWorkerClaim | None:
        """Claim the next available queued command, respecting unexpired locks."""

        now = datetime.now(timezone.utc)
        for command_id in self._queue_order:
            status_value = self._queue_statuses[command_id]
            if status_value in {OutboxStatus.COMPLETED, OutboxStatus.DEAD_LETTER}:
                continue
            if self._queue_available_at[command_id] > now:
                continue
            active_claim = self._queue_claims.get(command_id)
            if active_claim is not None and active_claim.lock_expires_at > now:
                continue
            claim = self._claim_command(
                command_id=command_id,
                worker_id=worker_id,
                lock_expires_at=lock_expires_at,
            )
            self._queue_claims[command_id] = claim
            self._queue_statuses[command_id] = OutboxStatus.CLAIMED
            return claim
        return None

    async def mark_complete(self, *, result: RuntimeWorkerResult) -> None:
        """Mark a claimed command complete."""

        self._queue_statuses[result.command_id] = OutboxStatus.COMPLETED
        self._queue_claims.pop(result.command_id, None)

    async def mark_retry(self, *, result: RuntimeWorkerResult) -> None:
        """Release a command so another worker may claim it later."""

        self._queue_statuses[result.command_id] = OutboxStatus.RETRY
        self._queue_available_at[result.command_id] = (
            result.retry_available_at or datetime.now(timezone.utc)
        )
        self._queue_claims.pop(result.command_id, None)

    async def mark_dead_letter(self, *, result: RuntimeWorkerResult) -> None:
        """Mark a command permanently failed after retries are exhausted."""

        self._queue_statuses[result.command_id] = OutboxStatus.DEAD_LETTER
        self._queue_claims.pop(result.command_id, None)

    async def seed_approval_request(
        self, record: ApprovalRequestRecord
    ) -> ApprovalRequestRecord:
        """Add a pending approval request for API tests or future worker fakes."""

        self.approval_requests[record.approval_id] = record
        return record

    def _ensure_run_idempotency_match(
        self,
        *,
        key: tuple[str, str, str],
        request: CreateRunRequest,
    ) -> None:
        """Raise a 409 error if a prior idempotency key was used with different inputs."""
        fingerprint = self._run_idempotency_fingerprint[key]
        if fingerprint != (request.conversation_id, request.user_input):
            raise RuntimeApiError(
                RuntimeErrorCode.VALIDATION_ERROR,
                Messages.Error.IDEMPOTENCY_CONFLICT,
                http_status=status.HTTP_409_CONFLICT,
                retryable=False,
                correlation_id=request.runtime_context.trace_id,
            )

    def _register_command(
        self,
        *,
        command_id: str,
        command_type: str,
        org_id: str,
        run_id: str,
        approval_id: str | None,
        payload: dict[str, object],
    ) -> None:
        """Add a command to the outbox queue in PENDING state."""
        self._queue_order.append(command_id)
        self._queue_payloads[command_id] = {
            **payload,
            _Fields.COMMAND_ID: command_id,
            _Fields.COMMAND_TYPE: command_type,
            _Fields.ORG_ID: org_id,
            _Fields.RUN_ID: run_id,
            _Fields.APPROVAL_ID: approval_id,
        }
        self._queue_statuses[command_id] = OutboxStatus.PENDING
        self._queue_attempts[command_id] = 0
        self._queue_available_at[command_id] = datetime.now(timezone.utc)

    def _claim_command(
        self,
        *,
        command_id: str,
        worker_id: str,
        lock_expires_at: datetime,
    ) -> RuntimeWorkerClaim:
        """Build a claim record and increment the attempt counter for a command."""
        payload = self._queue_payloads[command_id]
        self._queue_attempts[command_id] += 1
        return RuntimeWorkerClaim(
            command_id=command_id,
            command_type=str(payload[_Fields.COMMAND_TYPE]),
            org_id=str(payload[_Fields.ORG_ID]),
            run_id=str(payload[_Fields.RUN_ID]),
            approval_id=payload[_Fields.APPROVAL_ID]
            if isinstance(payload[_Fields.APPROVAL_ID], str)
            else None,
            locked_by=worker_id,
            lock_expires_at=lock_expires_at,
            attempts=self._queue_attempts[command_id],
            payload=payload,
        )
