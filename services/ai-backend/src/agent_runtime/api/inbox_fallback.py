"""Inbox fallback scheduler — implements the inline-vs-inbox routing rule.

Per ``docs/atlas-new-design/destinations/inbox-prd.md`` §1.3 + the binding
revision in ``docs/atlas-new-design/cross-audit.md`` §9.1:

    Inline-in-surface by default. A durable Inbox row is created when the user
    has not viewed the originating thread within ``INBOX_FALLBACK_INACTIVITY_MS``
    — **regardless of priority**. The window is tenant-configurable (Settings →
    Workspace → Inbox routing window; default 5min).

This scheduler owns:

1. Reading the tenant's ``inbox.routing.inactivity_minutes`` setting (or falling
   back to the deployment default).
2. Waiting that many minutes.
3. Asking a :class:`ThreadPresencePort` whether the user viewed the thread inside
   the window.
4. If not, calling :class:`InboxProducerPort.enqueue` with a stable
   idempotency key derived from the approval id.

The scheduler is *standalone* — it does not import the run handler — so the
orchestrator can wire it into ``runtime_worker.handlers.run`` at merge time
without parallel-wave conflicts. The runtime worker is expected to call
:meth:`schedule_approval_fallback` after emitting ``approval_requested``.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import Protocol, runtime_checkable

from agent_runtime.api.inbox_producer import (
    InboxItemDraft,
    InboxProducerError,
    InboxProducerPort,
)

_LOGGER = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


_DEFAULT_INACTIVITY_MINUTES = 5
_MIN_INACTIVITY_MINUTES = 1
_MAX_INACTIVITY_MINUTES = 240  # 4 hours; a hard cap so a misconfigured tenant
# can never strand an approval forever waiting for a fallback decision.


class _SettingKeys:
    """Dotted setting paths read from the tenant's runtime policy snapshot."""

    INACTIVITY_MINUTES = "inbox.routing.inactivity_minutes"


# ---------------------------------------------------------------------------
# Ports — presence + settings + clock
# ---------------------------------------------------------------------------


@runtime_checkable
class ThreadPresencePort(Protocol):
    """Returns the timestamp of the user's most recent view of ``thread_id``.

    Implementations consult the chat-surface presence signal (Wave 0-B
    introduces this port) or the persistence-layer ``last_seen_at`` column.
    Returning ``None`` means the user has *never* viewed the thread inside
    the inquired-about window.
    """

    async def last_viewed_at(
        self,
        *,
        tenant_id: str,
        user_id: str,
        thread_id: str,
    ) -> datetime | None:
        """Return the most recent view timestamp, or ``None`` if never viewed."""


@runtime_checkable
class TenantInboxSettingsPort(Protocol):
    """Resolves the per-tenant fallback inactivity window."""

    async def inactivity_minutes(self, *, tenant_id: str) -> int:
        """Return the tenant-configured inactivity window in minutes."""


@runtime_checkable
class ClockPort(Protocol):
    """Inject-the-clock seam so tests can advance time without sleeping."""

    def now(self) -> datetime:
        """Return the current wall-clock time."""


class SystemClock:
    """Default real-clock impl."""

    def now(self) -> datetime:
        return datetime.now(timezone.utc)


class StaticTenantInboxSettings:
    """Returns a single window for every tenant.

    Used in tests and as the deployment-level fallback before the
    ``tenants.inbox_fallback_inactivity_ms`` column lands (cross-audit §9.1).
    """

    def __init__(
        self, *, inactivity_minutes: int = _DEFAULT_INACTIVITY_MINUTES
    ) -> None:
        self._minutes = _clamp_minutes(inactivity_minutes)

    async def inactivity_minutes(self, *, tenant_id: str) -> int:  # noqa: ARG002
        return self._minutes


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _clamp_minutes(value: int) -> int:
    """Clamp a setting value to the safe [1, 240]-minute range."""
    if value < _MIN_INACTIVITY_MINUTES:
        return _MIN_INACTIVITY_MINUTES
    if value > _MAX_INACTIVITY_MINUTES:
        return _MAX_INACTIVITY_MINUTES
    return value


def approval_idempotency_key(approval_id: str) -> str:
    """Stable idempotency key for the approval → inbox fallback path.

    Backend enforces ``(producer_id, external_ref)`` uniqueness, so this key
    must be deterministic from the approval id. The ``approval-`` prefix
    matches the §7.4 example in the inbox PRD.
    """
    return f"approval-{approval_id}"


# ---------------------------------------------------------------------------
# Scheduler
# ---------------------------------------------------------------------------


