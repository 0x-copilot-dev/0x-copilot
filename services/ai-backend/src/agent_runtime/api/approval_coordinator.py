"""Approval lifecycle coordinator — single source of truth for approval-state transitions.

Handles inbox reads, approve/reject decisions, two-stage forwarding, and
undo-within-window operations. Multi-fire safe: token rotation mid-run may
re-enter the same cycle. Resume is enqueued via ``APPROVAL_RESOLVED``, never
executed inline.
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
    """Service layer for approval lifecycle: inbox reads, decisions, forwarding, and undo.

    Persists decisions, emits typed events, enqueues worker resume commands,
    and records audit rows. All public methods raise ``RuntimeApiError`` on
    invalid state; callers must not catch and swallow those.
    """

    # Chains longer than this are refused to prevent unbounded delegation trees.
    APPROVAL_FORWARD_MAX_CHAIN_DEPTH = 3

    # Only these kinds support forwarding; question-type approvals resolve inline.
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
        """Return the paginated recipient inbox for the given status filter.

        Enforces the hard cap on page size regardless of the caller-supplied
        limit so a single request cannot exhaust the store.
        """

        # Clamp to [1, MAX] so callers can't request an unlimited page.
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
        """Project a raw persistence record onto the inbox response shape.

        Metadata fields are typed defensively — the store may have been
        written by older code that stored non-string values.
        """
        approval_kind = record.metadata.get(Keys.Field.APPROVAL_KIND)
        action_summary = record.metadata.get(Keys.Field.ACTION_SUMMARY)
        # Two naming conventions exist in the wild; prefer the newer one.
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
        """Encode a (created_at, approval_id) pair as a URL-safe base64 cursor token."""
        raw = f"{created_at.isoformat()}|{approval_id}".encode()
        # Strip trailing "=" padding so the token is safe to embed in query strings.
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")

    @staticmethod
    def _decode_assigned_cursor(cursor: str | None) -> tuple[datetime, str] | None:
        """Decode a cursor token produced by ``_encode_assigned_cursor``.

        Returns ``None`` on any malformed input so a corrupted or truncated
        cursor silently starts from the beginning rather than raising 500.
        """
        if cursor is None:
            return None
        # Restore the base64 padding that was stripped at encode time.
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
        """Persist an approval decision, emit a typed event, and enqueue the worker resume.

        Forwarded decisions are delegated to ``_decide_forwarded`` because
        they create a child row rather than resolving the approval directly.
        """

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
        # Scope check: only the assigned user may resolve their own approval.
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
        # Map the decision enum to the persistence status; both share string values.
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
        # PR #43 — project batch_id / batch_index from the approval metadata
        # onto the resolved event so the FE can correlate the resolve back
        # to its requesting batch without parsing the approval_id string.
        resolved_payload: dict[str, object] = {
            Keys.Field.APPROVAL_ID: record.approval_id,
            Keys.Field.APPROVAL_KIND: approval_kind,
            Keys.Field.STATUS: self._wire_status_for(
                approval_kind=approval_kind,
                record_status=record.status.value,
            ),
            Keys.Payload.MESSAGE: Messages.Event.APPROVAL_RESOLVED,
            Keys.Field.DECISION: record.status.value,
        }
        batch_id = approval.metadata.get(Keys.Field.BATCH_ID)
        if isinstance(batch_id, str) and batch_id:
            resolved_payload[Keys.Field.BATCH_ID] = batch_id
        batch_index = approval.metadata.get(Keys.Field.BATCH_INDEX)
        if isinstance(batch_index, int) and not isinstance(batch_index, bool):
            resolved_payload[Keys.Field.BATCH_INDEX] = batch_index
        await self._event_producer.append_api_event(
            run=run,
            source=StreamEventSource.RUNTIME,
            event_type=RuntimeApiEventType.APPROVAL_RESOLVED,
            payload=resolved_payload,
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
        """Return the undo deadline only when the approval is approved AND reversible.

        Rejected decisions are irreversible by design; the metadata flag lets
        per-action type registrations opt specific actions into reversibility.
        """
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
        """Record undo intent for an approved, reversible action within the time window.

        Does not roll back the action — emits an ``APPROVAL_UNDO_REQUESTED`` event
        so a downstream worker (or human operator) can act on it. Raises 410 when
        the undo window has already closed.
        """

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
        """Extract the ISO-8601 ``decided_at`` timestamp stored in approval metadata.

        Returns ``None`` when the field is absent or unparseable rather than
        letting a stale or malformed value propagate to the undo-window check.
        """
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
            # The store raises a RuntimeError with a sentinel token when a
            # concurrent decision already claimed the approval between our
            # guard check and the write. Surface as 409 rather than 500.
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
        # Fire notification off the request thread — never block forward on delivery.
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
        """Validate all preconditions for forwarding before touching the store.

        Checks ordering: status → kind → chain depth → target membership.
        Each failure records an observability metric for dashboard visibility.
        """
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
        """Return the forwarding chain depth stored on the approval record."""
        return approval.chain_depth

    @classmethod
    def _wire_status_for(
        cls,
        *,
        approval_kind: object,
        record_status: str,
    ) -> str:
        """Translate a persistence status to the wire label for a given kind.

        ``ASK_A_QUESTION`` approvals use "answered"/"skipped" instead of
        "approved"/"rejected" so the frontend can render the correct copy
        without inspecting the approval kind itself.
        """
        if approval_kind == Values.ApprovalKind.ASK_A_QUESTION:
            if record_status == ApprovalStatus.APPROVED.value:
                return Values.Status.ANSWERED
            return Values.Status.SKIPPED
        return record_status

    async def _run_for_scope(
        self, *, org_id: str, user_id: str, run_id: str
    ) -> RunRecord:
        """Fetch the run and assert it belongs to ``user_id`` within ``org_id``."""
        run = await self._persistence.get_run(org_id=org_id, run_id=run_id)
        if run is None or run.user_id != user_id:
            raise RuntimeApiError(
                RuntimeErrorCode.CAPABILITY_NOT_FOUND,
                Messages.Error.RUN_NOT_FOUND,
                http_status=status.HTTP_404_NOT_FOUND,
                retryable=False,
            )
        return run
