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


import pytest
from langchain_core.tools import BaseTool

from agent_runtime.capabilities.tool_budget_guard import (
    ToolBudgetGuard,
    ToolBudgetGuardedRegistry,
    ToolBudgetGuardedTool,
    guard_model_tools,
    _Limits,
)
from agent_runtime.capabilities.tool_budget_middleware import ToolBudgetMiddleware
from agent_runtime.capabilities.task_policy import (
    RequestFingerprint,
    TaskFamily,
    TaskPolicyProfile,
    ToolPolicyRejected,
    ToolUseController,
)
from agent_runtime.execution.contracts import StreamEventSource
from agent_runtime.execution.tool_errors import (
    BudgetExceeded,
    RunFatalToolError,
    ToolBudgetRejected,
)
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

    async def test_passthrough_when_no_guard_bound_async(self) -> None:
        inner = _RecordingTool()
        wrapped = ToolBudgetGuardedTool(
            name=inner.name,
            description=inner.description,
            inner=inner,
        )
        result = await wrapped._arun("hello")
        assert result == "echo-ok"
        assert len(inner.calls) == 1

    async def test_admits_under_cap_and_records_into_ledger(self) -> None:
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
            result = await wrapped._arun("hi")
        finally:
            ToolBudgetGuard.unbind(token)
        # The tool's own output leads; a low-headroom cap (3) also annotates it.
        assert result.startswith("echo-ok")
        # One admitted call landed on the ledger.
        assert ledger.charged_calls("echo") == 1

    async def test_task_policy_duplicate_refuses_before_inner_tool_dispatch(
        self,
    ) -> None:
        inner = _RecordingTool()
        wrapped = ToolBudgetGuardedTool(
            name=inner.name,
            description=inner.description,
            inner=inner,
        )
        ledger = ToolCallLedger(run_id="run-task-policy")
        controller = ToolUseController(
            profile=TaskPolicyProfile(
                profile_id="research",
                revision="v1",
                task_family=TaskFamily.PUBLIC_RESEARCH,
                enforce_exact_duplicates=True,
            )
        )
        guard = ToolBudgetGuard(
            middleware=ToolBudgetMiddleware(
                [_budget(org_id=None, tool_name="echo", max_calls_per_run=3)]
            ),
            ledger=ledger,
            task_policy_controller=controller,
            task_request_fingerprint=RequestFingerprint(key=b"f" * 32),
        )
        token = ToolBudgetGuard.bind_for_run(guard)
        try:
            assert (await wrapped._arun("same request")).startswith("echo-ok")
            with pytest.raises(ToolPolicyRejected):
                await wrapped._arun("same request")
        finally:
            ToolBudgetGuard.unbind(token)

        assert len(inner.calls) == 1
        assert ledger.charged_calls("echo") == 1

    @staticmethod
    def _capped_guard(
        *,
        run_id: str,
        cap: int = 2,
        max_surfaced_rejections: int | None = None,
    ) -> ToolBudgetGuard:
        """Build a guard whose ``echo`` budget is already fully consumed."""

        ledger = ToolCallLedger(run_id=run_id)
        for index in range(cap):
            ledger.started(f"prior-{index}", tool_name="echo", budget_scoped=True)
        return ToolBudgetGuard(
            middleware=ToolBudgetMiddleware(
                [_budget(org_id=None, tool_name="echo", max_calls_per_run=cap)]
            ),
            ledger=ledger,
            max_surfaced_rejections=max_surfaced_rejections,
        )

    async def test_hard_reject_is_surfaced_not_fatal(self) -> None:
        """HARD-cap rejection raises the NON-fatal ``ToolBudgetRejected``.

        Refusing the call is what bounds the spend — the inner tool never
        runs either way. Raising a run-fatal error on top of that would
        additionally discard every tool result the run had already
        gathered, which is why the cap is surfaced to the model instead:
        the policy turns it into a ``ToolMessage`` and the model
        finalizes with what it has.
        """

        inner = _RecordingTool()
        wrapped = ToolBudgetGuardedTool(
            name=inner.name,
            description=inner.description,
            inner=inner,
        )
        guard = self._capped_guard(run_id="run-2")
        token = ToolBudgetGuard.bind_for_run(guard)
        try:
            with pytest.raises(ToolBudgetRejected) as caught:
                await wrapped._arun("hi")
        finally:
            ToolBudgetGuard.unbind(token)
        # Non-fatal: the run handler must not treat this as terminal.
        assert not isinstance(caught.value, RunFatalToolError)
        assert "echo" in caught.value.safe_summary
        assert "budget" in caught.value.safe_summary.lower()
        assert inner.calls == []  # inner tool short-circuited — spend is bounded.

    async def test_rejection_escalates_to_fatal_after_grace_exhausted(self) -> None:
        """A model that answers every refusal with another call still terminates.

        The allowance exists so a looping model cannot spin forever on
        free refusals; the first calls past the cap are surfaced, and
        only the ones beyond the allowance fail the run.
        """

        inner = _RecordingTool()
        wrapped = ToolBudgetGuardedTool(
            name=inner.name,
            description=inner.description,
            inner=inner,
        )
        guard = self._capped_guard(run_id="run-2b", max_surfaced_rejections=3)
        token = ToolBudgetGuard.bind_for_run(guard)
        try:
            for _ in range(3):
                with pytest.raises(ToolBudgetRejected):
                    await wrapped._arun("hi")
            # Fourth refusal is past the allowance → run-fatal.
            with pytest.raises(BudgetExceeded):
                await wrapped._arun("hi")
        finally:
            ToolBudgetGuard.unbind(token)
        assert inner.calls == []  # never executed, at any point.

    def test_hard_reject_is_surfaced_on_sync_path(self) -> None:
        """The sync dispatch path shares the async path's non-fatal behavior."""

        inner = _RecordingTool()
        wrapped = ToolBudgetGuardedTool(
            name=inner.name,
            description=inner.description,
            inner=inner,
        )
        guard = self._capped_guard(run_id="run-2c")
        token = ToolBudgetGuard.bind_for_run(guard)
        try:
            with pytest.raises(ToolBudgetRejected):
                wrapped._run("hi")
        finally:
            ToolBudgetGuard.unbind(token)
        assert inner.calls == []

    def test_default_allowance_leaves_room_for_a_parallel_fan_out(self) -> None:
        """The shipped allowance must exceed a single parallel tool batch.

        If it were 1, one turn that fans out several calls would exhaust
        the allowance before the model ever got a turn to write its
        answer — reintroducing the dead run this guard exists to avoid.
        """

        assert _Limits.MAX_SURFACED_REJECTIONS >= 4

    async def test_result_stays_clean_while_there_is_headroom(self) -> None:
        """No note until the tail is in sight — every result would be noise."""

        inner = _RecordingTool()
        wrapped = ToolBudgetGuardedTool(
            name=inner.name, description=inner.description, inner=inner
        )
        guard = ToolBudgetGuard(
            middleware=ToolBudgetMiddleware(
                [_budget(org_id=None, tool_name="echo", max_calls_per_run=10)]
            ),
            ledger=ToolCallLedger(run_id="run-note-0"),
        )
        token = ToolBudgetGuard.bind_for_run(guard)
        try:
            result = await wrapped._arun("hi")
        finally:
            ToolBudgetGuard.unbind(token)
        assert result == "echo-ok"

    async def test_counts_down_the_remaining_calls_as_the_cap_nears(self) -> None:
        """The model gets the remaining count as a planning signal.

        Without it the budget is invisible until the moment it refuses, so
        the model plans as if calls were unlimited and then stops abruptly
        with the work half-done.
        """

        inner = _RecordingTool()
        wrapped = ToolBudgetGuardedTool(
            name=inner.name, description=inner.description, inner=inner
        )
        guard = ToolBudgetGuard(
            middleware=ToolBudgetMiddleware(
                [_budget(org_id=None, tool_name="echo", max_calls_per_run=4)]
            ),
            ledger=ToolCallLedger(run_id="run-note-1"),
        )
        token = ToolBudgetGuard.bind_for_run(guard)
        try:
            results = [await wrapped._arun("hi") for _ in range(4)]
        finally:
            ToolBudgetGuard.unbind(token)

        # First call still has 3 left — at the threshold, so it reports.
        assert "3 calls left" in results[0]
        assert "2 calls left" in results[1]
        # Singular at one remaining — the note is read by a model, not parsed.
        assert "1 call left" in results[2]
        assert "None left" in results[3]
        assert "Do not call it again" in results[3]
        # The tool's own output is never replaced, only annotated.
        assert all(r.startswith("echo-ok") for r in results)

    async def test_note_says_the_count_is_per_turn(self) -> None:
        """A run IS a turn — the model must not carry an exhausted budget over."""

        inner = _RecordingTool()
        wrapped = ToolBudgetGuardedTool(
            name=inner.name, description=inner.description, inner=inner
        )
        guard = ToolBudgetGuard(
            middleware=ToolBudgetMiddleware(
                [_budget(org_id=None, tool_name="echo", max_calls_per_run=1)]
            ),
            ledger=ToolCallLedger(run_id="run-note-2"),
        )
        token = ToolBudgetGuard.bind_for_run(guard)
        try:
            result = await wrapped._arun("hi")
        finally:
            ToolBudgetGuard.unbind(token)
        assert "this turn" in result

    async def test_ungoverned_tool_gets_no_note(self) -> None:
        """With no budget there is no honest number to report."""

        inner = _RecordingTool()
        wrapped = ToolBudgetGuardedTool(
            name=inner.name, description=inner.description, inner=inner
        )
        guard = ToolBudgetGuard(
            middleware=ToolBudgetMiddleware(
                [_budget(org_id=None, tool_name="some_other_tool")]
            ),
            ledger=ToolCallLedger(run_id="run-note-3"),
        )
        token = ToolBudgetGuard.bind_for_run(guard)
        try:
            result = await wrapped._arun("hi")
        finally:
            ToolBudgetGuard.unbind(token)
        assert result == "echo-ok"

    async def test_rejection_names_the_requested_tool_not_the_wildcard(self) -> None:
        """A wildcard budget must still name the tool the model actually called.

        Reporting ``'*'`` names nothing the model can act on, and reads
        as a bug in the logs.
        """

        inner = _RecordingTool()
        wrapped = ToolBudgetGuardedTool(
            name=inner.name,
            description=inner.description,
            inner=inner,
        )
        ledger = ToolCallLedger(run_id="run-2d")
        ledger.started("prior-0", tool_name="echo", budget_scoped=True)
        guard = ToolBudgetGuard(
            middleware=ToolBudgetMiddleware(
                [_budget(org_id=None, tool_name="*", max_calls_per_run=1)]
            ),
            ledger=ledger,
        )
        token = ToolBudgetGuard.bind_for_run(guard)
        try:
            with pytest.raises(ToolBudgetRejected) as caught:
                await wrapped._arun("hi")
        finally:
            ToolBudgetGuard.unbind(token)
        assert "'echo'" in caught.value.safe_summary
        assert "'*'" not in caught.value.safe_summary

    async def test_soft_warn_emits_budget_warning_and_admits(self) -> None:
        inner = _RecordingTool()
        wrapped = ToolBudgetGuardedTool(
            name=inner.name,
            description=inner.description,
            inner=inner,
        )
        producer = self._FakeProducer()
        ledger = ToolCallLedger(run_id="run-3")
        for index in range(2):
            ledger.started(f"prior-{index}", tool_name="echo", budget_scoped=True)
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
            result = await wrapped._arun("hi")
        finally:
            ToolBudgetGuard.unbind(token)
        assert result.startswith("echo-ok")
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


