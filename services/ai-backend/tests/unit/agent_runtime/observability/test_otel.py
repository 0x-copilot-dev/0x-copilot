"""Tests for the ai-backend OTEL bootstrap and SafeAttributeSpanProcessor."""

from __future__ import annotations

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider

from agent_runtime.observability.otel import (
    SafeAttributeSpanProcessor,
    TelemetryBootstrap,
)


class SafeAttributeMixin:
    @staticmethod
    def make_span(attrs: dict[str, object]):
        provider = TracerProvider()
        tracer = provider.get_tracer("test")
        span = tracer.start_span("test")
        for k, v in attrs.items():
            span.set_attribute(k, v)  # type: ignore[arg-type]
        span.end()
        return span


class TestSafeAttributeSpanProcessor(SafeAttributeMixin):
    def test_drops_url_db_exception_message(self) -> None:
        span = self.make_span(
            {
                "http.url": "https://x/v1?org_id=acme",
                "db.statement": "SELECT * FROM users",
                "exception.message": "leak alice@example.com",
                "exception.type": "ValueError",
                "http.method": "POST",
                "http.status_code": 500,
            }
        )
        SafeAttributeSpanProcessor().on_end(span)  # type: ignore[arg-type]
        attrs = dict(span.attributes or {})
        assert "http.url" not in attrs
        assert "db.statement" not in attrs
        assert "exception.message" not in attrs
        assert attrs["exception.type"] == "ValueError"
        assert attrs["http.method"] == "POST"
        assert attrs["http.status_code"] == 500

    def test_drops_pattern_keys(self) -> None:
        span = self.make_span(
            {
                "request.body": "secret",
                "model.completion": "PII",
                "tool.payload": "...",
                "user.password": "p",
                "auth.token": "t",
                "messages.0": "hi",
                "safe.metric": 42,
            }
        )
        SafeAttributeSpanProcessor().on_end(span)  # type: ignore[arg-type]
        attrs = dict(span.attributes or {})
        for banned in (
            "request.body",
            "model.completion",
            "tool.payload",
            "user.password",
            "auth.token",
            "messages.0",
        ):
            assert banned not in attrs
        assert attrs["safe.metric"] == 42


class TestTelemetryBootstrap:
    def setup_method(self) -> None:
        TelemetryBootstrap.reset_for_tests()

    def teardown_method(self) -> None:
        TelemetryBootstrap.reset_for_tests()

    def test_dev_without_endpoint_no_ops(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
        monkeypatch.setenv("RUNTIME_ENVIRONMENT", "development")
        TelemetryBootstrap.configure()

    def test_production_fails_closed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
        monkeypatch.setenv("RUNTIME_ENVIRONMENT", "production")
        with pytest.raises(RuntimeError, match="OTEL_EXPORTER_OTLP_ENDPOINT"):
            TelemetryBootstrap.configure()


class TestLoggerTraceCorrelation:
    def test_logger_inherits_active_trace_id(self, caplog) -> None:
        from agent_runtime.observability.http_logging import LoggingConfigurator
        import logging

        TelemetryBootstrap.reset_for_tests()
        TelemetryBootstrap.configure()
        tracer = trace.get_tracer("test")
        logger = LoggingConfigurator.get_logger("test_aibackend_otel")
        with tracer.start_as_current_span("op") as span:
            with caplog.at_level(logging.INFO):
                logger.info("step")
            expected = format(span.get_span_context().trace_id, "032x")

        record = caplog.records[-1]
        payload = record.log_event  # type: ignore[attr-defined]
        assert payload["trace_id"] == expected
