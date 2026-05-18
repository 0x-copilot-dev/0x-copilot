"""Tests for the Tools SSE adapter — Phase 10 P10-A2.

Coverage:

* One full envelope sequence: publish a ``tool.created`` event, drain
  via :meth:`ToolsSseAdapter.stream` with ``follow=False``, and assert
  the SSE frame shape (event name / id / data).
* ``Last-Event-ID`` resolver semantics (header wins; falls back to
  ``?after_sequence``; invalid header collapses to query).
* Per-channel tenant isolation — events published on ``(org_a, user_1)``
  never surface on ``(org_b, user_1)``.
"""

from __future__ import annotations

import asyncio
import json

from backend_app.tools.sse import (
    InMemoryToolsActivityBus,
    LastEventIdResolver,
    ToolsSseAdapter,
)


def _drain(coro_iter) -> list[bytes]:
    """Synchronously drain an async-iter into a list."""
    loop = asyncio.new_event_loop()
    try:
        frames: list[bytes] = []

        async def _go() -> None:
            async for chunk in coro_iter:
                frames.append(chunk)

        loop.run_until_complete(_go())
        return frames
    finally:
        loop.close()


class TestPublishAndStream:
    def test_full_envelope_sequence(self) -> None:
        bus = InMemoryToolsActivityBus()
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(
                bus.publish(
                    org_id="org_acme",
                    user_id="usr_sarah",
                    event_type="tool.created",
                    tool={"id": "tool_x", "name": "Slack"},
                )
            )
        finally:
            loop.close()
        frames = _drain(
            ToolsSseAdapter.stream(
                bus=bus,
                org_id="org_acme",
                user_id="usr_sarah",
                after_sequence=0,
                follow=False,
            )
        )
        assert len(frames) == 1
        text = frames[0].decode("utf-8")
        assert text.startswith("event: tool_event\n")
        assert "\nid: 1\n" in text
        # data line is a single JSON object on one line.
        data_line = [line for line in text.split("\n") if line.startswith("data: ")][0][
            len("data: ") :
        ]
        payload = json.loads(data_line)
        assert payload["event_type"] == "tool.created"
        assert payload["sequence_no"] == 1
        assert payload["tool"]["id"] == "tool_x"

    def test_tenant_isolation_between_channels(self) -> None:
        bus = InMemoryToolsActivityBus()
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(
                bus.publish(
                    org_id="org_acme",
                    user_id="usr_sarah",
                    event_type="tool.created",
                    tool={"id": "tool_acme"},
                )
            )
            loop.run_until_complete(
                bus.publish(
                    org_id="org_zeta",
                    user_id="usr_alice",
                    event_type="tool.created",
                    tool={"id": "tool_zeta"},
                )
            )
        finally:
            loop.close()
        # org_acme channel sees only org_acme's row.
        frames_acme = _drain(
            ToolsSseAdapter.stream(
                bus=bus,
                org_id="org_acme",
                user_id="usr_sarah",
                after_sequence=0,
                follow=False,
            )
        )
        assert len(frames_acme) == 1
        assert b"tool_acme" in frames_acme[0]
        assert b"tool_zeta" not in frames_acme[0]


class TestLastEventIdResolver:
    def test_header_wins_when_parseable(self) -> None:
        assert (
            LastEventIdResolver.resolve(header_value="42", query_after_sequence=0) == 42
        )

    def test_query_fallback_when_header_invalid(self) -> None:
        assert (
            LastEventIdResolver.resolve(
                header_value="not-a-number", query_after_sequence=7
            )
            == 7
        )

    def test_zero_when_neither_supplied(self) -> None:
        assert (
            LastEventIdResolver.resolve(header_value=None, query_after_sequence=0) == 0
        )

    def test_negative_header_falls_back(self) -> None:
        assert (
            LastEventIdResolver.resolve(header_value="-5", query_after_sequence=3) == 3
        )


class TestPublishValidation:
    def test_heartbeat_rejects_tool_payload(self) -> None:
        bus = InMemoryToolsActivityBus()
        loop = asyncio.new_event_loop()
        try:
            import pytest

            with pytest.raises(ValueError):
                loop.run_until_complete(
                    bus.publish(
                        org_id="org_acme",
                        user_id="usr_sarah",
                        event_type="tool.heartbeat",
                        tool={"id": "tool_x"},
                    )
                )
        finally:
            loop.close()

    def test_invoked_requires_invocation(self) -> None:
        bus = InMemoryToolsActivityBus()
        loop = asyncio.new_event_loop()
        try:
            import pytest

            with pytest.raises(ValueError):
                loop.run_until_complete(
                    bus.publish(
                        org_id="org_acme",
                        user_id="usr_sarah",
                        event_type="tool.invoked",
                    )
                )
        finally:
            loop.close()
