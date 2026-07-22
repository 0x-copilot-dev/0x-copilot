"""Tests for the Connectors destination SSE stream (P11-A2 §4.9)."""

from __future__ import annotations

import asyncio
import time

from backend_app.connectors.sse import (
    Constants,
    InMemoryConnectorActivityBus,
    ConnectorSseAdapter,
    LastEventIdResolver,
)


def _payload(connector_id: str = "conn_1") -> dict:
    return {
        "id": connector_id,
        "tenant_id": "org_a",
        "slug": "gmail",
        "display_name": "Gmail",
        "status": "connected",
        "owner_user_id": "usr_1",
    }


class TestActivityBus:
    def test_publish_assigns_monotonic_sequence_no(self) -> None:
        bus = InMemoryConnectorActivityBus()

        async def exercise() -> None:
            first = await bus.publish(
                org_id="org_a",
                user_id="usr_1",
                event_type="connector.created",
                connector=_payload("conn_1"),
            )
            second = await bus.publish(
                org_id="org_a",
                user_id="usr_1",
                event_type="connector.status_changed",
                connector=_payload("conn_1"),
            )
            assert first.sequence_no == 1
            assert second.sequence_no == 2

        asyncio.run(exercise())

    def test_per_channel_sequences_are_independent(self) -> None:
        bus = InMemoryConnectorActivityBus()

        async def exercise() -> None:
            await bus.publish(
                org_id="org_a",
                user_id="usr_1",
                event_type="connector.created",
                connector=_payload("conn_1"),
            )
            second = await bus.publish(
                org_id="org_a",
                user_id="usr_2",
                event_type="connector.created",
                connector=_payload("conn_2"),
            )
            assert second.sequence_no == 1

        asyncio.run(exercise())

    def test_tenant_isolation_cross_org(self) -> None:
        bus = InMemoryConnectorActivityBus()

        async def exercise() -> None:
            await bus.publish(
                org_id="org_a",
                user_id="usr_1",
                event_type="connector.created",
                connector=_payload("conn_1"),
            )
            await bus.publish(
                org_id="org_b",
                user_id="usr_1",
                event_type="connector.created",
                connector=_payload("conn_2"),
            )

        asyncio.run(exercise())
        a_rows = list(bus.list_after(org_id="org_a", user_id="usr_1", after_sequence=0))
        b_rows = list(bus.list_after(org_id="org_b", user_id="usr_1", after_sequence=0))
        assert [r.connector["id"] for r in a_rows if r.connector] == ["conn_1"]
        assert [r.connector["id"] for r in b_rows if r.connector] == ["conn_2"]


class TestEnvelopeSequence:
    def test_publish_creates_one_envelope_per_call(self) -> None:
        bus = InMemoryConnectorActivityBus()

        async def exercise() -> None:
            for slug in ("gmail", "slack", "github"):
                await bus.publish(
                    org_id="org_a",
                    user_id="usr_1",
                    event_type="connector.created",
                    connector={**_payload(slug), "slug": slug},
                )

        asyncio.run(exercise())
        events = list(bus.list_after(org_id="org_a", user_id="usr_1", after_sequence=0))
        slugs = [e.connector["slug"] for e in events if e.connector]
        assert slugs == ["gmail", "slack", "github"]
        # Sequences are 1..3 monotonic.
        assert [e.sequence_no for e in events] == [1, 2, 3]


class TestSseFraming:
    def test_format_event_emits_event_id_data_lines(self) -> None:
        bus = InMemoryConnectorActivityBus()

        async def exercise() -> None:
            envelope = await bus.publish(
                org_id="org_a",
                user_id="usr_1",
                event_type="connector.created",
                connector=_payload(),
            )
            frame = ConnectorSseAdapter.format_event(envelope)
            assert frame.startswith(b"event: connector_event\n")
            assert b"id: 1\n" in frame
            assert b'"event_type":"connector.created"' in frame

        asyncio.run(exercise())


