"""Worker-side audit emission for privileged run, tool-call, approval, and fork outcomes."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, ClassVar

from agent_runtime.api.ports import PersistencePort
from agent_runtime.observability.http_logging import LoggingConfigurator
from runtime_api.schemas import (
    AgentRunStatus,
    ApprovalDecision,
    ApprovalRequestRecord,
    RunRecord,
)


class _Actions:
    """Stable audit ``action`` strings emitted by the worker."""

    RUN_STARTED = "run_started"
    RUN_COMPLETED = "run_completed"
    RUN_FAILED = "run_failed"
    RUN_TIMED_OUT = "run_timed_out"
    APPROVAL_DECISION = "approval_decision"
    TOOL_CALL_OUTCOME = "tool_call_outcome"
    CONVERSATION_FORK = "conversation.fork"


class _Outcomes:
    """``outcome`` values per the runtime_audit_log CHECK constraint."""

    SUCCESS = "success"
    FAILURE = "failure"
    DENIED = "denied"


class _ResourceTypes:
    AGENT_RUN = "agent_run"
    APPROVAL = "approval"
    TOOL_CALL = "tool_call"
    # Fork target (the new conversation row). Metadata carries both
    # source_conversation_id and share_id so SIEM queries can pivot on either side.
    CONVERSATION = "conversation"


class _ActorTypes:
    WORKER = "worker"
    USER = "user"
    # System-driven rejections (expiry sweeper, membership cascade) use
    # actor_type=system so SIEM dashboards can split background from operator decisions.
    SYSTEM = "system"


class WorkerAuditEmitter:
    """Typed audit-emission surface for the runtime worker.

    The constructor takes the same async persistence port the handler holds.
    Each method captures one privileged-action outcome. Errors raised by the
    store are caught and logged via the structured logger so audit failures
    never cascade into worker failures -- the durability guarantee comes from
    the chain + retry semantics in the persistence layer, not from this
    emitter.
    """

    _LOGGER_NAME: ClassVar[str] = "runtime_worker.audit"

    def __init__(self, persistence: PersistencePort) -> None:
        self._persistence = persistence
        self._logger = LoggingConfigurator.get_logger(self._LOGGER_NAME)

    async def emit_run_started(self, run: RunRecord) -> None:
        """Emit a ``run_started`` audit event for the given run record."""
        await self._emit(
            event_type=_Actions.RUN_STARTED,
            run=run,
            actor_type=_ActorTypes.WORKER,
            resource_type=_ResourceTypes.AGENT_RUN,
            resource_id=run.run_id,
            outcome=_Outcomes.SUCCESS,
            metadata={"conversation_id": run.conversation_id},
        )

    async def emit_run_completed(
        self,
        run: RunRecord,
        *,
        duration_ms: int | None = None,
    ) -> None:
        """Emit a ``run_completed`` audit event, optionally recording elapsed time."""
        metadata: dict[str, Any] = {
            "conversation_id": run.conversation_id,
            "status": AgentRunStatus.COMPLETED.value,
        }
        if duration_ms is not None:
            metadata["duration_ms"] = int(duration_ms)
        await self._emit(
            event_type=_Actions.RUN_COMPLETED,
            run=run,
            actor_type=_ActorTypes.WORKER,
            resource_type=_ResourceTypes.AGENT_RUN,
            resource_id=run.run_id,
            outcome=_Outcomes.SUCCESS,
            metadata=metadata,
        )

    async def emit_run_failed(
        self,
        run: RunRecord,
        *,
        status: AgentRunStatus,
        error_class: str | None = None,
        error_code: str | None = None,
        duration_ms: int | None = None,
    ) -> None:
        """Emit a ``run_failed`` or ``run_timed_out`` audit event depending on ``status``."""
        action = (
            _Actions.RUN_TIMED_OUT
            if status is AgentRunStatus.TIMED_OUT
            else _Actions.RUN_FAILED
        )
        metadata: dict[str, Any] = {
            "conversation_id": run.conversation_id,
            "status": status.value,
        }
        if error_class is not None:
            metadata["error_class"] = error_class
        if error_code is not None:
            metadata["error_code"] = error_code
        if duration_ms is not None:
            metadata["duration_ms"] = int(duration_ms)
        await self._emit(
            event_type=action,
            run=run,
            actor_type=_ActorTypes.WORKER,
            resource_type=_ResourceTypes.AGENT_RUN,
            resource_id=run.run_id,
            outcome=_Outcomes.FAILURE,
            metadata=metadata,
        )

    async def emit_approval_decision(
        self,
        approval: ApprovalRequestRecord,
        *,
        decision: ApprovalDecision,
        decided_by_user_id: str | None,
        reason: str | None = None,
    ) -> None:
        """Emit an ``approval_decision`` audit event for an approved or denied approval request."""
        outcome = (
            _Outcomes.SUCCESS
            if decision is ApprovalDecision.APPROVED
            else _Outcomes.DENIED
        )
        metadata: dict[str, Any] = {
            "decision": decision.value,
            "approval_id": approval.approval_id,
            "run_id": approval.run_id,
        }
        if decided_by_user_id:
            metadata["decided_by_user_id"] = decided_by_user_id
        # Short reason code ("expired", "recipient_membership_revoked") lets SIEM
        # dashboards split background-driven decisions without parsing free text.
        if reason:
            metadata["reason"] = reason
        # Promote actor_type to system when the decider is the runtime sentinel so
        # the SIEM exporter can distinguish background from operator decisions.
        from agent_runtime.api.constants import Values  # local: avoid cycle

        actor_type = (
            _ActorTypes.SYSTEM
            if decided_by_user_id == Values.SYSTEM_USER_ID
            else _ActorTypes.USER
        )
        await self._emit(
            event_type=_Actions.APPROVAL_DECISION,
            org_id=approval.org_id,
            user_id=approval.user_id,
            run_id=approval.run_id,
            actor_type=actor_type,
            resource_type=_ResourceTypes.APPROVAL,
            resource_id=approval.approval_id,
            outcome=outcome,
            metadata=metadata,
        )

    async def emit_tool_call_outcome(
        self,
        run: RunRecord,
        *,
        tool_name: str,
        call_id: str,
        outcome: str,
        duration_ms: int | None = None,
        error_code: str | None = None,
    ) -> None:
        """Emit a ``tool_call_outcome`` audit event; tool inputs/outputs are never included."""
        # Only tool name, call_id, outcome enum, and timing/error codes go in metadata —
        # payload content is excluded to avoid leaking sensitive data into the audit log.
        metadata: dict[str, Any] = {
            "tool_name": tool_name,
            "call_id": call_id,
            "outcome": outcome,
        }
        if duration_ms is not None:
            metadata["duration_ms"] = int(duration_ms)
        if error_code is not None:
            metadata["error_code"] = error_code
        await self._emit(
            event_type=_Actions.TOOL_CALL_OUTCOME,
            run=run,
            actor_type=_ActorTypes.WORKER,
            resource_type=_ResourceTypes.TOOL_CALL,
            resource_id=call_id,
            outcome=_Outcomes.SUCCESS
            if outcome.lower() in {"completed", "success"}
            else _Outcomes.FAILURE,
            metadata=metadata,
        )

    async def emit_conversation_fork(
        self,
        *,
        org_id: str,
        actor_user_id: str,
        source_conversation_id: str,
        target_conversation_id: str,
        snapshot_at: datetime,
        message_count: int,
        share_id: str | None = None,
        from_message_id: str | None = None,
        orphan_warnings: int = 0,
    ) -> None:
        """Emit a ``conversation.fork`` audit event for share-initiated or self-initiated forks.

        Exactly one of ``share_id`` or ``from_message_id`` is populated so SIEM queries can
        disambiguate the two fork pathways. ``orphan_warnings`` counts messages whose parent
        could not be resolved in the snapshot set — a data-integrity signal only.
        """

        metadata: dict[str, Any] = {
            "source_conversation_id": source_conversation_id,
            "target_conversation_id": target_conversation_id,
            "snapshot_at": snapshot_at.isoformat(),
            "message_count": int(message_count),
        }
        if share_id is not None:
            metadata["share_id"] = share_id
        if from_message_id is not None:
            metadata["from_message_id"] = from_message_id
        if orphan_warnings > 0:
            metadata["orphan_parent_warnings"] = int(orphan_warnings)
        await self._emit(
            event_type=_Actions.CONVERSATION_FORK,
            org_id=org_id,
            user_id=actor_user_id,
            actor_type=_ActorTypes.USER,
            resource_type=_ResourceTypes.CONVERSATION,
            resource_id=target_conversation_id,
            outcome=_Outcomes.SUCCESS,
            metadata=metadata,
        )

    async def _emit(
        self,
        *,
        event_type: str,
        run: RunRecord | None = None,
        org_id: str | None = None,
        user_id: str | None = None,
        run_id: str | None = None,
        actor_type: str,
        resource_type: str,
        resource_id: str,
        outcome: str,
        metadata: dict[str, Any],
    ) -> None:
        """Build and write one audit record; swallows store errors so callers are never interrupted."""
        record_org_id = org_id or (run.org_id if run else None) or "unknown"
        record_user_id = (
            user_id if user_id is not None else (run.user_id if run else None)
        )
        record_run_id = run_id or (run.run_id if run else None)
        record: dict[str, object] = {
            "audit_id": _AuditIdGenerator.next(event_type=event_type),
            "org_id": record_org_id,
            "user_id": record_user_id,
            "actor_type": actor_type,
            "resource_type": resource_type,
            "resource_id": resource_id,
            "run_id": record_run_id,
            "trace_id": getattr(run, "trace_id", None) if run else None,
            "outcome": outcome,
            "metadata": dict(metadata),
        }
        try:
            await self._persistence.write_audit_log(
                event_type=event_type, record=record
            )
        except Exception as exc:
            # Audit emission must never break the worker. Log the failure
            # with structured fields and move on -- the chain still
            # records the gap (the next successful append will sit at a
            # seq beyond what we expected).
            self._logger.exception(
                "audit_emit_failed",
                error_class=type(exc).__name__,
                metadata={
                    "event_type": event_type,
                    "resource_type": resource_type,
                    "resource_id": resource_id,
                },
            )


class _AuditIdGenerator:
    """Generate deterministic, unique audit ids for emitted events."""

    _COUNTER: ClassVar[int] = 0

    @classmethod
    def next(cls, *, event_type: str) -> str:
        """Return a unique audit id embedding the event type, nanosecond timestamp, and a counter."""
        cls._COUNTER += 1
        ts_ns = int(datetime.now(timezone.utc).timestamp() * 1_000_000_000)
        return f"audit_{event_type}_{ts_ns}_{cls._COUNTER}"
