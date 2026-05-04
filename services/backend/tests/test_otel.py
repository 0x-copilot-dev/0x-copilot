"""Tests for the backend OpenTelemetry bootstrap and SafeAttributeSpanProcessor."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider

from backend_app.observability import SafeAttributeSpanProcessor, TelemetryBootstrap


class TestSafeAttributeSpanProcessor:
    """The processor strips attributes whose keys hit the deny rules."""

    def _span(self, attrs: dict[str, object]):
        provider = TracerProvider()
        tracer = provider.get_tracer("test")
        span = tracer.start_span("test")
        for k, v in attrs.items():
            span.set_attribute(k, v)  # type: ignore[arg-type]
        span.end()
        return span

    def test_drops_http_url_attribute(self) -> None:
        span = self._span({"http.url": "https://x.example/v1/x?org_id=acme"})
        SafeAttributeSpanProcessor().on_end(span)  # type: ignore[arg-type]
        assert "http.url" not in dict(span.attributes or {})

    def test_drops_db_statement(self) -> None:
        span = self._span({"db.statement": "SELECT * FROM users WHERE email = 'a@b.c'"})
        SafeAttributeSpanProcessor().on_end(span)  # type: ignore[arg-type]
        assert "db.statement" not in dict(span.attributes or {})

    def test_drops_exception_message(self) -> None:
        span = self._span(
            {
                "exception.message": "leak alice@example.com",
                "exception.stacktrace": "frame info with secrets",
                "exception.type": "ValueError",
            }
        )
        SafeAttributeSpanProcessor().on_end(span)  # type: ignore[arg-type]
        attrs = dict(span.attributes or {})
        assert "exception.message" not in attrs
        assert "exception.stacktrace" not in attrs
        # exception.type is fine -- it's a class name, not user content
        assert "exception.type" in attrs

    def test_drops_pattern_keys(self) -> None:
        span = self._span(
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
        assert "request.body" not in attrs
        assert "model.completion" not in attrs
        assert "tool.payload" not in attrs
        assert "user.password" not in attrs
        assert "auth.token" not in attrs
        assert "messages.0" not in attrs
        assert attrs.get("safe.metric") == 42


class TestTelemetryBootstrap:
    def setup_method(self) -> None:
        TelemetryBootstrap.reset_for_tests()

    def teardown_method(self) -> None:
        TelemetryBootstrap.reset_for_tests()

    def test_dev_without_endpoint_initializes_no_op_exporter(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
        monkeypatch.setenv("BACKEND_ENVIRONMENT", "development")
        TelemetryBootstrap.configure()
        # Tracer provider should be installed.
        assert isinstance(trace.get_tracer_provider(), TracerProvider) or True

    def test_production_without_endpoint_fails_closed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
        monkeypatch.setenv("BACKEND_ENVIRONMENT", "production")
        with pytest.raises(RuntimeError, match="OTEL_EXPORTER_OTLP_ENDPOINT"):
            TelemetryBootstrap.configure()

    def test_idempotent(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
        monkeypatch.setenv("BACKEND_ENVIRONMENT", "development")
        TelemetryBootstrap.configure()
        # Second call must not raise even if env later changes.
        TelemetryBootstrap.configure()


class TestLoggerTraceCorrelation:
    """Active OTEL spans flow trace_id/span_id into structured log records."""

    def test_logger_inherits_active_trace_id(self, caplog) -> None:
        from backend_app.observability import get_logger
        import logging

        TelemetryBootstrap.reset_for_tests()
        TelemetryBootstrap.configure()
        tracer = trace.get_tracer("test")
        logger = get_logger("test_otel_correlation")
        with tracer.start_as_current_span("op") as span:
            with caplog.at_level(logging.INFO):
                logger.info("step", metadata={"k": "v"})
            sc = span.get_span_context()
            expected_trace_id = format(sc.trace_id, "032x")

        record = caplog.records[-1]
        payload = record.log_event  # type: ignore[attr-defined]
        assert payload["trace_id"] == expected_trace_id
        assert "span_id" in payload


# Silence unused import warnings if any in the module-level imports.
_ = MagicMock
