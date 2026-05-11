"""Cross-process trace propagation across ``_dispatch`` (P13 step 1).

When the API enqueues a ``RuntimeRunCommand`` it stamps the active
W3C ``traceparent`` onto ``command.trace_propagation``. The worker
must extract that context before starting its own dispatch span so
the resulting trace_id matches the API request's trace_id end-to-end.

These tests bypass the heavy worker fixture by stubbing every handler;
the only behavior under test is ``RuntimeWorker._dispatch`` re-parenting
the span tree from the carrier headers on ``claim.payload``.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)

from agent_runtime.observability.otel import TelemetryBootstrap
from agent_runtime.observability.queue_propagation import QueueTracePropagator
from agent_runtime.persistence.constants import Values as PersistenceValues
from agent_runtime.persistence.records import RuntimeWorkerClaim
from runtime_worker.loop import RuntimeWorker


class _StubHandler:
    def __init__(self) -> None:
        self.calls: list[Any] = []

    async def handle(self, command: Any) -> None:
        self.calls.append(command)


class _NullQueue:
    """Stand-in queue that ignores every call."""

    async def enqueue_run(self, command: Any) -> None: ...
    async def enqueue_cancel(self, command: Any) -> None: ...
    async def enqueue_approval_resolved(self, command: Any) -> None: ...
    async def claim_next(self, **_: Any) -> None:
        return None

    async def mark_complete(self, **_: Any) -> None: ...
    async def mark_retry(self, **_: Any) -> None: ...
    async def mark_dead_letter(self, **_: Any) -> None: ...


class _NullPersistence:
    pass


class _NullEventStore:
    pass


class TraceCaptureMixin:
    """Bootstrap a TracerProvider with an in-memory span exporter."""

    exporter: InMemorySpanExporter

    def setup_method(self) -> None:
        TelemetryBootstrap.reset_for_tests()
        # Configure the global TracerProvider (without an endpoint, no
        # OTLP exporter is wired). Then attach an in-memory exporter so
        # spans the worker emits land somewhere we can read.
        TelemetryBootstrap.configure()
        provider = trace.get_tracer_provider()
        assert isinstance(provider, TracerProvider)
        self.exporter = InMemorySpanExporter()
        provider.add_span_processor(SimpleSpanProcessor(self.exporter))

    def teardown_method(self) -> None:
        self.exporter.clear()
        TelemetryBootstrap.reset_for_tests()


class _RunCommandFixture:
    """Build a valid run-command payload + claim for the dispatch path."""

    @staticmethod
    def claim_with_carrier(carrier: dict[str, str]) -> RuntimeWorkerClaim:
        run_id = f"run_{uuid4().hex[:8]}"
        command_id = uuid4().hex
        # Minimal model_dump shape for ``RuntimeRunCommand.model_validate``.
        payload: dict[str, object] = {
            "command_id": command_id,
            "command_type": PersistenceValues.EventType.RUN_REQUESTED,
            "run_id": run_id,
            "conversation_id": f"conv_{uuid4().hex[:8]}",
            "org_id": "org_123",
            "user_id": "user_123",
            "trace_id": f"trace_{run_id}",
            "runtime_context": {
                "user_id": "user_123",
                "org_id": "org_123",
                "roles": ["employee"],
                "model_profile": {
                    "provider": "openai",
                    "model_name": "gpt-5.4-mini",
                    "max_input_tokens": 128000,
                    "timeout_seconds": 30,
                    "temperature": 0,
                    "supports_streaming": True,
                },
                "run_id": run_id,
                "trace_id": f"trace_{run_id}",
            },
            "trace_propagation": carrier,
            "approval_id": None,
        }
        return RuntimeWorkerClaim(
            command_id=command_id,
            command_type=PersistenceValues.EventType.RUN_REQUESTED,
            org_id="org_123",
            run_id=run_id,
            approval_id=None,
            locked_by="worker_1",
            lock_expires_at=datetime.now(timezone.utc) + timedelta(seconds=60),
            attempts=1,
            payload=payload,
        )


def _build_worker(*, run_handler: _StubHandler) -> RuntimeWorker:
    """Construct a worker with stub handlers; queue is never consulted."""

    return RuntimeWorker(
        persistence=_NullPersistence(),  # type: ignore[arg-type]
        event_store=_NullEventStore(),  # type: ignore[arg-type]
        queue=_NullQueue(),  # type: ignore[arg-type]
        run_handler=run_handler,  # type: ignore[arg-type]
        cancel_handler=_StubHandler(),  # type: ignore[arg-type]
        approval_handler=_StubHandler(),  # type: ignore[arg-type]
    )


class TestDispatchReparentsToApiSpan(TraceCaptureMixin):
    async def test_dispatch_with_carrier_continues_api_trace(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("RUNTIME_PROPAGATE_QUEUE_TRACE", "true")
        # Simulate the API producing a propagation carrier under its
        # active request span.
        api_tracer = trace.get_tracer("test_api")
        with api_tracer.start_as_current_span("api.create_run") as api_span:
            carrier = QueueTracePropagator.inject()
            expected_trace_id = api_span.get_span_context().trace_id

        run_handler = _StubHandler()
        worker = _build_worker(run_handler=run_handler)
        claim = _RunCommandFixture.claim_with_carrier(carrier)

        await worker._dispatch(claim)

        # The run-handler stub was invoked with the validated command.
        assert len(run_handler.calls) == 1

        # The worker emitted exactly one ``runtime_worker.run`` span
        # whose trace_id matches the API request's trace_id.
        finished = [
            span
            for span in self.exporter.get_finished_spans()
            if span.name == "runtime_worker.run"
        ]
        assert len(finished) == 1
        span: ReadableSpan = finished[0]
        assert span.get_span_context().trace_id == expected_trace_id

    async def test_dispatch_without_carrier_starts_fresh_trace(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("RUNTIME_PROPAGATE_QUEUE_TRACE", "true")
        run_handler = _StubHandler()
        worker = _build_worker(run_handler=run_handler)
        claim = _RunCommandFixture.claim_with_carrier({})

        await worker._dispatch(claim)

        finished = [
            span
            for span in self.exporter.get_finished_spans()
            if span.name == "runtime_worker.run"
        ]
        assert len(finished) == 1
        # No producer span → trace is fresh, but it's still valid.
        assert finished[0].get_span_context().is_valid

    async def test_dispatch_with_propagation_disabled_starts_fresh_trace(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("RUNTIME_PROPAGATE_QUEUE_TRACE", "false")
        # Even with a carrier on the payload, the worker should ignore
        # it when the flag is off — both sides obey the same switch.
        api_tracer = trace.get_tracer("test_api")
        with api_tracer.start_as_current_span("api.create_run") as api_span:
            # Force a non-empty carrier by injecting under "true" env,
            # then flip back to "false" for the worker side.
            monkeypatch.setenv("RUNTIME_PROPAGATE_QUEUE_TRACE", "true")
            carrier = QueueTracePropagator.inject()
            api_trace_id = api_span.get_span_context().trace_id
        monkeypatch.setenv("RUNTIME_PROPAGATE_QUEUE_TRACE", "false")

        run_handler = _StubHandler()
        worker = _build_worker(run_handler=run_handler)
        claim = _RunCommandFixture.claim_with_carrier(carrier)

        await worker._dispatch(claim)

        finished = [
            span
            for span in self.exporter.get_finished_spans()
            if span.name == "runtime_worker.run"
        ]
        assert len(finished) == 1
        assert finished[0].get_span_context().trace_id != api_trace_id
