"""OpenTelemetry bootstrap for the backend facade."""

from __future__ import annotations

import os
import re
from typing import ClassVar

from opentelemetry import metrics, trace
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import (
    OTLPMetricExporter,
)
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
    OTLPSpanExporter,
)
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan, Span, SpanProcessor, TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor


_SERVICE_NAME = "backend-facade"

_DENY_ATTR_KEYS: frozenset[str] = frozenset(
    {
        "http.url",
        "http.target",
        "url.full",
        "url.query",
        "url.path",
        "db.statement",
        "db.statement.parameters",
        "db.user",
        "http.request.body",
        "http.response.body",
        "exception.message",
        "exception.stacktrace",
        "code.filepath",
        "code.namespace",
    }
)

_DENY_ATTR_PATTERN = re.compile(
    r"(body|payload|content|query|prompt|completion|messages|secret|token|password|authorization|credential|api[_-]?key|cookie|session)",
    re.I,
)


class SafeAttributeSpanProcessor(SpanProcessor):
    """Strip span attributes whose keys hit the deny rules before export."""

    def on_start(
        self,
        span: Span,
        parent_context: object | None = None,
    ) -> None:  # type: ignore[override]
        return None

    def on_end(self, span: ReadableSpan) -> None:  # type: ignore[override]
        attributes = getattr(span, "_attributes", None)
        if attributes is None:
            return
        keys_to_drop = [
            key for key in list(attributes.keys()) if not self._is_safe_key(key)
        ]
        for key in keys_to_drop:
            try:
                del attributes[key]
            except (KeyError, TypeError):
                continue

    def shutdown(self) -> None:  # type: ignore[override]
        return None

    def force_flush(self, timeout_millis: int = 30_000) -> bool:  # type: ignore[override]
        return True

    @staticmethod
    def _is_safe_key(key: str) -> bool:
        if key in _DENY_ATTR_KEYS:
            return False
        if _DENY_ATTR_PATTERN.search(key):
            return False
        return True


class TelemetryBootstrap:
    """One-call OTEL setup for facade processes."""

    _CONFIGURED: ClassVar[bool] = False

    @classmethod
    def configure(
        cls,
        *,
        service_name: str = _SERVICE_NAME,
        env: str | None = None,
        otlp_endpoint: str | None = None,
    ) -> None:
        if cls._CONFIGURED:
            return

        env_value = (
            env or os.environ.get("FACADE_ENVIRONMENT", "development").strip().lower()
        )
        endpoint = (
            otlp_endpoint or os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "").strip()
        )

        if env_value == "production" and not endpoint:
            raise RuntimeError(
                "OTEL_EXPORTER_OTLP_ENDPOINT must be set in production",
            )

        os.environ.setdefault("OTEL_INSTRUMENTATION_HTTP_CAPTURE_BODY", "false")

        resource = Resource.create(
            {
                "service.name": service_name,
                "deployment.environment": env_value,
                "service.version": os.environ.get("SERVICE_VERSION", "0.0.0"),
            }
        )

        tracer_provider = TracerProvider(resource=resource)
        tracer_provider.add_span_processor(SafeAttributeSpanProcessor())
        if endpoint:
            tracer_provider.add_span_processor(
                BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint, insecure=True))
            )
        trace.set_tracer_provider(tracer_provider)

        readers: list = []
        if endpoint:
            readers.append(
                PeriodicExportingMetricReader(
                    OTLPMetricExporter(endpoint=endpoint, insecure=True)
                )
            )
        meter_provider = MeterProvider(resource=resource, metric_readers=readers)
        metrics.set_meter_provider(meter_provider)

        cls._CONFIGURED = True

    @classmethod
    def instrument_fastapi(cls, app: object) -> None:
        FastAPIInstrumentor.instrument_app(  # type: ignore[arg-type]
            app,
            excluded_urls="/healthz,/readyz",
        )

    @classmethod
    def instrument_httpx_clients(cls) -> None:
        HTTPXClientInstrumentor().instrument()

    @classmethod
    def get_tracer(cls, name: str) -> trace.Tracer:
        return trace.get_tracer(name)

    @classmethod
    def get_meter(cls, name: str) -> metrics.Meter:
        return metrics.get_meter(name)

    @classmethod
    def reset_for_tests(cls) -> None:
        cls._CONFIGURED = False
