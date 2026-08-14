"""Desktop F5 wiring: one pre-model admission, then durable projection."""

from __future__ import annotations

import os
from typing import Any, cast

os.environ.setdefault("OPENAI_API_KEY", "sk-test-tool-result-admission")

from langchain.agents.middleware.types import ToolCallRequest
from langchain_core.messages import ToolMessage
from langchain_core.tools import BaseTool

from agent_runtime.api.constants import Keys
from agent_runtime.capabilities.middleware.runtime_tool_control import (
    RuntimeToolControlMiddleware,
)
from agent_runtime.capabilities.tool_budget_guard import ToolBudgetGuard
from agent_runtime.context.tool_result_admission import ToolResultCap
from agent_runtime.execution.contracts import AgentRuntimeContext
from runtime_adapters.file import FileRuntimeApiStore
from runtime_adapters.in_memory import InMemoryRuntimeApiStore
from runtime_api.schemas import AgentRunStatus, RunRecord
from runtime_worker.handlers.run import RuntimeRunHandler


_RUN_ID = "run-tool-admission"
_RAW_TAIL = "RAW_CONTENT_MUST_NOT_REACH_MODEL"


def _run() -> RunRecord:
    return RunRecord(
        run_id=_RUN_ID,
        conversation_id="conv-tool-admission",
        org_id="org-tool-admission",
        user_id="user-tool-admission",
        user_message_id="msg-tool-admission",
        trace_id="trace-tool-admission",
        model_provider="openai",
        model_name="gpt-5.4-mini",
        status=AgentRunStatus.RUNNING,
        runtime_context=AgentRuntimeContext(
            user_id="user-tool-admission",
            org_id="org-tool-admission",
            roles=["member"],
            run_id=_RUN_ID,
            trace_id="trace-tool-admission",
            model_profile={
                "provider": "openai",
                "model_name": "gpt-5.4-mini",
                "max_input_tokens": 128_000,
                "timeout_seconds": 30,
                "temperature": 0,
                "supports_streaming": True,
            },
        ),
    )


class _LargeResultTool(BaseTool):
    name: str = "large_result"
    description: str = "Return a large result."
    result: str

    def _run(self, *_args: Any, **_kwargs: Any) -> str:
        return self.result

    async def _arun(self, *_args: Any, **_kwargs: Any) -> str:
        return self.result


async def test_file_runtime_admits_without_budget_rows_and_projects_once(
    tmp_path,
) -> None:
    store = FileRuntimeApiStore(tmp_path / "store")
    await store.open()
    run = _run()
    handler = RuntimeRunHandler(persistence=store, event_store=store)

    # No tool-budget rows are seeded. Desktop admission must still activate.
    guard = await handler._build_tool_budget_guard(run)
    assert guard is not None
    adapter = handler._file_store_wiring().tool_result_admission()
    offloader = handler.stream_event_mapper.message_processor._tool_result_offloader
    assert adapter is not None
    assert offloader is not None

    calls = 0
    original_admit = adapter.admit

    def counted_admit(*args: Any, **kwargs: Any):
        nonlocal calls
        calls += 1
        return original_admit(*args, **kwargs)

    adapter.admit = counted_admit  # type: ignore[method-assign]
    raw = ("large tool output\n" * 5_000) + _RAW_TAIL
    tool = _LargeResultTool(result=raw)
    request = ToolCallRequest(
        tool_call={
            "name": tool.name,
            "args": {},
            "id": "large-result-call",
            "type": "tool_call",
        },
        tool=tool,
        state={},
        runtime=cast(Any, object()),
    )

    async def execute(inner_request: ToolCallRequest) -> ToolMessage:
        return ToolMessage(
            content=await tool._arun(),
            tool_call_id=inner_request.tool_call["id"],
        )

    token = ToolBudgetGuard.bind_for_run(guard)
    try:
        result = await RuntimeToolControlMiddleware().awrap_tool_call(request, execute)
    finally:
        ToolBudgetGuard.unbind(token)

    model_content = result.content
    assert isinstance(model_content, str)
    assert len(model_content) <= 4_096
    assert _RAW_TAIL not in model_content

    projected = offloader.apply_with_notice(
        {
            Keys.Field.TOOL_NAME: tool.name,
            Keys.Field.OUTPUT: {Keys.Field.CONTENT: model_content},
        },
        trace_id=run.trace_id or run.run_id,
        projection_key=run.run_id,
        projection_content=model_content,
    ).payload

    # The stream projector consumed the exact pre-model decision. It did not
    # call admission again or write the already-bounded representation.
    assert calls == 1
    output_ref = projected[Keys.Field.OUTPUT_REF]
    assert isinstance(output_ref, str)
    sha = output_ref.removeprefix("/large_tool_results/")
    assert store.object_store.get(sha).decode("utf-8") == raw
    assert adapter.consume_projection(model_content, projection_key=run.run_id) is None
    await store.close()


