"""Notification dispatcher (PR 1.4.1).

Two-stage approval forwarding (PR 1.4) creates a child approval row
addressed to a second workspace user. Without a notification, that user
has no signal — they'd have to open the conversation themselves to see
the assigned card. This module owns the "tell the recipient something
happened" port plus its default and production implementations.

Anti-pattern check: this is *not* a parallel notification system. The
production composite dispatcher fans out through the channels we already
have:

  1. The per-user inbox SSE bus (in-process; defined in
     ``runtime_api.sse.inbox_bus``) for connected clients.
  2. The services/backend ``/internal/v1/notifications/email`` endpoint
     (the same path MFA uses) for the email channel.

Slack DM and desktop push are W4.1; they'll add new call sites against
this same port without changing its contract.

Dispatch fires fire-and-forget from ``RuntimeApiService._decide_forwarded``
*after* the persistence transaction commits, off the request thread via
``asyncio.create_task``. Failures log a structured warning but do not
roll back the forward — the chain re-converges at the next sweeper tick
if the recipient never sees the notification.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Awaitable, Callable
from typing import Protocol, runtime_checkable

from runtime_api.schemas import (
    ApprovalDecision,
    ApprovalRequestRecord,
    ShareSnapshot,
)

logger = logging.getLogger(__name__)


@runtime_checkable
class NotificationDispatcher(Protocol):
    """Inform the recipient that an approval is assigned to them, or
    that an approval they're tracking has resolved.

    Both methods are best-effort. Implementations MUST NOT raise; they
    must catch and log internally so the request handler that fires this
    via ``asyncio.create_task`` doesn't propagate noise.
    """

    async def notify_approval_assigned(
        self,
        *,
        approval: ApprovalRequestRecord,
        forwarded_by_user_id: str,
    ) -> None: ...

    async def notify_approval_resolved(
        self,
        *,
        approval: ApprovalRequestRecord,
        decision: ApprovalDecision,
        decided_by_user_id: str,
    ) -> None: ...

    # PR 6.2 — fired (best-effort, off the request thread) after a
    # recipient forks a shared conversation. Default to a no-op so
    # impls that don't care about share fan-out don't have to override.
    async def notify_share_forked(
        self,
        *,
        share: ShareSnapshot,
        forked_by_user_id: str,
        new_conversation_id: str,
    ) -> None: ...


class LoggingNotificationDispatcher:
    """Default dispatcher: structured logs only.

    Used in dev + tests + as a graceful fallback when the production
    dispatcher's dependencies aren't wired. Operationally meaningful in
    development: tail the logs and you see the same events the inbox SSE
    push would have emitted.
    """

    async def notify_approval_assigned(
        self,
        *,
        approval: ApprovalRequestRecord,
        forwarded_by_user_id: str,
    ) -> None:
        logger.info(
            "approval.notify.assigned",
            extra={
                "metadata": {
                    "approval_id": approval.approval_id,
                    "target_user_id": approval.user_id,
                    "forwarded_by_user_id": forwarded_by_user_id,
                    "org_id": approval.org_id,
                    "conversation_id": approval.conversation_id,
                }
            },
        )

    async def notify_approval_resolved(
        self,
        *,
        approval: ApprovalRequestRecord,
        decision: ApprovalDecision,
        decided_by_user_id: str,
    ) -> None:
        logger.info(
            "approval.notify.resolved",
            extra={
                "metadata": {
                    "approval_id": approval.approval_id,
                    "decision": decision.value,
                    "decided_by_user_id": decided_by_user_id,
                    "org_id": approval.org_id,
                    "conversation_id": approval.conversation_id,
                }
            },
        )

    async def notify_share_forked(
        self,
        *,
        share: ShareSnapshot,
        forked_by_user_id: str,
        new_conversation_id: str,
    ) -> None:
        logger.info(
            "share.notify.forked",
            extra={
                "metadata": {
                    "share_id": share.share_id,
                    "source_conversation_id": share.conversation_id,
                    "new_conversation_id": new_conversation_id,
                    "forked_by_user_id": forked_by_user_id,
                    "shared_by_user_id": share.created_by_user_id,
                    "org_id": share.org_id,
                }
            },
        )


# In-process inbox publish callable. The composite dispatcher takes this
# as a constructor arg so the SSE bus stays loosely coupled — no import
# cycle between agent_runtime.api and runtime_api.sse.
InboxPublish = Callable[[ApprovalRequestRecord, str, str], Awaitable[None]]
"""Signature: (approval, event_type, actor_user_id) -> awaitable[None].