class TestLoopBoundWakeup:
    """PRD-I I2 — bound-loop publish_nowait wakes waiters immediately."""

    POLL_SLICE = Constants.Cadence.WAIT_TIMEOUT_SECONDS

    def test_bound_bus_wakes_waiter_well_under_the_poll_slice(self) -> None:
        bus = InMemoryConnectorActivityBus()

        async def exercise() -> float:
            bus.bind_loop(asyncio.get_running_loop())
            started = time.monotonic()
            waiter = asyncio.ensure_future(
                bus.wait(org_id="org_a", user_id="usr_1", timeout=self.POLL_SLICE)
            )
            await asyncio.sleep(0.05)  # let the waiter register + block
            # Publish from a worker thread — same shape as the threadpool
            # ``def`` MCP route driving the write-through glue.
            await asyncio.to_thread(
                bus.publish_nowait,
                org_id="org_a",
                user_id="usr_1",
                event_type="connector.created",
                connector=_payload(),
            )
            await waiter
            return time.monotonic() - started

        elapsed = asyncio.run(exercise())
        assert elapsed < 1.0, f"wakeup took {elapsed:.2f}s (poll-slice fallback?)"

    def test_unbound_bus_keeps_legacy_poll_slice_semantics(self) -> None:
        bus = InMemoryConnectorActivityBus()

        async def exercise() -> float:
            assert not bus.loop_bound
            started = time.monotonic()
            waiter = asyncio.ensure_future(
                bus.wait(org_id="org_a", user_id="usr_1", timeout=0.3)
            )
            await asyncio.sleep(0.05)
            envelope = await asyncio.to_thread(
                bus.publish_nowait,
                org_id="org_a",
                user_id="usr_1",
                event_type="connector.created",
                connector=_payload(),
            )
            assert envelope.sequence_no == 1  # append unchanged
            await waiter
            return time.monotonic() - started

        elapsed = asyncio.run(exercise())
        # No wakeup: the waiter runs out its full timeout slice.
        assert elapsed >= 0.3

    def test_no_cross_tenant_wakeup(self) -> None:
        bus = InMemoryConnectorActivityBus()

        async def exercise() -> float:
            bus.bind_loop(asyncio.get_running_loop())
            started = time.monotonic()
            waiter = asyncio.ensure_future(
                bus.wait(org_id="org_b", user_id="usr_1", timeout=0.5)
            )
            await asyncio.sleep(0.05)
            await asyncio.to_thread(
                bus.publish_nowait,
                org_id="org_a",
                user_id="usr_1",
                event_type="connector.created",
                connector=_payload(),
            )
            await waiter
            return time.monotonic() - started

        elapsed = asyncio.run(exercise())
        # The org_b waiter must NOT be woken by org_a's publish.
        assert elapsed >= 0.5

    def test_closed_loop_degrades_to_poll_slice_without_failing_publish(self) -> None:
        """NFR-I.1 — a dead bound loop never fails the mutation path."""

        bus = InMemoryConnectorActivityBus()
        loop = asyncio.new_event_loop()
        loop.close()
        bus.bind_loop(loop)
        envelope = bus.publish_nowait(
            org_id="org_a",
            user_id="usr_1",
            event_type="connector.created",
            connector=_payload(),
        )
        assert envelope.sequence_no == 1
        rows = list(bus.list_after(org_id="org_a", user_id="usr_1", after_sequence=0))
        assert len(rows) == 1

    def test_unbind_restores_legacy_semantics(self) -> None:
        bus = InMemoryConnectorActivityBus()
        loop = asyncio.new_event_loop()
        try:
            bus.bind_loop(loop)
            assert bus.loop_bound
            bus.unbind_loop()
            assert not bus.loop_bound
            envelope = bus.publish_nowait(
                org_id="org_a",
                user_id="usr_1",
                event_type="connector.created",
                connector=_payload(),
            )
            assert envelope.sequence_no == 1
        finally:
            loop.close()


class TestLastEventIdResolver:
    def test_header_wins_over_query(self) -> None:
        resolved = LastEventIdResolver.resolve(header_value="7", query_after_sequence=3)
        assert resolved == 7

    def test_invalid_header_falls_back_to_query(self) -> None:
        resolved = LastEventIdResolver.resolve(
            header_value="not-a-number", query_after_sequence=4
        )
        assert resolved == 4

    def test_negative_header_falls_back_to_query(self) -> None:
        resolved = LastEventIdResolver.resolve(
            header_value="-1", query_after_sequence=2
        )
        assert resolved == 2


class TestConstantsLockedToWireShape:
    def test_event_name_locked(self) -> None:
        assert Constants.Sse.EVENT_NAME == "connector_event"

    def test_heartbeat_interval_matches_inbox(self) -> None:
        assert Constants.Cadence.HEARTBEAT_INTERVAL_SECONDS == 30.0