async def test_non_file_runtime_builds_no_offload_adapter() -> None:
    """No object store, so no *offload* — which is not the same as no bound.

    Renamed from ``..._preserves_no_admission_behavior``: that name asserted
    the defect. The adapter is still correctly absent here, because offloading
    needs somewhere to put the bytes; what changed is that its absence no
    longer means the result reaches the model unbounded. See
    :func:`test_non_file_runtime_caps_an_oversized_result_and_says_so`.
    """

    store = InMemoryRuntimeApiStore()
    handler = RuntimeRunHandler(persistence=store, event_store=store)

    guard = await handler._build_tool_budget_guard(_run())
    assert guard is None or guard._tool_result_admission is None
    assert handler.stream_event_mapper.message_processor._tool_result_offloader is None


def _request(tool: BaseTool, *, call_id: str) -> ToolCallRequest:
    return ToolCallRequest(
        tool_call={
            "name": tool.name,
            "args": {},
            "id": call_id,
            "type": "tool_call",
        },
        tool=tool,
        state={},
        runtime=cast(Any, object()),
    )


async def _through_the_tool_seam(
    tool: BaseTool,
    *,
    call_id: str,
    guard: ToolBudgetGuard | None,
) -> ToolMessage:
    """Drive one tool call through the live graph seam.

    ``RuntimeControlMiddleware`` is installed unconditionally by the deep agent
    builder (``agent_runtime/execution/factory.py``), so this is the path every
    graph-visible tool call takes on every store backend.
    """

    async def execute(request: ToolCallRequest) -> ToolMessage:
        return ToolMessage(
            content=await tool._arun(),
            tool_call_id=request.tool_call["id"],
        )

    token = None if guard is None else ToolBudgetGuard.bind_for_run(guard)
    try:
        assert ToolBudgetGuard.active() is guard
        result = await RuntimeToolControlMiddleware().awrap_tool_call(
            _request(tool, call_id=call_id),
            execute,
        )
    finally:
        if token is not None:
            ToolBudgetGuard.unbind(token)
    assert isinstance(result, ToolMessage)
    return result


async def _non_file_guard() -> ToolBudgetGuard:
    """Build the guard a web / postgres / in-memory run really gets.

    The seeded wildcard budget row means the guard *is* built here — what is
    missing is the offload adapter, because the store has no object store to
    move oversized bytes into. That combination is the whole defect: an
    admission hook that was reached on every call and handed the result back
    untouched.
    """

    store = InMemoryRuntimeApiStore()
    handler = RuntimeRunHandler(persistence=store, event_store=store)
    guard = await handler._build_tool_budget_guard(_run())
    assert guard is not None
    assert guard._tool_result_admission is None
    assert handler.stream_event_mapper.message_processor._tool_result_offloader is None
    return guard


async def test_non_file_runtime_caps_an_oversized_result_and_says_so() -> None:
    """The web / postgres / in-memory hole: no object store, so no bound.

    The offload adapter needs somewhere to put the bytes, so it is desktop-only
    and correctly ``None`` here. Before the cap that meant *no* limit at all on
    these backends: a multi-megabyte MCP read went into model context whole,
    while the identical run on the desktop was reduced to a 4 KiB stub.
    """

    raw = ("large tool output\n" * 5_000) + _RAW_TAIL
    assert len(raw) > ToolResultCap.OVERSIZED_ABOVE_CHARS

    result = await _through_the_tool_seam(
        _LargeResultTool(result=raw),
        call_id="uncapped-backend-call",
        guard=await _non_file_guard(),
    )

    model_content = result.content
    assert isinstance(model_content, str)
    assert len(model_content) <= ToolResultCap.MODEL_CONTENT_LIMIT_CHARS
    assert _RAW_TAIL not in model_content
    # Truncation the model can act on rather than a silent clip.
    assert ToolResultCap.Messages.MARKER in model_content
    assert f"of {len(raw):,} characters" in model_content
    assert ToolResultCap.Messages.RECOVERY in model_content
    # Honest about this backend: nothing was retained, so nothing is fetchable.
    assert "/large_tool_results/" not in model_content


async def test_non_file_runtime_leaves_a_small_result_byte_identical() -> None:
    """The cap is a ceiling, not a rewrite: ordinary results are untouched."""

    exact = "small exact result"

    result = await _through_the_tool_seam(
        _LargeResultTool(result=exact),
        call_id="small-result-call",
        guard=await _non_file_guard(),
    )

    assert result.content == exact


async def test_tool_seam_caps_even_when_no_budget_guard_was_built() -> None:
    """A guard is optional; the model-context bound is not.

    ``_build_tool_budget_guard`` returns ``None`` when the store exposes no
    budget rows and no offload target — a stub persistence port, or a real one
    whose budget read failed, which is deliberately swallowed so optional
    policy I/O cannot stop a run. That branch skipped admission entirely.
    """

    raw = ("unguarded tool output\n" * 5_000) + _RAW_TAIL

    result = await _through_the_tool_seam(
        _LargeResultTool(result=raw),
        call_id="unguarded-call",
        guard=None,
    )

    model_content = result.content
    assert isinstance(model_content, str)
    assert len(model_content) <= ToolResultCap.MODEL_CONTENT_LIMIT_CHARS
    assert _RAW_TAIL not in model_content
    assert ToolResultCap.Messages.MARKER in model_content
