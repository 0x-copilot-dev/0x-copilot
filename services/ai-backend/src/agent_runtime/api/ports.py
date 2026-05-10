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
from agent_runtime.persistence.records import (
    BudgetRecord,
    BudgetReservationRecord,
    BudgetWithState,
    ChargeOutcome,
    CompressionEventRecord,
    ModelPricingRecord,
    RetentionPolicyRecord,
    RuntimeModelCallUsageRecord,
    RuntimeRunUsageRecord,
    RuntimeWorkerClaim,
    RuntimeWorkerResult,
    ToolBudgetRecord,
    UsageConversationAggregateRecord,
    UsageDailyConnectorRow,
    UsageDailyOrgRow,
    UsageDailyUserRow,
)


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

    def list_tool_budgets_for_org(self, *, org_id: str) -> Sequence[ToolBudgetRecord]:
        """Return per-tool budgets visible to ``org_id`` (B8).

        Includes both the org's own rows (``org_id = %s``) and the global
        seed/default rows (``org_id IS NULL``). The
        :class:`ToolBudgetMiddleware` performs its own most-specific-wins
        resolution against this snapshot.
        """

    # ------------------------------------------------------------------
    # Usage + pricing (B1, B2, B3, B4).
    #
    # Sync mirror of :class:`AsyncPersistencePort`'s usage surface, kept
    # in lockstep so the bridge in ``runtime_adapters.async_wrappers``
    # can wrap each call without ``# type: ignore``. Idempotency rules
    # match the async port: ``record_run_usage`` on ``run_id``,
    # ``record_model_call_usage`` on the row's UUID id.
    # ------------------------------------------------------------------

    def record_run_usage(self, record: RuntimeRunUsageRecord) -> None:
        """Idempotent write of a per-run usage row (B1)."""

    def record_model_call_usage(self, record: RuntimeModelCallUsageRecord) -> None:
        """Append a per-LLM-call usage row (B2)."""

    def update_run_usage_cost(
        self,
        *,
        run_id: str,
        cost_micro_usd: int,
        pricing_id: str,
        pricing_version: str,
    ) -> None:
        """Stamp computed cost onto an existing run-usage row (B3)."""

    def update_model_call_usage_cost(
        self,
        *,
        usage_id: str,
        cost_micro_usd: int,
        pricing_id: str,
        pricing_version: str,
    ) -> None:
        """Stamp computed cost onto an existing per-call usage row (B3)."""

    def upsert_pricing(self, record: ModelPricingRecord) -> ModelPricingRecord:
        """Insert or update a pricing row keyed by (provider, model, region) (B3).

        Implementations close the previous active row by setting
        ``effective_until`` when a row with a later ``effective_from``
        replaces it, so the partial unique index on ``effective_until IS
        NULL`` stays satisfied.
        """

    def lookup_pricing(
        self,
        *,
        provider: str,
        model_name: str,
        region: str,
        at: datetime,
    ) -> ModelPricingRecord | None:
        """Return the pricing row in effect for ``at`` or ``None`` (B3)."""

    def list_runs_missing_cost(
        self,
        *,
        limit: int,
        cursor: str | None = None,
    ) -> Sequence[RuntimeRunUsageRecord]:
        """Return run-usage rows where ``cost_micro_usd IS NULL`` for backfill (B3)."""

    def upsert_user_daily_usage(self, row: UsageDailyUserRow) -> None:
        """Idempotent UPSERT of one daily per-user rollup row (B4)."""

    def upsert_org_daily_usage(self, row: UsageDailyOrgRow) -> None:
        """Idempotent UPSERT of one daily per-org rollup row (B4)."""

    def upsert_connector_daily_usage(self, row: UsageDailyConnectorRow) -> None:
        """Idempotent UPSERT of one daily per-connector rollup row (PR 7.2)."""

    def query_user_daily_usage(
        self,
        *,
        org_id: str,
        user_id: str,
        start_day: datetime,
        end_day: datetime,
    ) -> Sequence[UsageDailyUserRow]:
        """Read per-user rollup rows in ``[start_day, end_day]`` (B4)."""

    def query_org_daily_usage(
        self,
        *,
        org_id: str,
        start_day: datetime,
        end_day: datetime,
    ) -> Sequence[UsageDailyOrgRow]:
        """Read per-org rollup rows in ``[start_day, end_day]`` (B4)."""

    def query_connector_daily_usage(
        self,
        *,
        org_id: str,
        start_day: datetime,
        end_day: datetime,
    ) -> Sequence[UsageDailyConnectorRow]:
        """Read per-connector rollup rows in ``[start_day, end_day]`` (PR 7.2)."""

    def query_model_call_usage_for_range(
        self,
        *,
        org_id: str | None,
        start: datetime,
        end: datetime,
    ) -> Sequence[RuntimeModelCallUsageRecord]:
        """Scan per-LLM-call usage rows for the connector rollup loop +
        cold-start fallback (PR 7.2).

        ``org_id=None`` is the rollup-loop signal to scan across tenants.
        """

    def list_audit_log_events(
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
        """Paginated read across ``runtime_audit_log`` (PR 7.1)."""

    def query_last_completed_tool_connector_slug(
        self,
        *,
        org_id: str,
        run_id: str,
        before: datetime,
    ) -> str | None:
        """Return the connector_slug of the most recent completed tool
        invocation on ``run_id`` whose ``completed_at`` is strictly before
        ``before`` (PR 7.2 attribution rule).
        """

    def query_run_usage(
        self,
        *,
        org_id: str,
        run_id: str,
    ) -> RuntimeRunUsageRecord | None:
        """Look up a single run-usage row scoped to org (B4)."""

    def query_run_usage_for_range(
        self,
        *,
        org_id: str | None,
        user_id: str | None,
        start: datetime,
        end: datetime,
    ) -> Sequence[RuntimeRunUsageRecord]:
        """Read raw run-usage rows for the rollup loop + cold-start fallback.

        ``org_id=None`` is the rollup-loop signal to scan across tenants.
        """

    def query_top_conversations(
        self,
        *,
        org_id: str,
        user_id: str,
        start: datetime,
        end: datetime,
        limit: int,
    ) -> Sequence[UsageConversationAggregateRecord]:
        """Return top conversation aggregates by total tokens for the range."""

    def query_model_call_usage_for_run(
        self,
        *,
        org_id: str,
        run_id: str,
    ) -> Sequence[RuntimeModelCallUsageRecord]:
        """Return per-LLM-call rows for a run, scoped by org (B4 / B5)."""

    def query_latest_run_usage_for_conversation(
        self,
        *,
        org_id: str,
        user_id: str,
        conversation_id: str,
    ) -> RuntimeRunUsageRecord | None:
        """Return the most recently-completed run usage row for a conversation (B5).

        Excludes rows where ``pii_purged_at IS NOT NULL``. Returns ``None``
        when the conversation has no completed runs yet.
        """

    def query_compression_events_for_run(
        self,
        *,
        org_id: str,
        run_id: str,
    ) -> Sequence[CompressionEventRecord]:
        """Return compression events for a run, ordered by ``created_at`` (B5)."""

    # ------------------------------------------------------------------
    # Budgets (B7).
    #
    # ``lookup_budgets_for_run`` is the hot path on the worker preflight.
    # ``charge_budget`` is post-run; CAS on ``row_version`` AND idempotency
    # on ``last_charged_run_id`` mean the same run cannot double-charge
    # under retry.
    # ------------------------------------------------------------------

    def lookup_budgets_for_run(
        self,
        *,
        org_id: str,
        user_id: str,
        now: datetime | None = None,
    ) -> Sequence[BudgetWithState]:
        """Active budgets matching ``(org_id, user_id)`` plus their state.

        When ``now`` is provided, implementations MUST use it for period
        window computation instead of wall-clock ``datetime.now()`` so
        the enforcer keeps one consistent clock across preflight + reserve.
        """

    def charge_budget(
        self,
        *,
        budget_id: str,
        period_start: object,  # datetime.date — typed as object to avoid an extra import
        period_end: object,
        delta_micro_usd: int,
        delta_tokens: int,
        run_id: str,
        now: datetime,
    ) -> ChargeOutcome:
        """Apply a charge to a budget's state via CAS UPDATE.

        Returns :class:`ChargeOutcome.IDEMPOTENT_NOOP` when the same
        ``run_id`` has already been charged, :class:`APPLIED` on a fresh
        write, and :class:`EXHAUSTED_RETRIES` when row_version drift does
        not stabilize within the adapter's internal retry budget.
        """

    def reserve_budget(
        self,
        *,
        budget_id: str,
        period_start: object,  # datetime.date
        run_id: str,
        reserved_micro_usd: int,
        reserved_tokens: int,
        now: datetime,
    ) -> BudgetReservationRecord | None:
        """Create a pre-flight reservation, idempotent on (budget_id, run_id).

        Returns ``None`` when the run already holds an unconsumed
        reservation against this budget.
        """

    def consume_budget_reservation(self, *, reservation_id: str, now: datetime) -> None:
        """Mark a reservation consumed so the reaper skips it."""

    def reap_expired_budget_reservations(self, *, now: datetime) -> int:
        """Purge reservations whose ``expires_at < now`` and are unconsumed.

        Returns the number purged, for observability.
        """

    def list_budgets(self, *, org_id: str) -> Sequence[BudgetRecord]:
        """List configured budgets for an org (admin endpoint)."""

    def get_budget(self, *, org_id: str, budget_id: str) -> BudgetRecord | None:
        """Fetch one budget scoped to an org."""

    def create_budget(self, record: BudgetRecord) -> BudgetRecord:
        """Insert a new budget."""

    def update_budget(self, record: BudgetRecord) -> BudgetRecord:
        """Update mutable fields on an existing budget."""

    def delete_budget(self, *, org_id: str, budget_id: str) -> None:
        """Hard-delete a budget (cascades to state + reservations)."""

    # ------------------------------------------------------------------
    # Retention (C8).
    #
    # The cross-tenant sweep methods (``list_retention_orgs``,
    # ``sweep_retention_kind``) are async-only — they live on
    # :class:`AsyncPersistencePort` and are called by the worker's
    # retention sweeper, which runs only against the async backend.
    # ------------------------------------------------------------------

    def list_retention_policies(
        self, *, org_id: str
    ) -> Sequence[RetentionPolicyRecord]:
        """Return every retention policy for an org (small list, no paging)."""

    def upsert_retention_policy(
        self, record: RetentionPolicyRecord
    ) -> RetentionPolicyRecord:
        """Idempotent insert or update keyed by ``(org_id, scope, resource_id, kind)``."""

    def delete_retention_policy(self, *, org_id: str, policy_id: str) -> None:
        """Remove one retention policy."""


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
