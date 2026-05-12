"""Handler-level integration test for the UsageRecorder injection (01c).

The recorder is the single boundary for usage writes; ``handlers/run.py``
delegates per-call and run-level writes to it. This test runs a full
fake-LLM turn with an injected ``InMemoryUsageRecorder`` and asserts:

- One ``record_run`` call lands on the recorder (the run-level row).
- One ``record_call`` call lands per AIMessage with usage (the
  per-call row).
- Both writes share the same ``pricing_at`` (the run's ``completed_at``)
  so a clock crossing a minute boundary mid-run cannot stamp two
  different pricing versions.
"""

from __future__ import annotations

from datetime import datetime
from typing import Sequence


from agent_runtime.execution.contracts import (
    AgentRuntimeContext,
    RuntimeDependencies,
)
from agent_runtime.execution.factory import RuntimeHarness
from agent_runtime.observability.usage_recorder import InMemoryUsageRecorder
from runtime_adapters.in_memory.runtime_api_store import InMemoryRuntimeApiStore
from runtime_worker.handlers.run import RuntimeRunHandler
from runtime_worker.loop import RuntimeWorker

# Reuse the heavy test fixtures so a recorder-injected handler runs
# against the same in-memory plumbing the rest of the worker suite uses.
from tests.unit.runtime_worker.test_runtime_worker import (
    _TestHelpers,
    _TestSettings,
)


async def test_handler_routes_writes_through_injected_recorder() -> None:
    store = InMemoryRuntimeApiStore()
    settings = _TestSettings.create()
    run_id = await _TestHelpers.create_queued_run(store, settings)
    recorder = InMemoryUsageRecorder()

    def fake_agent_factory(
        *,
        context: AgentRuntimeContext,
        dependencies: RuntimeDependencies,
    ) -> RuntimeHarness:
        return RuntimeHarness(
            agent=object(),
            context=context,
            dependencies=dependencies,
            tools=(),
            mcp_servers=(),
            subagents=(),
            memory_backend=None,
            skill_directories=(),
        )

    async def fake_invoker(
        _harness: RuntimeHarness, _messages: Sequence[object]
    ) -> object:
        return {
            "messages": [{"role": "assistant", "content": "Hello from the worker."}]
        }

    worker = RuntimeWorker(
        persistence=store,
        event_store=store,
        queue=store,
        settings=settings,
        run_handler=RuntimeRunHandler(
            persistence=store,
            event_store=store,
            agent_factory=fake_agent_factory,
            runtime_invoker=fake_invoker,
            usage_recorder=recorder,
        ),
    )

    processed = await worker.run_until_idle()

    assert processed == 1
    assert store.runs[run_id].status == "completed"

    # Exactly one run-level record landed on the recorder.
    assert len(recorder.runs) == 1
    assert recorder.runs[0].run_id == run_id
    # The persistence store also sees it via the recorder's writethrough.
    # (InMemoryUsageRecorder doesn't write to persistence — only the
    # production PostgresUsageRecorder does. So the assertion is on
    # the recorder, not on store.run_usage.)
    assert run_id not in store.run_usage  # recorder is the boundary

    # Per-call records may or may not land depending on whether the
    # fake invoker produced usage. The fake_invoker above returns a
    # plain dict with no usage_metadata, so no per-call rows expected.
    # The assertion below pins that contract — when there's no usage,
    # there's no per-call row.
    assert recorder.calls == []


async def test_pricing_at_is_shared_across_run_and_call(monkeypatch) -> None:
    """Both record_run and record_call (when they fire together) MUST
    pass the same ``pricing_at`` so a price change crossing a minute
    boundary doesn't stamp two different versions.

    This is a contract test: we inject a recorder that records the
    ``pricing_at`` arguments, then assert they're equal across the
    run-level and per-call writes.
    """

    captured_at: list[datetime] = []

    class _PricingPinnedRecorder(InMemoryUsageRecorder):
        async def record_call(self, record, *, pricing_at):  # type: ignore[override]
            captured_at.append(pricing_at)
            return await super().record_call(record, pricing_at=pricing_at)

        async def record_run(self, record, *, pricing_at):  # type: ignore[override]
            captured_at.append(pricing_at)
            return await super().record_run(record, pricing_at=pricing_at)

    store = InMemoryRuntimeApiStore()
    settings = _TestSettings.create()
    await _TestHelpers.create_queued_run(store, settings)
    recorder = _PricingPinnedRecorder()

    # Use the same fake-LLM rig that test_runtime_worker uses to land
    # both a run-level and at least one per-call write. The
    # streaming-deltas test produces a per-call slot with usage, so
    # both call_record AND run_record will hit the recorder.
    class FakeChunk:
        """Fake AIMessageChunk with an ``id`` so the per-call accumulator
        can dedup by it and emit a MODEL_CALL_COMPLETED — which is the
        boundary that exercises ``record_call``."""

        def __init__(
            self, content: object, usage_metadata: dict[str, object] | None = None
        ) -> None:
            self.content = content
            self.usage_metadata = usage_metadata
            self.id = "msg_fake_1"

    def fake_agent_factory(
        *, context: AgentRuntimeContext, dependencies: RuntimeDependencies
    ) -> RuntimeHarness:
        return RuntimeHarness(
            agent=object(),
            context=context,
            dependencies=dependencies,
            tools=(),
            mcp_servers=(),
            subagents=(),
            memory_backend=None,
            skill_directories=(),
        )

    async def fake_streamer(_h: RuntimeHarness, _m: Sequence[object]):
        # Yield the chunk object directly (not wrapped in a LangGraph
        # ``{"type": "messages", "data": (...)}`` envelope). The
        # ``_MessageIdExtractor`` picks up ``chunk.id`` from the
        # attribute path, and the provider extractor finds
        # ``chunk.usage_metadata``. This produces a finalized per-call
        # slot → exercises both ``record_call`` and ``record_run``.
        yield FakeChunk(
            [{"type": "text", "text": "Hi"}],
            usage_metadata={
                "input_tokens": 10,
                "output_tokens": 5,
                "total_tokens": 15,
            },
        )

    worker = RuntimeWorker(
        persistence=store,
        event_store=store,
        queue=store,
        settings=settings,
        run_handler=RuntimeRunHandler(
            persistence=store,
            event_store=store,
            agent_factory=fake_agent_factory,
            runtime_streamer=fake_streamer,
            usage_recorder=recorder,
        ),
    )

    await worker.run_until_idle()

    # We expect at least one record_run + one record_call, both pinned
    # to the run's completed_at.
    assert len(captured_at) >= 2
    # All pricing_at values within one run must be equal.
    assert len(set(captured_at)) == 1, (
        "All record_call + record_run calls must share one pricing_at, "
        f"got {captured_at!r}"
    )