class InboxFallbackScheduler:
    """Schedules a delayed inactivity check after an approval interrupt.

    Usage::

        scheduler = InboxFallbackScheduler(
            producer=producer,
            presence=presence,
            tenant_settings=settings,
        )
        await scheduler.schedule_approval_fallback(
            tenant_id=tenant_id,
            recipient_user_id=run.user_id,
            thread_id=conversation_id,
            run_id=run.run_id,
            approval_id=approval_id,
            agent_name="Atlas",
            subject_preview="An action needs your approval",
            body_preview="Open the thread to review the proposed edit.",
        )

    The scheduler does not own a background task lifecycle; callers awaiting
    :meth:`schedule_approval_fallback` get a coroutine they can either ``await``
    inline (tests, sync flows) or fire-and-forget via ``asyncio.create_task``
    (production worker, to avoid blocking the run-handler critical path).
    """

    def __init__(
        self,
        *,
        producer: InboxProducerPort,
        presence: ThreadPresencePort,
        tenant_settings: TenantInboxSettingsPort,
        clock: ClockPort | None = None,
        sleep: Callable[[float], Awaitable[None]] | None = None,
    ) -> None:
        self._producer = producer
        self._presence = presence
        self._tenant_settings = tenant_settings
        self._clock = clock or SystemClock()
        # ``asyncio.sleep`` by default; tests inject a fake that advances a
        # virtual clock so they don't actually wait 5 minutes.
        self._sleep = sleep or asyncio.sleep

    async def schedule_approval_fallback(
        self,
        *,
        tenant_id: str,
        recipient_user_id: str,
        thread_id: str,
        run_id: str,
        approval_id: str,
        agent_id: str | None = None,
        agent_name: str | None = None,
        subject: str = "Approval needed",
        preview: str = "An action needs your approval.",
        body: str = "Open the thread to review the proposed edit.",
    ) -> bool:
        """Wait the inactivity window, then enqueue an inbox item if needed.

        Returns ``True`` when an inbox item was produced, ``False`` when the
        user viewed the thread inside the window (no inbox row needed).
        """

        minutes = _clamp_minutes(
            await self._tenant_settings.inactivity_minutes(tenant_id=tenant_id)
        )
        scheduled_at = self._clock.now()
        await self._sleep(minutes * 60)

        last_viewed = await self._presence.last_viewed_at(
            tenant_id=tenant_id,
            user_id=recipient_user_id,
            thread_id=thread_id,
        )
        if last_viewed is not None and last_viewed >= scheduled_at:
            # User came back inside the window — inline approval is enough.
            return False

        # The 5-minute (default) deadline ran out: pull the user back via Inbox.
        draft = InboxItemDraft(
            recipient_user_id=recipient_user_id,
            kind="approval_request",
            subject=subject,
            preview=preview,
            body=body,
            sender_agent_id=agent_id,
            sender_agent_name=agent_name,
            thread_id=thread_id,
            run_id=run_id,
            approval_id=approval_id,
            priority="med",
        )
        try:
            await self._producer.enqueue(
                draft,
                tenant_id=tenant_id,
                target_user_id=recipient_user_id,
                idempotency_key=approval_idempotency_key(approval_id),
            )
        except InboxProducerError as exc:
            # Programmer error: surfaced via metric/log but the runtime keeps
            # going — the inline approval is still the source of truth.
            _LOGGER.warning(
                "inbox_fallback.enqueue_invalid",
                extra={
                    "metadata": {
                        "tenant_id": tenant_id,
                        "recipient_user_id": recipient_user_id,
                        "approval_id": approval_id,
                        "error": str(exc),
                    }
                },
            )
            return False
        return True


# ---------------------------------------------------------------------------
# Static helpers — tenant-policy reading
# ---------------------------------------------------------------------------


class PolicySnapshotTenantInboxSettings:
    """Resolves the inactivity window from the run's frozen policy snapshot.

    The snapshot is populated at run-start by
    ``user_policies_resolver.HttpUserPoliciesResolver``; the inactivity window
    lives at ``policies["inbox"]["routing"]["inactivity_minutes"]``. When the
    key is absent (older snapshots, dev), falls back to the deployment
    default of 5 minutes — matches §9.1's binding decision.
    """

    def __init__(self, snapshot: dict[str, object]) -> None:
        self._snapshot = snapshot

    async def inactivity_minutes(self, *, tenant_id: str) -> int:  # noqa: ARG002
        value = self._snapshot
        for segment in _SettingKeys.INACTIVITY_MINUTES.split("."):
            if not isinstance(value, dict):
                return _DEFAULT_INACTIVITY_MINUTES
            value = value.get(segment)  # type: ignore[assignment]
            if value is None:
                return _DEFAULT_INACTIVITY_MINUTES
        if isinstance(value, bool):
            return _DEFAULT_INACTIVITY_MINUTES
        if isinstance(value, int):
            return _clamp_minutes(value)
        if isinstance(value, str) and value.strip().isdigit():
            return _clamp_minutes(int(value.strip()))
        return _DEFAULT_INACTIVITY_MINUTES


__all__ = [
    "ClockPort",
    "InboxFallbackScheduler",
    "PolicySnapshotTenantInboxSettings",
    "StaticTenantInboxSettings",
    "SystemClock",
    "TenantInboxSettingsPort",
    "ThreadPresencePort",
    "approval_idempotency_key",
]
