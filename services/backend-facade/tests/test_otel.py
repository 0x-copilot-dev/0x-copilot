"""Tests for the backend-facade OTEL bootstrap and SafeAttributeSpanProcessor."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider

from backend_facade.app import create_app
from backend_facade.observability import (
    SafeAttributeSpanProcessor,
    TelemetryBootstrap,
)


class TestSafeAttributeSpanProcessor:
    def _span(self, attrs: dict[str, object]):
        provider = TracerProvider()
        tracer = provider.get_tracer("test")
        span = tracer.start_span("test")
        for k, v in attrs.items():
            span.set_attribute(k, v)  # type: ignore[arg-type]
        span.end()
        return span

    def test_drops_dangerous_keys_and_keeps_safe(self) -> None:
        span = self._span(
            {
                "http.url": "https://x/v1?org_id=acme",
                "http.method": "GET",
                "http.status_code": 200,
                "exception.message": "leak",
                "exception.type": "ValueError",
                "request.body": "x",
                "messages": "hi",
                "safe.metric": 42,
            }
        )
        SafeAttributeSpanProcessor().on_end(span)  # type: ignore[arg-type]
        attrs = dict(span.attributes or {})
        assert "http.url" not in attrs
        assert "exception.message" not in attrs
        assert "request.body" not in attrs
        assert "messages" not in attrs
        assert attrs["http.method"] == "GET"
        assert attrs["http.status_code"] == 200
        assert attrs["exception.type"] == "ValueError"
        assert attrs["safe.metric"] == 42


class TestTelemetryBootstrap:
    def setup_method(self) -> None:
        TelemetryBootstrap.reset_for_tests()

    def teardown_method(self) -> None:
        TelemetryBootstrap.reset_for_tests()

    def test_dev_without_endpoint_initializes_no_op(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
        monkeypatch.setenv("FACADE_ENVIRONMENT", "development")
        TelemetryBootstrap.configure()

    def test_production_fails_closed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
        monkeypatch.setenv("FACADE_ENVIRONMENT", "production")
        with pytest.raises(RuntimeError, match="OTEL_EXPORTER_OTLP_ENDPOINT"):
            TelemetryBootstrap.configure()


class TestBrowserOtlpForwarder:
    def test_no_collector_configured_returns_no_content_without_auth(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("OTEL_COLLECTOR_HTTP_URL", raising=False)
        app = create_app()

        response = TestClient(app).post("/v1/telemetry/otlp/v1/traces", content=b"{}")

        assert response.status_code == 204


class TestLoggerTraceCorrelation:
    def test_logger_inherits_active_trace_id(self, caplog) -> None:
        from backend_facade.observability import get_logger
        import logging

        TelemetryBootstrap.reset_for_tests()
        TelemetryBootstrap.configure()
        tracer = trace.get_tracer("test")
        logger = get_logger("test_facade_otel")
        with tracer.start_as_current_span("op") as span:
            with caplog.at_level(logging.INFO):
                logger.info("step")
            expected = format(span.get_span_context().trace_id, "032x")

        record = caplog.records[-1]
        payload = record.log_event  # type: ignore[attr-defined]
        assert payload["trace_id"] == expected
