"""Tests for cross-process trace propagation across the runtime queue."""

from __future__ import annotations

import pytest
from opentelemetry import trace

from agent_runtime.observability.otel import TelemetryBootstrap
from agent_runtime.observability.queue_propagation import QueueTracePropagator


class TracerProviderMixin:
    """Bootstrap a process-wide TracerProvider once per test class."""

    def setup_method(self) -> None:
        TelemetryBootstrap.reset_for_tests()
        TelemetryBootstrap.configure()

    def teardown_method(self) -> None:
        TelemetryBootstrap.reset_for_tests()


class TestQueueTracePropagatorEnabled(TracerProviderMixin):
    def test_inject_with_active_span_returns_traceparent(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("RUNTIME_PROPAGATE_QUEUE_TRACE", "true")
        tracer = trace.get_tracer("test_queue_propagation")
        with tracer.start_as_current_span("producer"):
            carrier = QueueTracePropagator.inject()
        assert "traceparent" in carrier
        # The traceparent value follows ``00-<trace-id>-<span-id>-<flags>``.
        assert carrier["traceparent"].count("-") == 3

    def test_inject_without_active_span_returns_empty(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("RUNTIME_PROPAGATE_QUEUE_TRACE", "true")
        # No active span — propagation has nothing to inject.
        carrier = QueueTracePropagator.inject()
        assert carrier == {}

    def test_extract_round_trip_recovers_trace_id(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("RUNTIME_PROPAGATE_QUEUE_TRACE", "true")
        tracer = trace.get_tracer("test_queue_propagation")
        with tracer.start_as_current_span("producer") as producer_span:
            carrier = QueueTracePropagator.inject()
            expected_trace_id = producer_span.get_span_context().trace_id

        ctx = QueueTracePropagator.extract(carrier)
        with tracer.start_as_current_span("consumer", context=ctx) as consumer_span:
            assert consumer_span.get_span_context().trace_id == expected_trace_id

    def test_extract_malformed_carrier_returns_default_context(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("RUNTIME_PROPAGATE_QUEUE_TRACE", "true")
        ctx = QueueTracePropagator.extract({"traceparent": "not-a-valid-traceparent"})
        # Default context yields a fresh trace — never raises.
        tracer = trace.get_tracer("test_queue_propagation")
        with tracer.start_as_current_span("consumer", context=ctx) as span:
            assert span.get_span_context().is_valid

    def test_extract_none_carrier_returns_default_context(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("RUNTIME_PROPAGATE_QUEUE_TRACE", "true")
        ctx = QueueTracePropagator.extract(None)
        tracer = trace.get_tracer("test_queue_propagation")
        with tracer.start_as_current_span("consumer", context=ctx) as span:
            assert span.get_span_context().is_valid

    def test_extract_drops_non_string_carrier_entries(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("RUNTIME_PROPAGATE_QUEUE_TRACE", "true")
        # JSON round-trips can produce non-string values for "string"
        # fields when a misconfigured upstream writes them. The cleaner
        # must drop them rather than raise inside the propagator.
        ctx = QueueTracePropagator.extract({"traceparent": 42, "tracestate": None})
        tracer = trace.get_tracer("test_queue_propagation")
        with tracer.start_as_current_span("consumer", context=ctx) as span:
            assert span.get_span_context().is_valid


class TestQueueTracePropagatorDisabled(TracerProviderMixin):
    def test_inject_returns_empty_when_disabled(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("RUNTIME_PROPAGATE_QUEUE_TRACE", "false")
        tracer = trace.get_tracer("test_queue_propagation")
        with tracer.start_as_current_span("producer"):
            carrier = QueueTracePropagator.inject()
        assert carrier == {}

    def test_extract_returns_default_context_when_disabled(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("RUNTIME_PROPAGATE_QUEUE_TRACE", "false")
        # Even with a real-looking carrier, propagation is off.
        ctx = QueueTracePropagator.extract(
            {"traceparent": "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01"}
        )
        # The returned context is the default Context; the child trace
        # is a fresh one rather than the one encoded in the carrier.
        tracer = trace.get_tracer("test_queue_propagation")
        with tracer.start_as_current_span("consumer", context=ctx) as span:
            sc = span.get_span_context()
            assert sc.is_valid
            assert format(sc.trace_id, "032x") != "0af7651916cd43dd8448eb211c80319c"


class TestQueueTracePropagatorEnablementFlag:
    def test_default_is_enabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("RUNTIME_PROPAGATE_QUEUE_TRACE", raising=False)
        assert QueueTracePropagator.enabled() is True

    @pytest.mark.parametrize("falsey", ["false", "0", "no", "off", "", "FALSE", "Off"])
    def test_explicit_disables(
        self, monkeypatch: pytest.MonkeyPatch, falsey: str
    ) -> None:
        monkeypatch.setenv("RUNTIME_PROPAGATE_QUEUE_TRACE", falsey)
        assert QueueTracePropagator.enabled() is False

    @pytest.mark.parametrize("truthy", ["true", "1", "yes", "on", "TRUE"])
    def test_explicit_enables(
        self, monkeypatch: pytest.MonkeyPatch, truthy: str
    ) -> None:
        monkeypatch.setenv("RUNTIME_PROPAGATE_QUEUE_TRACE", truthy)
        assert QueueTracePropagator.enabled() is True
