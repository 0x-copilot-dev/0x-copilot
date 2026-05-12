"""Approval lifecycle coordinator (P22 / PR 4).

Owns: ``list_assigned_approvals`` (inbox read), ``record_approval_decision``,
``request_approval_undo``. Single source of truth for approval-state
transitions.

Approvals cover both human-in-the-loop decisions and MCP auth resolution.
Multi-fire safe — token rotation mid-run fires the same cycle again. Resume
happens via a separate ``APPROVAL_RESOLVED`` queue command, not inline.
"""

from __future__ import annotations

import asyncio
import base64
from datetime import datetime, timedelta, timezone

from starlette import status

from agent_runtime.api.constants import Keys, Messages, Values
from agent_runtime.api.events import RuntimeEventProducer
from agent_runtime.api.membership import (
    InMemoryWorkspaceMembershipResolver,
    MembershipResolverUnavailable,
    WorkspaceMembershipResolver,
)
from agent_runtime.api.notifications import (
    LoggingNotificationDispatcher,
    NotificationDispatcher,
)
from agent_runtime.api.ports import PersistencePort, RuntimeQueuePort
from agent_runtime.execution.contracts import RuntimeErrorCode, StreamEventSource
from agent_runtime.observability.approval_metrics import (
    ApprovalMetrics,
    ForwardInvalidReason,
)
from agent_runtime.observability.queue_propagation import QueueTracePropagator
from runtime_api.http.errors import RuntimeApiError
from runtime_api.schemas import (
    ApprovalDecision,
    ApprovalDecisionRecord,
    ApprovalDecisionRequest,
    ApprovalDecisionResponse,
    ApprovalForwardTarget,
    ApprovalRequestRecord,
    ApprovalStatus,
    ApprovalUndoResponse,
    AssignedApproval,
    AssignedApprovalsResponse,
    RuntimeApiEventType,
    RuntimeApprovalResolvedCommand,
    RunRecord,
    UNDO_WINDOW_SECONDS,
)


