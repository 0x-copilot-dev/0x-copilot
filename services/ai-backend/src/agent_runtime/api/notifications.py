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
from typing import Final, Literal, Protocol, runtime_checkable

from runtime_api.schemas import (
    ApprovalDecision,
    ApprovalRequestRecord,
    ShareSnapshot,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# User preference matrix (PR 4.1 follow-up)
# ---------------------------------------------------------------------------
# The frontend stores a 4×3 matrix at ``user_preferences.notifications.matrix``
# keyed by event × channel. The dispatcher consults the recipient's matrix
# before each channel push and skips channels the user has turned off. When
# no row exists for a user (``fetch_*`` returns ``None``), we fall back to
# the FE's deployment defaults — opt-out, never opt-in.

NotificationEvent = Literal[
    "mention", "approval_needed", "run_finished", "weekly_digest"
]
"""Event types the matrix is keyed by. Mirrors ``NotificationEvent`` in
``packages/api-types/src/index.ts``. Adding a new event here is the
schema-side of the change; the FE matrix gains a row in lockstep."""

NotificationChannel = Literal["email", "slack", "desktop"]
"""Delivery channels. ``desktop`` maps to the in-product inbox-bus push
(the closest match for "live in-product notification"); ``email`` rides
the existing email endpoint; ``slack`` is reserved for the W4.1 Slack DM
adapter (no-op until that lands)."""

NotificationMatrix = dict[NotificationEvent, dict[NotificationChannel, bool]]


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
    """Resolve a user's notification matrix.

    Called fire-and-forget on the dispatch path. Implementations MUST NOT
    raise; on transient backend failure return ``None`` so the caller
    falls back to deployment defaults (the safer choice for a "should I
    notify?" check — silence is not a regression).

    Production injects an HTTP client that hits the backend's
    ``/internal/v1/me/preferences`` endpoint with the recipient's
    ``user_id`` + ``org_id``. Tests inject ``InMemoryUserPreferenceFetcher``.
    """

    async def fetch_notification_matrix(
        self,
        *,
        user_id: str,
        org_id: str,
    ) -> NotificationMatrix | None: ...


class _DefaultsOnlyUserPreferenceFetcher:
    """Fetcher that always returns ``None`` so the caller uses the
    deployment defaults. Used when no real fetcher is wired (dev /
    tests / deployments that haven't shipped the matrix yet)."""

    async def fetch_notification_matrix(
        self,
        *,
        user_id: str,
        org_id: str,
    ) -> NotificationMatrix | None:
        return None


class InMemoryUserPreferenceFetcher:
    """Deterministic fetcher for tests. Wraps a ``{user_id: matrix}``
    map and returns ``None`` for unknown users (= use defaults)."""

    def __init__(
        self,
        matrices: dict[str, NotificationMatrix] | None = None,
    ) -> None:
        self._by_user: dict[str, NotificationMatrix] = dict(matrices or {})

    def set(self, user_id: str, matrix: NotificationMatrix) -> None:
        self._by_user[user_id] = matrix

    async def fetch_notification_matrix(
        self,
        *,
        user_id: str,
        org_id: str,
    ) -> NotificationMatrix | None:
        return self._by_user.get(user_id)


def _channel_allowed(
    matrix: NotificationMatrix | None,
    *,
    event: NotificationEvent,
    channel: NotificationChannel,
) -> bool:
    """Return ``True`` if the (event, channel) cell is enabled in the
    user's matrix. Falls back to the deployment defaults when the user
    has no row, or when the row is missing the cell (forward compat with
    deploys that ship a partial matrix).

    Reasoning by example:
    - User opted out of email for ``approval_needed`` → return ``False``.
    - User has no preferences row → use ``_DEFAULT_MATRIX`` → ``True``
      for email + desktop (the FE's documented default).
    - User row is missing the new event ``share_forked`` → fall through
      to the default for whatever event we map it to.
    """

    if matrix is not None:
        event_row = matrix.get(event)
        if event_row is not None and channel in event_row:
            return bool(event_row[channel])
    default_row = _DEFAULT_MATRIX.get(event, {})
    return bool(default_row.get(channel, True))


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
    SLACK_ENABLED = "RUNTIME_APPROVAL_SLACK_ENABLED"


# ---------------------------------------------------------------------------
# Slack DM port (PR 4.1 follow-up — Slack channel for the matrix)
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
    """Send a Slack DM for a notification event.

    Best-effort, fire-and-forget. Implementations MUST NOT raise; they
    must catch and log internally so the request handler that fires
    this via ``asyncio.create_task`` doesn't propagate noise.

    ``recipient_user_id`` is the workspace user the notification is
    addressed to. Resolving that to a Slack user id is the adapter's
    job (typically via the Slack ``users.lookupByEmail`` API or a
    persisted workspace-user → slack-user mapping).
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
    """Default Slack adapter: structured logs only.

    Used in dev + tests + as a graceful fallback when the production
    adapter's dependencies aren't wired. Operators tail the logs and
    see the same events the real DM would carry, exactly the same
    pattern ``LoggingEmailDispatcher`` uses for the email channel.
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
    """Production dispatcher: fans out to the inbox SSE bus + email + Slack.

    Each channel is independently optional. Email is gated on
    ``RUNTIME_APPROVAL_EMAIL_ENABLED`` so deployments without an SMTP
    relay degrade to inbox-only without code change. Slack is gated on
    ``RUNTIME_APPROVAL_SLACK_ENABLED`` + a ``SlackDispatcherPort``
    adapter being injected; the default ``LoggingSlackDispatcher`` is
    safe but writes structured logs only.

    Class name is preserved (callers reference it by name across the
    runtime); the docstring carries the truth that it now handles three
    channels.
    """

    def __init__(
        self,
        *,
        publish_inbox: InboxPublish,
        post: HttpPoster | None = None,
        backend_base_url: str | None = None,
        service_token: str | None = None,
        email_enabled: bool | None = None,
        preference_fetcher: UserPreferenceFetcher | None = None,
        slack: SlackDispatcherPort | None = None,
        slack_enabled: bool | None = None,
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
        # PR 4.1 follow-up: consult the recipient's notification matrix
        # before fanning out. Default: a no-op fetcher → deployment
        # defaults apply, so behavior pre-PR is unchanged for any deploy
        # that hasn't wired the HTTP fetcher yet.
        self._preference_fetcher: UserPreferenceFetcher = (
            preference_fetcher or _DefaultsOnlyUserPreferenceFetcher()
        )
        # PR 4.1 follow-up — Slack channel. If no port is injected, we
        # still allow the channel (gated by env + matrix) to fall back
        # to ``LoggingSlackDispatcher`` so the dispatch path is exercised
        # in dev and "would-have-sent" entries appear in logs.
        self._slack: SlackDispatcherPort = slack or LoggingSlackDispatcher()
        self._slack_enabled = (
            slack_enabled
            if slack_enabled is not None
            else _env_bool(_Env.SLACK_ENABLED, False)
        )

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
        if (
            self._email_enabled
            and self._post is not None
            and _channel_allowed(matrix, event="approval_needed", channel="email")
        ):
            await self._safe_email(
                approval=approval,
                template="approval_assigned",
                actor_user_id=forwarded_by_user_id,
            )
        if self._slack_enabled and _channel_allowed(
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
        # Resolution maps to ``run_finished`` in the matrix — the
        # approval thread completing is the user-facing equivalent of a
        # background run reaching a terminal state.
        matrix = await self._safe_fetch_matrix(
            user_id=approval.user_id, org_id=approval.org_id
        )
        if _channel_allowed(matrix, event="run_finished", channel="desktop"):
            await self._safe_publish(
                approval=approval,
                event_type="approval_resolved",
                actor_user_id=decided_by_user_id,
            )
        if (
            self._email_enabled
            and self._post is not None
            and _channel_allowed(matrix, event="run_finished", channel="email")
        ):
            await self._safe_email(
                approval=approval,
                template="approval_resolved",
                actor_user_id=decided_by_user_id,
                extra={"decision": decision.value},
            )
        if self._slack_enabled and _channel_allowed(
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
        # Share fork is conceptually a "mention" — someone interacted
        # with content the user shared, like an @-mention thread reply.
        # Until the FE matrix gains a dedicated ``share_forked`` row, we
        # ride the closest existing event so the user's preferences still
        # apply. Failure here is best-effort logging — the fork already
        # committed; we never block on the notification path.
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
        if self._slack_enabled and _channel_allowed(
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
        """Wrap the fetcher in a try/except so a misbehaving impl never
        breaks the dispatch path. Returning ``None`` here is the safe
        fall-through: deployment defaults apply."""

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
        """Mirror of ``_safe_email``: never raise, log on failure.

        The Slack adapter contract already promises non-raising
        behavior, but defence-in-depth for adapters that misbehave
        means we wrap once here too — the dispatch path is fired off
        the request thread via ``asyncio.create_task`` and a stray
        exception there has nowhere to go.
        """

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
