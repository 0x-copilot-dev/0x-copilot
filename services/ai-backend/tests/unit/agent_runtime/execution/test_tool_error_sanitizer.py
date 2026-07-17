"""Tests for :class:`ErrorSanitizer` and :class:`ErrorHintExtractor`."""

from __future__ import annotations

import httpx
import pytest
from pydantic import BaseModel, ValidationError

from agent_runtime.execution.tool_error_sanitizer import (
    ErrorHintExtractor,
    ErrorSanitizer,
)


class _Args(BaseModel):
    limit: int


class TestErrorSanitizerStripsInternals:
    def test_strips_unix_file_paths(self) -> None:
        msg = ErrorSanitizer.sanitize(
            RuntimeError("boom at /Users/parthpahwa/Documents/work/0x-copilot/file.py")
        )
        assert "/Users/" not in msg
        assert "[redacted]" in msg

    def test_strips_long_hex_ids(self) -> None:
        msg = ErrorSanitizer.sanitize(
            RuntimeError("run_id=8475dbace42f4e34a2d2fb1555a542e0 failed")
        )
        assert "8475dbace42f4e34a2d2fb1555a542e0" not in msg
        assert "[redacted]" in msg

    def test_strips_uuid_form_ids(self) -> None:
        msg = ErrorSanitizer.sanitize(
            RuntimeError("conv 11e2bece-3fc4-4cda-8e57-820bc4c7793d not found")
        )
        assert "11e2bece" not in msg
        assert "[redacted]" in msg

    def test_strips_postgres_connection_string(self) -> None:
        msg = ErrorSanitizer.sanitize(
            RuntimeError("cannot connect to postgresql://user:pass@host:5432/db")
        )
        assert "postgresql://" not in msg
        assert "pass" not in msg

    def test_strips_bearer_token(self) -> None:
        msg = ErrorSanitizer.sanitize(RuntimeError("auth header Bearer abc123xyz"))
        assert "abc123xyz" not in msg

    def test_strips_openai_style_key(self) -> None:
        msg = ErrorSanitizer.sanitize(
            RuntimeError("invalid key sk-abcdef0123456789abcdef0123456789")
        )
        assert "sk-abcdef" not in msg

    def test_strips_password_kvp(self) -> None:
        msg = ErrorSanitizer.sanitize(
            RuntimeError("conn fail: password=hunter2 user=admin")
        )
        assert "hunter2" not in msg

    def test_preserves_short_actionable_text(self) -> None:
        msg = ErrorSanitizer.sanitize(ValueError("limit must be between 1 and 100"))
        assert "limit must be between 1 and 100" in msg

    def test_caps_runaway_message_with_truncation_marker(self) -> None:
        msg = ErrorSanitizer.sanitize(RuntimeError("x" * 5000))
        assert len(msg) <= 2048
        assert msg.endswith("…[truncated]")

    def test_strips_traceback_frame_lines(self) -> None:
        text = (
            "Traceback (most recent call last):\n"
            '  File "/Users/dev/code/foo.py", line 10, in inner\n'
            "    raise ValueError('boom')\n"
            "ValueError: boom\n"
        )
        msg = ErrorSanitizer.sanitize(RuntimeError(text))
        assert "Traceback" not in msg
        assert "/Users/dev/" not in msg
        assert "line 10" not in msg


class TestErrorHintExtractor:
    def test_pydantic_validation_error_yields_field_hints(self) -> None:
        with pytest.raises(ValidationError) as caught:
            _Args.model_validate({"limit": "not-an-int"})
        hints = ErrorHintExtractor.extract(caught.value)
        assert hints["category"] == "validation_error"
        assert "limit" in hints["invalid_args"]
        assert hints["details"][0]["field"] == "limit"

    def test_httpx_http_status_error_yields_status_and_retry(self) -> None:
        request = httpx.Request("GET", "https://example.com/api")
        response = httpx.Response(
            status_code=429,
            headers={"Retry-After": "30"},
            request=request,
        )
        exc = httpx.HTTPStatusError(
            "429 Too Many Requests", request=request, response=response
        )
        hints = ErrorHintExtractor.extract(exc)
        assert hints["category"] == "http_status"
        assert hints["status_code"] == 429
        assert hints["retry_after_seconds"] == 30
        assert hints["transient"] is True

    def test_httpx_500_marked_transient_no_retry_after(self) -> None:
        request = httpx.Request("GET", "https://example.com/api")
        response = httpx.Response(status_code=503, request=request)
        exc = httpx.HTTPStatusError("503", request=request, response=response)
        hints = ErrorHintExtractor.extract(exc)
        assert hints["status_code"] == 503
        assert hints["transient"] is True
        assert hints["retry_after_seconds"] is None

    def test_httpx_400_not_transient(self) -> None:
        request = httpx.Request("GET", "https://example.com/api")
        response = httpx.Response(status_code=400, request=request)
        exc = httpx.HTTPStatusError("400", request=request, response=response)
        hints = ErrorHintExtractor.extract(exc)
        assert hints["transient"] is False

    def test_httpx_connect_error_yields_transport_transient(self) -> None:
        exc = httpx.ConnectError("connection refused")
        hints = ErrorHintExtractor.extract(exc)
        assert hints["category"] == "transport"
        assert hints["transient"] is True

    def test_unknown_exception_returns_empty_hints(self) -> None:
        assert ErrorHintExtractor.extract(ValueError("bare")) == {}

    def test_ddgs_like_exception_detects_all_engines_failed(self) -> None:
        class DDGSException(Exception):
            __module__ = "ddgs.ddgs"

        hints = ErrorHintExtractor.extract(
            DDGSException("All engines failed for query 'foo'")
        )
        assert hints["category"] == "search_provider"
        assert hints["all_engines_failed"] is True
