"""Tests for the backend observability module.

The structural rule is "no LLM I/O or PII in logs". These tests pin the
behaviors that enforce it: metadata redaction, JSON formatting, and the
request-context middleware that binds correlation IDs.
"""

from __future__ import annotations

import json
import logging

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend_app.observability import (
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
    def test_drops_sensitive_keys_case_insensitive(self) -> None:
        event = LogEvent(
            service="backend",
            env="test",
            event="x",
            metadata={
                "API_KEY": "sk-leak",
                "Authorization": "Bearer tok",
                "Password": "p",
                "Session_Token": "s",
                "safe_field": "ok",
            },
        )
        assert event.metadata == {"safe_field": "ok"}

    def test_drops_non_scalar_values(self) -> None:
        event = LogEvent(
            service="backend",
            env="test",
            event="x",
            metadata={
                "list_field": [1, 2, 3],
                "dict_field": {"nested": "value"},
                "object_field": object(),
                "ok_int": 42,
                "ok_str": "value",
                "ok_bool": True,
                "ok_none": None,
            },
        )
        assert event.metadata == {
            "ok_int": 42,
            "ok_str": "value",
            "ok_bool": True,
            "ok_none": None,
        }

    def test_to_log_dict_omits_none_fields(self) -> None:
        event = LogEvent(service="backend", env="test", event="x")
        dump = event.to_log_dict()
        assert "request_id" not in dump
        assert "trace_id" not in dump
        assert dump["service"] == "backend"


class TestJsonLogFormatter:
    def test_emits_structured_payload_when_present(self) -> None:
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
            "service": "backend",
            "env": "test",
            "level": "info",
            "event": "audit.recorded",
            "request_id": "req_abc",
            "metadata": {"action": "skill_created"},
        }
        line = formatter.format(record)
        decoded = json.loads(line)
        assert decoded["event"] == "audit.recorded"
        assert decoded["request_id"] == "req_abc"
        assert decoded["metadata"] == {"action": "skill_created"}

    def test_wraps_unstructured_records_into_safe_shape(self) -> None:
        formatter = JsonLogFormatter()
        record = logging.LogRecord(
            name="uvicorn",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg="started server",
            args=(),
            exc_info=None,
        )
        decoded = json.loads(formatter.format(record))
        assert decoded["event"] == "uvicorn"
        assert decoded["safe_message"] == "started server"
        assert decoded["level"] == "info"

    def test_traceback_does_not_include_exception_message_text(self) -> None:
        formatter = JsonLogFormatter()
        try:
            raise ValueError("PII_LEAK_alice@example.com_secret")
        except ValueError:
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
        assert decoded["error_class"] == "ValueError"
        assert isinstance(decoded["traceback"], list)
        assert decoded["traceback"], "expected at least one frame"
        # The exception message must not appear in any traceback frame string.
        joined = " ".join(decoded["traceback"])
        assert "PII_LEAK_alice" not in joined
        assert "secret" not in joined


class TestStructuredLogger:
    def test_inherits_request_id_from_context(self, caplog) -> None:
        logger = StructuredLogger(logging.getLogger("test_struct_a"))
        ctx = RequestContext(
            request_id="req_test",
            org_id="org_x",
            user_id="user_y",
            method="GET",
            route="/v1/test",
        )
        from backend_app.observability.request_context import _REQUEST_CONTEXT

        token = _REQUEST_CONTEXT.set(ctx)
        try:
            with caplog.at_level(logging.INFO):
                logger.info("audit.recorded", metadata={"action": "test"})
        finally:
            _REQUEST_CONTEXT.reset(token)

        record = caplog.records[-1]
        payload = record.log_event  # type: ignore[attr-defined]
        assert payload["request_id"] == "req_test"
        assert payload["org_id"] == "org_x"
        assert payload["user_id"] == "user_y"
        assert payload["metadata"] == {"action": "test"}

    def test_drops_secret_from_metadata(self, caplog) -> None:
        logger = StructuredLogger(logging.getLogger("test_struct_b"))
        with caplog.at_level(logging.INFO):
            logger.info(
                "oauth.exchange",
                metadata={
                    "outcome": "success",
                    "access_token": "ya29.leak",
                    "client_secret": "shh",
                },
            )
        record = caplog.records[-1]
        payload = record.log_event  # type: ignore[attr-defined]
        assert payload["metadata"] == {"outcome": "success"}


