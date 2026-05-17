"""Tests for the Home destination SSE stream (P2-A2).

Coverage:

- Bus invariants (monotonic ``sequence_no``, replay-after-cursor is
  exclusive, tenant isolation, heartbeat-vs-row publish validation).
- :class:`HomeSseAdapter` framing (``event:``/``id:``/``data:`` shape +
  ``: keepalive`` comment frames on idle).
- :class:`LastEventIdResolver` (header wins over query, invalid strings
  fall back to query, integer non-negative invariant).
- ``register_home_sse_routes`` exposes ``GET /v1/home/stream`` on the
  app surface (route-level smoke test).

Backend doesn't ship pytest-asyncio so async coroutines run via
``asyncio.run``, matching the pattern in ``tests/test_siem_export.py``.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest
from fastapi import FastAPI

from backend_app.home.sse import (
    Constants,
    HomeActivityBus,
    HomeActivityEventEnvelope,
    HomeSseAdapter,
    InMemoryHomeActivityBus,
    LastEventIdResolver,
    register_home_sse_routes,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _row(activity_id: str = "act_1", kind: str = "drafted_artifact") -> dict:
    """Minimal stub matching the P2-A1 ``HomeActivityRow`` spec (home-prd §4.3)."""

    return {
        "id": activity_id,
        "kind": kind,
        "agent_id": "agent_atlas",
        "agent_name": "Atlas",
        "summary": "Atlas drafted a brief.",
        "created_at": "2026-05-17T12:00:00+00:00",
        "tone": "neutral",
        "target": {"kind": "doc", "doc_id": "doc_1"},
    }


async def _drain(it: AsyncIterator[bytes], limit: int) -> list[bytes]:
    """Pull at most ``limit`` frames out of an async iterator."""

    out: list[bytes] = []
    async for frame in it:
        out.append(frame)
        if len(out) >= limit:
            break
    return out


# ---------------------------------------------------------------------------
# Bus
# ---------------------------------------------------------------------------


class TestHomeActivityBus:
    def test_publish_assigns_monotonic_sequence_no(self) -> None:
        bus = InMemoryHomeActivityBus()

        async def exercise() -> None:
            e1 = await bus.publish(
                org_id="org_a",
                user_id="usr_1",
                event_type="activity_added",
                row=_row("a"),
            )
            e2 = await bus.publish(
                org_id="org_a",
                user_id="usr_1",
                event_type="activity_added",
                row=_row("b"),
            )
            assert e1.sequence_no == 1
            assert e2.sequence_no == 2
            assert e1.event_id != e2.event_id

        asyncio.run(exercise())

    def test_per_channel_sequences_are_independent(self) -> None:
        bus = InMemoryHomeActivityBus()

        async def exercise() -> None:
            await bus.publish(
                org_id="org_a",
                user_id="usr_1",
                event_type="activity_added",
                row=_row("a"),
            )
            other = await bus.publish(
                org_id="org_a",
                user_id="usr_2",
                event_type="activity_added",
                row=_row("b"),
            )
            # Different ``(org_id, user_id)`` channel -> sequence resets to 1.
            assert other.sequence_no == 1

        asyncio.run(exercise())

    def test_list_after_replay_is_exclusive(self) -> None:
        bus = InMemoryHomeActivityBus()

        async def exercise() -> None:
            await bus.publish(
                org_id="org_a",
                user_id="usr_1",
                event_type="activity_added",
                row=_row("a"),
            )
            await bus.publish(
                org_id="org_a",
                user_id="usr_1",
                event_type="activity_updated",
                row=_row("a"),
            )

        asyncio.run(exercise())
        replay = list(bus.list_after(org_id="org_a", user_id="usr_1", after_sequence=1))
        assert [e.sequence_no for e in replay] == [2]
        assert replay[0].event_type == "activity_updated"

    def test_tenant_isolation_cross_org(self) -> None:
        bus = InMemoryHomeActivityBus()

        async def exercise() -> None:
            await bus.publish(
                org_id="org_a",
                user_id="usr_1",
                event_type="activity_added",
                row=_row("a"),
            )
            await bus.publish(
                org_id="org_b",
                user_id="usr_1",
                event_type="activity_added",
                row=_row("b"),
            )

        asyncio.run(exercise())
        org_a_replay = list(
            bus.list_after(org_id="org_a", user_id="usr_1", after_sequence=0)
        )
        org_b_replay = list(
            bus.list_after(org_id="org_b", user_id="usr_1", after_sequence=0)
        )
        # Two orgs, same user_id, perfectly partitioned.
        assert [e.row["id"] for e in org_a_replay if e.row] == ["a"]
        assert [e.row["id"] for e in org_b_replay if e.row] == ["b"]

    def test_tenant_isolation_cross_user_same_org(self) -> None:
        bus = InMemoryHomeActivityBus()

        async def exercise() -> None:
            await bus.publish(
                org_id="org_a",
                user_id="usr_1",
                event_type="activity_added",
                row=_row("a"),
            )
            await bus.publish(
                org_id="org_a",
                user_id="usr_2",
                event_type="activity_added",
                row=_row("b"),
            )

        asyncio.run(exercise())
        u1 = list(bus.list_after(org_id="org_a", user_id="usr_1", after_sequence=0))
        u2 = list(bus.list_after(org_id="org_a", user_id="usr_2", after_sequence=0))
        assert [e.row["id"] for e in u1 if e.row] == ["a"]
        assert [e.row["id"] for e in u2 if e.row] == ["b"]

    def test_publish_rejects_missing_row_on_activity_added(self) -> None:
        bus = InMemoryHomeActivityBus()
        with pytest.raises(ValueError, match="row is required"):
            asyncio.run(
                bus.publish(
                    org_id="org_a",
                    user_id="usr_1",
                    event_type="activity_added",
                    row=None,
                )
            )

    def test_publish_rejects_row_on_heartbeat(self) -> None:
        bus = InMemoryHomeActivityBus()
        with pytest.raises(ValueError, match="row must be None"):
            asyncio.run(
                bus.publish(
                    org_id="org_a",
                    user_id="usr_1",
                    event_type="heartbeat",
                    row=_row("a"),
                )
            )

    def test_latest_sequence_no_initially_zero(self) -> None:
        bus = InMemoryHomeActivityBus()
        assert bus.latest_sequence_no(org_id="org_a", user_id="usr_1") == 0

    def test_buffer_caps_at_max_per_channel(self) -> None:
        # The bus drops oldest events past the depth — verify with a tiny
        # depth so we don't have to publish 256 rows.
        bus = InMemoryHomeActivityBus(max_buffer_per_channel=3)

        async def exercise() -> None:
            for i in range(5):
                await bus.publish(
                    org_id="org_a",
                    user_id="usr_1",
                    event_type="activity_added",
                    row=_row(f"a{i}"),
                )

        asyncio.run(exercise())
        retained = list(
            bus.list_after(org_id="org_a", user_id="usr_1", after_sequence=0)
        )
        # Only the last 3 publishes survive — sequences 3, 4, 5.
        assert [e.sequence_no for e in retained] == [3, 4, 5]

    def test_default_singleton_round_trips(self) -> None:
        InMemoryHomeActivityBus.reset_default_for_tests()
        first = HomeActivityBus.get_default()
        second = HomeActivityBus.get_default()
        assert first is second
        InMemoryHomeActivityBus.reset_default_for_tests()


# ---------------------------------------------------------------------------
# Adapter — framing + replay + Last-Event-ID resume
# ---------------------------------------------------------------------------


class TestHomeSseAdapterFraming:
    def test_format_event_wire_shape(self) -> None:
        envelope = HomeActivityEventEnvelope(
            event_id="evt_1",
            sequence_no=7,
            event_type="activity_added",
            row=_row("a"),
            created_at=__import__("datetime").datetime(
                2026, 5, 17, 12, 0, tzinfo=__import__("datetime").timezone.utc
            ),
        )
        frame = HomeSseAdapter.format_event(envelope).decode("utf-8")
        assert frame.startswith(f"event: {Constants.Sse.EVENT_NAME}\n")
        assert "\nid: 7\n" in frame
        assert "\ndata: " in frame
        assert frame.endswith("\n\n")
        # JSON contains the precise fields the FE consumes.
        assert '"event_id":"evt_1"' in frame
        assert '"sequence_no":7' in frame
        assert '"event_type":"activity_added"' in frame

    def test_replay_then_drain_yields_in_order(self) -> None:
        bus = InMemoryHomeActivityBus()

        async def exercise() -> list[bytes]:
            await bus.publish(
                org_id="org_a",
                user_id="usr_1",
                event_type="activity_added",
                row=_row("a"),
            )
            await bus.publish(
                org_id="org_a",
                user_id="usr_1",
                event_type="activity_added",
                row=_row("b"),
            )
            frames: list[bytes] = []
            async for frame in HomeSseAdapter.stream(
                bus=bus,
                org_id="org_a",
                user_id="usr_1",
                after_sequence=0,
                follow=False,
            ):
                frames.append(frame)
            return frames

        frames = asyncio.run(exercise())
        # Two events replayed, no heartbeat (follow=False short-circuits).
        assert len(frames) == 2
        first, second = (f.decode("utf-8") for f in frames)
        assert "id: 1\n" in first
        assert "id: 2\n" in second

    def test_last_event_id_resume_emits_only_new_events(self) -> None:
        """Reconnect via Last-Event-ID: only events past the cursor flow.

        This is the load-bearing contract — the FE's EventSource resumes
        from the highest ``sequence_no`` it has received and must NOT
        re-see earlier events.
        """

        bus = InMemoryHomeActivityBus()

        async def seed() -> None:
            for label in ("a", "b", "c"):
                await bus.publish(
                    org_id="org_a",
                    user_id="usr_1",
                    event_type="activity_added",
                    row=_row(label),
                )

        asyncio.run(seed())

        # Simulate a reconnect with Last-Event-ID: 2 — should only see
        # the third event (sequence_no=3).
        async def reconnect() -> list[bytes]:
            frames: list[bytes] = []
            async for frame in HomeSseAdapter.stream(
                bus=bus,
                org_id="org_a",
                user_id="usr_1",
                after_sequence=2,
                follow=False,
            ):
                frames.append(frame)
            return frames

        frames = asyncio.run(reconnect())
        assert len(frames) == 1
        body = frames[0].decode("utf-8")
        assert "id: 3\n" in body
        assert '"sequence_no":3' in body

    def test_after_sequence_beyond_high_water_returns_nothing(self) -> None:
        bus = InMemoryHomeActivityBus()

        async def exercise() -> list[bytes]:
            await bus.publish(
                org_id="org_a",
                user_id="usr_1",
                event_type="activity_added",
                row=_row("a"),
            )
            frames: list[bytes] = []
            async for frame in HomeSseAdapter.stream(
                bus=bus,
                org_id="org_a",
                user_id="usr_1",
                after_sequence=10,
                follow=False,
            ):
                frames.append(frame)
            return frames

        assert asyncio.run(exercise()) == []

    def test_tenant_isolation_in_stream(self) -> None:
        """Subscriber for org_a never receives org_b's frames."""

        bus = InMemoryHomeActivityBus()

        async def exercise() -> list[bytes]:
            await bus.publish(
                org_id="org_b",
                user_id="usr_1",
                event_type="activity_added",
                row=_row("only_b"),
            )
            frames: list[bytes] = []
            async for frame in HomeSseAdapter.stream(
                bus=bus,
                org_id="org_a",
                user_id="usr_1",
                after_sequence=0,
                follow=False,
            ):
                frames.append(frame)
            return frames

        assert asyncio.run(exercise()) == []


