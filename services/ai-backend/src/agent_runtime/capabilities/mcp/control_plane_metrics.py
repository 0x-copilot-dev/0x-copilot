"""Closed-vocabulary, body-free MCP control-plane OpenTelemetry metrics."""

from __future__ import annotations

from enum import StrEnum
from time import monotonic
from typing import Protocol

from opentelemetry import metrics


class McpControlPlaneEvent(StrEnum):
    CACHE = "cache"
    EXACT = "exact"
    SUBJECT = "subject"
    FEED = "feed"
    INVALIDATION = "invalidation"
    CURSOR_ACK = "cursor_ack"
    POLLER = "poller"


class McpControlPlaneOutcome(StrEnum):
    FRESH = "fresh"
    NOT_TRACKED = "not_tracked"
    REVISION_CHANGED = "revision_changed"
    EXPIRED = "expired"
    EVICTED = "evicted"
    RACE = "race"
    UNTRACKED = "untracked"
    ADMITTED = "admitted"
    DECLINED = "declined"
    APPLIED = "applied"
    OFFLINE = "offline"
    CURSOR_EXPIRED = "cursor_expired"
    STALLED = "stalled"
    BOUND = "bound"
    FAILED = "failed"
    STARTED = "started"
    STOPPED = "stopped"
    TIMED_OUT = "timed_out"


class McpControlPlaneMeasure(StrEnum):
    ACTIVE_SUBJECTS = "active_subjects"
    HTTP_CALLS = "http_calls"
    PAGES = "pages"
    NOTICES = "notices"
    BYTES = "bytes"


class McpControlPlaneMetricsPort(Protocol):
    def event(
        self, *, event: McpControlPlaneEvent, outcome: McpControlPlaneOutcome
    ) -> None: ...
    def count(
        self,
        *,
        event: McpControlPlaneEvent,
        measure: McpControlPlaneMeasure,
        value: int,
    ) -> None: ...
    def latency(self, *, event: McpControlPlaneEvent, seconds: float) -> None: ...


class McpControlPlaneMetrics:
    """Fail-soft OTel recorder; enum-only labels cannot contain identifiers."""

    def __init__(self) -> None:
        meter = metrics.get_meter("agent_runtime.mcp_control_plane")
        self._events = meter.create_counter("mcp_control_plane_events_total")
        self._counts = meter.create_counter("mcp_control_plane_count_total")
        self._latency = meter.create_histogram(
            "mcp_control_plane_latency_seconds", unit="s"
        )

    def event(
        self, *, event: McpControlPlaneEvent, outcome: McpControlPlaneOutcome
    ) -> None:
        try:
            self._events.add(1, {"event": event.value, "outcome": outcome.value})
        except Exception:
            return

    def count(
        self,
        *,
        event: McpControlPlaneEvent,
        measure: McpControlPlaneMeasure,
        value: int,
    ) -> None:
        try:
            if value > 0:
                self._counts.add(
                    value, {"event": event.value, "measure": measure.value}
                )
        except Exception:
            return

    def latency(self, *, event: McpControlPlaneEvent, seconds: float) -> None:
        try:
            self._latency.record(max(0.0, seconds), {"event": event.value})
        except Exception:
            return


class NoopMcpControlPlaneMetrics:
    def event(self, **_kwargs: object) -> None:
        return

    def count(self, **_kwargs: object) -> None:
        return

    def latency(self, **_kwargs: object) -> None:
        return


def elapsed(started: float) -> float:
    return monotonic() - started
