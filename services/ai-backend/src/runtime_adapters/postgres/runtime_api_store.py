"""Postgres-backed runtime API, event store, and durable queue adapter."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from starlette import status

from agent_runtime.execution.contracts import (
    RuntimeErrorCode,
    RuntimeErrorEnvelope,
    StreamEventSource,
)
from agent_runtime.api.constants import Messages
from runtime_api.schemas import (
    AgentRunStatus,
    ApprovalDecisionRecord,
    ApprovalRequestRecord,
    ConversationRecord,
    CreateConversationRequest,
    CreateRunRequest,
    HistoryDeletionResponse,
    MessageRecord,
    MessageRole,
    RuntimeApprovalResolvedCommand,
    RuntimeCancelCommand,
    RuntimeApiEventType,
    RuntimeEventDraft,
    RuntimeEventEnvelope,
    RuntimeEventPresentationProjector,
    RuntimeRunCommand,
    RunRecord,
)
from runtime_api.http.errors import RuntimeApiError
from agent_runtime.persistence.constants import Values as PersistenceValues
from agent_runtime.persistence.records import RuntimeWorkerClaim, RuntimeWorkerResult
from agent_runtime.persistence.records import OutboxStatus
from agent_runtime.persistence.schema.postgres import (
    POSTGRES_AGENT_RUNTIME_MIGRATION_SQL,
)


class PostgresRuntimeApiStore:
    """Postgres implementation of persistence, event store, and queue ports."""

    def __init__(self, database_url: str) -> None:
        self.database_url = database_url

    def migrate(self) -> None:
        """Apply the runtime schema migration."""

        with self._connect() as conn:
            conn.execute(POSTGRES_AGENT_RUNTIME_MIGRATION_SQL)
            conn.commit()

    def create_conversation(
        self, request: CreateConversationRequest
    ) -> ConversationRecord:
        """Create or idempotently return a scoped conversation."""

        with self._connect() as conn:
            if request.idempotency_key is not None:
                existing = conn.execute(
                    """
                    SELECT * FROM agent_conversations
                    WHERE org_id = %s AND user_id = %s AND idempotency_key = %s
                    """,
                    (request.org_id, request.user_id, request.idempotency_key),
                ).fetchone()
                if existing is not None:
                    return self._conversation_record(existing)

            record = ConversationRecord(
                org_id=request.org_id,
                user_id=request.user_id,
                assistant_id=request.assistant_id,
                title=request.title,
                metadata=request.metadata,
                idempotency_key=request.idempotency_key,
            )
            conn.execute(
                """
                INSERT INTO agent_conversations (
                    id, org_id, user_id, assistant_id, title, status, created_at,
                    updated_at, archived_at, metadata_json, schema_version, idempotency_key
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    record.conversation_id,
                    record.org_id,
                    record.user_id,
                    record.assistant_id,
                    record.title,
                    record.status.value,
                    record.created_at,
                    record.updated_at,
                    record.archived_at,
                    Jsonb(record.metadata),
                    record.schema_version,
                    record.idempotency_key,
                ),
            )
            conn.commit()
            return record

    def get_conversation(
        self,
        *,
        org_id: str,
        user_id: str,
        conversation_id: str,
    ) -> ConversationRecord | None:
        """Return a conversation only when org and user scope match."""

        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM agent_conversations
                WHERE id = %s AND org_id = %s AND user_id = %s
                """,
                (conversation_id, org_id, user_id),
            ).fetchone()
        return self._conversation_record(row) if row is not None else None

    def list_conversations(
        self,
        *,
        org_id: str,
        user_id: str,
        limit: int,
        include_archived: bool = False,
    ) -> Sequence[ConversationRecord]:
        """Return scoped conversations ordered by latest update."""

        archived_filter = "" if include_archived else "AND status <> 'archived'"
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT * FROM agent_conversations
                WHERE org_id = %s AND user_id = %s {archived_filter}
                ORDER BY updated_at DESC
                LIMIT %s
                """,
                (org_id, user_id, limit),
            ).fetchall()
        return tuple(self._conversation_record(row) for row in rows)

    def list_messages(
        self,
        *,
        org_id: str,
        conversation_id: str,
        limit: int,
        include_deleted: bool = False,
    ) -> Sequence[MessageRecord]:
        """Return messages ordered by creation time."""

        deleted_filter = "" if include_deleted else "AND deleted_at IS NULL"
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT * FROM agent_messages
                WHERE org_id = %s AND conversation_id = %s {deleted_filter}
                ORDER BY created_at ASC
                LIMIT %s
                """,
                (org_id, conversation_id, limit),
            ).fetchall()
        return tuple(self._message_record(row) for row in rows)

    def append_message(self, message: MessageRecord) -> MessageRecord:
        """Append a runtime-created message."""

        with self._connect() as conn:
            self._insert_message(conn, message)
            conn.execute(
                "UPDATE agent_conversations SET updated_at = %s WHERE id = %s",
                (message.created_at, message.conversation_id),
            )
            conn.commit()
        return message

    def create_run_with_user_message(
        self,
        *,
        request: CreateRunRequest,
        conversation: ConversationRecord,
    ) -> tuple[RunRecord, MessageRecord, bool]:
        """Create a user message and run, or return an idempotent existing run."""

        context = request.runtime_context
        if context is None:
            raise RuntimeApiError(
                RuntimeErrorCode.VALIDATION_ERROR,
                "Runtime context is required.",
                http_status=status.HTTP_400_BAD_REQUEST,
                retryable=False,
            )

        with self._connect() as conn:
            if request.idempotency_key is not None:
                existing = conn.execute(
                    """
                    SELECT r.*, m.content_text AS user_content_text
                    FROM agent_runs r
                    JOIN agent_messages m ON m.id = r.user_message_id
                    WHERE r.org_id = %s AND r.user_id = %s AND r.idempotency_key = %s
                    """,
                    (context.org_id, context.user_id, request.idempotency_key),
                ).fetchone()
                if existing is not None:
                    if (
                        existing["conversation_id"],
                        existing["user_content_text"],
                    ) != (request.conversation_id, request.user_input):
                        raise RuntimeApiError(
                            RuntimeErrorCode.VALIDATION_ERROR,
                            Messages.Error.IDEMPOTENCY_CONFLICT,
                            http_status=status.HTTP_409_CONFLICT,
                            retryable=False,
                            correlation_id=context.trace_id,
                        )
                    run = self._run_record(existing)
                    message_row = conn.execute(
                        "SELECT * FROM agent_messages WHERE id = %s",
                        (run.user_message_id,),
                    ).fetchone()
                    return run, self._message_record(message_row), False

            user_message = self._message_for_run_request(
                conn=conn,
                request=request,
                conversation=conversation,
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
            if request.regenerate_from_message_id is None:
                self._insert_message(conn, user_message)
            self._insert_run(conn, run)
            if request.regenerate_from_message_id is None:
                conn.execute(
                    "UPDATE agent_messages SET run_id = %s WHERE id = %s",
                    (run.run_id, user_message.message_id),
                )
            conn.execute(
                "UPDATE agent_conversations SET updated_at = %s WHERE id = %s",
                (run.created_at, conversation.conversation_id),
            )
            conn.commit()
            if request.regenerate_from_message_id is None:
                user_message = user_message.model_copy(update={"run_id": run.run_id})
            return run, user_message, True

    def get_run(self, *, org_id: str, run_id: str) -> RunRecord | None:
        """Return a run scoped by organization."""

        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM agent_runs WHERE id = %s AND org_id = %s",
                (run_id, org_id),
            ).fetchone()
        return self._run_record(row) if row is not None else None

    def update_run_status(self, *, run_id: str, status: AgentRunStatus) -> RunRecord:
        """Update mutable run status and return the new record."""

        with self._connect() as conn:
            existing = conn.execute(
                "SELECT * FROM agent_runs WHERE id = %s", (run_id,)
            ).fetchone()
            updates: dict[str, object] = {"status": status.value}
            now = datetime.now(UTC)
            if status == AgentRunStatus.RUNNING and existing["started_at"] is None:
                updates["started_at"] = now
            if status in {
                AgentRunStatus.COMPLETED,
                AgentRunStatus.FAILED,
                AgentRunStatus.TIMED_OUT,
            }:
                updates["completed_at"] = now
            if status == AgentRunStatus.CANCELLED:
                updates["cancelled_at"] = now
            assignments = ", ".join(f"{key} = %s" for key in updates)
            row = conn.execute(
                f"UPDATE agent_runs SET {assignments} WHERE id = %s RETURNING *",
                (*updates.values(), run_id),
            ).fetchone()
            conn.commit()
        return self._run_record(row)

    def set_run_latest_sequence(
        self, *, run_id: str, latest_sequence_no: int
    ) -> RunRecord:
        """Persist latest event sequence for run inspection."""

        with self._connect() as conn:
            row = conn.execute(
                """
                UPDATE agent_runs SET latest_sequence_no = %s
                WHERE id = %s RETURNING *
                """,
                (latest_sequence_no, run_id),
            ).fetchone()
            conn.commit()
        return self._run_record(row)

    def record_approval_decision(
        self,
        *,
        record: ApprovalDecisionRecord,
    ) -> ApprovalDecisionRecord:
        """Persist an approval decision against the approval request row."""

        with self._connect() as conn:
            conn.execute(
                """
                UPDATE runtime_approval_requests
                SET status = %s, decided_by_user_id = %s, decision_reason = %s, decided_at = %s
                WHERE id = %s AND org_id = %s
                """,
                (
                    record.status.value,
                    record.decided_by_user_id,
                    record.reason,
                    record.decided_at,
                    record.approval_id,
                    record.org_id,
                ),
            )
            conn.commit()
        return record

    def get_approval_request(
        self,
        *,
        org_id: str,
        approval_id: str,
    ) -> ApprovalRequestRecord | None:
        """Return a pending or resolved approval request."""

        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT a.*, r.conversation_id, r.user_id
                FROM runtime_approval_requests a
                JOIN agent_runs r ON r.id = a.run_id
                WHERE a.id = %s AND a.org_id = %s
                """,
                (approval_id, org_id),
            ).fetchone()
        if row is None:
            return None
        return ApprovalRequestRecord(
            approval_id=row["id"],
            run_id=row["run_id"],
            conversation_id=row["conversation_id"],
            org_id=row["org_id"],
            user_id=row["user_id"],
            status=row["status"],
            created_at=row["created_at"],
            expires_at=row["expires_at"],
        )

    def write_audit_log(self, *, event_type: str, record: object) -> None:
        """Append an audit record for security-relevant actions."""

        data = record if isinstance(record, dict) else {"record": str(record)}
        now = datetime.now(UTC)
        metadata = (
            data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
        )
        audit_id = str(data.get("audit_event_id") or f"audit_{now.timestamp_ns()}")
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO runtime_audit_log (
                    id, org_id, user_id, actor_type, action, resource_type, resource_id,
                    run_id, trace_id, outcome, metadata_json_redacted, created_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    audit_id,
                    str(data.get("org_id", "unknown")),
                    data.get("user_id")
                    if isinstance(data.get("user_id"), str)
                    else None,
                    str(data.get("actor_type", "system")),
                    event_type,
                    str(data.get("resource_type", "runtime")),
                    str(data.get("resource_id", "unknown")),
                    data.get("run_id") if isinstance(data.get("run_id"), str) else None,
                    data.get("trace_id")
                    if isinstance(data.get("trace_id"), str)
                    else None,
                    str(data.get("outcome", "success")),
                    Jsonb(metadata),
                    now,
                ),
            )
            conn.commit()

    def delete_user_history(
        self,
        *,
        org_id: str,
        user_id: str,
        reason: str | None = None,
    ) -> HistoryDeletionResponse:
        """Tombstone user-visible history while preserving audit/event evidence."""

        now = datetime.now(UTC)
        audit_event_id = f"history_delete_{now.timestamp_ns()}"
        with self._connect() as conn:
            hold = conn.execute(
                """
                SELECT id FROM runtime_legal_holds
                WHERE org_id = %s
                  AND released_at IS NULL
                  AND (
                    (scope = 'org' AND resource_id = %s)
                    OR (scope = 'user' AND user_id = %s)
                    OR (
                        scope = 'conversation'
                        AND resource_id IN (
                            SELECT id FROM agent_conversations WHERE org_id = %s AND user_id = %s
                        )
                    )
                  )
                LIMIT 1
                """,
                (org_id, org_id, user_id, org_id, user_id),
            ).fetchone()
            if hold is not None:
                raise RuntimeApiError(
                    RuntimeErrorCode.VALIDATION_ERROR,
                    "Deletion is blocked by an active legal hold.",
                    http_status=status.HTTP_409_CONFLICT,
                    retryable=False,
                )
            conversations_archived = conn.execute(
                """
                UPDATE agent_conversations
                SET status = 'archived', archived_at = COALESCE(archived_at, %s), updated_at = %s
                WHERE org_id = %s AND user_id = %s AND status <> 'archived'
                """,
                (now, now, org_id, user_id),
            ).rowcount
            messages_tombstoned = conn.execute(
                """
                UPDATE agent_messages
                SET status = 'deleted', deleted_at = COALESCE(deleted_at, %s),
                    content_text = '[deleted by user request]'
                WHERE org_id = %s
                  AND conversation_id IN (
                    SELECT id FROM agent_conversations WHERE org_id = %s AND user_id = %s
                  )
                  AND deleted_at IS NULL
                """,
                (now, org_id, org_id, user_id),
            ).rowcount
            runs_cancelled = conn.execute(
                """
                UPDATE agent_runs
                SET status = 'cancelled', cancelled_at = COALESCE(cancelled_at, %s)
                WHERE org_id = %s AND user_id = %s
                  AND status NOT IN ('cancelled', 'completed', 'failed', 'timed_out')
                """,
                (now, org_id, user_id),
            ).rowcount
            events_row = conn.execute(
                """
                SELECT COUNT(*) AS count
                FROM runtime_events e
                JOIN agent_runs r ON r.id = e.run_id
                WHERE e.org_id = %s AND r.user_id = %s
                """,
                (org_id, user_id),
            ).fetchone()
            events_retained = int(events_row["count"]) if events_row is not None else 0
            conn.execute(
                """
                INSERT INTO runtime_audit_log (
                    id, org_id, user_id, actor_type, action, resource_type, resource_id,
                    run_id, trace_id, outcome, metadata_json_redacted, created_at
                )
                VALUES (%s, %s, %s, 'user', 'user_history_deleted', 'user_history', %s,
                        NULL, NULL, 'success', %s, %s)
                """,
                (
                    audit_event_id,
                    org_id,
                    user_id,
                    user_id,
                    Jsonb(
                        {
                            "reason": reason,
                            "conversations_archived": conversations_archived,
                            "messages_tombstoned": messages_tombstoned,
                            "runs_cancelled": runs_cancelled,
                            "events_retained": events_retained,
                        }
                    ),
                    now,
                ),
            )
            evidence_id = f"deletion_evidence_{now.timestamp_ns()}"
            conn.execute(
                """
                INSERT INTO runtime_deletion_evidence (
                    id, org_id, user_id, request_type, reason, conversations_archived,
                    messages_tombstoned, runs_cancelled, events_retained, audit_event_id,
                    created_at
                )
                VALUES (%s, %s, %s, 'user_history_delete', %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    evidence_id,
                    org_id,
                    user_id,
                    reason,
                    conversations_archived,
                    messages_tombstoned,
                    runs_cancelled,
                    events_retained,
                    audit_event_id,
                    now,
                ),
            )
            conn.commit()
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
        """Append one event with the next per-run sequence number."""

        with self._connect() as conn:
            run = conn.execute(
                "SELECT org_id FROM agent_runs WHERE id = %s FOR UPDATE",
                (event.run_id,),
            ).fetchone()
            sequence_row = conn.execute(
                """
                SELECT COALESCE(MAX(sequence_no), 0) + 1 AS next_sequence
                FROM runtime_events
                WHERE run_id = %s
                """,
                (event.run_id,),
            ).fetchone()
            envelope = RuntimeEventEnvelope(
                run_id=event.run_id,
                conversation_id=event.conversation_id,
                sequence_no=sequence_row["next_sequence"],
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
            conn.execute(
                """
                INSERT INTO runtime_events (
                    id, run_id, conversation_id, org_id, sequence_no, event_protocol_version,
                    source, event_type, parent_event_id, span_id, parent_span_id,
                    parent_task_id, task_id, subagent_id, display_title, summary, status,
                    trace_id, payload_json_redacted, metadata_json_redacted, visibility,
                    redaction_state, created_at
                )
                VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s
                )
                """,
                (
                    envelope.event_id,
                    envelope.run_id,
                    envelope.conversation_id,
                    run["org_id"],
                    envelope.sequence_no,
                    envelope.event_protocol_version,
                    envelope.source.value,
                    envelope.event_type.value,
                    envelope.parent_event_id,
                    envelope.span_id,
                    envelope.parent_span_id,
                    envelope.parent_task_id,
                    envelope.task_id,
                    envelope.subagent_id,
                    envelope.display_title,
                    envelope.summary,
                    envelope.status,
                    envelope.trace_id,
                    Jsonb(envelope.payload),
                    Jsonb(envelope.metadata),
                    envelope.visibility.value,
                    envelope.redaction_state.value,
                    envelope.created_at,
                ),
            )
            conn.commit()
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

        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM runtime_events
                WHERE org_id = %s AND run_id = %s AND sequence_no > %s
                ORDER BY sequence_no ASC
                """,
                (org_id, run_id, after_sequence),
            ).fetchall()
        return tuple(self._event_envelope(row) for row in rows)

    def get_latest_sequence(self, *, run_id: str) -> int:
        """Return latest persisted sequence number for a run."""

        with self._connect() as conn:
            row = conn.execute(
                "SELECT COALESCE(MAX(sequence_no), 0) AS latest FROM runtime_events WHERE run_id = %s",
                (run_id,),
            ).fetchone()
        return int(row["latest"])

    def enqueue_run(self, command: RuntimeRunCommand) -> None:
        """Enqueue a run command for workers."""

        self._enqueue_command(
            command_id=command.command_id,
            command_type=PersistenceValues.EventType.RUN_REQUESTED,
            org_id=command.org_id,
            aggregate_id=command.run_id,
            payload=command.model_dump(mode="json"),
        )

    def enqueue_cancel(self, command: RuntimeCancelCommand) -> None:
        """Enqueue a cancellation command for workers."""

        self._enqueue_command(
            command_id=command.command_id,
            command_type=PersistenceValues.EventType.RUN_CANCEL_REQUESTED,
            org_id=command.org_id,
            aggregate_id=command.run_id,
            payload=command.model_dump(mode="json"),
        )

    def enqueue_approval_resolved(
        self, command: RuntimeApprovalResolvedCommand
    ) -> None:
        """Enqueue an approval resolution command for workers."""

        self._enqueue_command(
            command_id=command.command_id,
            command_type=PersistenceValues.EventType.APPROVAL_RESOLVED,
            org_id=command.org_id,
            aggregate_id=command.run_id,
            payload=command.model_dump(mode="json"),
        )

    def claim_next(
        self,
        *,
        worker_id: str,
        lock_expires_at: datetime,
    ) -> RuntimeWorkerClaim | None:
        """Claim the next available runtime command using SKIP LOCKED."""

        with self._connect() as conn:
            row = conn.execute(
                """
                WITH next_event AS (
                    SELECT id
                    FROM runtime_outbox_events
                    WHERE (
                        status IN ('pending', 'retry')
                        OR (status = 'claimed' AND lock_expires_at <= now())
                    )
                    AND available_at <= now()
                    ORDER BY available_at ASC, created_at ASC
                    LIMIT 1
                    FOR UPDATE SKIP LOCKED
                )
                UPDATE runtime_outbox_events outbox
                SET status = 'claimed',
                    attempts = outbox.attempts + 1,
                    locked_by = %s,
                    lock_expires_at = %s,
                    updated_at = now()
                FROM next_event
                WHERE outbox.id = next_event.id
                RETURNING outbox.*
                """,
                (worker_id, lock_expires_at),
            ).fetchone()
            conn.commit()
        if row is None:
            return None
        payload = dict(row["payload_json"])
        return RuntimeWorkerClaim(
            command_id=row["id"],
            command_type=row["event_type"],
            org_id=row["org_id"],
            run_id=payload.get("run_id")
            if isinstance(payload.get("run_id"), str)
            else row["aggregate_id"],
            approval_id=payload.get("approval_id")
            if isinstance(payload.get("approval_id"), str)
            else None,
            locked_by=row["locked_by"],
            lock_expires_at=row["lock_expires_at"],
            attempts=row["attempts"],
            payload=payload,
        )

    def mark_complete(self, *, result: RuntimeWorkerResult) -> None:
        """Mark a claimed command complete."""

        self._mark_outbox(result=result, status_value=OutboxStatus.COMPLETED)

    def mark_retry(self, *, result: RuntimeWorkerResult) -> None:
        """Release a claimed command for retry after its available time."""

        self._mark_outbox(result=result, status_value=OutboxStatus.RETRY)

    def mark_dead_letter(self, *, result: RuntimeWorkerResult) -> None:
        """Mark a command permanently failed after retries are exhausted."""

        self._mark_outbox(result=result, status_value=OutboxStatus.DEAD_LETTER)

    def _message_for_run_request(
        self,
        *,
        conn: psycopg.Connection,
        request: CreateRunRequest,
        conversation: ConversationRecord,
    ) -> MessageRecord:
        """Return the existing or newly created user message for a run request."""

        context = request.runtime_context
        if request.regenerate_from_message_id is not None:
            regenerated = self._regenerated_user_message(
                conn=conn,
                message_id=request.regenerate_from_message_id,
            )
            if regenerated is not None:
                return regenerated
        parent_message_id = request.parent_message_id or self._latest_message_id(
            conn=conn,
            org_id=conversation.org_id,
            conversation_id=conversation.conversation_id,
        )
        return MessageRecord(
            conversation_id=conversation.conversation_id,
            org_id=conversation.org_id,
            run_id=None,
            role=MessageRole.USER,
            content_text=request.user_input,
            content_format=request.content_format,
            content=tuple(
                part.model_dump(mode="json", exclude_none=True)
                for part in request.content
            ),
            attachments=tuple(
                attachment.model_dump(mode="json", exclude_none=True)
                for attachment in request.attachments
            ),
            quote=request.quote,
            metadata=self._message_metadata(request),
            parent_message_id=parent_message_id,
            source_message_id=request.source_message_id,
            branch_id=request.branch_id,
            trace_id=context.trace_id,
        )

    def _regenerated_user_message(
        self,
        *,
        conn: psycopg.Connection,
        message_id: str,
    ) -> MessageRecord | None:
        row = conn.execute(
            "SELECT * FROM agent_messages WHERE id = %s",
            (message_id,),
        ).fetchone()
        if row is None:
            return None
        message = self._message_record(row)
        if message.role == MessageRole.USER:
            return message
        if message.parent_message_id is None:
            return None
        parent_row = conn.execute(
            "SELECT * FROM agent_messages WHERE id = %s",
            (message.parent_message_id,),
        ).fetchone()
        if parent_row is None:
            return None
        parent = self._message_record(parent_row)
        return parent if parent.role == MessageRole.USER else None

    @staticmethod
    def _latest_message_id(
        *,
        conn: psycopg.Connection,
        org_id: str,
        conversation_id: str,
    ) -> str | None:
        row = conn.execute(
            """
            SELECT id FROM agent_messages
            WHERE org_id = %s AND conversation_id = %s AND deleted_at IS NULL
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (org_id, conversation_id),
        ).fetchone()
        return row["id"] if row is not None else None

    @staticmethod
    def _message_metadata(request: CreateRunRequest) -> dict[str, object]:
        metadata: dict[str, object] = {}
        if request.quote is not None:
            metadata["quote"] = request.quote
        if request.source_message_id is not None:
            metadata["source_message_id"] = request.source_message_id
        if request.branch_id is not None:
            metadata["branch_id"] = request.branch_id
        if request.regenerate_from_message_id is not None:
            metadata["regenerate_from_message_id"] = request.regenerate_from_message_id
        return metadata

    def _connect(self) -> psycopg.Connection:
        return psycopg.connect(self.database_url, row_factory=dict_row)

    def _enqueue_command(
        self,
        *,
        command_id: str,
        command_type: str,
        org_id: str,
        aggregate_id: str,
        payload: dict[str, object],
    ) -> None:
        now = datetime.now(UTC)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO runtime_outbox_events (
                    id, aggregate_type, aggregate_id, org_id, event_type, payload_json,
                    status, attempts, available_at, locked_by, lock_expires_at, created_at, updated_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, 'pending', 0, %s, NULL, NULL, %s, %s)
                """,
                (
                    command_id,
                    PersistenceValues.AggregateType.AGENT_RUN,
                    aggregate_id,
                    org_id,
                    command_type,
                    Jsonb(payload),
                    now,
                    now,
                    now,
                ),
            )
            conn.commit()

    def _mark_outbox(
        self, *, result: RuntimeWorkerResult, status_value: OutboxStatus
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE runtime_outbox_events
                SET status = %s, available_at = COALESCE(%s, available_at),
                    locked_by = NULL, lock_expires_at = NULL, updated_at = now()
                WHERE id = %s
                """,
                (status_value.value, result.retry_available_at, result.command_id),
            )
            conn.commit()

    @classmethod
    def _conversation_record(cls, row: dict[str, object]) -> ConversationRecord:
        return ConversationRecord(
            conversation_id=row["id"],
            org_id=row["org_id"],
            user_id=row["user_id"],
            assistant_id=row["assistant_id"],
            title=row["title"],
            status=row["status"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            archived_at=row["archived_at"],
            metadata=dict(row["metadata_json"]),
            schema_version=row["schema_version"],
            idempotency_key=row["idempotency_key"],
        )

    @classmethod
    def _message_record(cls, row: dict[str, object]) -> MessageRecord:
        return MessageRecord(
            message_id=row["id"],
            conversation_id=row["conversation_id"],
            org_id=row["org_id"],
            run_id=row["run_id"],
            role=row["role"],
            content_text=row["content_text"],
            content_format=row["content_format"],
            content=tuple(dict(part) for part in row["content_json"]),
            attachments=tuple(
                dict(attachment) for attachment in row["attachments_json"]
            ),
            quote=dict(row["quote_json"]) if row["quote_json"] is not None else None,
            metadata=dict(row["metadata_json"]),
            parent_message_id=row["parent_message_id"],
            source_message_id=row["source_message_id"],
            branch_id=row["branch_id"],
            token_count=row["token_count"],
            trace_id=row["trace_id"],
            status=row["status"],
            created_at=row["created_at"],
            edited_at=row["edited_at"],
            deleted_at=row["deleted_at"],
        )

    @classmethod
    def _run_record(cls, row: dict[str, object]) -> RunRecord:
        safe_error = None
        if row["safe_error_code"] is not None and row["safe_error_message"] is not None:
            safe_error = RuntimeErrorEnvelope(
                code=row["safe_error_code"],
                safe_message=row["safe_error_message"],
                retryable=False,
                correlation_id=row["trace_id"],
            )
        return RunRecord(
            run_id=row["id"],
            conversation_id=row["conversation_id"],
            org_id=row["org_id"],
            user_id=row["user_id"],
            user_message_id=row["user_message_id"],
            idempotency_key=row["idempotency_key"],
            trace_id=row["trace_id"],
            status=row["status"],
            model_provider=row["model_provider"],
            model_name=row["model_name"],
            runtime_context=dict(row["runtime_context_json"]),
            request_options=dict(row["request_options_json"]),
            created_at=row["created_at"],
            started_at=row["started_at"],
            completed_at=row["completed_at"],
            cancelled_at=row["cancelled_at"],
            safe_error=safe_error,
            latest_sequence_no=row["latest_sequence_no"],
        )

    @classmethod
    def _event_envelope(cls, row: dict[str, object]) -> RuntimeEventEnvelope:
        return RuntimeEventEnvelope(
            event_id=row["id"],
            run_id=row["run_id"],
            conversation_id=row["conversation_id"],
            sequence_no=row["sequence_no"],
            source=row["source"],
            event_type=row["event_type"],
            trace_id=row["trace_id"],
            parent_event_id=row["parent_event_id"],
            span_id=row["span_id"],
            parent_span_id=row["parent_span_id"],
            parent_task_id=row["parent_task_id"],
            task_id=row["task_id"],
            subagent_id=row["subagent_id"],
            display_title=row["display_title"],
            summary=row["summary"],
            status=row["status"],
            activity_kind=RuntimeEventPresentationProjector.activity_kind_for(
                event_type=RuntimeApiEventType(row["event_type"]),
                source=StreamEventSource(row["source"]),
            ),
            visibility=row["visibility"],
            redaction_state=row["redaction_state"],
            payload=dict(row["payload_json_redacted"]),
            metadata=dict(row["metadata_json_redacted"]),
            created_at=row["created_at"],
        )

    @classmethod
    def _insert_message(cls, conn: psycopg.Connection, message: MessageRecord) -> None:
        conn.execute(
            """
            INSERT INTO agent_messages (
                id, conversation_id, org_id, run_id, role, content_text, content_format,
                content_json, attachments_json, quote_json, metadata_json,
                parent_message_id, source_message_id, branch_id, token_count, trace_id,
                status, created_at, edited_at, deleted_at
            )
            VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s
            )
            """,
            (
                message.message_id,
                message.conversation_id,
                message.org_id,
                message.run_id,
                message.role.value,
                message.content_text,
                message.content_format,
                Jsonb(message.content),
                Jsonb(message.attachments),
                Jsonb(message.quote) if message.quote is not None else None,
                Jsonb(message.metadata),
                message.parent_message_id,
                message.source_message_id,
                message.branch_id,
                message.token_count,
                message.trace_id,
                message.status.value,
                message.created_at,
                message.edited_at,
                message.deleted_at,
            ),
        )

    @classmethod
    def _insert_run(cls, conn: psycopg.Connection, run: RunRecord) -> None:
        conn.execute(
            """
            INSERT INTO agent_runs (
                id, conversation_id, org_id, user_id, user_message_id, idempotency_key,
                trace_id, status, model_provider, model_name, model_config_json,
                runtime_context_json, runtime_version, request_options_json,
                latest_sequence_no, row_version, created_at, started_at, completed_at,
                cancelled_at, safe_error_code, safe_error_message
            )
            VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                NULL, %s, %s, 1, %s, %s, %s, %s, %s, %s
            )
            """,
            (
                run.run_id,
                run.conversation_id,
                run.org_id,
                run.user_id,
                run.user_message_id,
                run.idempotency_key,
                run.trace_id,
                run.status.value,
                run.model_provider,
                run.model_name,
                Jsonb(run.runtime_context.model_profile.model_dump(mode="json")),
                Jsonb(run.runtime_context.model_dump(mode="json")),
                Jsonb(run.request_options),
                run.latest_sequence_no,
                run.created_at,
                run.started_at,
                run.completed_at,
                run.cancelled_at,
                run.safe_error.code.value if run.safe_error else None,
                run.safe_error.safe_message if run.safe_error else None,
            ),
        )