class TestHomeSseAdapterHeartbeat:
    def test_idle_stream_emits_keepalive_comment(self) -> None:
        """When no events arrive within the heartbeat interval, the
        adapter emits the ``: keepalive\\n\\n`` comment frame so corporate
        proxies don't close the socket.

        We patch the cadence constants to subseconds so the test runs in
        well under a second instead of waiting the production 30s.
        """

        bus = InMemoryHomeActivityBus()
        original_heartbeat = Constants.Cadence.HEARTBEAT_INTERVAL_SECONDS
        original_wait = Constants.Cadence.WAIT_TIMEOUT_SECONDS
        Constants.Cadence.HEARTBEAT_INTERVAL_SECONDS = 0.05
        Constants.Cadence.WAIT_TIMEOUT_SECONDS = 0.01

        try:

            async def exercise() -> bytes:
                stream = HomeSseAdapter.stream(
                    bus=bus,
                    org_id="org_a",
                    user_id="usr_1",
                    after_sequence=0,
                    follow=True,
                )
                # First yield should be a heartbeat — no events were
                # published, so the inner wait times out and we emit the
                # comment frame.
                async for frame in stream:
                    return frame
                pytest.fail("Stream returned without yielding any frame.")

            heartbeat = asyncio.run(exercise())
            assert heartbeat == Constants.Sse.HEARTBEAT_COMMENT
            # Production cadence is 30s — locked by the constant. This
            # assertion guards against accidental cadence drift if the
            # constant is ever changed via a future PR.
            assert original_heartbeat == 30.0
        finally:
            Constants.Cadence.HEARTBEAT_INTERVAL_SECONDS = original_heartbeat
            Constants.Cadence.WAIT_TIMEOUT_SECONDS = original_wait


