"""Tests for HTTP-scope structured logging and the request-context middleware."""

from __future__ import annotations

import json
import logging

from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent_runtime.observability.http_logging import (
    HttpAccessLogEmitter,
    HttpLogEvent,
    HttpRequestContext,
    HttpRequestContextHolder,
    HttpStructuredLogger,
    JsonLogFormatter,
    LoggingConfigurator,
    RequestContextMiddleware,
)


class HttpLogEventMixin:
    @staticmethod
    def make_event(**overrides: object) -> HttpLogEvent:
        defaults: dict[str, object] = {
            "service": "ai-backend",
            "env": "test",
            "event": "http_request",
        }
        defaults.update(overrides)
        return HttpLogEvent(**defaults)  # type: ignore[arg-type]


class TestHttpLogEvent(HttpLogEventMixin):
    def test_drops_sensitive_metadata_keys(self) -> None:
        event = self.make_event(
            metadata={
                "API_KEY": "leak",
                "Authorization": "Bearer x",
                "session_token": "s",
                "password": "p",
                "ok_field": "value",
            }
        )
        assert event.metadata == {"ok_field": "value"}

    def test_drops_non_scalar_values(self) -> None:
        event = self.make_event(
            metadata={
                "list_field": [1, 2],
                "dict_field": {"a": 1},
                "str_field": "ok",
                "int_field": 42,
                "none_field": None,
            }
        )
        assert event.metadata == {
            "str_field": "ok",
            "int_field": 42,
            "none_field": None,
        }

    def test_to_log_dict_omits_none_fields(self) -> None:
        event = self.make_event()
        dump = event.to_log_dict()
        assert "request_id" not in dump
        assert "trace_id" not in dump
        assert dump["service"] == "ai-backend"


class TestJsonLogFormatter:
    def test_recognizes_log_event_extra(self) -> None:
        formatter = JsonLogFormatter()
        record = logging.LogRecord(
            name="t",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg="ignored",
            args=(),
            exc_info=None,
        )
        record.log_event = {  # type: ignore[attr-defined]
            "service": "ai-backend",
            "env": "test",
            "level": "info",
            "event": "http_request",
        }
        decoded = json.loads(formatter.format(record))
        assert decoded["event"] == "http_request"

    def test_recognizes_runtime_extra_for_legacy_logger(self) -> None:
        formatter = JsonLogFormatter()
        record = logging.LogRecord(
            name="t",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg="ignored",
            args=(),
            exc_info=None,
        )
        record.runtime = {  # type: ignore[attr-defined]
            "event": "runtime.invoke",
            "level": "info",
            "request_id": "request_123",
            "run_id": "run_123",
            "trace_id": "trace_123",
            "subsystem": "runtime",
            "operation": "runtime.invoke",
            "status": "ok",
        }
        decoded = json.loads(formatter.format(record))
        assert decoded["event"] == "runtime.invoke"
        assert decoded["run_id"] == "run_123"

    def test_traceback_strips_exception_message(self) -> None:
        formatter = JsonLogFormatter()
        try:
            raise ValueError("AI_LEAK_alice@example.com_token_xyz")
        except ValueError:
            import sys

            exc_info = sys.exc_info()
            record = logging.LogRecord(
                name="t",
                level=logging.ERROR,
                pathname=__file__,
                lineno=1,
                msg="failed",
                args=(),
                exc_info=exc_info,
            )
        decoded = json.loads(formatter.format(record))
        assert decoded["error_class"] == "ValueError"
        joined = " ".join(decoded["traceback"])
        assert "AI_LEAK_alice" not in joined
        assert "token_xyz" not in joined


