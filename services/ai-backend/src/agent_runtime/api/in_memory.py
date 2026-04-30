"""Deterministic in-memory runtime API ports for local tests and development."""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from datetime import UTC, datetime

from starlette import status

from agent_runtime.agent.contracts import RuntimeErrorCode
from agent_runtime.api.constants import Messages
from agent_runtime.api.contracts import (
    AgentRunStatus,
    ApprovalDecisionRecord,
    ApprovalRequestRecord,
    ConversationRecord,
    CreateConversationRequest,
    CreateRunRequest,
    MessageRecord,
    MessageRole,
    RuntimeApprovalResolvedCommand,
    RuntimeCancelCommand,
    RuntimeEventDraft,
    RuntimeEventEnvelope,
    RuntimeRunCommand,
    RunRecord,
)
from agent_runtime.api.errors import RuntimeApiError


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
        self.audit_log: list[tuple[str, object]] = []
        self._conversation_idempotency: dict[tuple[str, str, str], str] = {}
        self._run_idempotency: dict[tuple[str, str, str], str] = {}
        self._run_idempotency_fingerprint: dict[tuple[str, str, str], tuple[str, str]] = {}

    def create_conversation(self, request: CreateConversationRequest) -> ConversationRecord:
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
        if status in {AgentRunStatus.COMPLETED, AgentRunStatus.FAILED, AgentRunStatus.TIMED_OUT}:
            updates["completed_at"] = datetime.now(UTC)
        if status == AgentRunStatus.CANCELLED:
            updates["cancelled_at"] = datetime.now(UTC)
        updated = run.model_copy(update=updates)
        self.runs[run_id] = updated
        return updated

    def set_run_latest_sequence(self, *, run_id: str, latest_sequence_no: int) -> RunRecord:
        """Persist latest event sequence for run inspection."""

        updated = self.runs[run_id].model_copy(update={"latest_sequence_no": latest_sequence_no})
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
            parent_task_id=event.parent_task_id,
            payload=event.payload,
            metadata=event.metadata,
        )
        events.append(envelope)
        return envelope

    def append_events(self, events: Sequence[RuntimeEventDraft]) -> Sequence[RuntimeEventEnvelope]:
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

    async def subscribe_run_events(
        self,
        *,
        org_id: str,
        run_id: str,
        after_sequence: int,
    ) -> AsyncIterator[RuntimeEventEnvelope]:
        """Yield replayed events and close; durable workers arrive later."""

        for event in self.list_events_after(
            org_id=org_id,
            run_id=run_id,
            after_sequence=after_sequence,
        ):
            yield event

    def enqueue_run(self, command: RuntimeRunCommand) -> None:
        """Enqueue a run command for deterministic worker tests."""

        self.run_commands.append(command)

    def enqueue_cancel(self, command: RuntimeCancelCommand) -> None:
        """Enqueue a cancel command for deterministic worker tests."""

        self.cancel_commands.append(command)

    def enqueue_approval_resolved(self, command: RuntimeApprovalResolvedCommand) -> None:
        """Enqueue an approval resolution command for deterministic worker tests."""

        self.approval_commands.append(command)

    def seed_approval_request(self, record: ApprovalRequestRecord) -> ApprovalRequestRecord:
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
