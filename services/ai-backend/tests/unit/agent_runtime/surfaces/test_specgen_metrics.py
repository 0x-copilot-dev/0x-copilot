"""Metering counters for surface-spec generation (generative-UI PRD-11, AC4).

Drives the generator with a fake completion and reads the real OTel pipeline
through an in-memory reader — no live model, no new metrics backend. Asserts
``surfaces_specgen_total{verdict}`` increments per verdict path, token counters
accumulate, and the scheduler's ``surfaces_render_fallback_total`` proxy fires on
a spec-less envelope. Also covers the budget warn-log.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator

import pytest
from opentelemetry.metrics import _internal as metrics_internal
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader

from agent_runtime.capabilities.surfaces.generator import (
    GenToolDescriptor,
    SpecCompletionResult,
    SurfaceGenerationScheduler,
    SurfaceSpecGenerator,
)
from agent_runtime.capabilities.surfaces.store import InMemorySurfaceSpecStore
from agent_runtime.observability.surface_specgen_metrics import (
    RenderFallbackTier,
    SpecgenVerdict,
    SurfaceSpecgenMetrics,
    TokenDirection,
)

_LINEAR_SAMPLE: dict[str, object] = {
    "issue": {
        "title": "Fix login redirect loop",
        "identifier": "ENG-1421",
        "state": {"name": "In Progress"},
        "url": "https://linear.app/acme/issue/ENG-1421",
    }
}

_VALID_CANDIDATE: dict[str, object] = {
    "spec_version": 1,
    "archetype": "record",
    "title_path": "issue.title",
    "fields": [{"label": "State", "path": "issue.state.name", "format": "badge"}],
    "link": {"label": "Open in Linear", "url_path": "issue.url"},
}

_DESCRIPTOR = GenToolDescriptor(name="get_issue", description="Fetch a Linear issue.")


class FakeCompletion:
    def __init__(self, candidates: list[object]) -> None:
        self._candidates = list(candidates)

    async def complete(self, *, system: str, user: str) -> SpecCompletionResult:
        candidate = self._candidates.pop(0)
        raw = json.dumps(candidate) if isinstance(candidate, dict) else str(candidate)
        return SpecCompletionResult(
            candidate=candidate,
            raw_text=raw,
            model="fake-nano",
            input_tokens=120,
            output_tokens=48,
        )


@pytest.fixture
def metric_reader() -> Iterator[InMemoryMetricReader]:
    """Install a fresh global MeterProvider with an in-memory reader per test.

    Sets the internal ``_METER_PROVIDER`` directly (and restores it) rather than
    ``set_meter_provider``, whose run-once guard would make every test after the
    first a no-op and leak cumulative counters across tests. Because
    ``SurfaceSpecgenMetrics`` binds its meter in ``__init__``, the tests
    construct the facade inside the test body — after this fixture runs.
    """

    reader = InMemoryMetricReader()
    provider = MeterProvider(metric_readers=[reader])
    previous = metrics_internal._METER_PROVIDER
    metrics_internal._METER_PROVIDER = provider
    try:
        yield reader
    finally:
        metrics_internal._METER_PROVIDER = previous


def _counter_points(
    reader: InMemoryMetricReader, name: str
) -> dict[tuple[tuple[str, object], ...], int]:
    """Return {sorted-attribute-tuple: value} for every point of a counter."""

    data = reader.get_metrics_data()
    points: dict[tuple[tuple[str, object], ...], int] = {}
    for resource_metric in data.resource_metrics:
        for scope_metric in resource_metric.scope_metrics:
            for metric in scope_metric.metrics:
                if metric.name != name:
                    continue
                for point in metric.data.data_points:
                    key = tuple(sorted(point.attributes.items()))
                    points[key] = point.value
    return points


class TestSpecgenCounters:
    async def test_ok_verdict_increments_total_and_tokens(
        self, metric_reader: InMemoryMetricReader
    ) -> None:
        generator = SurfaceSpecGenerator(
            completion=FakeCompletion([dict(_VALID_CANDIDATE)]),
            metrics=SurfaceSpecgenMetrics(),
        )
        await generator.generate(
            server="linear", tool_descriptor=_DESCRIPTOR, sample_output=_LINEAR_SAMPLE
        )

        totals = _counter_points(metric_reader, "surfaces_specgen_total")
        assert totals[(("verdict", SpecgenVerdict.OK),)] == 1

        tokens = _counter_points(metric_reader, "surfaces_specgen_tokens")
        assert tokens[(("direction", TokenDirection.INPUT),)] == 120
        assert tokens[(("direction", TokenDirection.OUTPUT),)] == 48

    async def test_retry_then_ok_records_both_verdicts(
        self, metric_reader: InMemoryMetricReader
    ) -> None:
        bad = dict(_VALID_CANDIDATE)
        bad["title_path"] = "issue.does_not_exist"  # fails lint on attempt 1
        generator = SurfaceSpecGenerator(
            completion=FakeCompletion([bad, dict(_VALID_CANDIDATE)]),
            metrics=SurfaceSpecgenMetrics(),
        )
        await generator.generate(
            server="linear", tool_descriptor=_DESCRIPTOR, sample_output=_LINEAR_SAMPLE
        )

        totals = _counter_points(metric_reader, "surfaces_specgen_total")
        assert totals[(("verdict", SpecgenVerdict.LINT_FAILED),)] == 1
        assert totals[(("verdict", SpecgenVerdict.RETRY_OK),)] == 1

    async def test_schema_invalid_verdict_is_counted(
        self, metric_reader: InMemoryMetricReader
    ) -> None:
        generator = SurfaceSpecGenerator(
            completion=FakeCompletion(["not json", "still not"]),
            metrics=SurfaceSpecgenMetrics(),
        )
        await generator.generate(
            server="linear", tool_descriptor=_DESCRIPTOR, sample_output=_LINEAR_SAMPLE
        )
        totals = _counter_points(metric_reader, "surfaces_specgen_total")
        assert totals[(("verdict", SpecgenVerdict.SCHEMA_INVALID),)] == 2


class TestRenderFallbackProxy:
    def test_specless_envelope_increments_fallback(
        self, metric_reader: InMemoryMetricReader
    ) -> None:
        store = InMemorySurfaceSpecStore()
        metrics = SurfaceSpecgenMetrics()
        generator = SurfaceSpecGenerator(completion=FakeCompletion([]), metrics=metrics)
        scheduled: list[object] = []
        scheduler = SurfaceGenerationScheduler(
            generator=generator,
            store=store,
            emit=lambda _payload: _noop(),
            model_id="fake-nano",
            schedule=lambda coro: scheduled.append(coro),
            max_per_run=5,
            metrics=metrics,
        )
        scheduler.maybe_schedule(
            server="linear",
            tool="get_issue",
            tool_descriptor=_DESCRIPTOR,
            output=_LINEAR_SAMPLE,
            surface_uri="record://linear/get_issue/ENG-1421",
        )
        # Close the coroutine we captured but never awaited (no event loop here).
        for coro in scheduled:
            getattr(coro, "close", lambda: None)()

        fallback = _counter_points(metric_reader, "surfaces_render_fallback_total")
        assert fallback[(("tier", RenderFallbackTier.TIER3),)] == 1


class TestBudgetAlarm:
    def test_warns_once_when_cap_exceeded(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        store = InMemorySurfaceSpecStore()
        generator = SurfaceSpecGenerator(
            completion=FakeCompletion([]), metrics=SurfaceSpecgenMetrics()
        )
        captured: list[object] = []
        scheduler = SurfaceGenerationScheduler(
            generator=generator,
            store=store,
            emit=lambda _payload: _noop(),
            model_id="fake-nano",
            schedule=lambda coro: captured.append(coro),
            max_per_run=1,
        )

        def _miss(uid: str) -> None:
            scheduler.maybe_schedule(
                server="linear",
                tool=f"tool_{uid}",
                tool_descriptor=_DESCRIPTOR,
                output={"issue": {"title": uid}},
                surface_uri=f"record://linear/tool_{uid}/{uid}",
            )

        with caplog.at_level(logging.WARNING):
            _miss("a")  # fills the single budget slot
            _miss("b")  # exceeds → warns
            _miss("c")  # exceeds again → must NOT warn a second time

        for coro in captured:
            getattr(coro, "close", lambda: None)()

        budget_lines = [
            r for r in caplog.records if "budget_exceeded" in r.getMessage()
        ]
        assert len(budget_lines) == 1


async def _noop() -> None:
    return None