class ApprovalCoordinator:
    """Coordinate approval lifecycle commands and inbox reads."""

    APPROVAL_FORWARD_MAX_CHAIN_DEPTH = 3

    APPROVAL_FORWARDABLE_KINDS = frozenset(
        {
            Values.ApprovalKind.ACTION,
            Values.ApprovalKind.MCP_TOOL,
        }
    )

    def __init__(
        self,
        *,
        persistence: PersistencePort,
        queue: RuntimeQueuePort,
        event_producer: RuntimeEventProducer,
        membership_resolver: WorkspaceMembershipResolver | None = None,
        notification_dispatcher: NotificationDispatcher | None = None,
    ) -> None:
        self._persistence = persistence
        self._queue = queue
        self._event_producer = event_producer
        self._membership_resolver: WorkspaceMembershipResolver = (
            membership_resolver or InMemoryWorkspaceMembershipResolver()
        )
        self._notifications: NotificationDispatcher = (
            notification_dispatcher or LoggingNotificationDispatcher()
        )
        self._approval_metrics = ApprovalMetrics()

    async def list_assigned_approvals(
        self,
        *,
        org_id: str,
        user_id: str,
        status_filter: ApprovalStatus,
        limit: int,
        cursor: str | None,
    ) -> AssignedApprovalsResponse:
        """Return the recipient inbox view."""

        bounded = min(
            max(1, limit),
            Values.MAX_ASSIGNED_APPROVAL_LIMIT,
        )
        decoded_cursor = self._decode_assigned_cursor(cursor)
        records = await self._persistence.list_assigned_approvals(
            org_id=org_id,
            requested_by_user_id=user_id,
            status=status_filter.value,
            limit=bounded,
            cursor=decoded_cursor,
        )
        approvals = tuple(self._record_to_assigned(record) for record in records)
        next_cursor = (
            self._encode_assigned_cursor(
                records[-1].created_at, records[-1].approval_id
            )
            if len(records) == bounded and records
            else None
        )
        return AssignedApprovalsResponse(
            approvals=approvals,
            next_cursor=next_cursor,
        )

    @classmethod
    def _record_to_assigned(cls, record: ApprovalRequestRecord) -> AssignedApproval:
        approval_kind = record.metadata.get(Keys.Field.APPROVAL_KIND)
        action_summary = record.metadata.get(Keys.Field.ACTION_SUMMARY)
        risk_class = record.metadata.get("risk_level") or record.metadata.get(
            "risk_class"
        )
        forwarded_by = record.metadata.get(Keys.Field.FORWARDED_BY_USER_ID)
        return AssignedApproval(
            approval_id=record.approval_id,
            conversation_id=record.conversation_id,
            run_id=record.run_id,
            approval_kind=approval_kind if isinstance(approval_kind, str) else "action",
            status=record.status,
            chain_parent_approval_id=record.chain_parent_approval_id,
            forwarded_by_user_id=forwarded_by
            if isinstance(forwarded_by, str)
            else None,
            forwarded_at=record.forwarded_at,
            action_summary=action_summary if isinstance(action_summary, str) else "",
            risk_class=risk_class if isinstance(risk_class, str) else None,
            expires_at=record.expires_at,
            created_at=record.created_at,
        )

    @staticmethod
    def _encode_assigned_cursor(created_at: datetime, approval_id: str) -> str:
        raw = f"{created_at.isoformat()}|{approval_id}".encode()
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")

    @staticmethod
    def _decode_assigned_cursor(cursor: str | None) -> tuple[datetime, str] | None:
        if cursor is None:
            return None
        padding = "=" * (-len(cursor) % 4)
        try:
            raw = base64.urlsafe_b64decode(cursor + padding).decode()
            iso, approval_id = raw.split("|", 1)
            return datetime.fromisoformat(iso), approval_id
        except (ValueError, UnicodeDecodeError):
            return None

    async def record_approval_decision(
        self,
        *,
        org_id: str,
        approval_id: str,
        request: ApprovalDecisionRequest,
    ) -> ApprovalDecisionResponse:
        """Persist an approval decision and enqueue the worker resume command."""

        approval = await self._persistence.get_approval_request(
            org_id=org_id, approval_id=approval_id
        )
        if approval is None:
            raise RuntimeApiError(
                RuntimeErrorCode.CAPABILITY_NOT_FOUND,
                Messages.Error.APPROVAL_NOT_FOUND,
                http_status=status.HTTP_404_NOT_FOUND,
                retryable=False,
            )
        if approval.user_id != request.decided_by_user_id:
            raise RuntimeApiError(
                RuntimeErrorCode.PERMISSION_DENIED,
                "Approval decision user does not match approval scope.",
                http_status=status.HTTP_403_FORBIDDEN,
                retryable=False,
            )
        if request.decision is ApprovalDecision.FORWARDED:
            return await self._decide_forwarded(
                approval=approval,
                request=request,
            )
        status_value = (
            ApprovalStatus.APPROVED
            if request.decision.value == ApprovalStatus.APPROVED.value
            else ApprovalStatus.REJECTED
        )
        record = await self._persistence.record_approval_decision(
            record=ApprovalDecisionRecord(
                approval_id=approval.approval_id,
                run_id=approval.run_id,
                conversation_id=approval.conversation_id,
                org_id=approval.org_id,
                user_id=approval.user_id,
                status=status_value,
                decided_by_user_id=request.decided_by_user_id,
                reason=request.reason,
                answer=request.answer,
            )
        )
        run = await self._run_for_scope(
            org_id=record.org_id,
            user_id=record.user_id,
            run_id=record.run_id,
        )
        approval_kind = approval.metadata.get(Keys.Field.APPROVAL_KIND)
        await self._event_producer.append_api_event(
            run=run,
            source=StreamEventSource.RUNTIME,
            event_type=RuntimeApiEventType.APPROVAL_RESOLVED,
            payload={
                Keys.Field.APPROVAL_ID: record.approval_id,
                Keys.Field.APPROVAL_KIND: approval_kind,
                Keys.Field.STATUS: self._wire_status_for(
                    approval_kind=approval_kind,
                    record_status=record.status.value,
                ),
                Keys.Payload.MESSAGE: Messages.Event.APPROVAL_RESOLVED,
                Keys.Field.DECISION: record.status.value,
            },
        )
        await self._queue.enqueue_approval_resolved(
            RuntimeApprovalResolvedCommand(
                approval_id=record.approval_id,
                run_id=record.run_id,
                org_id=record.org_id,
                decision=request.decision,
                answer=request.answer,
                trace_propagation=QueueTracePropagator.inject(),
            )
        )
        await self._persistence.write_audit_log(
            event_type="approval_decision_recorded",
            record={
                "org_id": record.org_id,
                "user_id": record.user_id,
                "resource_type": "approval",
                "resource_id": record.approval_id,
                "run_id": record.run_id,
                "outcome": "success",
                "metadata": {"status": record.status.value},
            },
        )
        return ApprovalDecisionResponse(
            approval_id=record.approval_id,
            run_id=record.run_id,
            status=record.status,
            decided_at=record.decided_at,
            undo_expires_at=self._undo_expires_at_for(approval=approval, record=record),
        )

    @staticmethod
    def _undo_expires_at_for(
        *,
        approval: ApprovalRequestRecord,
        record: ApprovalDecisionRecord,
    ) -> datetime | None:
        if record.status is not ApprovalStatus.APPROVED:
            return None
        if approval.metadata.get("reversible") != "yes":
            return None
        return record.decided_at + timedelta(seconds=UNDO_WINDOW_SECONDS)

    async def request_approval_undo(
        self,
        *,
        org_id: str,
        approval_id: str,
        decided_by_user_id: str,
    ) -> ApprovalUndoResponse:
        """Record the user's intent to undo an approved + reversible action."""

        approval = await self._persistence.get_approval_request(
            org_id=org_id, approval_id=approval_id
        )
        if approval is None:
            raise RuntimeApiError(
                RuntimeErrorCode.CAPABILITY_NOT_FOUND,
                Messages.Error.APPROVAL_NOT_FOUND,
                http_status=status.HTTP_404_NOT_FOUND,
                retryable=False,
            )
        if approval.user_id != decided_by_user_id:
            raise RuntimeApiError(
                RuntimeErrorCode.PERMISSION_DENIED,
                "Approval decision user does not match approval scope.",
                http_status=status.HTTP_403_FORBIDDEN,
                retryable=False,
            )
        if approval.status is not ApprovalStatus.APPROVED:
            raise RuntimeApiError(
                RuntimeErrorCode.VALIDATION_ERROR,
                "Only approved decisions are reversible.",
                http_status=status.HTTP_422_UNPROCESSABLE_ENTITY,
                retryable=False,
            )
        if approval.metadata.get("reversible") != "yes":
            raise RuntimeApiError(
                RuntimeErrorCode.VALIDATION_ERROR,
                "This approval was not flagged reversible.",
                http_status=status.HTTP_422_UNPROCESSABLE_ENTITY,
                retryable=False,
            )
        decided_at = self._decision_decided_at(approval=approval)
        if decided_at is None:
            raise RuntimeApiError(
                RuntimeErrorCode.VALIDATION_ERROR,
                "Approval has no decision yet.",
                http_status=status.HTTP_422_UNPROCESSABLE_ENTITY,
                retryable=False,
            )
        undo_expires_at = decided_at + timedelta(seconds=UNDO_WINDOW_SECONDS)
        now = datetime.now(timezone.utc)
        if now >= undo_expires_at:
            raise RuntimeApiError(
                RuntimeErrorCode.VALIDATION_ERROR,
                "Undo window expired.",
                http_status=status.HTTP_410_GONE,
                retryable=False,
            )
        run = await self._run_for_scope(
            org_id=approval.org_id,
            user_id=approval.user_id,
            run_id=approval.run_id,
        )
        approval_kind = approval.metadata.get(Keys.Field.APPROVAL_KIND)
        await self._event_producer.append_api_event(
            run=run,
            source=StreamEventSource.RUNTIME,
            event_type=RuntimeApiEventType.APPROVAL_UNDO_REQUESTED,
            payload={
                Keys.Field.APPROVAL_ID: approval.approval_id,
                Keys.Field.APPROVAL_KIND: approval_kind,
                "decided_by_user_id": decided_by_user_id,
                "undo_requested_at": now.isoformat(),
                "undo_expires_at": undo_expires_at.isoformat(),
            },
        )
        await self._persistence.write_audit_log(
            event_type="approval_undo_requested",
            record={
                "org_id": approval.org_id,
                "user_id": approval.user_id,
                "resource_type": "approval",
                "resource_id": approval.approval_id,
                "run_id": approval.run_id,
                "outcome": "success",
                "metadata": {
                    "approval_kind": approval_kind,
                    "vendor": approval.metadata.get("vendor"),
                    "tool_name": approval.metadata.get("tool_name"),
                    "undo_expires_at": undo_expires_at.isoformat(),
                    "undo_requested_at": now.isoformat(),
                },
            },
        )
        return ApprovalUndoResponse(
            approval_id=approval.approval_id,
            run_id=approval.run_id,
            undo_requested_at=now,
            undo_expires_at=undo_expires_at,
        )

    @staticmethod
    def _decision_decided_at(*, approval: ApprovalRequestRecord) -> datetime | None:
        raw = approval.metadata.get("decided_at")
        if not isinstance(raw, str):
            return None
        try:
            return datetime.fromisoformat(raw)
        except ValueError:
            return None

    async def _decide_forwarded(
        self,
        *,
        approval: ApprovalRequestRecord,
        request: ApprovalDecisionRequest,
    ) -> ApprovalDecisionResponse:
        """Forward a pending approval to a second workspace user."""

        target = request.forward_to
        if target is None:
            raise RuntimeApiError(
                RuntimeErrorCode.VALIDATION_ERROR,
                Messages.Error.APPROVAL_FORWARD_INVALID_TARGET,
                http_status=status.HTTP_400_BAD_REQUEST,
                retryable=False,
            )
        await self._guard_forwardable(approval=approval, target=target)
        run = await self._persistence.get_run(
            org_id=approval.org_id,
            run_id=approval.run_id,
        )
        if run is None:
            raise RuntimeApiError(
                RuntimeErrorCode.CAPABILITY_NOT_FOUND,
                Messages.Error.RUN_NOT_FOUND,
                http_status=status.HTTP_404_NOT_FOUND,
                retryable=False,
            )
        now = datetime.now(timezone.utc)
        child_metadata = dict(approval.metadata)
        child_metadata[Keys.Field.CHAIN_PARENT_APPROVAL_ID] = approval.approval_id
        child_metadata[Keys.Field.FORWARDED_BY_USER_ID] = request.decided_by_user_id
        child = ApprovalRequestRecord(
            run_id=approval.run_id,
            conversation_id=approval.conversation_id,
            org_id=approval.org_id,
            user_id=target.user_id,
            status=ApprovalStatus.PENDING,
            created_at=now,
            expires_at=approval.expires_at,
            metadata=child_metadata,
            chain_parent_approval_id=approval.approval_id,
            chain_depth=approval.chain_depth + 1,
        )
        try:
            updated_parent, child = await self._persistence.forward_approval_request(
                parent_approval_id=approval.approval_id,
                org_id=approval.org_id,
                decided_by_user_id=request.decided_by_user_id,
                forwarded_to_user_id=target.user_id,
                decision_reason=request.reason,
                child=child,
                now=now,
            )
        except RuntimeError as exc:
            message = str(exc)
            if "no_longer_pending" in message or "not_pending" in message:
                raise RuntimeApiError(
                    RuntimeErrorCode.VALIDATION_ERROR,
                    Messages.Error.APPROVAL_FORWARD_NOT_PENDING,
                    http_status=status.HTTP_409_CONFLICT,
                    retryable=False,
                ) from exc
            raise
        approval_kind = approval.metadata.get(Keys.Field.APPROVAL_KIND)
        action_summary = approval.metadata.get(Keys.Field.ACTION_SUMMARY)
        await self._event_producer.append_api_event(
            run=run,
            source=StreamEventSource.RUNTIME,
            event_type=RuntimeApiEventType.APPROVAL_RESOLVED,
            payload={
                Keys.Field.APPROVAL_ID: approval.approval_id,
                Keys.Field.APPROVAL_KIND: approval_kind,
                Keys.Field.STATUS: Values.Status.FORWARDED,
                Keys.Field.DECISION: ApprovalStatus.FORWARDED.value,
                Keys.Payload.MESSAGE: Messages.Event.APPROVAL_RESOLVED,
            },
        )
        await self._event_producer.append_api_event(
            run=run,
            source=StreamEventSource.RUNTIME,
            event_type=RuntimeApiEventType.APPROVAL_FORWARDED,
            payload={
                Keys.Field.APPROVAL_ID: child.approval_id,
                Keys.Field.CHAIN_PARENT_APPROVAL_ID: approval.approval_id,
                Keys.Field.APPROVAL_KIND: approval_kind,
                Keys.Field.FORWARDED_BY_USER_ID: request.decided_by_user_id,
                Keys.Field.FORWARDED_TO_USER_ID: target.user_id,
                Keys.Field.FORWARDED_AT: now.isoformat(),
                Keys.Field.ACTION_SUMMARY: action_summary,
                Keys.Field.STATUS: Values.Status.WAITING,
                Keys.Payload.MESSAGE: Messages.Event.APPROVAL_FORWARDED,
            },
        )
        await self._event_producer.append_api_event(
            run=run,
            source=StreamEventSource.RUNTIME,
            event_type=RuntimeApiEventType.APPROVAL_REQUESTED,
            payload={
                Keys.Field.APPROVAL_ID: child.approval_id,
                Keys.Field.CHAIN_PARENT_APPROVAL_ID: approval.approval_id,
                Keys.Field.APPROVAL_KIND: approval_kind,
                Keys.Field.REQUESTED_BY_USER_ID: target.user_id,
                **{
                    key: value
                    for key, value in approval.metadata.items()
                    if isinstance(key, str)
                    and key
                    in (
                        Keys.Field.SERVER_ID,
                        Keys.Field.SERVER_NAME,
                        "display_name",
                        Keys.Field.TOOL_NAME,
                        "risk_level",
                        Keys.Field.SOURCE_TOOL_CALL_ID,
                    )
                },
                Keys.Payload.MESSAGE: action_summary
                if isinstance(action_summary, str)
                else "",
            },
        )
        await self._persistence.write_audit_log(
            event_type=Messages.Audit.APPROVAL_FORWARD,
            record={
                "org_id": approval.org_id,
                "user_id": request.decided_by_user_id,
                "resource_type": "approval",
                "resource_id": approval.approval_id,
                "run_id": approval.run_id,
                "outcome": "success",
                "metadata": {
                    "chain_parent_approval_id": approval.approval_id,
                    "child_approval_id": child.approval_id,
                    "forwarded_to_user_id": target.user_id,
                    "approval_kind": approval_kind,
                    "reason": request.reason,
                },
            },
        )
        asyncio.create_task(
            self._notifications.notify_approval_assigned(
                approval=child,
                forwarded_by_user_id=request.decided_by_user_id,
            )
        )
        self._approval_metrics.record_forward_success(
            approval_kind=approval_kind if isinstance(approval_kind, str) else None,
            depth=child.chain_depth,
        )
        return ApprovalDecisionResponse(
            approval_id=approval.approval_id,
            run_id=approval.run_id,
            status=ApprovalStatus.FORWARDED,
            decided_at=now,
            forwarded_to_user_id=target.user_id,
            child_approval_id=child.approval_id,
        )

    async def _guard_forwardable(
        self,
        *,
        approval: ApprovalRequestRecord,
        target: ApprovalForwardTarget,
    ) -> None:
        if approval.status is not ApprovalStatus.PENDING:
            self._approval_metrics.record_forward_invalid(
                reason=ForwardInvalidReason.NOT_PENDING
            )
            raise RuntimeApiError(
                RuntimeErrorCode.VALIDATION_ERROR,
                Messages.Error.APPROVAL_FORWARD_NOT_PENDING,
                http_status=status.HTTP_409_CONFLICT,
                retryable=False,
            )
        approval_kind = approval.metadata.get(Keys.Field.APPROVAL_KIND)
        if approval_kind not in self.APPROVAL_FORWARDABLE_KINDS:
            self._approval_metrics.record_forward_invalid(
                reason=ForwardInvalidReason.KIND_NOT_SUPPORTED
            )
            raise RuntimeApiError(
                RuntimeErrorCode.VALIDATION_ERROR,
                Messages.Error.APPROVAL_FORWARD_KIND_NOT_SUPPORTED,
                http_status=status.HTTP_422_UNPROCESSABLE_CONTENT,
                retryable=False,
            )
        depth = self._chain_depth(approval=approval)
        if depth >= self.APPROVAL_FORWARD_MAX_CHAIN_DEPTH:
            self._approval_metrics.record_forward_invalid(
                reason=ForwardInvalidReason.CHAIN_TOO_DEEP
            )
            raise RuntimeApiError(
                RuntimeErrorCode.VALIDATION_ERROR,
                Messages.Error.APPROVAL_FORWARD_CHAIN_TOO_DEEP,
                http_status=status.HTTP_422_UNPROCESSABLE_CONTENT,
                retryable=False,
            )
        try:
            is_active = await self._membership_resolver.is_active_member(
                org_id=approval.org_id, user_id=target.user_id
            )
        except MembershipResolverUnavailable as exc:
            self._approval_metrics.record_forward_invalid(
                reason=ForwardInvalidReason.RESOLVER_UNAVAILABLE
            )
            raise RuntimeApiError(
                RuntimeErrorCode.DEPENDENCY_ERROR,
                Messages.Error.SAFE_FALLBACK,
                http_status=status.HTTP_503_SERVICE_UNAVAILABLE,
                retryable=True,
            ) from exc
        if not is_active:
            self._approval_metrics.record_forward_invalid(
                reason=ForwardInvalidReason.TARGET_INVALID
            )
            raise RuntimeApiError(
                RuntimeErrorCode.VALIDATION_ERROR,
                Messages.Error.APPROVAL_FORWARD_INVALID_TARGET,
                http_status=status.HTTP_422_UNPROCESSABLE_CONTENT,
                retryable=False,
            )

    @classmethod
    def _chain_depth(cls, *, approval: ApprovalRequestRecord) -> int:
        return approval.chain_depth

    @classmethod
    def _wire_status_for(
        cls,
        *,
        approval_kind: object,
        record_status: str,
    ) -> str:
        if approval_kind == Values.ApprovalKind.ASK_A_QUESTION:
            if record_status == ApprovalStatus.APPROVED.value:
                return Values.Status.ANSWERED
            return Values.Status.SKIPPED
        return record_status

    async def _run_for_scope(
        self, *, org_id: str, user_id: str, run_id: str
    ) -> RunRecord:
        run = await self._persistence.get_run(org_id=org_id, run_id=run_id)
        if run is None or run.user_id != user_id:
            raise RuntimeApiError(
                RuntimeErrorCode.CAPABILITY_NOT_FOUND,
                Messages.Error.RUN_NOT_FOUND,
                http_status=status.HTTP_404_NOT_FOUND,
                retryable=False,
            )
        return run
