"""Notification dispatcher port and implementations for approval and share events.

Owns the "tell the recipient something happened" port. The production
``InboxAndEmailNotificationDispatcher`` fans out over three channels:
inbox SSE bus (desktop), email via the backend's email endpoint, and Slack DM
(when a ``SlackDispatcherPort`` adapter is injected).

Dispatch is fire-and-forget: callers invoke these methods via
``asyncio.create_task`` after the persistence transaction commits so a
notification failure never blocks or rolls back the underlying write.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Awaitable, Callable
from typing import Final, Literal, Protocol, runtime_checkable

from runtime_api.schemas import (
    ApprovalDecision,
    ApprovalRequestRecord,
    ShareSnapshot,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# User notification preference matrix
# ---------------------------------------------------------------------------
# The frontend stores a 4×3 matrix at ``user_preferences.notifications.matrix``
# keyed by event × channel. The dispatcher consults the recipient's matrix
# before each channel push and skips channels the user has turned off. When
# no row exists for a user (``fetch_*`` returns ``None``), we fall back to
# the FE's deployment defaults — opt-out, never opt-in.

NotificationEvent = Literal[
    "mention", "approval_needed", "run_finished", "weekly_digest"
]
"""Event types that key the per-user notification preference matrix.

Must stay in sync with the frontend's ``NotificationEvent`` type in
``packages/api-types/src/index.ts``; adding a new event here requires
the frontend matrix to gain a matching row.
"""

NotificationChannel = Literal["email", "slack", "desktop"]
"""Delivery channels for notification events.

``desktop`` maps to the in-product inbox SSE bus; ``email`` to the backend
email endpoint; ``slack`` to the Slack DM adapter (no-op until wired).
"""

NotificationMatrix = dict[NotificationEvent, dict[NotificationChannel, bool]]
"""Sparse preference matrix: ``{event: {channel: enabled}}``."""


# Deployment-default matrix — must stay in sync with the FE at
# ``apps/frontend/src/features/me/useUserPreferences.ts``. We keep a copy
# here so the dispatcher can answer "is channel X allowed for event Y"
# even when the user has never opened Settings → Notifications.
_DEFAULT_MATRIX: Final[NotificationMatrix] = {
    "mention": {"email": True, "slack": False, "desktop": True},
    "approval_needed": {"email": True, "slack": False, "desktop": True},
    "run_finished": {"email": False, "slack": False, "desktop": True},
    "weekly_digest": {"email": True, "slack": False, "desktop": False},
}


@runtime_checkable
class UserPreferenceFetcher(Protocol):
    """Port for fetching a user's notification preference matrix.

    Implementations must never raise; return ``None`` on transient backend
    failures so the caller falls back to deployment defaults. Silence is
    the safe default — missing preferences should not block notifications.
    """

    async def fetch_notification_matrix(
        self,
        *,
        user_id: str,
        org_id: str,
    ) -> NotificationMatrix | None: ...


class _DefaultsOnlyUserPreferenceFetcher:
    """No-op fetcher that always returns ``None``, causing callers to use deployment defaults.

    Used when no HTTP fetcher is wired at construction time.
    """

    async def fetch_notification_matrix(
        self,
        *,
        user_id: str,
        org_id: str,
    ) -> NotificationMatrix | None:
        return None


class InMemoryUserPreferenceFetcher:
    """Test fetcher backed by an explicit ``{user_id: matrix}`` map.

    Returns ``None`` for unknown users, which triggers deployment-default
    fallback in the dispatcher — the same behaviour as a first-time user.
    """

    def __init__(
        self,
        matrices: dict[str, NotificationMatrix] | None = None,
    ) -> None:
        self._by_user: dict[str, NotificationMatrix] = dict(matrices or {})

    def set(self, user_id: str, matrix: NotificationMatrix) -> None:
        """Register or overwrite a user's notification matrix."""
        self._by_user[user_id] = matrix

    async def fetch_notification_matrix(
        self,
        *,
        user_id: str,
        org_id: str,
    ) -> NotificationMatrix | None:
        """Return the matrix for ``user_id``, or ``None`` if not configured."""
        return self._by_user.get(user_id)


def _channel_allowed(
    matrix: NotificationMatrix | None,
    *,
    event: NotificationEvent,
    channel: NotificationChannel,
) -> bool:
    """Return ``True`` if the (event, channel) cell permits delivery.

    Falls back to ``_DEFAULT_MATRIX`` when the user has no preferences row, or
    when the row is missing a cell — forwards-compatible with deployments that
    ship a partial matrix before the FE adds a new row.
    """

    if matrix is not None:
        event_row = matrix.get(event)
        if event_row is not None and channel in event_row:
            return bool(event_row[channel])
    default_row = _DEFAULT_MATRIX.get(event, {})
    return bool(default_row.get(channel, True))