class TestHttpStructuredLogger:
    def test_inherits_request_id_from_context(self, caplog) -> None:
        logger = HttpStructuredLogger(logging.getLogger("test_ai_struct"))
        ctx = HttpRequestContext(
            request_id="req_ai",
            org_id="org_x",
            user_id="user_y",
            method="POST",
            route="/v1/agent/runs",
        )
        token = HttpRequestContextHolder.set(ctx)
        try:
            with caplog.at_level(logging.INFO):
                logger.info("agent.run.started", metadata={"run_id_count": 1})
        finally:
            HttpRequestContextHolder.reset(token)

        record = caplog.records[-1]
        payload = record.log_event  # type: ignore[attr-defined]
        assert payload["request_id"] == "req_ai"
        assert payload["org_id"] == "org_x"
        assert payload["user_id"] == "user_y"
        assert payload["metadata"] == {"run_id_count": 1}

    def test_drops_secret_in_metadata(self, caplog) -> None:
        logger = HttpStructuredLogger(logging.getLogger("test_ai_struct_b"))
        with caplog.at_level(logging.INFO):
            logger.info(
                "mcp.token.refreshed",
                metadata={
                    "outcome": "success",
                    "access_token": "leak",
                    "duration_ms": 12,
                },
            )
        record = caplog.records[-1]
        payload = record.log_event  # type: ignore[attr-defined]
        assert payload["metadata"] == {"outcome": "success", "duration_ms": 12}


class TestRequestContextMiddleware:
    def _build_app(self) -> FastAPI:
        app = FastAPI()
        app.add_middleware(RequestContextMiddleware)

        @app.get("/v1/agent/runs/{run_id}")
        def get_run(run_id: str) -> dict[str, object]:
            ctx = HttpRequestContextHolder.get()
            assert ctx is not None
            return {
                "request_id": ctx.request_id,
                "org_id": ctx.org_id,
                "user_id": ctx.user_id,
            }

        return app

    def test_generates_request_id_when_absent(self) -> None:
        client = TestClient(self._build_app())
        response = client.get("/v1/agent/runs/abc")
        assert response.status_code == 200
        rid = response.headers.get("x-request-id")
        assert rid is not None
        assert rid.startswith("req_")
        assert response.json()["request_id"] == rid

    def test_reuses_inbound_request_id(self) -> None:
        client = TestClient(self._build_app())
        response = client.get(
            "/v1/agent/runs/abc",
            headers={"x-request-id": "req_external"},
        )
        assert response.headers["x-request-id"] == "req_external"

    def test_propagates_identity_headers(self) -> None:
        client = TestClient(self._build_app())
        response = client.get(
            "/v1/agent/runs/abc",
            headers={
                "x-enterprise-org-id": "org_alpha",
                "x-enterprise-user-id": "user_beta",
            },
        )
        body = response.json()
        assert body["org_id"] == "org_alpha"
        assert body["user_id"] == "user_beta"

    def test_resets_context_after_request(self) -> None:
        client = TestClient(self._build_app())
        client.get("/v1/agent/runs/abc")
        assert HttpRequestContextHolder.get() is None


class TestHttpAccessLogEmitter:
    def test_status_drives_level(self, caplog) -> None:
        ctx = HttpRequestContext(
            request_id="req_x",
            org_id=None,
            user_id=None,
            method="GET",
            route="/v1/x",
        )
        with caplog.at_level(logging.INFO):
            HttpAccessLogEmitter.emit(ctx, status=200, duration_ms=1, error_class=None)
            HttpAccessLogEmitter.emit(ctx, status=404, duration_ms=1, error_class=None)
            HttpAccessLogEmitter.emit(ctx, status=500, duration_ms=1, error_class=None)
            HttpAccessLogEmitter.emit(
                ctx, status=200, duration_ms=1, error_class="RuntimeError"
            )
        levels = [r.levelname for r in caplog.records]
        assert levels == ["INFO", "WARNING", "ERROR", "ERROR"]


class TestLoggingConfigurator:
    def test_configure_is_idempotent(self) -> None:
        LoggingConfigurator.configure()
        first = list(logging.getLogger().handlers)
        LoggingConfigurator.configure()
        second = list(logging.getLogger().handlers)
        assert len(first) == len(second) == 1