class TestRequestContextMiddleware:
    def _build_app(self, captured: list) -> FastAPI:
        def emitter(ctx, status, duration_ms, error_class):  # type: ignore[no-untyped-def]
            captured.append((ctx, status, duration_ms, error_class))

        app = FastAPI()
        app.add_middleware(RequestContextMiddleware, access_log_emitter=emitter)

        @app.get("/echo")
        def echo() -> dict[str, object]:
            ctx = current_context()
            assert ctx is not None
            return {
                "request_id": ctx.request_id,
                "org_id": ctx.org_id,
                "user_id": ctx.user_id,
            }

        return app

    def test_generates_request_id_when_absent(self) -> None:
        captured: list = []
        client = TestClient(self._build_app(captured))
        response = client.get("/echo")
        assert response.status_code == 200
        body = response.json()
        assert body["request_id"].startswith("req_")
        assert response.headers["x-request-id"] == body["request_id"]

    def test_reuses_inbound_request_id(self) -> None:
        captured: list = []
        client = TestClient(self._build_app(captured))
        response = client.get("/echo", headers={"x-request-id": "req_supplied"})
        assert response.headers["x-request-id"] == "req_supplied"
        assert response.json()["request_id"] == "req_supplied"

    def test_propagates_identity_headers_to_context(self) -> None:
        captured: list = []
        client = TestClient(self._build_app(captured))
        response = client.get(
            "/echo",
            headers={
                "x-enterprise-org-id": "org_alpha",
                "x-enterprise-user-id": "user_beta",
            },
        )
        body = response.json()
        assert body["org_id"] == "org_alpha"
        assert body["user_id"] == "user_beta"

    def test_resets_context_after_request(self) -> None:
        captured: list = []
        client = TestClient(self._build_app(captured))
        client.get("/echo")
        # After the request returns, no context should remain in this thread.
        assert current_context() is None

    def test_emits_access_log_with_route_template(self) -> None:
        captured: list = []
        app = FastAPI()

        def emitter(ctx, status, duration_ms, error_class):  # type: ignore[no-untyped-def]
            captured.append((ctx, status, duration_ms, error_class))

        app.add_middleware(RequestContextMiddleware, access_log_emitter=emitter)

        @app.get("/v1/items/{item_id}")
        def get_item(item_id: str) -> dict[str, str]:
            return {"item_id": item_id}

        client = TestClient(app)
        response = client.get("/v1/items/abc-123")
        assert response.status_code == 200

        assert len(captured) == 1
        ctx, status, duration_ms, error_class = captured[0]
        assert status == 200
        assert duration_ms >= 0
        assert error_class is None
        assert ctx.route == "/v1/items/{item_id}"
        # The literal item_id must NOT appear in the captured route.
        assert "abc-123" not in (ctx.route or "")


class TestEmitAccessLog:
    def test_status_drives_log_level(self, caplog) -> None:
        ctx = RequestContext(
            request_id="req_x",
            org_id=None,
            user_id=None,
            method="GET",
            route="/v1/x",
        )
        with caplog.at_level(logging.INFO):
            emit_access_log(ctx, status=200, duration_ms=12, error_class=None)
            emit_access_log(ctx, status=404, duration_ms=12, error_class=None)
            emit_access_log(ctx, status=500, duration_ms=12, error_class=None)
            emit_access_log(ctx, status=200, duration_ms=12, error_class="RuntimeError")
        levels = [r.levelname for r in caplog.records]
        assert levels == ["INFO", "WARNING", "ERROR", "ERROR"]


class TestConfigureLogging:
    def test_idempotent(self) -> None:
        configure_logging()
        first_handlers = list(logging.getLogger().handlers)
        configure_logging()
        second_handlers = list(logging.getLogger().handlers)
        # The handler list should be the same length (cleared and re-added once).
        assert len(first_handlers) == len(second_handlers) == 1
