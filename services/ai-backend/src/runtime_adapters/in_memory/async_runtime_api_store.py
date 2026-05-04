"""Async in-memory runtime API store for tests and local dev.

Composition wrapper over :class:`InMemoryRuntimeApiStore`. Every method just
awaits the sync version, which is safe under ``asyncio`` concurrency because
each sync method runs to completion without yielding (no ``await`` inside),
and CPython dict mutations within a single function are not preempted by the
event loop.

Use this when a caller needs an :class:`AsyncPersistencePort` /
:class:`AsyncEventStorePort` / :class:`AsyncRuntimeQueuePort` implementation
and you don't want to spin up Postgres (tests, local dev).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from agent_runtime.persistence.records import (
    RuntimeWorkerClaim,
    RuntimeWorkerResult,
)
from runtime_adapters.in_memory.runtime_api_store import InMemoryRuntimeApiStore
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


class AsyncInMemoryRuntimeApiStore:
    """Async wrapper over :class:`InMemoryRuntimeApiStore`."""

    def __init__(self, store: InMemoryRuntimeApiStore | None = None) -> None:
        self._store = store or InMemoryRuntimeApiStore()

    @property
    def underlying(self) -> InMemoryRuntimeApiStore:
        """Expose the wrapped sync store for tests that need direct access."""

        return self._store

    # PersistencePort -----------------------------------------------------

    async def create_conversation(
        self, request: CreateConversationRequest
    ) -> ConversationRecord:
        return self._store.create_conversation(request)

    async def get_conversation(
        self,
        *,
        org_id: str,
        user_id: str,
        conversation_id: str,
    ) -> ConversationRecord | None:
        return self._store.get_conversation(
            org_id=org_id, user_id=user_id, conversation_id=conversation_id
        )

    async def list_conversations(
        self,
        *,
        org_id: str,
        user_id: str,
        limit: int,
        include_archived: bool = False,
    ) -> Sequence[ConversationRecord]:
        return self._store.list_conversations(
            org_id=org_id,
            user_id=user_id,
            limit=limit,
            include_archived=include_archived,
        )

    async def list_messages(
        self,
        *,
        org_id: str,
        conversation_id: str,
        limit: int,
        include_deleted: bool = False,
    ) -> Sequence[MessageRecord]:
        return self._store.list_messages(
            org_id=org_id,
            conversation_id=conversation_id,
            limit=limit,
            include_deleted=include_deleted,
        )

    async def append_message(self, message: MessageRecord) -> MessageRecord:
        return self._store.append_message(message)

    async def create_run_with_user_message(
        self,
        *,
        request: CreateRunRequest,
        conversation: ConversationRecord,
    ) -> tuple[RunRecord, MessageRecord, bool]:
        return self._store.create_run_with_user_message(
            request=request, conversation=conversation
        )

    async def get_run(self, *, org_id: str, run_id: str) -> RunRecord | None:
        return self._store.get_run(org_id=org_id, run_id=run_id)

    async def update_run_status(
        self, *, run_id: str, status: AgentRunStatus
    ) -> RunRecord:
        return self._store.update_run_status(run_id=run_id, status=status)

    async def set_run_latest_sequence(
        self, *, run_id: str, latest_sequence_no: int
    ) -> RunRecord:
        # Mirror the postgres adapter's monotonic semantics (H3).
        current = self._store.runs[run_id].latest_sequence_no
        if current is not None and current >= latest_sequence_no:
            return self._store.runs[run_id]
        return self._store.set_run_latest_sequence(
            run_id=run_id, latest_sequence_no=latest_sequence_no
        )

    async def record_approval_decision(
        self,
        *,
        record: ApprovalDecisionRecord,
    ) -> ApprovalDecisionRecord:
        return self._store.record_approval_decision(record=record)

    async def create_approval_request(
        self,
        *,
        record: ApprovalRequestRecord,
    ) -> ApprovalRequestRecord:
        return self._store.create_approval_request(record=record)

    async def get_approval_request(
        self,
        *,
        org_id: str,
        approval_id: str,
    ) -> ApprovalRequestRecord | None:
        return self._store.get_approval_request(org_id=org_id, approval_id=approval_id)

    async def write_audit_log(
        self, *, event_type: str, record: dict[str, object]
    ) -> None:
        self._store.write_audit_log(event_type=event_type, record=record)

    async def delete_user_history(
        self,
        *,
        org_id: str,
        user_id: str,
        reason: str | None = None,
    ) -> HistoryDeletionResponse:
        return self._store.delete_user_history(
            org_id=org_id, user_id=user_id, reason=reason
        )

    # Usage + pricing (B1, B2, B3, B4) -----------------------------------

    async def record_run_usage(self, record):  # type: ignore[no-untyped-def]
        self._store.record_run_usage(record)

    async def record_model_call_usage(self, record):  # type: ignore[no-untyped-def]
        self._store.record_model_call_usage(record)

    async def update_run_usage_cost(
        self,
        *,
        run_id: str,
        cost_micro_usd: int,
        pricing_id: str,
        pricing_version: str,
    ) -> None:
        self._store.update_run_usage_cost(
            run_id=run_id,
            cost_micro_usd=cost_micro_usd,
            pricing_id=pricing_id,
            pricing_version=pricing_version,
        )

    async def update_model_call_usage_cost(
        self,
        *,
        usage_id: str,
        cost_micro_usd: int,
        pricing_id: str,
        pricing_version: str,
    ) -> None:
        self._store.update_model_call_usage_cost(
            usage_id=usage_id,
            cost_micro_usd=cost_micro_usd,
            pricing_id=pricing_id,
            pricing_version=pricing_version,
        )

    async def upsert_pricing(self, record):  # type: ignore[no-untyped-def]
        return self._store.upsert_pricing(record)

    async def lookup_pricing(
        self,
        *,
        provider: str,
        model_name: str,
        region: str,
        at: datetime,
    ):  # type: ignore[no-untyped-def]
        return self._store.lookup_pricing(
            provider=provider, model_name=model_name, region=region, at=at
        )

    async def list_runs_missing_cost(
        self,
        *,
        limit: int,
        cursor: str | None = None,
    ):  # type: ignore[no-untyped-def]
        return self._store.list_runs_missing_cost(limit=limit, cursor=cursor)

    async def upsert_user_daily_usage(self, row):  # type: ignore[no-untyped-def]
        self._store.upsert_user_daily_usage(row)

    async def upsert_org_daily_usage(self, row):  # type: ignore[no-untyped-def]
        self._store.upsert_org_daily_usage(row)

    async def query_user_daily_usage(
        self,
        *,
        org_id: str,
        user_id: str,
        start_day: datetime,
        end_day: datetime,
    ):  # type: ignore[no-untyped-def]
        return self._store.query_user_daily_usage(
            org_id=org_id,
            user_id=user_id,
            start_day=start_day,
            end_day=end_day,
        )

    async def query_org_daily_usage(
        self,
        *,
        org_id: str,
        start_day: datetime,
        end_day: datetime,
    ):  # type: ignore[no-untyped-def]
        return self._store.query_org_daily_usage(
            org_id=org_id, start_day=start_day, end_day=end_day
        )

    async def query_run_usage(
        self,
        *,
        org_id: str,
        run_id: str,
    ):  # type: ignore[no-untyped-def]
        return self._store.query_run_usage(org_id=org_id, run_id=run_id)

    async def query_run_usage_for_range(
        self,
        *,
        org_id: str,
        user_id: str | None,
        start: datetime,
        end: datetime,
    ):  # type: ignore[no-untyped-def]
        return self._store.query_run_usage_for_range(
            org_id=org_id, user_id=user_id, start=start, end=end
        )

    async def query_top_conversations(
        self,
        *,
        org_id: str,
        user_id: str,
        start: datetime,
        end: datetime,
        limit: int,
    ):  # type: ignore[no-untyped-def]
        return self._store.query_top_conversations(
            org_id=org_id,
            user_id=user_id,
            start=start,
            end=end,
            limit=limit,
        )

    async def query_model_call_usage_for_run(
        self,
        *,
        org_id: str,
        run_id: str,
    ):  # type: ignore[no-untyped-def]
        return self._store.query_model_call_usage_for_run(org_id=org_id, run_id=run_id)

    async def query_latest_run_usage_for_conversation(
        self,
        *,
        org_id: str,
        user_id: str,
        conversation_id: str,
    ):  # type: ignore[no-untyped-def]
        return self._store.query_latest_run_usage_for_conversation(
            org_id=org_id, user_id=user_id, conversation_id=conversation_id
        )

    async def query_compression_events_for_run(
        self,
        *,
        org_id: str,
        run_id: str,
    ):  # type: ignore[no-untyped-def]
        return self._store.query_compression_events_for_run(
            org_id=org_id, run_id=run_id
        )

    # EventStorePort ------------------------------------------------------

    async def append_event(self, event: RuntimeEventDraft) -> RuntimeEventEnvelope:
        return self._store.append_event(event)

    async def list_events_after(
        self,
        *,
        org_id: str,
        run_id: str,
        after_sequence: int,
    ) -> Sequence[RuntimeEventEnvelope]:
        return self._store.list_events_after(
            org_id=org_id, run_id=run_id, after_sequence=after_sequence
        )

    async def get_latest_sequence(self, *, run_id: str) -> int:
        return self._store.get_latest_sequence(run_id=run_id)

    # RuntimeQueuePort ----------------------------------------------------

    async def enqueue_run(self, command: RuntimeRunCommand) -> None:
        self._store.enqueue_run(command)

    async def enqueue_cancel(self, command: RuntimeCancelCommand) -> None:
        self._store.enqueue_cancel(command)

    async def enqueue_approval_resolved(
        self, command: RuntimeApprovalResolvedCommand
    ) -> None:
        self._store.enqueue_approval_resolved(command)

    async def claim_next(
        self,
        *,
        worker_id: str,
        lock_expires_at: datetime,
    ) -> RuntimeWorkerClaim | None:
        return self._store.claim_next(
            worker_id=worker_id, lock_expires_at=lock_expires_at
        )

    async def mark_complete(self, *, result: RuntimeWorkerResult) -> None:
        self._store.mark_complete(result=result)

    async def mark_retry(self, *, result: RuntimeWorkerResult) -> None:
        self._store.mark_retry(result=result)

    async def mark_dead_letter(self, *, result: RuntimeWorkerResult) -> None:
        self._store.mark_dead_letter(result=result)
