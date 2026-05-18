"""Tests for the Team SSE bus + adapter (P12-A2 §4.1 / sub-PRD §3.1).

A single envelope sequence: publish → list_after returns it →
format_event encodes it to the SSE wire shape. Mirrors the inbox SSE
test discipline.
"""

from __future__ import annotations

import asyncio

import pytest

from backend_app.team.sse import (
    Constants,
    InMemoryTeamActivityBus,
    LastEventIdResolver,
    TeamSseAdapter,
    TeamStreamEnvelope,
)


def _person_payload() -> dict[str, object]:
    return {
        "id": "usr_member",
        "tenant_id": "org_acme",
        "display_name": "Member User",
        "email": "member@acme.com",
        "role": "member",
        "presence": "active",
        "last_seen_at": None,
        "joined_at": "2026-01-01T00:00:00+00:00",
        "agents_count": 0,
        "projects_count": 0,
        "is_self": False,
    }


def test_publish_assigns_monotonic_sequence() -> None:
    bus = InMemoryTeamActivityBus()

    async def _go() -> tuple[TeamStreamEnvelope, TeamStreamEnvelope]:
        a = await bus.publish(
            tenant_id="org_acme",
            user_id="usr_admin",
            event_type="team.role_changed",
            person=_person_payload(),
        )
        b = await bus.publish(
            tenant_id="org_acme",
            user_id="usr_admin",
            event_type="team.role_changed",
            person=_person_payload(),
        )
        return a, b

    a, b = asyncio.run(_go())
    assert a.sequence_no == 1
    assert b.sequence_no == 2
    assert a.event_type == "team.role_changed"


def test_publish_rejects_missing_person_on_non_heartbeat() -> None:
    bus = InMemoryTeamActivityBus()
    with pytest.raises(ValueError):

        async def _go() -> None:
            await bus.publish(
                tenant_id="org_acme",
                user_id="usr_admin",
                event_type="team.role_changed",
                person=None,
            )

        asyncio.run(_go())


def test_list_after_filters_by_sequence_and_channel() -> None:
    bus = InMemoryTeamActivityBus()

    async def _go() -> None:
        await bus.publish(
            tenant_id="org_acme",
            user_id="usr_admin",
            event_type="team.role_changed",
            person=_person_payload(),
        )
        await bus.publish(
            tenant_id="org_acme",
            user_id="usr_admin",
            event_type="team.role_changed",
            person=_person_payload(),
        )
        await bus.publish(
            tenant_id="org_acme",
            user_id="usr_other",
            event_type="team.role_changed",
            person=_person_payload(),
        )

    asyncio.run(_go())
    # Channel A: skipping the first event returns only the second.
    after = tuple(
        bus.list_after(tenant_id="org_acme", user_id="usr_admin", after_sequence=1)
    )
    assert [e.sequence_no for e in after] == [2]
    # Tenant isolation: usr_other's channel returns its own events
    # independently, never bleeding into usr_admin's stream.
    other = tuple(
        bus.list_after(tenant_id="org_acme", user_id="usr_other", after_sequence=0)
    )
    assert [e.sequence_no for e in other] == [1]


def test_format_event_wire_shape() -> None:
    bus = InMemoryTeamActivityBus()

    async def _go() -> TeamStreamEnvelope:
        return await bus.publish(
            tenant_id="org_acme",
            user_id="usr_admin",
            event_type="team.presence_changed",
            person=_person_payload(),
        )

    envelope = asyncio.run(_go())
    framed = TeamSseAdapter.format_event(envelope).decode("utf-8")
    assert framed.startswith(f"event: {Constants.Sse.EVENT_NAME}\n")
    assert "id: 1\n" in framed
    assert framed.endswith("\n\n")
    assert '"event_type":"team.presence_changed"' in framed


def test_last_event_id_resolver_prefers_header() -> None:
    assert LastEventIdResolver.resolve(header_value="42", query_after_sequence=5) == 42


def test_last_event_id_resolver_falls_back_to_query() -> None:
    assert LastEventIdResolver.resolve(header_value=None, query_after_sequence=7) == 7


def test_last_event_id_resolver_handles_unparseable_header() -> None:
    assert (
        LastEventIdResolver.resolve(header_value="garbage", query_after_sequence=3) == 3
    )


def test_stream_drains_buffered_events_when_follow_false() -> None:
    """Sanity-check the adapter happy path with ``follow=False`` — the
    bus replays the buffered events then closes; production callers
    use ``follow=True`` (which loops + heartbeats)."""

    bus = InMemoryTeamActivityBus()

    async def _go() -> list[bytes]:
        await bus.publish(
            tenant_id="org_acme",
            user_id="usr_admin",
            event_type="team.invited",
            person=_person_payload(),
        )
        frames: list[bytes] = []
        async for frame in TeamSseAdapter.stream(
            bus=bus,
            tenant_id="org_acme",
            user_id="usr_admin",
            after_sequence=0,
            follow=False,
        ):
            frames.append(frame)
        return frames

    frames = asyncio.run(_go())
    assert len(frames) == 1
    assert b"team.invited" in frames[0]
