"""Tests for the inline-vs-inbox routing rule (P4-A2).

Per ``docs/atlas-new-design/cross-audit.md`` §9.1 binding revision:

* Inline-in-surface by default.
* Durable Inbox row when the user has NOT viewed the originating thread
  within ``INBOX_FALLBACK_INACTIVITY_MS`` (tenant-configurable; default
  5min) — **regardless of priority**.

This module exercises :class:`InboxFallbackScheduler` end-to-end with a
fake clock + presence + producer. The fake :func:`_sleep` advances the
virtual clock so the test never actually waits 5 minutes.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from agent_runtime.api.inbox_fallback import (
    InboxFallbackScheduler,
    PolicySnapshotTenantInboxSettings,
    StaticTenantInboxSettings,
    approval_idempotency_key,
)
from agent_runtime.api.inbox_producer import NullInboxProducer


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeClock:
    """Virtual clock advanced by the fake sleep function."""

    def __init__(self, start: datetime) -> None:
        self._now = start

    def now(self) -> datetime:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now = self._now + timedelta(seconds=seconds)


class _FakePresence:
    """Returns the user's last-viewed-at timestamp from a fixed mapping."""

    def __init__(self, mapping: dict[tuple[str, str, str], datetime]) -> None:
        self._mapping = mapping

    async def last_viewed_at(
        self,
        *,
        tenant_id: str,
        user_id: str,
        thread_id: str,
    ) -> datetime | None:
        return self._mapping.get((tenant_id, user_id, thread_id))


def _make_sleep(clock: _FakeClock):
    """Build a sleep coroutine that advances ``clock`` instead of waiting."""

    async def sleep(seconds: float) -> None:
        clock.advance(seconds)

    return sleep


# ---------------------------------------------------------------------------
# Routing rule — happy path + non-happy path
# ---------------------------------------------------------------------------


class TestRouting:
    @pytest.mark.asyncio
    async def test_user_viewed_inside_window_no_inbox_item(self) -> None:
        """User came back inside the 5-min window — no durable Inbox row."""
        start = datetime(2026, 5, 17, 12, 0, 0, tzinfo=timezone.utc)
        clock = _FakeClock(start)
        # User viewed the thread 2 minutes after the approval interrupt — well
        # inside the 5-minute window.
        presence = _FakePresence(
            {("org_a", "user_a", "conv_001"): start + timedelta(minutes=2)}
        )
        producer = NullInboxProducer()
        scheduler = InboxFallbackScheduler(
            producer=producer,
            presence=presence,
            tenant_settings=StaticTenantInboxSettings(inactivity_minutes=5),
            clock=clock,
            sleep=_make_sleep(clock),
        )

        produced = await scheduler.schedule_approval_fallback(
            tenant_id="org_a",
            recipient_user_id="user_a",
            thread_id="conv_001",
            run_id="run_001",
            approval_id="approval_001",
            agent_id="atlas",
            agent_name="Atlas",
        )

        assert produced is False
        assert producer.calls == []

    @pytest.mark.asyncio
    async def test_user_inactive_inbox_item_produced(self) -> None:
        """User never came back during the window — produce an Inbox row."""
        start = datetime(2026, 5, 17, 12, 0, 0, tzinfo=timezone.utc)
        clock = _FakeClock(start)
        # Empty presence map: user never viewed the thread.
        presence = _FakePresence({})
        producer = NullInboxProducer()
        scheduler = InboxFallbackScheduler(
            producer=producer,
            presence=presence,
            tenant_settings=StaticTenantInboxSettings(inactivity_minutes=5),
            clock=clock,
            sleep=_make_sleep(clock),
        )

        produced = await scheduler.schedule_approval_fallback(
            tenant_id="org_a",
            recipient_user_id="user_a",
            thread_id="conv_001",
            run_id="run_001",
            approval_id="approval_001",
            agent_id="atlas",
            agent_name="Atlas",
        )

        assert produced is True
        assert len(producer.calls) == 1
        draft, tenant_id, target_user_id, idempotency_key = producer.calls[0]
        assert tenant_id == "org_a"
        assert target_user_id == "user_a"
        # §7.4: idempotency_key = approval-<approval_id>
        assert idempotency_key == "approval-approval_001"
        assert draft.kind == "approval_request"
        assert draft.approval_id == "approval_001"
        assert draft.thread_id == "conv_001"
        assert draft.run_id == "run_001"
        assert draft.sender_agent_id == "atlas"
        assert draft.sender_agent_name == "Atlas"
        # Clock advanced by the full window — 5 minutes = 300 seconds.
        assert clock.now() == start + timedelta(minutes=5)

    @pytest.mark.asyncio
    async def test_user_viewed_before_window_started_still_produces(self) -> None:
        """A view *before* the approval interrupt does not satisfy the window."""
        start = datetime(2026, 5, 17, 12, 0, 0, tzinfo=timezone.utc)
        clock = _FakeClock(start)
        # User viewed the thread 10 minutes before the approval; that view is
        # stale and should not suppress the inbox row.
        presence = _FakePresence(
            {("org_a", "user_a", "conv_001"): start - timedelta(minutes=10)}
        )
        producer = NullInboxProducer()
        scheduler = InboxFallbackScheduler(
            producer=producer,
            presence=presence,
            tenant_settings=StaticTenantInboxSettings(inactivity_minutes=5),
            clock=clock,
            sleep=_make_sleep(clock),
        )

        produced = await scheduler.schedule_approval_fallback(
            tenant_id="org_a",
            recipient_user_id="user_a",
            thread_id="conv_001",
            run_id="run_001",
            approval_id="approval_001",
        )

        assert produced is True
        assert len(producer.calls) == 1

    @pytest.mark.asyncio
    async def test_idempotency_key_is_stable_across_calls(self) -> None:
        """Same approval_id → same idempotency_key regardless of how many times
        the scheduler runs (backend dedupes on (producer_id, external_ref))."""

        assert approval_idempotency_key("approval_001") == "approval-approval_001"
        assert approval_idempotency_key("approval_001") == "approval-approval_001"


