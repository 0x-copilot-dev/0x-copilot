"""B8 — wiring tests for :class:`ToolBudgetGuard` + :class:`ToolBudgetGuardedTool`.

The middleware itself is exercised in
``test_tool_budget_middleware.py``; this file pins the wiring layer:

- :class:`ToolBudgetGuardedTool` admits and delegates to the inner tool
  when the guard is unbound (passthrough).
- It rejects with the safe public message when the active guard's
  middleware says reject.
- It admits + emits a ``BUDGET_WARNING`` event under soft enforcement.
- :class:`ToolBudgetGuardedRegistry` wraps every BaseTool in the
  inner registry's output with the guard.
- The persistence-port snapshot loader feeds the runtime correctly.
"""

from __future__ import annotations

import asyncio

from langchain_core.tools import BaseTool

from agent_runtime.capabilities.tool_budget_guard import (
    ToolBudgetGuard,
    ToolBudgetGuardedRegistry,
    ToolBudgetGuardedTool,
)
from agent_runtime.capabilities.tool_budget_middleware import ToolBudgetMiddleware
from agent_runtime.execution.contracts import StreamEventSource
from agent_runtime.persistence.records import (
    ToolBudgetEnforcement,
    ToolBudgetRecord,
)
from runtime_adapters.in_memory.runtime_api_store import InMemoryRuntimeApiStore
from runtime_api.schemas import RuntimeApiEventType
from runtime_worker.tool_call_ledger import ToolCallLedger


# --- mixins (per tests/CLAUDE.md) --------------------------------------------


class _FakeProducerMixin:
    class _FakeProducer:
        """Minimal stand-in for :class:`RuntimeEventProducer` that records calls."""

        def __init__(self) -> None:
            self.events: list[dict[str, object]] = []

        async def append_api_event(self, **kwargs: object) -> None:
            self.events.append(kwargs)


class _RecordingTool(BaseTool):
    """Tiny inner tool that records every call and returns a fixed string."""

    name: str = "echo"
    description: str = "Echoes the input back for tests."

    calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def _run(self, *args: object, **kwargs: object) -> str:
        self.calls.append((args, kwargs))
        return "echo-ok"

    async def _arun(self, *args: object, **kwargs: object) -> str:
        self.calls.append((args, kwargs))
        return "echo-ok"


def _budget(
    *,
    org_id: str | None,
    tool_name: str,
    max_calls_per_run: int = 2,
    enforcement: ToolBudgetEnforcement = ToolBudgetEnforcement.HARD,
) -> ToolBudgetRecord:
    return ToolBudgetRecord(
        org_id=org_id,
        tool_name=tool_name,
        max_calls_per_run=max_calls_per_run,
        enforcement=enforcement,
    )


def _make_run() -> object:
    """Synthesise a minimal run record stub for emit_warning's payload.

    The producer fake doesn't introspect the run; only ``run_id`` is read
    by callers in production paths. A simple namespace satisfies the
    duck-typed call.
    """

    class _Run:
        run_id = "run-x"
        conversation_id = "conv-x"
        org_id = "org-x"
        trace_id = "trace-x"

    return _Run()


# --- guarded-tool semantics --------------------------------------------------