@runtime_checkable
class NotificationDispatcher(Protocol):
    """Port for delivering approval assignment and resolution notifications to recipients.

    All methods are best-effort: implementations must catch and log exceptions
    internally so failures never propagate to the ``asyncio.create_task`` caller.
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

    async def notify_share_forked(
        self,
        *,
        share: ShareSnapshot,
        forked_by_user_id: str,
        new_conversation_id: str,
    ) -> None: ...


class LoggingNotificationDispatcher:
    """Structured-log-only dispatcher used in dev, tests, and as a fallback.

    Emits the same event metadata the inbox SSE push would carry so operators
    can tail logs and observe what notifications would have been sent.
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
"""Callable type for in-process inbox SSE bus push: ``(approval, event_type, actor_user_id)``.

``event_type`` is ``"approval_assigned"`` or ``"approval_resolved"``;
``actor_user_id`` is the forwarder (assigned) or decider (resolved).
Injected at construction to break the import cycle between
``agent_runtime.api`` and ``runtime_api.sse``.
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


# ---------------------------------------------------------------------------
# Slack DM notification port
# ---------------------------------------------------------------------------
# The notification matrix supports three channels: email, slack, desktop.
# Email rides the existing email port; desktop rides the inbox SSE bus.
# Slack DM is the third surface — same contract shape as the email port,
# fire-and-forget, never raises. Until a real Slack-app integration
# ships, the default ``LoggingSlackDispatcher`` writes a structured log
# line so operators can see "would-have-sent" events; production deploys
# inject a real ``SlackDispatcherPort`` adapter at app construction.


@runtime_checkable
class SlackDispatcherPort(Protocol):
    """Port for sending a Slack DM notification.

    Best-effort, fire-and-forget — implementations must never raise. Resolving
    the workspace ``recipient_user_id`` to a Slack user ID is the adapter's
    responsibility (typically via ``users.lookupByEmail`` or a stored mapping).
    """

    async def send_notification(
        self,
        *,
        recipient_user_id: str,
        org_id: str,
        template: str,
        text: str,
        link_url: str | None = None,
        metadata: dict[str, object] | None = None,
    ) -> None: ...


class LoggingSlackDispatcher:
    """Structured-log-only Slack adapter used in dev, tests, and as a fallback.

    Operators see "would-have-sent" log lines with the same metadata the real
    DM would carry, making it easy to verify notification behaviour without
    a live Slack app integration.
    """

    async def send_notification(
        self,
        *,
        recipient_user_id: str,
        org_id: str,
        template: str,
        text: str,
        link_url: str | None = None,
        metadata: dict[str, object] | None = None,
    ) -> None:
        logger.info(
            "slack.notify.dispatch",
            extra={
                "metadata": {
                    "recipient_user_id": recipient_user_id,
                    "org_id": org_id,
                    "template": template,
                    "text_preview": (text[:120] + "…") if len(text) > 120 else text,
                    "link_url": link_url,
                    "extra": metadata or {},
                }
            },
        )


class InboxAndEmailNotificationDispatcher:
    """Production dispatcher that fans out to inbox SSE bus, email, and Slack.

    Each channel is gated on whether its dependency is injected at construction:
    inbox is always active; email requires an ``HttpPoster``; Slack requires a
    ``SlackDispatcherPort``. The notification preference matrix is consulted for
    every channel before dispatch so per-user opt-outs are honoured.
    """

    def __init__(
        self,
        *,
        publish_inbox: InboxPublish,
        post: HttpPoster | None = None,
        backend_base_url: str | None = None,
        service_token: str | None = None,
        preference_fetcher: UserPreferenceFetcher | None = None,
        slack: SlackDispatcherPort | None = None,
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
        # Defaults-only fetcher keeps existing behaviour when no HTTP fetcher is wired.
        self._preference_fetcher: UserPreferenceFetcher = (
            preference_fetcher or _DefaultsOnlyUserPreferenceFetcher()
        )
        self._slack: SlackDispatcherPort | None = slack

    async def notify_approval_assigned(
        self,
        *,
        approval: ApprovalRequestRecord,
        forwarded_by_user_id: str,
    ) -> None:
        matrix = await self._safe_fetch_matrix(
            user_id=approval.user_id, org_id=approval.org_id
        )
        if _channel_allowed(matrix, event="approval_needed", channel="desktop"):
            await self._safe_publish(
                approval=approval,
                event_type="approval_assigned",
                actor_user_id=forwarded_by_user_id,
            )
        if self._post is not None and _channel_allowed(
            matrix, event="approval_needed", channel="email"
        ):
            await self._safe_email(
                approval=approval,
                template="approval_assigned",
                actor_user_id=forwarded_by_user_id,
            )
        if self._slack is not None and _channel_allowed(
            matrix, event="approval_needed", channel="slack"
        ):
            await self._safe_slack(
                recipient_user_id=approval.user_id,
                org_id=approval.org_id,
                template="approval_assigned",
                text=(
                    f"{forwarded_by_user_id} forwarded an approval to you. "
                    f"(approval {approval.approval_id})"
                ),
                metadata={
                    "approval_id": approval.approval_id,
                    "conversation_id": approval.conversation_id,
                    "run_id": approval.run_id,
                    "forwarded_by_user_id": forwarded_by_user_id,
                },
            )

    async def notify_approval_resolved(
        self,
        *,
        approval: ApprovalRequestRecord,
        decision: ApprovalDecision,
        decided_by_user_id: str,
    ) -> None:
        # Map approval resolution to ``run_finished`` — both represent a background
        # process completing, so the user's "notify me when runs finish" preference
        # applies without needing a dedicated matrix row.
        matrix = await self._safe_fetch_matrix(
            user_id=approval.user_id, org_id=approval.org_id
        )
        if _channel_allowed(matrix, event="run_finished", channel="desktop"):
            await self._safe_publish(
                approval=approval,
                event_type="approval_resolved",
                actor_user_id=decided_by_user_id,
            )
        if self._post is not None and _channel_allowed(
            matrix, event="run_finished", channel="email"
        ):
            await self._safe_email(
                approval=approval,
                template="approval_resolved",
                actor_user_id=decided_by_user_id,
                extra={"decision": decision.value},
            )
        if self._slack is not None and _channel_allowed(
            matrix, event="run_finished", channel="slack"
        ):
            await self._safe_slack(
                recipient_user_id=approval.user_id,
                org_id=approval.org_id,
                template="approval_resolved",
                text=(
                    f"Approval {approval.approval_id} was {decision.value} "
                    f"by {decided_by_user_id}."
                ),
                metadata={
                    "approval_id": approval.approval_id,
                    "conversation_id": approval.conversation_id,
                    "run_id": approval.run_id,
                    "decision": decision.value,
                    "decided_by_user_id": decided_by_user_id,
                },
            )

    async def notify_share_forked(
        self,
        *,
        share: ShareSnapshot,
        forked_by_user_id: str,
        new_conversation_id: str,
    ) -> None:
        # Share fork maps to "mention" — someone interacted with shared content,
        # analogous to an @-reply. Using the nearest existing event type means
        # user preferences still apply before a dedicated matrix row ships.
        matrix = await self._safe_fetch_matrix(
            user_id=share.created_by_user_id, org_id=share.org_id
        )
        if _channel_allowed(matrix, event="mention", channel="desktop"):
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
        if self._slack is not None and _channel_allowed(
            matrix, event="mention", channel="slack"
        ):
            await self._safe_slack(
                recipient_user_id=share.created_by_user_id,
                org_id=share.org_id,
                template="share_forked",
                text=(
                    f"{forked_by_user_id} forked a conversation you shared "
                    f"({share.share_id})."
                ),
                metadata={
                    "share_id": share.share_id,
                    "source_conversation_id": share.conversation_id,
                    "new_conversation_id": new_conversation_id,
                    "forked_by_user_id": forked_by_user_id,
                },
            )

    async def _safe_fetch_matrix(
        self, *, user_id: str, org_id: str
    ) -> NotificationMatrix | None:
        """Fetch the user's matrix, returning ``None`` on any error so deployment defaults apply."""

        try:
            return await self._preference_fetcher.fetch_notification_matrix(
                user_id=user_id, org_id=org_id
            )
        except Exception:
            logger.warning(
                "approval.notify.preference_fetch_failed",
                extra={"metadata": {"user_id": user_id, "org_id": org_id}},
                exc_info=True,
            )
            return None

    async def _safe_publish(
        self,
        *,
        approval: ApprovalRequestRecord,
        event_type: str,
        actor_user_id: str,
    ) -> None:
        """Push to the inbox SSE bus, logging and swallowing any failure."""
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

    async def _safe_slack(
        self,
        *,
        recipient_user_id: str,
        org_id: str,
        template: str,
        text: str,
        metadata: dict[str, object] | None = None,
    ) -> None:
        """Invoke the Slack adapter, logging and swallowing any exception.

        The adapter contract already promises non-raising behaviour, but
        defence-in-depth wraps it again here because stray exceptions on a
        ``asyncio.create_task`` call-site have no error handler to catch them.
        """

        if self._slack is None:
            return
        try:
            await self._slack.send_notification(
                recipient_user_id=recipient_user_id,
                org_id=org_id,
                template=template,
                text=text,
                metadata=metadata,
            )
        except Exception:
            logger.warning(
                "approval.notify.slack_failed",
                extra={
                    "metadata": {
                        "recipient_user_id": recipient_user_id,
                        "template": template,
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
        """POST an email notification to the backend endpoint, logging and swallowing failures."""
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
