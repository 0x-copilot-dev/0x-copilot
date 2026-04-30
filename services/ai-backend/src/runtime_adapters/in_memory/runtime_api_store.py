"""Deterministic in-memory runtime API ports for local tests and development."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

from starlette import status

from agent_runtime.execution.contracts import RuntimeErrorCode
from agent_runtime.api.constants import Messages
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
from runtime_api.http.errors import RuntimeApiError
from agent_runtime.persistence.constants import Values as PersistenceValues
from agent_runtime.persistence.records import (
    OutboxStatus,
    RuntimeWorkerClaim,
    RuntimeWorkerResult,
)

RuntimeApiServiceTerminalStatuses = frozenset(
    {
        AgentRunStatus.CANCELLED,
        AgentRunStatus.COMPLETED,
        AgentRunStatus.FAILED,
        AgentRunStatus.TIMED_OUT,
    }
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
        self.audit_log: list[tuple[str, object]] = []
        self._conversation_idempotency: dict[tuple[str, str, str], str] = {}
        self._run_idempotency: dict[tuple[str, str, str], str] = {}
        self._run_idempotency_fingerprint: dict[
            tuple[str, str, str], tuple[str, str]
        ] = {}

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
        return message

    def create_run_with_user_message(
        self,
        *,
        request: CreateRunRequest,
        conversation: ConversationRecord,
    ) -> tuple[RunRecord, MessageRecord, bool]:
        """Create message/run records or return an idempotent prior run."""

        context = request.runtime_context
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

        user_message = MessageRecord(
            conversation_id=conversation.conversation_id,
            org_id=conversation.org_id,
            run_id=context.run_id,
            role=MessageRole.USER,
            content_text=request.user_input,
            content_format=request.content_format,
            trace_id=context.trace_id,
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
        self.messages[user_message.message_id] = user_message
        self.runs[run.run_id] = run
        self.events_by_run.setdefault(run.run_id, [])
        if request.idempotency_key is not None:
            key = (context.org_id, context.user_id, request.idempotency_key)
            self._run_idempotency[key] = run.run_id
            self._run_idempotency_fingerprint[key] = (
                request.conversation_id,
                request.user_input,
            )
        return run, user_message, True

    def get_run(self, *, org_id: str, run_id: str) -> RunRecord | None:
        """Return a run scoped by organization."""

        run = self.runs.get(run_id)
        if run is None or run.org_id != org_id:
            return None
        return run

    def update_run_status(self, *, run_id: str, status: AgentRunStatus) -> RunRecord:
        """Update run status and relevant timestamps."""

        run = self.runs[run_id]
        updates: dict[str, object] = {"status": status}
        if status == AgentRunStatus.RUNNING and run.started_at is None:
            updates["started_at"] = datetime.now(UTC)
        if status in {
            AgentRunStatus.COMPLETED,
            AgentRunStatus.FAILED,
            AgentRunStatus.TIMED_OUT,
        }:
            updates["completed_at"] = datetime.now(UTC)
        if status == AgentRunStatus.CANCELLED:
            updates["cancelled_at"] = datetime.now(UTC)
        updated = run.model_copy(update=updates)
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

    def write_audit_log(self, *, event_type: str, record: object) -> None:
        """Append a deterministic audit record for assertions."""

        self.audit_log.append((event_type, record))

    def delete_user_history(
        self,
        *,
        org_id: str,
        user_id: str,
        reason: str | None = None,
    ) -> HistoryDeletionResponse:
        """Tombstone user-visible history while preserving audit evidence."""

        now = datetime.now(UTC)
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
            if run.status not in RuntimeApiServiceTerminalStatuses:
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
        self.audit_log.append(
            (
                "user_history_deleted",
                {
                    "audit_event_id": audit_event_id,
                    "org_id": org_id,
                    "user_id": user_id,
                    "reason": reason,
                    "deleted_at": now.isoformat(),
                },
            )
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
            payload=event.payload,
            metadata=event.metadata,
        )
        events.append(envelope)
        return envelope

    def append_events(
        self, events: Sequence[RuntimeEventDraft]
    ) -> Sequence[RuntimeEventEnvelope]:
        """Append multiple events in input order."""

        return tuple(self.append_event(event) for event in events)

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

        now = datetime.now(UTC)
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
            result.retry_available_at or datetime.now(UTC)
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
            "command_id": command_id,
            "command_type": command_type,
            "org_id": org_id,
            "run_id": run_id,
            "approval_id": approval_id,
        }
        self._queue_statuses[command_id] = OutboxStatus.PENDING
        self._queue_attempts[command_id] = 0
        self._queue_available_at[command_id] = datetime.now(UTC)

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
            command_type=str(payload["command_type"]),
            org_id=str(payload["org_id"]),
            run_id=str(payload["run_id"]),
            approval_id=payload["approval_id"]
            if isinstance(payload["approval_id"], str)
            else None,
            locked_by=worker_id,
            lock_expires_at=lock_expires_at,
            attempts=self._queue_attempts[command_id],
            payload=payload,
        )