def test_full_model_surface_wrapper_is_idempotent_for_injected_tools() -> None:
    """Factory-injected tools must receive the same guard as registry tools."""

    original = _RecordingTool()
    once = guard_model_tools([original])
    twice = guard_model_tools(list(once))

    assert isinstance(once[0], ToolBudgetGuardedTool)
    assert twice == once


# --- persistence port snapshot ---------------------------------------------


class TestToolBudgetSnapshotLoader:
    async def test_in_memory_seed_default_returns_global_row(self) -> None:
        store = InMemoryRuntimeApiStore()
        rows = await store.list_tool_budgets_for_org(org_id="any-org")
        assert len(rows) == 1
        seed = rows[0]
        assert seed.id == "seed_default"
        assert seed.org_id is None
        assert seed.tool_name == "*"
        assert seed.enforcement == ToolBudgetEnforcement.HARD

    async def test_per_org_row_is_returned_alongside_global(self) -> None:
        store = InMemoryRuntimeApiStore()
        store.tool_budgets["custom"] = ToolBudgetRecord(
            id="custom",
            org_id="org-y",
            tool_name="web_search",
            max_calls_per_run=3,
            enforcement=ToolBudgetEnforcement.HARD,
        )
        rows_for_org_y = await store.list_tool_budgets_for_org(org_id="org-y")
        # Both rows visible.
        assert len(rows_for_org_y) == 2
        rows_for_other_org = await store.list_tool_budgets_for_org(org_id="org-z")
        # The org-y row is invisible to org-z; only the global remains.
        assert len(rows_for_other_org) == 1
        assert rows_for_other_org[0].id == "seed_default"
