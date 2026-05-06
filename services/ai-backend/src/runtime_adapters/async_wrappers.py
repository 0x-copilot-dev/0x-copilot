"""Sync→async port wrappers used while the runtime is in transition.

The runtime event producer (and, after Phase D, the rest of the runtime
service) talks to ``AsyncPersistencePort`` / ``AsyncEventStorePort`` /
``AsyncRuntimeQueuePort``. When the configured backend is still synchronous
(``InMemoryRuntimeApiStore`` or ``PostgresRuntimeApiStore``), these wrappers
bridge each call via ``asyncio.to_thread`` so the async chain works
transparently. On a fully-async backend (``AsyncPostgresRuntimeApiStore``)
no wrapping happens — calls go directly to the awaitable methods.

Once Phase E retires the sync adapter, these wrappers go away with it.
"""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Sequence
from datetime import datetime

from agent_runtime.api.async_ports import (
    AsyncEventStorePort,
    AsyncPersistencePort,
    AsyncRuntimeQueuePort,
)
from agent_runtime.api.ports import EventStorePort, PersistencePort, RuntimeQueuePort
from agent_runtime.persistence.records import (
    RuntimeWorkerClaim,
    RuntimeWorkerResult,
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
    WorkspaceDefaultsRecord,
)


class SyncToAsyncPersistence:
    """Wrap a sync :class:`PersistencePort` in async signatures."""

    def __init__(self, sync_port: PersistencePort) -> None:
        self._port = sync_port

    async def create_conversation(
        self, request: CreateConversationRequest
    ) -> ConversationRecord:
        return await asyncio.to_thread(self._port.create_conversation, request)

    async def get_conversation(
        self,
        *,
        org_id: str,
        user_id: str,
        conversation_id: str,
    ) -> ConversationRecord | None:
        return await asyncio.to_thread(
            self._port.get_conversation,
            org_id=org_id,
            user_id=user_id,
            conversation_id=conversation_id,
        )

    async def get_conversation_for_org(
        self,
        *,
        org_id: str,
        conversation_id: str,
    ) -> ConversationRecord | None:
        return await asyncio.to_thread(
            self._port.get_conversation_for_org,
            org_id=org_id,
            conversation_id=conversation_id,
        )

    async def list_conversations(
        self,
        *,
        org_id: str,
        user_id: str,
        limit: int,
        include_archived: bool = False,
        include_deleted: bool = False,
    ) -> Sequence[ConversationRecord]:
        return await asyncio.to_thread(
            self._port.list_conversations,
            org_id=org_id,
            user_id=user_id,
            limit=limit,
            include_archived=include_archived,
            include_deleted=include_deleted,
        )

    async def list_messages(
        self,
        *,
        org_id: str,
        conversation_id: str,
        limit: int,
        include_deleted: bool = False,
    ) -> Sequence[MessageRecord]:
        return await asyncio.to_thread(
            self._port.list_messages,
            org_id=org_id,
            conversation_id=conversation_id,
            limit=limit,
            include_deleted=include_deleted,
        )

    async def append_message(self, message: MessageRecord) -> MessageRecord:
        return await asyncio.to_thread(self._port.append_message, message)

    async def insert_forked_conversation(
        self, conversation: ConversationRecord
    ) -> ConversationRecord:
        return await asyncio.to_thread(
            self._port.insert_forked_conversation, conversation
        )

    async def update_conversation_connectors(
        self,
        *,
        org_id: str,
        user_id: str,
        conversation_id: str,
        scopes_patch: dict[str, tuple[str, ...] | None],
        now: datetime,
    ) -> ConversationRecord | None:
        return await asyncio.to_thread(
            self._port.update_conversation_connectors,
            org_id=org_id,
            user_id=user_id,
            conversation_id=conversation_id,
            scopes_patch=scopes_patch,
            now=now,
        )

    async def create_run_with_user_message(
        self,
        *,
        request: CreateRunRequest,
        conversation: ConversationRecord,
    ) -> tuple[RunRecord, MessageRecord, bool]:
        return await asyncio.to_thread(
            self._port.create_run_with_user_message,
            request=request,
            conversation=conversation,
        )

    async def get_run(self, *, org_id: str, run_id: str) -> RunRecord | None:
        return await asyncio.to_thread(self._port.get_run, org_id=org_id, run_id=run_id)

    async def get_active_run_for_conversation(
        self,
        *,
        org_id: str,
        conversation_id: str,
    ) -> RunRecord | None:
        return await asyncio.to_thread(
            self._port.get_active_run_for_conversation,
            org_id=org_id,
            conversation_id=conversation_id,
        )

    async def update_run_status(
        self, *, run_id: str, status: AgentRunStatus
    ) -> RunRecord:
        return await asyncio.to_thread(
            self._port.update_run_status, run_id=run_id, status=status
        )

    async def set_run_latest_sequence(
        self, *, run_id: str, latest_sequence_no: int
    ) -> RunRecord:
        return await asyncio.to_thread(
            self._port.set_run_latest_sequence,
            run_id=run_id,
            latest_sequence_no=latest_sequence_no,
        )

    async def record_approval_decision(
        self,
        *,
        record: ApprovalDecisionRecord,
    ) -> ApprovalDecisionRecord:
        return await asyncio.to_thread(
            self._port.record_approval_decision, record=record
        )

    async def create_approval_request(
        self,
        *,
        record: ApprovalRequestRecord,
    ) -> ApprovalRequestRecord:
        return await asyncio.to_thread(
            self._port.create_approval_request, record=record
        )

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
        return await asyncio.to_thread(
            self._port.forward_approval_request,
            parent_approval_id=parent_approval_id,
            org_id=org_id,
            decided_by_user_id=decided_by_user_id,
            forwarded_to_user_id=forwarded_to_user_id,
            decision_reason=decision_reason,
            child=child,
            now=now,
        )

    async def get_approval_request(
        self,
        *,
        org_id: str,
        approval_id: str,
    ) -> ApprovalRequestRecord | None:
        return await asyncio.to_thread(
            self._port.get_approval_request,
            org_id=org_id,
            approval_id=approval_id,
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
        return await asyncio.to_thread(
            self._port.list_assigned_approvals,
            org_id=org_id,
            requested_by_user_id=requested_by_user_id,
            status=status,
            limit=limit,
            cursor=cursor,
        )

    async def list_pending_expired_approvals(
        self,
        *,
        now: datetime,
        limit: int,
    ) -> Sequence[ApprovalRequestRecord]:
        return await asyncio.to_thread(
            self._port.list_pending_expired_approvals,
            now=now,
            limit=limit,
        )

    async def list_pending_approvals_for_membership_audit(
        self,
        *,
        limit: int,
    ) -> Sequence[ApprovalRequestRecord]:
        return await asyncio.to_thread(
            self._port.list_pending_approvals_for_membership_audit,
            limit=limit,
        )

    async def write_audit_log(
        self, *, event_type: str, record: dict[str, object]
    ) -> None:
        await asyncio.to_thread(
            self._port.write_audit_log, event_type=event_type, record=record
        )

    async def delete_user_history(
        self,
        *,
        org_id: str,
        user_id: str,
        reason: str | None = None,
    ) -> HistoryDeletionResponse:
        return await asyncio.to_thread(
            self._port.delete_user_history,
            org_id=org_id,
            user_id=user_id,
            reason=reason,
        )

    # ----- PR 1.6: workspace defaults + conversation lifecycle ----- #

    async def get_workspace_defaults(
        self,
        *,
        org_id: str,
    ) -> WorkspaceDefaultsRecord | None:
        return await asyncio.to_thread(self._port.get_workspace_defaults, org_id=org_id)

    async def upsert_workspace_defaults(
        self,
        *,
        record: WorkspaceDefaultsRecord,
    ) -> WorkspaceDefaultsRecord:
        return await asyncio.to_thread(
            self._port.upsert_workspace_defaults, record=record
        )

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
        now: datetime,
    ) -> ConversationRecord | None:
        return await asyncio.to_thread(
            self._port.update_conversation,
            org_id=org_id,
            user_id=user_id,
            conversation_id=conversation_id,
            title=title,
            title_changed=title_changed,
            folder=folder,
            folder_changed=folder_changed,
            archived=archived,
            archived_changed=archived_changed,
            now=now,
        )

    async def soft_delete_conversation(
        self,
        *,
        org_id: str,
        user_id: str,
        conversation_id: str,
        now: datetime,
    ) -> ConversationRecord | None:
        return await asyncio.to_thread(
            self._port.soft_delete_conversation,
            org_id=org_id,
            user_id=user_id,
            conversation_id=conversation_id,
            now=now,
        )

    async def restore_conversation(
        self,
        *,
        org_id: str,
        user_id: str,
        conversation_id: str,
        now: datetime,
    ) -> ConversationRecord | None:
        return await asyncio.to_thread(
            self._port.restore_conversation,
            org_id=org_id,
            user_id=user_id,
            conversation_id=conversation_id,
            now=now,
        )

    # Usage + pricing (B1, B2, B3, B4) — defer to the underlying sync port.

    async def record_run_usage(self, record):  # type: ignore[no-untyped-def]
        await asyncio.to_thread(self._port.record_run_usage, record)

    async def record_model_call_usage(self, record):  # type: ignore[no-untyped-def]
        await asyncio.to_thread(self._port.record_model_call_usage, record)

    async def update_run_usage_cost(
        self,
        *,
        run_id: str,
        cost_micro_usd: int,
        pricing_id: str,
        pricing_version: str,
    ) -> None:
        await asyncio.to_thread(
            self._port.update_run_usage_cost,
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
        await asyncio.to_thread(
            self._port.update_model_call_usage_cost,
            usage_id=usage_id,
            cost_micro_usd=cost_micro_usd,
            pricing_id=pricing_id,
            pricing_version=pricing_version,
        )

    async def upsert_pricing(self, record):  # type: ignore[no-untyped-def]
        return await asyncio.to_thread(self._port.upsert_pricing, record)

    async def lookup_pricing(self, **kwargs):  # type: ignore[no-untyped-def]
        return await asyncio.to_thread(self._port.lookup_pricing, **kwargs)

    async def list_runs_missing_cost(self, **kwargs):  # type: ignore[no-untyped-def]
        return await asyncio.to_thread(self._port.list_runs_missing_cost, **kwargs)

    async def upsert_user_daily_usage(self, row):  # type: ignore[no-untyped-def]
        await asyncio.to_thread(self._port.upsert_user_daily_usage, row)

    async def upsert_org_daily_usage(self, row):  # type: ignore[no-untyped-def]
        await asyncio.to_thread(self._port.upsert_org_daily_usage, row)

    async def query_user_daily_usage(self, **kwargs):  # type: ignore[no-untyped-def]
        return await asyncio.to_thread(self._port.query_user_daily_usage, **kwargs)

    async def query_org_daily_usage(self, **kwargs):  # type: ignore[no-untyped-def]
        return await asyncio.to_thread(self._port.query_org_daily_usage, **kwargs)

    async def query_run_usage(self, **kwargs):  # type: ignore[no-untyped-def]
        return await asyncio.to_thread(self._port.query_run_usage, **kwargs)

    async def query_run_usage_for_range(self, **kwargs):  # type: ignore[no-untyped-def]
        return await asyncio.to_thread(self._port.query_run_usage_for_range, **kwargs)

    async def query_top_conversations(self, **kwargs):  # type: ignore[no-untyped-def]
        return await asyncio.to_thread(self._port.query_top_conversations, **kwargs)

    async def query_model_call_usage_for_run(self, **kwargs):  # type: ignore[no-untyped-def]
        return await asyncio.to_thread(
            self._port.query_model_call_usage_for_run, **kwargs
        )

    async def query_latest_run_usage_for_conversation(self, **kwargs):  # type: ignore[no-untyped-def]
        return await asyncio.to_thread(
            self._port.query_latest_run_usage_for_conversation, **kwargs
        )

    async def query_compression_events_for_run(self, **kwargs):  # type: ignore[no-untyped-def]
        return await asyncio.to_thread(
            self._port.query_compression_events_for_run, **kwargs
        )

    # Budgets (B7) ---------------------------------------------------------

    async def lookup_budgets_for_run(self, **kwargs):  # type: ignore[no-untyped-def]
        return await asyncio.to_thread(self._port.lookup_budgets_for_run, **kwargs)

    async def charge_budget(self, **kwargs):  # type: ignore[no-untyped-def]
        return await asyncio.to_thread(self._port.charge_budget, **kwargs)

    async def reserve_budget(self, **kwargs):  # type: ignore[no-untyped-def]
        return await asyncio.to_thread(self._port.reserve_budget, **kwargs)

    async def consume_budget_reservation(self, **kwargs):  # type: ignore[no-untyped-def]
        return await asyncio.to_thread(self._port.consume_budget_reservation, **kwargs)

    async def reap_expired_budget_reservations(self, **kwargs):  # type: ignore[no-untyped-def]
        return await asyncio.to_thread(
            self._port.reap_expired_budget_reservations, **kwargs
        )

    async def list_budgets(self, **kwargs):  # type: ignore[no-untyped-def]
        return await asyncio.to_thread(self._port.list_budgets, **kwargs)

    async def get_budget(self, **kwargs):  # type: ignore[no-untyped-def]
        return await asyncio.to_thread(self._port.get_budget, **kwargs)

    async def create_budget(self, record):  # type: ignore[no-untyped-def]
        return await asyncio.to_thread(self._port.create_budget, record)

    async def update_budget(self, record):  # type: ignore[no-untyped-def]
        return await asyncio.to_thread(self._port.update_budget, record)

    async def delete_budget(self, **kwargs):  # type: ignore[no-untyped-def]
        return await asyncio.to_thread(self._port.delete_budget, **kwargs)

    # Retention (C8) -------------------------------------------------------

    async def list_retention_policies(self, **kwargs):  # type: ignore[no-untyped-def]
        return await asyncio.to_thread(self._port.list_retention_policies, **kwargs)

    async def upsert_retention_policy(self, record):  # type: ignore[no-untyped-def]
        return await asyncio.to_thread(self._port.upsert_retention_policy, record)

    async def delete_retention_policy(self, **kwargs):  # type: ignore[no-untyped-def]
        return await asyncio.to_thread(self._port.delete_retention_policy, **kwargs)


class SyncToAsyncEventStore:
    """Wrap a sync :class:`EventStorePort` in async signatures."""

    def __init__(self, sync_port: EventStorePort) -> None:
        self._port = sync_port

    async def append_event(self, event: RuntimeEventDraft) -> RuntimeEventEnvelope:
        return await asyncio.to_thread(self._port.append_event, event)

    async def list_events_after(
        self,
        *,
        org_id: str,
        run_id: str,
        after_sequence: int,
    ) -> Sequence[RuntimeEventEnvelope]:
        return await asyncio.to_thread(
            self._port.list_events_after,
            org_id=org_id,
            run_id=run_id,
            after_sequence=after_sequence,
        )

    async def get_latest_sequence(self, *, run_id: str) -> int:
        return await asyncio.to_thread(self._port.get_latest_sequence, run_id=run_id)


class SyncToAsyncQueue:
    """Wrap a sync :class:`RuntimeQueuePort` in async signatures."""

    def __init__(self, sync_port: RuntimeQueuePort) -> None:
        self._port = sync_port

    async def enqueue_run(self, command: RuntimeRunCommand) -> None:
        await asyncio.to_thread(self._port.enqueue_run, command)

    async def enqueue_cancel(self, command: RuntimeCancelCommand) -> None:
        await asyncio.to_thread(self._port.enqueue_cancel, command)

    async def enqueue_approval_resolved(
        self, command: RuntimeApprovalResolvedCommand
    ) -> None:
        await asyncio.to_thread(self._port.enqueue_approval_resolved, command)

    async def claim_next(
        self,
        *,
        worker_id: str,
        lock_expires_at: datetime,
    ) -> RuntimeWorkerClaim | None:
        return await asyncio.to_thread(
            self._port.claim_next,
            worker_id=worker_id,
            lock_expires_at=lock_expires_at,
        )

    async def mark_complete(self, *, result: RuntimeWorkerResult) -> None:
        await asyncio.to_thread(self._port.mark_complete, result=result)

    async def mark_retry(self, *, result: RuntimeWorkerResult) -> None:
        await asyncio.to_thread(self._port.mark_retry, result=result)

    async def mark_dead_letter(self, *, result: RuntimeWorkerResult) -> None:
        await asyncio.to_thread(self._port.mark_dead_letter, result=result)


def _is_async_port(port: object, sentinel_method: str) -> bool:
    """Return True iff a representative port method is a coroutine function.

    ``isinstance(port, AsyncXPort)`` over a Protocol checks for method names
    only — it returns True for sync stores too, because the names match. We
    actually need to distinguish *sync def* from *async def*, so we probe
    a representative method with ``inspect.iscoroutinefunction``.
    """

    method = getattr(port, sentinel_method, None)
    return inspect.iscoroutinefunction(method)


def adapt_persistence_to_async(
    port: PersistencePort | AsyncPersistencePort,
) -> AsyncPersistencePort:
    """Return ``port`` if already async, otherwise wrap it via to_thread."""

    if _is_async_port(port, "set_run_latest_sequence"):
        return port  # type: ignore[return-value]
    return SyncToAsyncPersistence(port)  # type: ignore[arg-type]


def adapt_event_store_to_async(
    port: EventStorePort | AsyncEventStorePort,
) -> AsyncEventStorePort:
    if _is_async_port(port, "append_event"):
        return port  # type: ignore[return-value]
    return SyncToAsyncEventStore(port)  # type: ignore[arg-type]


def adapt_queue_to_async(
    port: RuntimeQueuePort | AsyncRuntimeQueuePort,
) -> AsyncRuntimeQueuePort:
    if _is_async_port(port, "claim_next"):
        return port  # type: ignore[return-value]
    return SyncToAsyncQueue(port)  # type: ignore[arg-type]
