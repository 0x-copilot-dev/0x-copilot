"""Tests for the single-source ``MetadataRedactor`` (P13 step 2).

Before P13 each log model carried its own ``_MetadataRedactor`` with
slightly divergent value-type tuples. Consolidating to one shared
helper means new sensitive-key rules land in one place. These tests
pin the behavior shared by ``RuntimeLogEvent`` and ``HttpLogEvent`` so
no future change can quietly diverge the two surfaces again.
"""

from __future__ import annotations

import logging
from typing import ClassVar

import pytest

from agent_runtime.observability.http_logging import HttpLogEvent, HttpLogLevel
from agent_runtime.observability.logging import RuntimeLogEvent
from agent_runtime.observability.redactor import DENY_KEYS, MetadataRedactor


class _SampleMetadataMixin:
    """Representative metadata blobs the two log models must treat identically."""

    RUNTIME_EVENT_KWARGS: ClassVar[dict[str, str]] = {
        "event": "runtime.invoke.started",
        "request_id": "req_1",
        "run_id": "run_1",
        "trace_id": "trace_1",
        "subsystem": "runtime",
        "operation": "test",
        "status": "started",
    }

    HTTP_EVENT_KWARGS: ClassVar[dict[str, str]] = {
        "service": "ai-backend",
        "env": "test",
        "event": "http_request",
    }

    SAMPLES: ClassVar[tuple[tuple[str, dict[str, object], dict[str, object]], ...]] = (
        (
            "drops_deny_keys",
            {"api_key": "sk-1", "duration_ms": 12},
            {"duration_ms": 12},
        ),
        (
            "drops_non_string_keys",
            {"ok": "yes", 7: "weird"},
            {"ok": "yes"},
        ),
        (
            "drops_non_scalar_values",
            {"ok": "yes", "nested": {"x": 1}, "items": [1, 2]},
            {"ok": "yes"},
        ),
        (
            "passes_through_none_values",
            {"flag": None, "name": "alice"},
            {"flag": None, "name": "alice"},
        ),
        (
            "drops_all_known_deny_keys",
            {key: "<redacted-by-test>" for key in DENY_KEYS} | {"keep": "1"},
            {"keep": "1"},
        ),
    )


class TestMetadataRedactorDirect(_SampleMetadataMixin):
    @pytest.mark.parametrize(
        "name,input_,expected",
        [
            (name, input_, expected)
            for name, input_, expected in _SampleMetadataMixin.SAMPLES
        ],
    )
    def test_redact_matches_expected(
        self, name: str, input_: dict[str, object], expected: dict[str, object]
    ) -> None:
        assert MetadataRedactor.redact(input_) == expected, name

    def test_non_dict_input_yields_empty(self) -> None:
        assert MetadataRedactor.redact(None) == {}
        assert MetadataRedactor.redact("nope") == {}
        assert MetadataRedactor.redact([("k", "v")]) == {}

    def test_dropped_keys_logged_at_debug_only(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        # Production runs at INFO so the line is silent; this captures
        # at DEBUG to assert the operator-facing surfacing exists for
        # local debugging.
        with caplog.at_level(logging.DEBUG, logger="agent_runtime"):
            MetadataRedactor.redact({"api_key": "sk", "ok": "yes"})
        assert any(
            "Dropped metadata keys" in record.getMessage() for record in caplog.records
        )
        # Value text never appears in the debug log — only key names.
        assert all("sk" not in record.getMessage() for record in caplog.records)

    def test_clean_metadata_emits_no_debug_log(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.DEBUG, logger="agent_runtime"):
            MetadataRedactor.redact({"duration_ms": 5, "ok": True})
        assert not any(
            "Dropped metadata keys" in record.getMessage() for record in caplog.records
        )


class TestRuntimeAndHttpEventsAgree(_SampleMetadataMixin):
    """Both log models must filter ``metadata`` identically post-P13."""

    @pytest.mark.parametrize(
        "name,input_,expected",
        [
            (name, input_, expected)
            for name, input_, expected in _SampleMetadataMixin.SAMPLES
        ],
    )
    def test_runtime_event_metadata_matches_shared_redactor(
        self, name: str, input_: dict[str, object], expected: dict[str, object]
    ) -> None:
        event = RuntimeLogEvent(metadata=input_, **self.RUNTIME_EVENT_KWARGS)
        assert event.metadata == expected, name

    @pytest.mark.parametrize(
        "name,input_,expected",
        [
            (name, input_, expected)
            for name, input_, expected in _SampleMetadataMixin.SAMPLES
        ],
    )
    def test_http_event_metadata_matches_shared_redactor(
        self, name: str, input_: dict[str, object], expected: dict[str, object]
    ) -> None:
        event = HttpLogEvent(
            metadata=input_,
            level=HttpLogLevel.INFO,
            **self.HTTP_EVENT_KWARGS,
        )
        assert event.metadata == expected, name
