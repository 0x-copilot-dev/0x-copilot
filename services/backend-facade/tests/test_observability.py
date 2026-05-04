"""Tests for the backend-facade observability module."""

from __future__ import annotations

import json
import logging

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend_facade.observability import (
    JsonLogFormatter,
    LogEvent,
    RequestContext,
    RequestContextMiddleware,
    StructuredLogger,
    configure_logging,
    current_context,
    emit_access_log,
)


class TestMetadataRedaction:
    def test_drops_sensitive_keys(self) -> None:
        event = LogEvent(
            service="backend-facade",
            env="test",
            event="x",
            metadata={
                "session_token": "leak",
                "API_KEY": "k",
                "cookie": "c",
                "ok_field": "value",
            },
        )
        assert event.metadata == {"ok_field": "value"}

    def test_drops_non_scalars(self) -> None:
        event = LogEvent(
            service="backend-facade",
            env="test",
            event="x",
            metadata={"list": [1, 2], "obj": {"a": 1}, "str": "ok", "int": 1},
        )
        assert event.metadata == {"str": "ok", "int": 1}


class TestJsonLogFormatter:
    def test_renders_structured_payload(self) -> None:
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
            "service": "backend-facade",
            "env": "test",
            "level": "info",
            "event": "proxy.upstream.ok",
        }
        line = formatter.format(record)
        decoded = json.loads(line)
        assert decoded["service"] == "backend-facade"
        assert decoded["event"] == "proxy.upstream.ok"

    def test_traceback_strips_exception_message(self) -> None:
        formatter = JsonLogFormatter()
        try:
            raise RuntimeError("LEAK_ME_secret_hgs83")
        except RuntimeError:
            import sys

            exc_info = sys.exc_info()
            record = logging.LogRecord(
                name="t",
                level=logging.ERROR,
                pathname=__file__,
                lineno=1,
                msg="boom",
                args=(),
                exc_info=exc_info,
            )
        decoded = json.loads(formatter.format(record))
        assert decoded["error_class"] == "RuntimeError"
        joined = " ".join(decoded["traceback"])
        assert "LEAK_ME" not in joined


class TestStructuredLogger:
    def test_inherits_request_id_from_context(self, caplog) -> None:
        from backend_facade.observability.request_context import _REQUEST_CONTEXT

        logger = StructuredLogger(logging.getLogger("test_facade_struct"))
        ctx = RequestContext(
            request_id="req_facade",
            org_id="org_z",
            user_id="user_w",
            method="POST",
            route="/v1/agent/runs",
        )
        token = _REQUEST_CONTEXT.set(ctx)
        try:
            with caplog.at_level(logging.INFO):
                logger.info("proxy.upstream.ok", status_code=200, duration_ms=12)
        finally:
            _REQUEST_CONTEXT.reset(token)

        record = caplog.records[-1]
        payload = record.log_event  # type: ignore[attr-defined]
        assert payload["request_id"] == "req_facade"
        assert payload["org_id"] == "org_z"
        assert payload["status_code"] == 200


class TestRequestContextMiddleware:
    def _build_app(self) -> FastAPI:
        app = FastAPI()
        app.add_middleware(RequestContextMiddleware, access_log_emitter=emit_access_log)

        @app.get("/v1/agent/runs/{run_id}")
        def get_run(run_id: str) -> dict[str, object]:
            ctx = current_context()
            assert ctx is not None
            return {"request_id": ctx.request_id, "route": ctx.route}

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
            "/v1/agent/runs/xyz",
            headers={"x-request-id": "req_inbound"},
        )
        assert response.headers["x-request-id"] == "req_inbound"


class TestConfigureLogging:
    def test_idempotent(self) -> None:
        configure_logging()
        first = list(logging.getLogger().handlers)
        configure_logging()
        second = list(logging.getLogger().handlers)
        assert len(first) == len(second) == 1