# ---------------------------------------------------------------------------
# Tenant-configurable window
# ---------------------------------------------------------------------------


class TestTenantWindow:
    @pytest.mark.asyncio
    async def test_short_window_inbox_fires_sooner(self) -> None:
        """A tenant configured to 1 minute fires the Inbox row after 60s."""
        start = datetime(2026, 5, 17, 12, 0, 0, tzinfo=timezone.utc)
        clock = _FakeClock(start)
        presence = _FakePresence({})
        producer = NullInboxProducer()
        scheduler = InboxFallbackScheduler(
            producer=producer,
            presence=presence,
            tenant_settings=StaticTenantInboxSettings(inactivity_minutes=1),
            clock=clock,
            sleep=_make_sleep(clock),
        )

        await scheduler.schedule_approval_fallback(
            tenant_id="org_a",
            recipient_user_id="user_a",
            thread_id="conv_001",
            run_id="run_001",
            approval_id="approval_001",
        )
        # Window respected — clock advanced by 1 minute, not 5.
        assert clock.now() == start + timedelta(minutes=1)
        assert len(producer.calls) == 1

    @pytest.mark.asyncio
    async def test_policy_snapshot_resolves_window(self) -> None:
        """Tenant inactivity window read from the frozen policy snapshot."""
        snapshot = {"inbox": {"routing": {"inactivity_minutes": 2}}}
        settings = PolicySnapshotTenantInboxSettings(snapshot)
        assert await settings.inactivity_minutes(tenant_id="org_a") == 2

    @pytest.mark.asyncio
    async def test_policy_snapshot_defaults_to_five_when_absent(self) -> None:
        settings = PolicySnapshotTenantInboxSettings({})
        assert await settings.inactivity_minutes(tenant_id="org_a") == 5

    @pytest.mark.asyncio
    async def test_policy_snapshot_clamps_invalid(self) -> None:
        """A tenant cannot strand approvals forever — minutes are clamped."""
        settings = PolicySnapshotTenantInboxSettings(
            {"inbox": {"routing": {"inactivity_minutes": 10_000}}}
        )
        # Clamped to the 240-minute (4h) hard cap.
        assert await settings.inactivity_minutes(tenant_id="org_a") == 240

    @pytest.mark.asyncio
    async def test_policy_snapshot_treats_zero_as_min(self) -> None:
        settings = PolicySnapshotTenantInboxSettings(
            {"inbox": {"routing": {"inactivity_minutes": 0}}}
        )
        # Clamped up to 1-minute minimum.
        assert await settings.inactivity_minutes(tenant_id="org_a") == 1