``event_type`` is ``"approval_assigned"`` or ``"approval_resolved"``.
``actor_user_id`` is the workspace user whose action triggered the event
(forwarder for assigned, decider for resolved).
"""


# HTTP fetcher for the email endpoint. Same shape as the membership
# resolver's fetcher — intentional. Production injects an httpx wrapper;
# tests inject a fake.
HttpPoster = Callable[
    [str, dict[str, str], dict[str, object]],
    Awaitable[tuple[int, dict[str, object]]],
]


class _Env:
    BACKEND_BASE_URL = "BACKEND_BASE_URL"
    EMAIL_ENABLED = "RUNTIME_APPROVAL_EMAIL_ENABLED"
    SLACK_ENABLED = "RUNTIME_APPROVAL_SLACK_ENABLED"  # reserved for W4.1


class InboxAndEmailNotificationDispatcher:
    """Production dispatcher: fans out to the inbox SSE bus + email.

    Each channel is independently optional. Email is gated on
    ``RUNTIME_APPROVAL_EMAIL_ENABLED`` so deployments without an SMTP
    relay degrade to inbox-only without code change.
    """

    def __init__(
        self,
        *,
        publish_inbox: InboxPublish,
        post: HttpPoster | None = None,
        backend_base_url: str | None = None,
        service_token: str | None = None,
        email_enabled: bool | None = None,
    ) -> None:
        self._publish_inbox = publish_inbox
        self._post = post
        self._backend_base_url = (
            backend_base_url
            or os.environ.get(_Env.BACKEND_BASE_URL)
            or "http://backend:8100"
        ).rstrip("/")
        self._service_token = service_token or os.environ.get(
            "ENTERPRISE_SERVICE_TOKEN", ""
        )
        self._email_enabled = (
            email_enabled
            if email_enabled is not None
            else _env_bool(_Env.EMAIL_ENABLED, False)
        )

    async def notify_approval_assigned(
        self,
        *,
        approval: ApprovalRequestRecord,
        forwarded_by_user_id: str,
    ) -> None:
        await self._safe_publish(
            approval=approval,
            event_type="approval_assigned",
            actor_user_id=forwarded_by_user_id,
        )
        if self._email_enabled and self._post is not None:
            await self._safe_email(
                approval=approval,
                template="approval_assigned",
                actor_user_id=forwarded_by_user_id,
            )

    async def notify_approval_resolved(
        self,
        *,
        approval: ApprovalRequestRecord,
        decision: ApprovalDecision,
        decided_by_user_id: str,
    ) -> None:
        await self._safe_publish(
            approval=approval,
            event_type="approval_resolved",
            actor_user_id=decided_by_user_id,
        )
        # Resolution emails are intentionally gated separately — many
        # deployments will only want assignment emails (the actionable
        # ones), not resolution receipts. We send only when explicitly
        # opted in via the same env flag for v1; future: per-user
        # notification matrix (W4.1).
        if self._email_enabled and self._post is not None:
            await self._safe_email(
                approval=approval,
                template="approval_resolved",
                actor_user_id=decided_by_user_id,
                extra={"decision": decision.value},
            )

    async def notify_share_forked(
        self,
        *,
        share: ShareSnapshot,
        forked_by_user_id: str,
        new_conversation_id: str,
    ) -> None:
        # PR 6.2 — share-fork fan-out is opt-in per the W4.1 notification
        # matrix. v1 emits a structured log so SIEM dashboards can pick
        # it up; the FE inbox + email channels are wired when the
        # matrix lands. Failing here would be a policy regression — the
        # fork already committed; we observe the outcome, never block it.
        logger.info(
            "share.notify.forked",
            extra={
                "metadata": {
                    "share_id": share.share_id,
                    "source_conversation_id": share.conversation_id,
                    "new_conversation_id": new_conversation_id,
                    "forked_by_user_id": forked_by_user_id,
                    "shared_by_user_id": share.created_by_user_id,
                    "org_id": share.org_id,
                }
            },
        )

    async def _safe_publish(
        self,
        *,
        approval: ApprovalRequestRecord,
        event_type: str,
        actor_user_id: str,
    ) -> None:
        try:
            await self._publish_inbox(approval, event_type, actor_user_id)
        except Exception:
            logger.warning(
                "approval.notify.inbox_publish_failed",
                extra={
                    "metadata": {
                        "approval_id": approval.approval_id,
                        "event_type": event_type,
                    }
                },
                exc_info=True,
            )

    async def _safe_email(
        self,
        *,
        approval: ApprovalRequestRecord,
        template: str,
        actor_user_id: str,
        extra: dict[str, object] | None = None,
    ) -> None:
        if self._post is None:
            return
        url = f"{self._backend_base_url}/internal/v1/notifications/email"
        headers = {
            "x-enterprise-service-token": self._service_token,
            "x-enterprise-org-id": approval.org_id,
            "content-type": "application/json",
        }
        body: dict[str, object] = {
            "template": template,
            "recipient_user_id": approval.user_id,
            "actor_user_id": actor_user_id,
            "approval_id": approval.approval_id,
            "conversation_id": approval.conversation_id,
            "run_id": approval.run_id,
        }
        if extra:
            body.update(extra)
        try:
            status_code, _ = await self._post(url, headers, body)
            if status_code >= 400:
                logger.warning(
                    "approval.notify.email_status",
                    extra={
                        "metadata": {
                            "approval_id": approval.approval_id,
                            "status_code": status_code,
                            "template": template,
                        }
                    },
                )
        except Exception:
            logger.warning(
                "approval.notify.email_failed",
                extra={
                    "metadata": {
                        "approval_id": approval.approval_id,
                        "template": template,
                    }
                },
                exc_info=True,
            )


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}