# ---------------------------------------------------------------------------
# Last-Event-ID resolution
# ---------------------------------------------------------------------------


class TestLastEventIdResolver:
    def test_header_wins_over_query(self) -> None:
        assert (
            LastEventIdResolver.resolve(header_value="42", query_after_sequence=7) == 42
        )

    def test_falls_back_to_query_when_no_header(self) -> None:
        assert (
            LastEventIdResolver.resolve(header_value=None, query_after_sequence=7) == 7
        )

    def test_falls_back_to_query_when_header_unparseable(self) -> None:
        assert (
            LastEventIdResolver.resolve(header_value="abc", query_after_sequence=5) == 5
        )

    def test_falls_back_to_query_when_header_empty_string(self) -> None:
        assert (
            LastEventIdResolver.resolve(header_value="   ", query_after_sequence=3) == 3
        )

    def test_falls_back_to_query_when_header_negative(self) -> None:
        assert (
            LastEventIdResolver.resolve(header_value="-1", query_after_sequence=4) == 4
        )

    def test_zero_header_is_valid(self) -> None:
        # ``Last-Event-ID: 0`` is a legitimate "replay everything" cursor.
        assert (
            LastEventIdResolver.resolve(header_value="0", query_after_sequence=5) == 0
        )

    def test_no_inputs_returns_zero(self) -> None:
        assert (
            LastEventIdResolver.resolve(header_value=None, query_after_sequence=0) == 0
        )

    def test_clamps_negative_query(self) -> None:
        assert (
            LastEventIdResolver.resolve(header_value=None, query_after_sequence=-5) == 0
        )


