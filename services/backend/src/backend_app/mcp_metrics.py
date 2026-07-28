"""Body-free OpenTelemetry diagnostics for the MCP revision and session paths.

Metric attributes deliberately describe only a bounded phase/outcome vocabulary.
MCP identifiers, opaque leases/cursors, descriptor bodies, endpoints, and
credential material must never become telemetry dimensions.
"""

from __future__ import annotations

from typing import Any, Protocol

_METER_NAME = "backend.mcp"

_PHASE_OUTCOMES: dict[str, frozenset[str]] = {
    "card_validation": frozenset({"completed"}),
    "lease_acquisition": frozenset(
        {
            "acquired",
            "saturated",
            "unavailable",
            "stale",
            "shutting_down",
        }
    ),
    "lease_reuse": frozenset({"reused"}),
    "session_initialization": frozenset({"initialized", "unavailable"}),
    "saturation": frozenset({"rejected"}),
    "invalidation": frozenset({"scope", "credential_subject"}),
    "reconnect": frozenset(
        {"reconnected", "unavailable", "budget_exhausted", "ambiguous_or_stale"}
    ),
    "shutdown_drain": frozenset({"drained", "timed_out"}),
    "descriptor_validation": frozenset({"admitted", "rejected"}),
    "revision_publication": frozenset({"published"}),
    "coalescing": frozenset({"unchanged"}),
    "revision_exact_check": frozenset({"found", "not_found"}),
    "revision_feed": frozenset({"page_returned", "cursor_expired"}),
}

_COUNT_OUTCOMES: dict[str, frozenset[str]] = {
    "card_validation": frozenset({"evaluated", "admitted", "rejected"}),
    "descriptor_pages": frozenset({"tools", "resources"}),
    "descriptor_items": frozenset({"tools", "resources"}),
    "descriptor_bytes": frozenset({"tools", "resources"}),
    "revision_feed_notices": frozenset({"returned"}),
}

_POOL_STATES = frozenset({"total", "active", "idle", "opening"})


class McpDiagnosticsRecorder(Protocol):
    """Small production seam shared by the registry and session pool."""

    def record_phase(
        self, *, phase: str, outcome: str, duration_seconds: float
    ) -> None: ...

    def record_count(self, *, measure: str, value: int, outcome: str) -> None: ...

    def record_pool_size(self, *, state: str, value: int) -> None: ...


class _NoopRecorder:
    def record_phase(
        self, *, phase: str, outcome: str, duration_seconds: float
    ) -> None:
        return

    def record_count(self, *, measure: str, value: int, outcome: str) -> None:
        return

    def record_pool_size(self, *, state: str, value: int) -> None:
        return


class _OtelRecorder:
    def __init__(
        self,
        *,
        phase_counter: Any,
        phase_duration: Any,
        count_counter: Any,
        pool_size: Any,
    ) -> None:
        self._phase_counter = phase_counter
        self._phase_duration = phase_duration
        self._count_counter = count_counter
        self._pool_size = pool_size

    def record_phase(
        self, *, phase: str, outcome: str, duration_seconds: float
    ) -> None:
        if outcome not in _PHASE_OUTCOMES.get(phase, frozenset()):
            return
        attributes = {"phase": phase, "outcome": outcome}
        self._phase_counter.add(1, attributes)
        self._phase_duration.record(max(0.0, duration_seconds), attributes)

    def record_count(self, *, measure: str, value: int, outcome: str) -> None:
        if value > 0 and outcome in _COUNT_OUTCOMES.get(measure, frozenset()):
            self._count_counter.add(value, {"measure": measure, "outcome": outcome})

    def record_pool_size(self, *, state: str, value: int) -> None:
        if state in _POOL_STATES:
            self._pool_size.record(max(0, value), {"state": state})


class McpDiagnostics:
    """Module-level recorder cache, mirroring the token-vault metrics seam."""

    _recorder: McpDiagnosticsRecorder | None = None

    @classmethod
    def recorder(cls) -> McpDiagnosticsRecorder:
        if cls._recorder is None:
            cls._recorder = cls._build()
        return cls._recorder

    @classmethod
    def reset_for_testing(cls) -> None:
        cls._recorder = None

    @classmethod
    def _build(cls) -> McpDiagnosticsRecorder:
        try:
            from opentelemetry import metrics

            meter = metrics.get_meter(_METER_NAME)
            return _OtelRecorder(
                phase_counter=meter.create_counter(
                    "mcp_phase_total",
                    description="Body-free MCP lifecycle phase outcomes.",
                ),
                phase_duration=meter.create_histogram(
                    "mcp_phase_duration_seconds",
                    unit="s",
                    description="Body-free MCP lifecycle phase duration.",
                ),
                count_counter=meter.create_counter(
                    "mcp_diagnostic_count_total",
                    description="Body-free MCP paging, admission, and feed counts.",
                ),
                pool_size=meter.create_histogram(
                    "mcp_session_pool_size",
                    description="MCP session-pool size snapshots by state.",
                ),
            )
        except Exception:  # noqa: BLE001 - telemetry must never break MCP
            return _NoopRecorder()


__all__ = ["McpDiagnostics", "McpDiagnosticsRecorder"]
