"""Worker-level pin for ``reasoning_depth`` → execution params.

The depth → budget mapping is supposed to be the single application
point (in the resolver). The worker's job is to *read* the scaled values
off ``model_profile`` without re-applying anything. These tests assert
that the actual execution params the worker drives the LangGraph
invocation with are the depth-scaled ones, not the unscaled baseline —
i.e. depth isn't just stored cosmetically, it controls the run.
"""

from __future__ import annotations

from collections.abc import Sequence

import pytest

from agent_runtime.api.conversation_coordinator import ConversationCoordinator
from agent_runtime.api.events import RuntimeEventProducer
from agent_runtime.api.run_coordinator import RunCoordinator
from agent_runtime.execution.contracts import RuntimeDependencies
from agent_runtime.execution.factory import RuntimeHarness
from agent_runtime.execution.models import ModelConfigResolver
from agent_runtime.settings import RuntimeSettings
from runtime_adapters.in_memory import InMemoryRuntimeApiStore
from runtime_api.schemas import (
    AgentRunStatus,
    CreateConversationRequest,
    CreateRunRequest,
)
from runtime_worker.handlers.run import RuntimeRunHandler
from runtime_worker.loop import RuntimeWorker


def _settings() -> RuntimeSettings:
    return RuntimeSettings.load(
        environ={
            "OPENAI_API_KEY": "sk-test",
            "RUNTIME_DEFAULT_PROVIDER": "openai",
            "RUNTIME_DEFAULT_MODEL": "gpt-5.4-mini",
        }
    )


def _agent_factory(*, context, dependencies: RuntimeDependencies) -> RuntimeHarness:
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


class _ProfileCapturingInvoker:
    """Capture the ``model_profile`` the worker actually drives the run with.

    The worker reads ``command.runtime_context.model_profile.timeout_seconds``
    when wrapping the invoker in ``asyncio.wait_for`` and threads the same
    context into the runtime dependencies. Capturing it here lets us
    assert that the resolver's depth-scaled values reached the worker
    intact — pinning the 'mapping applied to actual execution params'
    invariant from acceptance criterion #7.
    """

    def __init__(self) -> None:
        self.observed: dict[str, object] = {}

    async def __call__(
        self, harness: RuntimeHarness, _messages: Sequence[object]
    ) -> dict[str, object]:
        profile = harness.context.model_profile
        self.observed = {
            "timeout_seconds": profile.timeout_seconds,
            "max_output_tokens": profile.max_output_tokens,
            "tool_call_budget": profile.tool_call_budget,
            "reasoning_depth": profile.reasoning_depth,
        }
        return {"messages": [{"role": "assistant", "content": "Ack."}]}


async def _seed_run(
    store: InMemoryRuntimeApiStore,
    settings: RuntimeSettings,
    *,
    reasoning_depth: str | None,
) -> str:
    model_resolver = ModelConfigResolver(settings)
    event_producer = RuntimeEventProducer(
        persistence=store, event_store=store, on_event_appended=None
    )
    run_coordinator = RunCoordinator(
        persistence=store,
        queue=store,
        event_producer=event_producer,
        settings=settings,
        model_resolver=model_resolver,
    )
    conv_coordinator = ConversationCoordinator(
        persistence=store, settings=settings, run_coordinator=run_coordinator
    )
    conversation = await conv_coordinator.create_conversation(
        CreateConversationRequest(
            org_id="org_depth_w",
            user_id="user_depth_w",
            assistant_id="assistant_depth_w",
        )
    )
    request_kwargs: dict[str, object] = {
        "conversation_id": conversation.conversation_id,
        "org_id": "org_depth_w",
        "user_id": "user_depth_w",
        "user_input": "hi",
        "model": {"provider": "openai", "model_name": "gpt-5.4-mini"},
    }
    if reasoning_depth is not None:
        request_kwargs["reasoning_depth"] = reasoning_depth
    response = await run_coordinator.create_run(CreateRunRequest(**request_kwargs))
    return response.run_id


@pytest.mark.parametrize(
    "depth, expected_timeout_mul, expected_budget_mul",
    [
        ("fast", 0.5, 0.5),
        ("balanced", 1.0, 1.0),
        ("deep", 2.0, 2.0),
    ],
)
async def test_worker_drives_run_with_depth_scaled_execution_params(
    depth: str,
    expected_timeout_mul: float,
    expected_budget_mul: float,
) -> None:
    """The worker reads ``timeout_seconds`` and ``tool_call_budget`` off
    the resolved ``model_profile``; both must reflect the depth multiplier
    documented in :class:`DepthBudgetTable`. If the mapping were only
    stored on a side-field (and not folded into the actual budgets), the
    multipliers below would not hold.
    """

    settings = _settings()
    # Baseline numbers come from the resolver — compute them via a
    # depth-less run so the assertion is robust to env defaults.
    baseline_store = InMemoryRuntimeApiStore()
    baseline_run_id = await _seed_run(baseline_store, settings, reasoning_depth=None)
    baseline_profile = baseline_store.runs[
        baseline_run_id
    ].runtime_context.model_profile

    scaled_store = InMemoryRuntimeApiStore()
    run_id = await _seed_run(scaled_store, settings, reasoning_depth=depth)
    invoker = _ProfileCapturingInvoker()
    worker = RuntimeWorker(
        persistence=scaled_store,
        event_store=scaled_store,
        queue=scaled_store,
        settings=settings,
        run_handler=RuntimeRunHandler(
            persistence=scaled_store,
            event_store=scaled_store,
            agent_factory=_agent_factory,
            runtime_invoker=invoker,
        ),
    )
    processed = await worker.run_until_idle()
    assert processed == 1
    assert scaled_store.runs[run_id].status == AgentRunStatus.COMPLETED

    expected_timeout = min(
        baseline_profile.timeout_seconds * expected_timeout_mul,
        600.0,  # contract ceiling
    )
    expected_budget = max(
        1,
        round(baseline_profile.tool_call_budget * expected_budget_mul),
    )
    assert invoker.observed["timeout_seconds"] == expected_timeout
    assert invoker.observed["tool_call_budget"] == expected_budget
    assert invoker.observed["reasoning_depth"] == depth


async def test_worker_no_depth_leaves_baseline_profile() -> None:
    """No-regression — omitting ``reasoning_depth`` leaves the
    ``model_profile`` numbers untouched and the field ``None``.
    """

    settings = _settings()
    store = InMemoryRuntimeApiStore()
    run_id = await _seed_run(store, settings, reasoning_depth=None)
    invoker = _ProfileCapturingInvoker()
    worker = RuntimeWorker(
        persistence=store,
        event_store=store,
        queue=store,
        settings=settings,
        run_handler=RuntimeRunHandler(
            persistence=store,
            event_store=store,
            agent_factory=_agent_factory,
            runtime_invoker=invoker,
        ),
    )
    assert await worker.run_until_idle() == 1
    assert store.runs[run_id].status == AgentRunStatus.COMPLETED
    expected_profile = store.runs[run_id].runtime_context.model_profile
    assert invoker.observed["timeout_seconds"] == expected_profile.timeout_seconds
    assert invoker.observed["tool_call_budget"] == expected_profile.tool_call_budget
    assert invoker.observed["reasoning_depth"] is None
