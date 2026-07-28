"""Qualification of the body-free MCP control-plane metrics port."""

from __future__ import annotations

import asyncio

from agent_runtime.capabilities.mcp.control_plane_metrics import (
    McpControlPlaneEvent,
    McpControlPlaneMeasure,
    McpControlPlaneOutcome,
)
from agent_runtime.capabilities.mcp.discovery_cache import McpDiscoveryCache
from agent_runtime.capabilities.mcp.freshness import (
    McpDescriptorFreshnessRequest,
    McpDescriptorRevision,
    McpDescriptorSubject,
    RevisionAwareMcpDiscoveryCache,
)
from agent_runtime.capabilities.mcp.revision_feed import (
    ActiveMcpRevisionSubjectRegistry,
    McpRevisionSubject,
)


class _Spy:
    def __init__(self) -> None:
        self.calls: list[tuple[object, ...]] = []

    def event(
        self, *, event: McpControlPlaneEvent, outcome: McpControlPlaneOutcome
    ) -> None:
        assert isinstance(event, McpControlPlaneEvent)
        assert isinstance(outcome, McpControlPlaneOutcome)
        self.calls.append((event, outcome))

    def count(
        self,
        *,
        event: McpControlPlaneEvent,
        measure: McpControlPlaneMeasure,
        value: int,
    ) -> None:
        assert isinstance(event, McpControlPlaneEvent)
        assert isinstance(measure, McpControlPlaneMeasure)
        assert isinstance(value, int)
        self.calls.append((event, measure, value))

    def latency(
        self,
        *,
        event: McpControlPlaneEvent,
        measure: McpControlPlaneMeasure,
        seconds: float,
    ) -> None:
        assert isinstance(event, McpControlPlaneEvent)
        assert isinstance(measure, McpControlPlaneMeasure)
        assert isinstance(seconds, float) and seconds >= 0
        self.calls.append((event, measure, seconds))


def _request() -> McpDescriptorFreshnessRequest:
    return McpDescriptorFreshnessRequest(
        server_name="private-server",
        subject=McpDescriptorSubject(org_id="private-org", user_id="private-user"),
        revision=McpDescriptorRevision(value="private-revision"),
    )


def test_subject_admission_and_cache_contention_emit_only_closed_facts() -> None:
    async def run() -> None:
        spy = _Spy()
        subjects = ActiveMcpRevisionSubjectRegistry(max_subjects=1, metrics=spy)
        assert await subjects.touch_verified(
            McpRevisionSubject(org_id="private-org", user_id="private-user")
        )
        assert not await subjects.touch_verified(
            McpRevisionSubject(org_id="other-org", user_id="other-user")
        )
        cache = RevisionAwareMcpDiscoveryCache(
            McpDiscoveryCache(), max_staleness_seconds=60, metrics=spy
        )
        gate = asyncio.Event()

        async def load():
            gate.set()
            await asyncio.sleep(0.01)

        first = asyncio.create_task(cache.get_or_load(_request(), load))
        await gate.wait()
        await cache.get_or_load(_request(), load)
        await first
        assert (
            McpControlPlaneEvent.SUBJECT,
            McpControlPlaneOutcome.ADMITTED,
        ) in spy.calls
        assert (
            McpControlPlaneEvent.SUBJECT,
            McpControlPlaneOutcome.DECLINED,
        ) in spy.calls
        assert (
            McpControlPlaneEvent.CACHE,
            McpControlPlaneOutcome.COALESCED,
        ) in spy.calls
        dumped = repr(spy.calls)
        assert (
            "private-org" not in dumped
            and "private-user" not in dumped
            and "private-revision" not in dumped
        )

    asyncio.run(run())