class TestToolBudgetGuardedTool(_FakeProducerMixin):
    def test_passthrough_when_no_guard_bound_sync(self) -> None:
        inner = _RecordingTool()
        wrapped = ToolBudgetGuardedTool(
            name=inner.name,
            description=inner.description,
            inner=inner,
        )
        result = wrapped._run("hello")
        assert result == "echo-ok"
        # Inner tool was actually invoked; the guard didn't gate it.
        assert len(inner.calls) == 1

    def test_passthrough_when_no_guard_bound_async(self) -> None:
        inner = _RecordingTool()
        wrapped = ToolBudgetGuardedTool(
            name=inner.name,
            description=inner.description,
            inner=inner,
        )
        result = asyncio.run(wrapped._arun("hello"))
        assert result == "echo-ok"
        assert len(inner.calls) == 1

    def test_admits_under_cap_and_records_into_ledger(self) -> None:
        inner = _RecordingTool()
        wrapped = ToolBudgetGuardedTool(
            name=inner.name,
            description=inner.description,
            inner=inner,
        )
        ledger = ToolCallLedger(run_id="run-1")
        guard = ToolBudgetGuard(
            middleware=ToolBudgetMiddleware(
                [_budget(org_id=None, tool_name="echo", max_calls_per_run=3)]
            ),
            ledger=ledger,
        )
        token = ToolBudgetGuard.bind_for_run(guard)
        try:
            result = asyncio.run(wrapped._arun("hi"))
        finally:
            ToolBudgetGuard.unbind(token)
        assert result == "echo-ok"
        # One admitted call landed on the ledger.
        assert ledger.charged_calls("echo") == 1

    def test_returns_safe_message_on_hard_reject(self) -> None:
        inner = _RecordingTool()
        wrapped = ToolBudgetGuardedTool(
            name=inner.name,
            description=inner.description,
            inner=inner,
        )
        ledger = ToolCallLedger(run_id="run-2")
        # Pre-fill the ledger so the next admit would exceed the cap.
        for index in range(2):
            ledger.started(f"prior-{index}", tool_name="echo")
        guard = ToolBudgetGuard(
            middleware=ToolBudgetMiddleware(
                [_budget(org_id=None, tool_name="echo", max_calls_per_run=2)]
            ),
            ledger=ledger,
        )
        token = ToolBudgetGuard.bind_for_run(guard)
        try:
            result = asyncio.run(wrapped._arun("hi"))
        finally:
            ToolBudgetGuard.unbind(token)
        # The safe message is what the model will see — it doesn't carry
        # the inner tool's output because the inner was never invoked.
        assert "echo" in result
        assert "budget" in result.lower()
        assert inner.calls == []  # inner tool short-circuited.

    def test_soft_warn_emits_budget_warning_and_admits(self) -> None:
        inner = _RecordingTool()
        wrapped = ToolBudgetGuardedTool(
            name=inner.name,
            description=inner.description,
            inner=inner,
        )
        producer = self._FakeProducer()
        ledger = ToolCallLedger(run_id="run-3")
        for index in range(2):
            ledger.started(f"prior-{index}", tool_name="echo")
        guard = ToolBudgetGuard(
            middleware=ToolBudgetMiddleware(
                [
                    _budget(
                        org_id=None,
                        tool_name="echo",
                        max_calls_per_run=2,
                        enforcement=ToolBudgetEnforcement.SOFT,
                    )
                ]
            ),
            ledger=ledger,
            run=_make_run(),
            event_producer=producer,
        )
        token = ToolBudgetGuard.bind_for_run(guard)
        try:
            result = asyncio.run(wrapped._arun("hi"))
        finally:
            ToolBudgetGuard.unbind(token)
        assert result == "echo-ok"
        # Inner tool ran (soft = admit) AND the warning was emitted.
        assert len(inner.calls) == 1
        assert len(producer.events) == 1
        emitted = producer.events[0]
        assert emitted["event_type"] is RuntimeApiEventType.BUDGET_WARNING
        assert emitted["source"] is StreamEventSource.SYSTEM
        payload = emitted["payload"]
        assert isinstance(payload, dict)
        assert payload["tool_name"] == "echo"
        assert payload["enforcement"] == "soft"


# --- registry wrapper --------------------------------------------------------


class _StaticRegistry:
    """Tool registry stub returning a fixed list."""

    def __init__(self, tools: tuple[object, ...]) -> None:
        self._tools = tools

    def list_available_tools(self, _context: object) -> tuple[object, ...]:
        return self._tools


class TestToolBudgetGuardedRegistry:
    def test_wraps_basetool_instances(self) -> None:
        inner = _RecordingTool()
        registry = ToolBudgetGuardedRegistry(inner=_StaticRegistry((inner,)))
        rendered = registry.list_available_tools(context=None)
        assert len(rendered) == 1
        wrapped = rendered[0]
        assert isinstance(wrapped, ToolBudgetGuardedTool)
        # Same name + description so the model surface is unchanged.
        assert wrapped.name == inner.name
        assert wrapped.description == inner.description

    def test_passes_through_non_basetool_objects(self) -> None:
        # Some adapters return internal descriptor objects rather than
        # full LangChain BaseTool instances. Those must not be wrapped
        # (the guard only knows how to gate BaseTool dispatch).
        sentinel = object()
        registry = ToolBudgetGuardedRegistry(inner=_StaticRegistry((sentinel,)))
        rendered = registry.list_available_tools(context=None)
        assert rendered == (sentinel,)

    def test_double_wrap_is_idempotent(self) -> None:
        inner = _RecordingTool()
        already_wrapped = ToolBudgetGuardedTool(
            name=inner.name,
            description=inner.description,
            inner=inner,
        )
        registry = ToolBudgetGuardedRegistry(inner=_StaticRegistry((already_wrapped,)))
        rendered = registry.list_available_tools(context=None)
        # The wrapper recognises its own kind and short-circuits.
        assert rendered[0] is already_wrapped


# --- persistence port snapshot ---------------------------------------------


class TestToolBudgetSnapshotLoader:
    def test_in_memory_seed_default_returns_global_row(self) -> None:
        store = InMemoryRuntimeApiStore()
        rows = store.list_tool_budgets_for_org(org_id="any-org")
        assert len(rows) == 1
        seed = rows[0]
        assert seed.id == "seed_default"
        assert seed.org_id is None
        assert seed.tool_name == "*"
        assert seed.enforcement == ToolBudgetEnforcement.HARD

    def test_per_org_row_is_returned_alongside_global(self) -> None:
        store = InMemoryRuntimeApiStore()
        store.tool_budgets["custom"] = ToolBudgetRecord(
            id="custom",
            org_id="org-y",
            tool_name="web_search",
            max_calls_per_run=3,
            enforcement=ToolBudgetEnforcement.HARD,
        )
        rows_for_org_y = store.list_tool_budgets_for_org(org_id="org-y")
        # Both rows visible.
        assert len(rows_for_org_y) == 2
        rows_for_other_org = store.list_tool_budgets_for_org(org_id="org-z")
        # The org-y row is invisible to org-z; only the global remains.
        assert len(rows_for_other_org) == 1
        assert rows_for_other_org[0].id == "seed_default"
