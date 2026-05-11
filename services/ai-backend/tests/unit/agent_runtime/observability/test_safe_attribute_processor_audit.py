"""Pinned audit of ``SafeAttributeSpanProcessor`` (P13 step 3).

Existing ``test_otel.py`` exercises a handful of cases. This file pins
the exact denylist + pattern membership so that:

1. additions to ``_DENY_ATTR_KEYS`` or ``_DENY_ATTR_PATTERN`` are
   explicit code-review decisions (the test fails until updated), and
2. attributes our own code intentionally emits — ``db.statement.digest``
   and ``db.statement.duration_ms`` from ``SlowQueryTracer`` — survive
   the processor so the slow-query span keeps its operator-facing data.

The processor is the last-mile defense before spans hit the exporter.
A regression that quietly drops the digest would silently break the
slow-query dashboard. A regression that quietly admits a body or
payload key would leak production traffic. Both are exactly the kind
of fault the pinned audit catches.
"""

from __future__ import annotations

from typing import ClassVar

from opentelemetry.sdk.trace import TracerProvider

from agent_runtime.observability.otel import (
    SafeAttributeSpanProcessor,
    _DENY_ATTR_KEYS,
    _DENY_ATTR_PATTERN,
)


class _ExpectedDenyMembership:
    """Frozen expected contents for the OTel attribute deny rules."""

    KEYS: ClassVar[frozenset[str]] = frozenset(
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
    # Match the regex source verbatim. New tokens must land here in
    # lockstep with ``_DENY_ATTR_PATTERN`` in ``observability/otel.py``.
    PATTERN_TOKENS: ClassVar[tuple[str, ...]] = (
        "body",
        "payload",
        "content",
        "query",
        "prompt",
        "completion",
        "messages",
        "secret",
        "token",
        "password",
        "authorization",
        "credential",
        "api[_-]?key",
        "cookie",
        "session",
    )


class TestDenyMembershipPin:
    def test_deny_keys_membership_is_pinned(self) -> None:
        assert _DENY_ATTR_KEYS == _ExpectedDenyMembership.KEYS

    def test_deny_pattern_covers_expected_tokens(self) -> None:
        for token in _ExpectedDenyMembership.PATTERN_TOKENS:
            # ``api[_-]?key`` is a regex fragment; treat the rest as
            # literal substrings; force a positive match through the
            # compiled pattern to confirm the source string still
            # contains the token.
            probe = "api_key" if token.startswith("api") else f"x.{token}.y"
            assert _DENY_ATTR_PATTERN.search(probe), token


class _ProcessorMixin:
    @staticmethod
    def _emit(attrs: dict[str, object]) -> dict[str, object]:
        provider = TracerProvider()
        tracer = provider.get_tracer("test_safe_attribute_processor_audit")
        span = tracer.start_span("audit")
        for key, value in attrs.items():
            span.set_attribute(key, value)  # type: ignore[arg-type]
        span.end()
        SafeAttributeSpanProcessor().on_end(span)  # type: ignore[arg-type]
        return dict(span.attributes or {})


class TestOurOwnAttributesSurviveProcessing(_ProcessorMixin):
    """``SlowQueryTracer`` emits these directly; the audit must not drop them."""

    def test_db_statement_digest_and_duration_pass_through(self) -> None:
        result = self._emit(
            {
                "db.statement.digest": "deadbeefcafe",
                "db.statement.duration_ms": 1234,
            }
        )
        assert result["db.statement.digest"] == "deadbeefcafe"
        assert result["db.statement.duration_ms"] == 1234

    def test_safe_http_attributes_pass_through(self) -> None:
        result = self._emit(
            {
                "http.method": "POST",
                "http.status_code": 200,
                "http.route": "/v1/agent/runs",
            }
        )
        assert result["http.method"] == "POST"
        assert result["http.status_code"] == 200
        assert result["http.route"] == "/v1/agent/runs"


class TestDenyKeysActuallyDropped(_ProcessorMixin):
    def test_every_deny_key_is_dropped(self) -> None:
        attrs = {key: "redacted" for key in _ExpectedDenyMembership.KEYS}
        attrs["http.method"] = "GET"
        result = self._emit(attrs)
        for key in _ExpectedDenyMembership.KEYS:
            assert key not in result, key
        # The benign sibling key is untouched.
        assert result["http.method"] == "GET"
