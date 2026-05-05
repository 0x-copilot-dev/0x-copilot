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

from agent_runtime.persistence.records import (
    BudgetRecord,
    BudgetReservationRecord,
    BudgetWithState,
    ChargeOutcome,
    CompressionEventRecord,
    ModelPricingRecord,
    RetentionKind,
    RetentionPolicyRecord,
    RetentionSweepOutcome,
    RuntimeModelCallUsageRecord,
    RuntimeRunUsageRecord,
    RuntimeWorkerClaim,
    RuntimeWorkerResult,
    UsageDailyOrgRow,
    UsageDailyUserRow,
)
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

    async def get_conversation_for_org(
        self,
        *,
        org_id: str,
        conversation_id: str,
    ) -> ConversationRecord | None:
        """Return a conversation for the tenant scope, ignoring user ownership.

        Admin-override path (PR 1.2.1). Authorization is enforced by the
        service layer; this port only enforces tenant isolation. Returns
        ``None`` for cross-tenant access.
        """

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

    async def update_conversation_connectors(
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
        scope. Implementations merge ``scopes_patch`` into the stored
        column atomically (keys present overwrite — including ``None`` to
        pause; keys absent are left untouched). Caller computes diff for
        audit before invocation.
        """

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
        """Atomic parent→FORWARDED + child INSERT for two-stage approvals.

        See sync ``PersistencePort.forward_approval_request`` for the
        contract. Implementations run both writes in a single transaction
        so partial chains never persist on failure (PR 1.4).
        """

    async def get_approval_request(
        self,
        *,
        org_id: str,
        approval_id: str,
    ) -> ApprovalRequestRecord | None:
        """Return a pending or resolved approval request."""

    async def list_assigned_approvals(
        self,
        *,
        org_id: str,
        requested_by_user_id: str,
        status: str,
        limit: int,
        cursor: tuple[datetime, str] | None,
    ) -> Sequence[ApprovalRequestRecord]:
        """Recipient inbox query (PR 1.4.1). See sync port for contract."""

    async def list_pending_expired_approvals(
        self,
        *,
        now: datetime,
        limit: int,
    ) -> Sequence[ApprovalRequestRecord]:
        """Sweeper expiry-pass query (PR 1.4.1)."""

    async def list_pending_approvals_for_membership_audit(
        self,
        *,
        limit: int,
    ) -> Sequence[ApprovalRequestRecord]:
        """Sweeper membership-cascade query (PR 1.4.1)."""

    async def write_audit_log(self, *, event_type: str, record: object) -> None:
        """Append an audit record for security-relevant actions."""

    async def list_audit_log_for_export(
        self,
        *,
        after_id: str | None,
        limit: int,
    ) -> Sequence[dict]:
        """Cross-tenant audit log read for the C9 SIEM cursor.

        Worker-role only — same trust contract as ``query_run_usage_for_range
        (org_id=None)``. Returns rows ordered by ``(created_at, id)`` ascending
        so the SIEM pump's cursor is monotonic.
        """

    async def delete_user_history(
        self,
        *,
        org_id: str,
        user_id: str,
        reason: str | None = None,
    ) -> HistoryDeletionResponse:
        """Tombstone user-visible history while retaining audit-safe evidence."""

    # ------------------------------------------------------------------
    # Usage + pricing (B1, B2, B3, B4).
    #
    # Writes are best-effort: the run-completion event is the source of
    # truth, the rows below are derived aggregates. ``record_run_usage``
    # is idempotent on ``run_id``; ``record_model_call_usage`` is
    # idempotent on the row's own UUID id (caller dedupes at the source
    # by AIMessage id). Pricing methods underwrite B3's catalog and
    # B4's rollup loop.
    # ------------------------------------------------------------------

    async def record_run_usage(self, record: RuntimeRunUsageRecord) -> None:
        """Idempotent write of a per-run usage row (B1)."""

    async def record_model_call_usage(
        self, record: RuntimeModelCallUsageRecord
    ) -> None:
        """Append a per-LLM-call usage row (B2)."""

    async def update_run_usage_cost(
        self,
        *,
        run_id: str,
        cost_micro_usd: int,
        pricing_id: str,
        pricing_version: str,
    ) -> None:
        """Stamp computed cost onto an existing run-usage row (B3)."""

    async def update_model_call_usage_cost(
        self,
        *,
        usage_id: str,
        cost_micro_usd: int,
        pricing_id: str,
        pricing_version: str,
    ) -> None:
        """Stamp computed cost onto an existing per-call usage row (B3)."""

    async def upsert_pricing(self, record: ModelPricingRecord) -> ModelPricingRecord:
        """Insert or update a pricing row keyed by (provider, model, region) (B3).

        Implementations close the previous active row by setting
        ``effective_until`` when a row with a later ``effective_from``
        replaces it, so the partial unique index on ``effective_until IS
        NULL`` stays satisfied.
        """

    async def lookup_pricing(
        self,
        *,
        provider: str,
        model_name: str,
        region: str,
        at: datetime,
    ) -> ModelPricingRecord | None:
        """Return the pricing row in effect for ``at`` or ``None`` (B3)."""

    async def list_runs_missing_cost(
        self,
        *,
        limit: int,
        cursor: str | None = None,
    ) -> Sequence[RuntimeRunUsageRecord]:
        """Return run-usage rows where ``cost_micro_usd IS NULL`` for backfill (B3)."""

    async def upsert_user_daily_usage(self, row: UsageDailyUserRow) -> None:
        """Idempotent UPSERT of one daily per-user rollup row (B4)."""

    async def upsert_org_daily_usage(self, row: UsageDailyOrgRow) -> None:
        """Idempotent UPSERT of one daily per-org rollup row (B4)."""

    async def query_user_daily_usage(
        self,
        *,
        org_id: str,
        user_id: str,
        start_day: datetime,
        end_day: datetime,
    ) -> Sequence[UsageDailyUserRow]:
        """Read per-user rollup rows in ``[start_day, end_day]`` (B4)."""

    async def query_org_daily_usage(
        self,
        *,
        org_id: str,
        start_day: datetime,
        end_day: datetime,
    ) -> Sequence[UsageDailyOrgRow]:
        """Read per-org rollup rows in ``[start_day, end_day]`` (B4)."""

    async def query_run_usage(
        self,
        *,
        org_id: str,
        run_id: str,
    ) -> RuntimeRunUsageRecord | None:
        """Look up a single run-usage row scoped to org (B4)."""

    async def query_run_usage_for_range(
        self,
        *,
        org_id: str | None,
        user_id: str | None,
        start: datetime,
        end: datetime,
    ) -> Sequence[RuntimeRunUsageRecord]:
        """Read raw run-usage rows for the rollup loop + cold-start fallback.

        ``org_id=None`` is the rollup-loop signal to scan across tenants;
        adapter implementations must restrict to ``app.role='worker'``
        equivalent semantics so the scan matches the operator role
        running the worker.
        """

    async def query_top_conversations(
        self,
        *,
        org_id: str,
        user_id: str,
        start: datetime,
        end: datetime,
        limit: int,
    ) -> Sequence[tuple[str, int]]:
        """Return top conversations by total tokens for the range (B4).

        Each tuple is ``(conversation_id, total_tokens)``.
        """

    async def query_model_call_usage_for_run(
        self,
        *,
        org_id: str,
        run_id: str,
    ) -> Sequence[RuntimeModelCallUsageRecord]:
        """Return per-LLM-call rows for a run, scoped by org (B4 / B5)."""

    async def query_latest_run_usage_for_conversation(
        self,
        *,
        org_id: str,
        user_id: str,
        conversation_id: str,
    ) -> RuntimeRunUsageRecord | None:
        """Return the most recently-completed run usage row for a conversation (B5).

        Used by the ``/v1/agent/conversations/{id}/context`` endpoint to
        answer "where did the tokens go in this conversation". Excludes
        rows where ``pii_purged_at IS NOT NULL`` since the user-visible
        view should not surface purged history. Returns ``None`` when the
        conversation has no completed runs yet.
        """

    async def query_compression_events_for_run(
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
    # It returns the ``BudgetWithState`` join including any active
    # reservations rolled into ``current_spend_*`` (so the enforcer can
    # decide without a second query).
    #
    # ``charge_budget`` is the post-run hook. CAS on ``row_version`` AND
    # idempotency on ``last_charged_run_id`` mean the same run cannot
    # double-charge under retry.
    # ------------------------------------------------------------------

    async def lookup_budgets_for_run(
        self,
        *,
        org_id: str,
        user_id: str,
        now: datetime | None = None,
    ) -> Sequence[BudgetWithState]:
        """Active budgets matching ``(org_id, user_id)`` plus their state.

        When ``now`` is provided, implementations MUST use it for period
        window computation instead of wall-clock ``datetime.now()`` —
        this is what lets the enforcer keep one consistent clock across
        the preflight + reserve sequence and lets unit tests freeze
        time. Postgres adapters use server-clock SQL and may ignore
        ``now`` in production deployments where the round-trip latency
        is negligible.
        """

    async def charge_budget(
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
        write, and :class:`EXHAUSTED_RETRIES` when row_version drift
        does not stabilize within the adapter's internal retry budget.
        """

    async def reserve_budget(
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
        reservation against this budget (idempotent retry path).
        """

    async def consume_budget_reservation(
        self, *, reservation_id: str, now: datetime
    ) -> None:
        """Mark a reservation consumed so the reaper skips it."""

    async def reap_expired_budget_reservations(self, *, now: datetime) -> int:
        """Purge reservations whose ``expires_at < now`` and are unconsumed.

        Returns the number purged, for observability.
        """

    async def list_budgets(self, *, org_id: str) -> Sequence[BudgetRecord]:
        """List configured budgets for an org (admin endpoint)."""

    async def get_budget(self, *, org_id: str, budget_id: str) -> BudgetRecord | None:
        """Fetch one budget scoped to an org."""

    async def create_budget(self, record: BudgetRecord) -> BudgetRecord:
        """Insert a new budget."""

    async def update_budget(self, record: BudgetRecord) -> BudgetRecord:
        """Update mutable fields on an existing budget."""

    async def delete_budget(self, *, org_id: str, budget_id: str) -> None:
        """Hard-delete a budget (cascades to state + reservations)."""

    # ------------------------------------------------------------------
    # Retention (C8).
    #
    # The sweeper runs in the worker process; the API service uses these
    # methods only for the admin CRUD endpoints (out of scope for this
    # PR; operators seed via SQL until A10 RBAC ships).
    # ------------------------------------------------------------------

    async def list_retention_orgs(self) -> Sequence[str]:
        """Return distinct org_ids that have any rows in retention-affected tables.

        The sweeper iterates one org at a time so cross-tenant scope is
        impossible. Worker-role only — same trust contract as
        ``query_run_usage_for_range(org_id=None)``.
        """

    async def list_retention_policies(
        self, *, org_id: str
    ) -> Sequence[RetentionPolicyRecord]:
        """Return every retention policy for an org (small list, no paging)."""

    async def upsert_retention_policy(
        self, record: RetentionPolicyRecord
    ) -> RetentionPolicyRecord:
        """Idempotent insert or update keyed by ``(org_id, scope, resource_id, kind)``."""

    async def delete_retention_policy(self, *, org_id: str, policy_id: str) -> None:
        """Remove one retention policy."""

    async def sweep_retention_kind(
        self,
        *,
        org_id: str,
        kind: RetentionKind,
        ttl_seconds: int,
        dry_run: bool = False,
    ) -> RetentionSweepOutcome:
        """Apply the per-kind retention strategy for one tenant.

        Per kind:

          - ``messages`` / ``events`` / ``memory_items``: tombstone (status
            flip / blank content) for rows older than ttl. Hard delete after
            a 30d grace; today the implementation only tombstones.
          - ``context_payloads``: hard delete where ``retention_until <
            now()`` (the column already exists in the schema and is
            authoritative) — the resolver's ttl is treated as a fallback.
          - ``checkpoints``: keep the latest N per ``(thread_id, namespace)``
            (default 10) plus anything inside the ttl window.

        Resources covered by an active ``runtime_legal_holds`` row are
        skipped and counted in ``skipped_legal_hold``.
        """


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