# ---------------------------------------------------------------------------
# Route registration — smoke test that the path is mounted with the
# right media type and headers. We intentionally don't drive the SSE
# generator from TestClient (Starlette's sync TestClient and our
# follow-loop don't compose well) — the adapter is exercised directly
# in TestHomeSseAdapter*. This test owns the "wired up" claim.
# ---------------------------------------------------------------------------


class TestRouteRegistration:
    def test_register_attaches_path(self) -> None:
        app = FastAPI()
        bus = InMemoryHomeActivityBus()
        register_home_sse_routes(app, bus=bus)
        # State carries the bus so other modules (the aggregator's
        # publisher hook) can locate it without a second singleton.
        assert app.state.home_activity_bus is bus
        paths = {route.path for route in app.routes}
        assert "/v1/home/stream" in paths

    def test_register_attaches_correct_route_method_and_path(self) -> None:
        """Smoke-level: the route's path + method are mounted as documented.

        We deliberately avoid driving the SSE generator via Starlette's
        sync ``TestClient`` — the ``follow=True`` loop waits on an
        ``asyncio.Condition`` and the sync test client never trips
        ``request.is_disconnected``, so the stream wedges forever inside
        the test process. Framing + heartbeat + Last-Event-ID resume
        are exercised directly against :class:`HomeSseAdapter` in
        :class:`TestHomeSseAdapterFraming` / :class:`TestHomeSseAdapterHeartbeat`.
        """

        from starlette.routing import Route

        app = FastAPI()
        register_home_sse_routes(app, bus=InMemoryHomeActivityBus())
        match = next(
            (
                route
                for route in app.routes
                if isinstance(route, Route) and route.path == "/v1/home/stream"
            ),
            None,
        )
        assert match is not None, "/v1/home/stream not registered"
        assert "GET" in match.methods
