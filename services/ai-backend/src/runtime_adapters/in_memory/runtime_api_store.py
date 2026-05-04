"""Deterministic in-memory runtime API ports for local tests and development."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timezone
from typing import Any

from starlette import status

from agent_runtime.api.constants import Messages
from agent_runtime.execution.contracts import RuntimeErrorCode
from agent_runtime.observability.audit_chain import AuditChainSigner
from agent_runtime.persistence.constants import Values as PersistenceValues
from agent_runtime.persistence.records import (
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
    UsageDailyOrgRow,
    UsageDailyUserRow,
)
from runtime_adapters.base import (
    RuntimeAdapterHelpers,
    StatusTransition,
    _Fields,
)
from runtime_api.http.errors import RuntimeApiError
from runtime_api.schemas import (
    AgentRunStatus,
    ApprovalDecisionRecord,
    ApprovalRequestRecord,
    ConversationStatus,
    ConversationRecord,
    CreateConversationRequest,
    CreateRunRequest,
    HistoryDeletionResponse,
    MessageRecord,
    MessageRole,
    MessageStatus,
    RuntimeApprovalResolvedCommand,
    RuntimeCancelCommand,
    RuntimeEventDraft,
    RuntimeEventEnvelope,
    RuntimeEventPresentationProjector,
    RuntimeRunCommand,
    RunRecord,
)


class InMemoryRuntimeApiStore:
    """In-memory implementation of persistence, event store, and queue ports."""

    def __init__(self) -> None:
        self.conversations: dict[str, ConversationRecord] = {}
        self.messages: dict[str, MessageRecord] = {}
        self.runs: dict[str, RunRecord] = {}
        self.approval_requests: dict[str, ApprovalRequestRecord] = {}
        self.approval_decisions: dict[str, ApprovalDecisionRecord] = {}
        self.events_by_run: dict[str, list[RuntimeEventEnvelope]] = {}
        self.run_commands: list[RuntimeRunCommand] = []
        self.cancel_commands: list[RuntimeCancelCommand] = []
        self.approval_commands: list[RuntimeApprovalResolvedCommand] = []
        self._queue_order: list[str] = []
        self._queue_payloads: dict[str, dict[str, object]] = {}
        self._queue_statuses: dict[str, OutboxStatus] = {}
        self._queue_attempts: dict[str, int] = {}
        self._queue_available_at: dict[str, datetime] = {}
        self._queue_claims: dict[str, RuntimeWorkerClaim] = {}
        self.audit_log: list[tuple[str, dict[str, object]]] = []
        self._audit_chain_signer = AuditChainSigner.from_env()
        self._audit_chain_heads_by_org: dict[str, bytes] = {}
        self._audit_chain_counts_by_org: dict[str, int] = {}
        self._conversation_idempotency: dict[tuple[str, str, str], str] = {}
        self._run_idempotency: dict[tuple[str, str, str], str] = {}
        self._run_idempotency_fingerprint: dict[
            tuple[str, str, str], tuple[str, str]
        ] = {}
        # Usage state (B1 / B2 / B3 / B4) -- in-memory only; tests assert
        # against these dicts directly.
        self.run_usage: dict[str, RuntimeRunUsageRecord] = {}
        self.model_call_usage: list[RuntimeModelCallUsageRecord] = []
        self.pricing_rows: list[ModelPricingRecord] = []
        self.user_daily_usage: dict[
            tuple[str, str, str, str, str], UsageDailyUserRow
        ] = {}
        self.org_daily_usage: dict[tuple[str, str, str, str], UsageDailyOrgRow] = {}
        # Compression events (B5 read-only path; no writer wired yet).
        self.compression_events: list[CompressionEventRecord] = []
        # Budgets (B7).
        self.budgets: dict[str, BudgetRecord] = {}
        # Keyed by (budget_id, period_start_isoformat) so the same budget
        # can have one state row per period and we don't accidentally
        # blow up old periods on a roll-over.
        self.budget_states: dict[tuple[str, str], BudgetStateRecord] = {}
        self.budget_reservations: dict[str, BudgetReservationRecord] = {}

    def create_conversation(
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
        )
        self.conversations[conversation.conversation_id] = conversation
        if request.idempotency_key is not None:
            self._conversation_idempotency[
                (request.org_id, request.user_id, request.idempotency_key)
            ] = conversation.conversation_id
        return conversation

    def get_conversation(
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

    def list_conversations(
        self,
        *,
        org_id: str,
        user_id: str,
        limit: int,
        include_archived: bool = False,
    ) -> Sequence[ConversationRecord]:
        """Return scoped conversations ordered by latest update."""

        records = [
            conversation
            for conversation in self.conversations.values()
            if conversation.org_id == org_id and conversation.user_id == user_id
        ]
        if not include_archived:
            records = [
                conversation
                for conversation in records
                if conversation.status != ConversationStatus.ARCHIVED
            ]
        return tuple(
            sorted(
                records, key=lambda conversation: conversation.updated_at, reverse=True
            )[:limit]
        )

    def list_messages(
        self,
        *,
        org_id: str,
        conversation_id: str,
        limit: int,
        include_deleted: bool = False,
    ) -> Sequence[MessageRecord]:
        """Return messages ordered by creation time."""

        records = [
            message
            for message in self.messages.values()
            if message.org_id == org_id and message.conversation_id == conversation_id
        ]
        if not include_deleted:
            records = [message for message in records if message.deleted_at is None]
        return tuple(sorted(records, key=lambda message: message.created_at)[:limit])

    def append_message(self, message: MessageRecord) -> MessageRecord:
        """Append a runtime-created message."""

        self.messages[message.message_id] = message
        conversation = self.conversations.get(message.conversation_id)
        if conversation is not None:
            self.conversations[message.conversation_id] = conversation.model_copy(
                update={"updated_at": message.created_at}
            )
        return message

    def create_run_with_user_message(
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

    def get_run(self, *, org_id: str, run_id: str) -> RunRecord | None:
        """Return a run scoped by organization."""

        run = self.runs.get(run_id)
        if run is None or run.org_id != org_id:
            return None
        return run

    def update_run_status(self, *, run_id: str, status: AgentRunStatus) -> RunRecord:
        """Update run status and relevant timestamps."""

        run = self.runs[run_id]
        timestamps = StatusTransition.timestamp_updates(
            status, already_started=run.started_at is not None
        )
        updated = run.model_copy(update={"status": status, **timestamps})
        self.runs[run_id] = updated
        return updated

    def set_run_latest_sequence(
        self, *, run_id: str, latest_sequence_no: int
    ) -> RunRecord:
        """Persist latest event sequence for run inspection."""

        updated = self.runs[run_id].model_copy(
            update={"latest_sequence_no": latest_sequence_no}
        )
        self.runs[run_id] = updated
        return updated

    def record_approval_decision(
        self,
        *,
        record: ApprovalDecisionRecord,
    ) -> ApprovalDecisionRecord:
        """Persist approval decision and update the request state."""

        self.approval_decisions[record.approval_id] = record
        request = self.approval_requests[record.approval_id]
        self.approval_requests[record.approval_id] = request.model_copy(
            update={"status": record.status}
        )
        return record

    def create_approval_request(
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

    def get_approval_request(
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

    def write_audit_log(self, *, event_type: str, record: dict[str, object]) -> None:
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
        # Only the canonical record fields are signed; chain fields are
        # excluded so the signature is independent of itself. Datetimes go
        # through the canonicalizer as ISO 8601.
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

    def delete_user_history(
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
        self.write_audit_log(
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

    # Usage + pricing (B1, B2, B3, B4) -----------------------------------

    def record_run_usage(self, record: RuntimeRunUsageRecord) -> None:
        """Idempotent on ``run_id``; second write is a no-op."""

        if record.run_id in self.run_usage:
            return
        self.run_usage[record.run_id] = record

    def record_model_call_usage(self, record: RuntimeModelCallUsageRecord) -> None:
        self.model_call_usage.append(record)

    def update_run_usage_cost(
        self,
        *,
        run_id: str,
        cost_micro_usd: int,
        pricing_id: str,
        pricing_version: str,
    ) -> None:
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

    def update_model_call_usage_cost(
        self,
        *,
        usage_id: str,
        cost_micro_usd: int,
        pricing_id: str,
        pricing_version: str,
    ) -> None:
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

    def upsert_pricing(self, record: ModelPricingRecord) -> ModelPricingRecord:
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

    def lookup_pricing(
        self,
        *,
        provider: str,
        model_name: str,
        region: str,
        at: datetime,
    ) -> ModelPricingRecord | None:
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

    def list_runs_missing_cost(
        self,
        *,
        limit: int,
        cursor: str | None = None,
    ) -> Sequence[RuntimeRunUsageRecord]:
        rows = sorted(
            (row for row in self.run_usage.values() if row.cost_micro_usd is None),
            key=lambda row: row.id,
        )
        if cursor is not None:
            rows = [row for row in rows if row.id > cursor]
        return tuple(rows[:limit])

    def upsert_user_daily_usage(self, row: UsageDailyUserRow) -> None:
        key = (
            row.org_id,
            row.user_id,
            row.day.isoformat(),
            row.model_provider,
            row.model_name,
        )
        self.user_daily_usage[key] = row

    def upsert_org_daily_usage(self, row: UsageDailyOrgRow) -> None:
        key = (
            row.org_id,
            row.day.isoformat(),
            row.model_provider,
            row.model_name,
        )
        self.org_daily_usage[key] = row

    def query_user_daily_usage(
        self,
        *,
        org_id: str,
        user_id: str,
        start_day: datetime,
        end_day: datetime,
    ) -> Sequence[UsageDailyUserRow]:
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

    def query_org_daily_usage(
        self,
        *,
        org_id: str,
        start_day: datetime,
        end_day: datetime,
    ) -> Sequence[UsageDailyOrgRow]:
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

    def query_run_usage(
        self,
        *,
        org_id: str,
        run_id: str,
    ) -> RuntimeRunUsageRecord | None:
        record = self.run_usage.get(run_id)
        if record is None or record.org_id != org_id:
            return None
        return record

    def query_run_usage_for_range(
        self,
        *,
        org_id: str | None,
        user_id: str | None,
        start: datetime,
        end: datetime,
    ) -> Sequence[RuntimeRunUsageRecord]:
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

    def query_top_conversations(
        self,
        *,
        org_id: str,
        user_id: str,
        start: datetime,
        end: datetime,
        limit: int,
    ) -> Sequence[tuple[str, int]]:
        totals: dict[str, int] = {}
        for row in self.run_usage.values():
            if (
                row.org_id != org_id
                or row.user_id != user_id
                or not (start <= row.completed_at <= end)
                or row.pii_purged_at is not None
            ):
                continue
            totals[row.conversation_id] = (
                totals.get(row.conversation_id, 0) + row.total_tokens
            )
        ranked = sorted(totals.items(), key=lambda item: item[1], reverse=True)
        return tuple(ranked[:limit])

    def query_model_call_usage_for_run(
        self,
        *,
        org_id: str,
        run_id: str,
    ) -> Sequence[RuntimeModelCallUsageRecord]:
        return tuple(
            row
            for row in self.model_call_usage
            if row.org_id == org_id and row.run_id == run_id
        )

    def query_latest_run_usage_for_conversation(
        self,
        *,
        org_id: str,
        user_id: str,
        conversation_id: str,
    ) -> RuntimeRunUsageRecord | None:
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

    def query_compression_events_for_run(
        self,
        *,
        org_id: str,
        run_id: str,
    ) -> Sequence[CompressionEventRecord]:
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
    # Budgets (B7).
    # ------------------------------------------------------------------

    def lookup_budgets_for_run(
        self,
        *,
        org_id: str,
        user_id: str,
    ) -> Sequence[BudgetWithState]:
        from datetime import date, datetime, timezone

        from agent_runtime.budgets.period import BudgetPeriodCalculator

        now = datetime.now(timezone.utc)
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

    def charge_budget(
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

    def reserve_budget(
        self,
        *,
        budget_id: str,
        period_start,
        run_id: str,
        reserved_micro_usd: int,
        reserved_tokens: int,
        now,
    ) -> BudgetReservationRecord | None:
        # Idempotent on (budget_id, run_id) for active reservations.
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

    def consume_budget_reservation(
        self,
        *,
        reservation_id: str,
        now,
    ) -> None:
        record = self.budget_reservations.get(reservation_id)
        if record is None or record.consumed_at is not None:
            return
        self.budget_reservations[reservation_id] = record.model_copy(
            update={"consumed_at": now}
        )

    def reap_expired_budget_reservations(self, *, now) -> int:
        purged = 0
        for reservation_id, record in list(self.budget_reservations.items()):
            if record.consumed_at is None and record.expires_at < now:
                del self.budget_reservations[reservation_id]
                purged += 1
        return purged

    def list_budgets(self, *, org_id: str) -> Sequence[BudgetRecord]:
        return tuple(
            sorted(
                (b for b in self.budgets.values() if b.org_id == org_id),
                key=lambda b: b.created_at,
                reverse=True,
            )
        )

    def get_budget(self, *, org_id: str, budget_id: str) -> BudgetRecord | None:
        record = self.budgets.get(budget_id)
        if record is None or record.org_id != org_id:
            return None
        return record

    def create_budget(self, record: BudgetRecord) -> BudgetRecord:
        # Enforce the spec's UNIQUE (org_id, COALESCE(user_id,'<org>'), scope, period).
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

    def update_budget(self, record: BudgetRecord) -> BudgetRecord:
        if record.id not in self.budgets:
            raise KeyError(record.id)
        self.budgets[record.id] = record
        return record

    def delete_budget(self, *, org_id: str, budget_id: str) -> None:
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

    def append_event(self, event: RuntimeEventDraft) -> RuntimeEventEnvelope:
        """Append one event with a monotonically increasing run sequence number."""

        events = self.events_by_run.setdefault(event.run_id, [])
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
        )
        events.append(envelope)
        return envelope

    def list_events_after(
        self,
        *,
        org_id: str,
        run_id: str,
        after_sequence: int,
    ) -> Sequence[RuntimeEventEnvelope]:
        """Return persisted events after a sequence number."""

        run = self.get_run(org_id=org_id, run_id=run_id)
        if run is None:
            return ()
        return tuple(
            event
            for event in self.events_by_run.get(run_id, ())
            if event.sequence_no > after_sequence
        )

    def get_latest_sequence(self, *, run_id: str) -> int:
        """Return latest persisted sequence number for a run."""

        return len(self.events_by_run.get(run_id, ()))

    def enqueue_run(self, command: RuntimeRunCommand) -> None:
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

    def enqueue_cancel(self, command: RuntimeCancelCommand) -> None:
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

    def enqueue_approval_resolved(
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

    def claim_next(
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

    def mark_complete(self, *, result: RuntimeWorkerResult) -> None:
        """Mark a claimed command complete."""

        self._queue_statuses[result.command_id] = OutboxStatus.COMPLETED
        self._queue_claims.pop(result.command_id, None)

    def mark_retry(self, *, result: RuntimeWorkerResult) -> None:
        """Release a command so another worker may claim it later."""

        self._queue_statuses[result.command_id] = OutboxStatus.RETRY
        self._queue_available_at[result.command_id] = (
            result.retry_available_at or datetime.now(timezone.utc)
        )
        self._queue_claims.pop(result.command_id, None)

    def mark_dead_letter(self, *, result: RuntimeWorkerResult) -> None:
        """Mark a command permanently failed after retries are exhausted."""

        self._queue_statuses[result.command_id] = OutboxStatus.DEAD_LETTER
        self._queue_claims.pop(result.command_id, None)

    def seed_approval_request(
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
