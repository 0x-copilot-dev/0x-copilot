"""B8 — wiring tests for :class:`ToolBudgetGuard` at the seam that enforces it.

The budget *decision* layer is exercised in ``test_tool_budget_middleware.py``;
this file pins the wiring layer — the guard as it behaves when a real tool call
crosses :class:`~agent_runtime.capabilities.middleware.runtime_tool_control.RuntimeControlMiddleware`,
which is the only budget gate on any shipped path:

- The guard passes a call through untouched when nothing is bound.
- It admits, charges the ledger, and annotates the model-visible result.
- It surfaces a hard-cap refusal as a typed ``ToolMessage`` rather than raising,
  and escalates to a run-fatal error only past the surfaced-rejection allowance.
- It admits + emits a ``BUDGET_WARNING`` event under soft enforcement.
- A declared ``content_and_artifact`` pair keeps the artifact out of the
  model-visible half.
- The persistence-port snapshot loader feeds the runtime correctly.

These drove ``ToolBudgetGuardedTool`` until it was deleted for being installed
nowhere; the guard behaviour they cover is unchanged, only the seam differs.
"""

from __future__ import annotations


from collections.abc import Callable
from typing import Any, cast

import pytest
from langchain.agents.middleware.types import ToolCallRequest
from langchain_core.messages import ToolMessage
from langchain_core.tools import BaseTool

from agent_runtime.capabilities.middleware.runtime_tool_control import (
    RuntimeControlMiddleware,
)
from agent_runtime.capabilities.tool_budget_guard import (
    ToolBudgetGuard,
    _Limits,
)
from agent_runtime.capabilities.tool_budget_middleware import ToolBudgetMiddleware
from agent_runtime.capabilities.task_policy import (
    RequestFingerprint,
    TaskFamily,
    TaskPolicyProfile,
    ToolOperationOutcome,
    ToolUseController,
    ToolUseDisposition,
    ToolUseFeedback,
)
from agent_runtime.control_plane.context import (
    TaskPolicyCapabilityProgress,
    TaskPolicyProgressProjection,
)
from agent_runtime.control_plane.feature_modes import FeatureMode
from agent_runtime.execution.contracts import StreamEventSource
from agent_runtime.context.memory import TokenBudgetPolicy
from agent_runtime.context.tool_result_admission import ToolResultAdmissionAdapter
from agent_runtime.execution.tool_errors import BudgetExceeded
from agent_runtime.execution.tool_refusals import ToolRefusal, ToolRefusals
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


class _ResultTool(BaseTool):
    """Return configurable content while recording whether dispatch occurred."""

    name: str = "echo"
    description: str = "Returns configured content for boundary tests."
    result: str
    call_count: int = 0

    def _run(self, *args: object, **kwargs: object) -> str:
        self.call_count += 1
        return self.result

    async def _arun(self, *args: object, **kwargs: object) -> str:
        self.call_count += 1
        return self.result


class _ContentAndArtifactTool(BaseTool):
    """Inner shaped like the built-in ``web_search`` and every MCP tool.

    Neither half of the declared pair is a string, which is what makes the
    note-append path interesting: :class:`ToolResultNote` finds nothing to
    extend and would insert the note as a third tuple element.
    """

    name: str = "echo"
    description: str = "Returns a declared (content, artifact) pair."
    response_format: str = "content_and_artifact"
    return_direct: bool = True
    tags: list[str] | None = ["builtin"]
    metadata: dict[str, object] | None = {"origin": "builtin"}
    extras: dict[str, object] | None = {"cache_control": {"type": "ephemeral"}}
    content: list[dict[str, str]] = []
    artifact: list[dict[str, str]] = []

    def _run(self, *_args: object, **_kwargs: object) -> tuple[object, object]:
        return self.content, self.artifact

    async def _arun(self, *_args: object, **_kwargs: object) -> tuple[object, object]:
        return self.content, self.artifact


class _ModelTurnStoppingController:
    def before_operation(self, _intent):
        return ToolUseFeedback(
            disposition=ToolUseDisposition.CONTINUE,
            reason_code="admitted",
        )

    def after_operation(self, _outcome):
        return ToolUseFeedback(
            disposition=ToolUseDisposition.CONTINUE,
            reason_code="completed",
        )

    def before_model_turn(self, **_kwargs):
        return ToolUseFeedback(
            disposition=ToolUseDisposition.STOP,
            reason_code="profile_model_turn_limit",
        )


class _AsyncDurableController:
    def __init__(
        self,
        observations: list[str],
        outcomes: list[ToolOperationOutcome] | None = None,
    ) -> None:
        self._observations = observations
        self._outcomes = outcomes

    async def before_operation(self, _intent):
        self._observations.append("intent_persisted")
        return ToolUseFeedback(
            disposition=ToolUseDisposition.CONTINUE,
            reason_code="admitted",
        )

    async def after_operation(self, outcome: ToolOperationOutcome):
        self._observations.append("outcome_persisted")
        if self._outcomes is not None:
            self._outcomes.append(outcome)
        return ToolUseFeedback(
            disposition=ToolUseDisposition.CONTINUE,
            reason_code="completed",
        )


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


class _SeamDispatchMixin:
    """Drive a tool the way the shipped runtime does — through the middleware.

    ``RuntimeControlMiddleware`` is the only budget gate on any shipped path,
    so it is the only place these guard behaviours can honestly be pinned. The
    handler hands the whole tool call to LangChain (``inner.ainvoke(tool_call)``)
    rather than calling ``_arun`` directly, because that is what ``ToolNode``
    does and it is what builds the ``ToolMessage`` — including splitting a
    declared ``content_and_artifact`` pair before the seam ever sees it.
    """

    @staticmethod
    def _request(
        tool: BaseTool | None,
        *,
        name: str = "echo",
        call_id: str = "call-1",
        args: dict[str, object] | None = None,
    ) -> ToolCallRequest:
        return ToolCallRequest(
            tool_call={
                "name": name,
                "args": {"input": "hi"} if args is None else args,
                "id": call_id,
                "type": "tool_call",
            },
            tool=tool,
            state={},
            runtime=cast(Any, object()),
        )

    @classmethod
    async def _adispatch(
        cls,
        inner: BaseTool,
        *,
        name: str = "echo",
        call_id: str = "call-1",
        args: dict[str, object] | None = None,
    ) -> ToolMessage:
        """Return the ``ToolMessage`` the seam publishes for one call."""

        request = cls._request(inner, name=name, call_id=call_id, args=args)

        async def handler(inner_request: ToolCallRequest) -> ToolMessage:
            return cast(ToolMessage, await inner.ainvoke(dict(inner_request.tool_call)))

        return cast(
            ToolMessage,
            await RuntimeControlMiddleware().awrap_tool_call(request, handler),
        )

    @classmethod
    def _dispatch(
        cls,
        inner: BaseTool,
        *,
        name: str = "echo",
        call_id: str = "call-1",
        args: dict[str, object] | None = None,
    ) -> ToolMessage:
        """Synchronous twin of :meth:`_adispatch`."""

        request = cls._request(inner, name=name, call_id=call_id, args=args)

        def handler(inner_request: ToolCallRequest) -> ToolMessage:
            return cast(ToolMessage, inner.invoke(dict(inner_request.tool_call)))

        return cast(
            ToolMessage,
            RuntimeControlMiddleware().wrap_tool_call(request, handler),
        )

    @staticmethod
    def _refusal(message: ToolMessage) -> ToolRefusal:
        """Assert ``message`` is a surfaced refusal and return its typed marker."""

        assert message.status == "error"
        refusal = ToolRefusals.read(message)
        assert refusal is not None, "a refusal must carry its typed marker"
        return refusal


class TestToolBudgetGuardAtTheSeam(_FakeProducerMixin, _SeamDispatchMixin):
    """Guard behaviour observed where it actually runs.

    Every one of these used to drive ``ToolBudgetGuardedTool``. The wrapper is
    gone; the guard is not, and neither is any behaviour below. The one thing
    that genuinely changed is how a refusal arrives: the wrapper raised out of
    the tool, which the error policy turned into a ``status="success"`` return
    published as ``completed``. The seam authors the message itself, so a
    refused call is an ``error`` carrying a typed marker.
    """

    async def test_passthrough_when_no_guard_bound(self) -> None:
        inner = _RecordingTool()

        message = await self._adispatch(inner)

        assert message.content == "echo-ok"
        # Inner tool was actually invoked; the seam didn't gate it.
        assert len(inner.calls) == 1

    def test_passthrough_when_no_guard_bound_sync(self) -> None:
        inner = _RecordingTool()

        message = self._dispatch(inner)

        assert message.content == "echo-ok"
        assert len(inner.calls) == 1

    async def test_admits_under_cap_and_records_into_ledger(self) -> None:
        inner = _RecordingTool()
        ledger = ToolCallLedger(run_id="run-1")
        guard = ToolBudgetGuard(
            middleware=ToolBudgetMiddleware(
                [_budget(org_id=None, tool_name="echo", max_calls_per_run=3)]
            ),
            ledger=ledger,
        )
        token = ToolBudgetGuard.bind_for_run(guard)
        try:
            message = await self._adispatch(inner)
        finally:
            ToolBudgetGuard.unbind(token)
        # The tool's own output leads; a low-headroom cap (3) also annotates it.
        assert message.content.startswith("echo-ok")
        # One admitted call landed on the ledger.
        assert ledger.charged_calls("echo") == 1

    async def test_task_policy_duplicate_refuses_before_inner_tool_dispatch(
        self,
    ) -> None:
        inner = _RecordingTool()
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
            first = await self._adispatch(inner, args={"input": "same request"})
            assert first.content.startswith("echo-ok")
            # ``ToolPolicyRejected`` is a ``ToolBudgetRejected``, so the seam
            # surfaces it as a refusal rather than raising it into the graph.
            repeat = await self._adispatch(inner, args={"input": "same request"})
        finally:
            ToolBudgetGuard.unbind(token)

        self._refusal(repeat)
        assert len(inner.calls) == 1
        assert ledger.charged_calls("echo") == 1

    async def test_task_policy_shadow_observes_duplicate_without_blocking(
        self,
    ) -> None:
        inner = _RecordingTool()
        profile = TaskPolicyProfile(
            profile_id="research",
            revision="v1",
            task_family=TaskFamily.PUBLIC_RESEARCH,
            enforce_exact_duplicates=True,
        )
        guard = ToolBudgetGuard(
            middleware=ToolBudgetMiddleware(()),
            ledger=ToolCallLedger(run_id="run-task-policy-shadow"),
            task_policy_controller=ToolUseController(profile=profile),
            task_request_fingerprint=RequestFingerprint(key=b"f" * 32),
            task_policy_mode=FeatureMode.SHADOW,
        )
        token = ToolBudgetGuard.bind_for_run(guard)
        try:
            first = await self._adispatch(inner, args={"input": "same request"})
            second = await self._adispatch(inner, args={"input": "same request"})
        finally:
            ToolBudgetGuard.unbind(token)

        assert first.content == "echo-ok"
        assert second.content == "echo-ok"
        assert len(inner.calls) == 2

    async def test_resume_overlay_preserves_prior_capability_budget_spend(
        self,
    ) -> None:
        inner = _RecordingTool()
        progress = TaskPolicyProgressProjection(
            profile_id="research",
            profile_revision="v1",
            task_family=TaskFamily.PUBLIC_RESEARCH.value,
            tool_calls_used=2,
            capabilities=(
                TaskPolicyCapabilityProgress(
                    capability_id="echo",
                    tool_calls_used=2,
                ),
            ),
        )
        guard = ToolBudgetGuard(
            middleware=ToolBudgetMiddleware(
                [_budget(org_id=None, tool_name="echo", max_calls_per_run=2)]
            ),
            ledger=ToolCallLedger(run_id="run-resumed-budget"),
            prior_task_policy_progress=progress,
        )
        token = ToolBudgetGuard.bind_for_run(guard)
        try:
            message = await self._adispatch(inner, args={"input": "after approval"})
        finally:
            ToolBudgetGuard.unbind(token)

        self._refusal(message)
        assert inner.calls == []

    def test_model_turn_limit_enforces_only_in_enforce_mode(self) -> None:
        enforce = ToolBudgetGuard(
            middleware=ToolBudgetMiddleware(()),
            ledger=ToolCallLedger(run_id="run-model-enforce"),
            task_policy_controller=_ModelTurnStoppingController(),
            task_request_fingerprint=RequestFingerprint(key=b"f" * 32),
            task_policy_mode=FeatureMode.ENFORCE,
        )
        with pytest.raises(BudgetExceeded, match="model-turn"):
            enforce.admit_model_turn(model_turn=3, execution_scope="supervisor")

        shadow = ToolBudgetGuard(
            middleware=ToolBudgetMiddleware(()),
            ledger=ToolCallLedger(run_id="run-model-shadow"),
            task_policy_controller=_ModelTurnStoppingController(),
            task_request_fingerprint=RequestFingerprint(key=b"f" * 32),
            task_policy_mode=FeatureMode.SHADOW,
        )
        shadow.admit_model_turn(model_turn=3, execution_scope="supervisor")

    async def test_async_controller_persists_admission_before_dispatch(
        self,
    ) -> None:
        observations: list[str] = []

        class _ObservedTool(_RecordingTool):
            async def _arun(self, *args: object, **kwargs: object) -> str:
                observations.append("tool_dispatched")
                return await super()._arun(*args, **kwargs)

        inner = _ObservedTool()
        guard = ToolBudgetGuard(
            middleware=ToolBudgetMiddleware(()),
            ledger=ToolCallLedger(run_id="run-async-controller"),
            task_policy_controller=_AsyncDurableController(observations),
            task_request_fingerprint=RequestFingerprint(key=b"f" * 32),
        )
        token = ToolBudgetGuard.bind_for_run(guard)
        try:
            assert (await self._adispatch(inner)).content == "echo-ok"
        finally:
            ToolBudgetGuard.unbind(token)

        assert observations == [
            "intent_persisted",
            "tool_dispatched",
            "outcome_persisted",
        ]

    async def test_generic_result_digest_is_advisory_not_source_identity(
        self,
    ) -> None:
        outcomes: list[ToolOperationOutcome] = []
        guard = ToolBudgetGuard(
            middleware=ToolBudgetMiddleware(()),
            ledger=ToolCallLedger(run_id="run-result-fallback"),
            task_policy_controller=_AsyncDurableController([], outcomes),
            task_request_fingerprint=RequestFingerprint(key=b"f" * 32),
        )
        token = ToolBudgetGuard.bind_for_run(guard)
        try:
            await self._adispatch(_RecordingTool())
        finally:
            ToolBudgetGuard.unbind(token)

        assert len(outcomes) == 1
        outcome = outcomes[0]
        assert outcome.result_fingerprint is not None
        assert outcome.evidence_fingerprint == outcome.result_fingerprint
        assert outcome.source_fingerprints == ()

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
        """A HARD-cap refusal is a tool result, not a run-ending error.

        Refusing the call is what bounds the spend — the inner tool never
        runs either way. Failing the run on top of that would additionally
        discard every tool result already gathered, which is why the cap is
        surfaced to the model instead: it finalizes with what it has.
        """

        inner = _RecordingTool()
        guard = self._capped_guard(run_id="run-2")
        token = ToolBudgetGuard.bind_for_run(guard)
        try:
            message = await self._adispatch(inner)
        finally:
            ToolBudgetGuard.unbind(token)

        refusal = self._refusal(message)
        assert refusal.code == "tool_budget_exceeded"
        assert "echo" in str(message.content)
        assert "budget" in str(message.content).lower()
        assert inner.calls == []  # inner tool short-circuited — spend is bounded.

    async def test_rejection_escalates_to_fatal_after_grace_exhausted(self) -> None:
        """A model that answers every refusal with another call still terminates.

        The allowance exists so a looping model cannot spin forever on free
        refusals; the first calls past the cap are surfaced, and only the ones
        beyond the allowance fail the run.
        """

        inner = _RecordingTool()
        guard = self._capped_guard(run_id="run-2b", max_surfaced_rejections=3)
        token = ToolBudgetGuard.bind_for_run(guard)
        try:
            for _ in range(3):
                self._refusal(await self._adispatch(inner))
            # Fourth refusal is past the allowance → run-fatal, and a fatal
            # error is raised into the graph rather than surfaced.
            with pytest.raises(BudgetExceeded):
                await self._adispatch(inner)
        finally:
            ToolBudgetGuard.unbind(token)
        assert inner.calls == []  # never executed, at any point.

    def test_hard_reject_is_surfaced_on_sync_path(self) -> None:
        """The sync dispatch path shares the async path's non-fatal behavior."""

        inner = _RecordingTool()
        guard = self._capped_guard(run_id="run-2c")
        token = ToolBudgetGuard.bind_for_run(guard)
        try:
            message = self._dispatch(inner)
        finally:
            ToolBudgetGuard.unbind(token)

        self._refusal(message)
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

        guard = ToolBudgetGuard(
            middleware=ToolBudgetMiddleware(
                [_budget(org_id=None, tool_name="echo", max_calls_per_run=10)]
            ),
            ledger=ToolCallLedger(run_id="run-note-0"),
        )
        token = ToolBudgetGuard.bind_for_run(guard)
        try:
            message = await self._adispatch(_RecordingTool())
        finally:
            ToolBudgetGuard.unbind(token)
        assert message.content == "echo-ok"

    @staticmethod
    def _offloading_guard(
        *,
        run_id: str,
        writer: Callable[[str], str],
    ) -> ToolBudgetGuard:
        return ToolBudgetGuard(
            middleware=ToolBudgetMiddleware(
                [_budget(org_id=None, tool_name="echo", max_calls_per_run=10)]
            ),
            ledger=ToolCallLedger(run_id=run_id),
            tool_result_admission=ToolResultAdmissionAdapter(
                writer,
                policy=TokenBudgetPolicy(
                    max_input_tokens=4_000,
                    recent_context_ratio=0.25,
                    summary_threshold_ratio=0.85,
                ),
            ),
        )

    async def test_bound_admission_offloads_before_async_result_reaches_model(
        self,
    ) -> None:
        unique_tail = "UNIQUE_RAW_TAIL"
        raw = ("oversized-result-" * 1_000) + unique_tail
        writes: list[str] = []
        reference = "/large_tool_results/async-result"
        inner = _ResultTool(result=raw)
        guard = self._offloading_guard(
            run_id="run-admission-async",
            writer=lambda content: writes.append(content) or reference,
        )

        token = ToolBudgetGuard.bind_for_run(guard)
        try:
            message = await self._adispatch(inner)
        finally:
            ToolBudgetGuard.unbind(token)

        assert inner.call_count == 1
        assert writes == [raw]
        content = str(message.content)
        assert reference in content
        assert unique_tail not in content
        assert len(content) <= 4_096

    def test_bound_admission_offloads_before_sync_result_reaches_model(self) -> None:
        unique_tail = "UNIQUE_SYNC_RAW_TAIL"
        raw = ("oversized-sync-result-" * 1_000) + unique_tail
        writes: list[str] = []
        reference = "/large_tool_results/sync-result"
        inner = _ResultTool(result=raw)
        guard = self._offloading_guard(
            run_id="run-admission-sync",
            writer=lambda content: writes.append(content) or reference,
        )

        token = ToolBudgetGuard.bind_for_run(guard)
        try:
            message = self._dispatch(inner)
        finally:
            ToolBudgetGuard.unbind(token)

        assert inner.call_count == 1
        assert writes == [raw]
        content = str(message.content)
        assert reference in content
        assert unique_tail not in content
        assert len(content) <= 4_096

    async def test_bound_admission_preserves_small_string_exactly(self) -> None:
        writes: list[str] = []
        inner = _ResultTool(result="small exact result")
        guard = self._offloading_guard(
            run_id="run-admission-inline",
            writer=lambda content: (
                writes.append(content) or "/large_tool_results/unused"
            ),
        )

        token = ToolBudgetGuard.bind_for_run(guard)
        try:
            message = await self._adispatch(inner)
        finally:
            ToolBudgetGuard.unbind(token)

        assert message.content == "small exact result"
        assert writes == []

    async def test_bound_admission_failure_never_falls_back_to_raw_result(
        self,
    ) -> None:
        raw = "oversized-sensitive-result-" * 1_000
        inner = _ResultTool(result=raw)

        def fail_offload(_content: str) -> str:
            raise OSError("offload unavailable")

        guard = self._offloading_guard(
            run_id="run-admission-failure",
            writer=fail_offload,
        )

        token = ToolBudgetGuard.bind_for_run(guard)
        try:
            with pytest.raises(OSError, match="offload unavailable"):
                await self._adispatch(inner)
        finally:
            ToolBudgetGuard.unbind(token)

        assert inner.call_count == 1

    async def test_counts_down_the_remaining_calls_as_the_cap_nears(self) -> None:
        """The model gets the remaining count as a planning signal.

        Without it the budget is invisible until the moment it refuses, so
        the model plans as if calls were unlimited and then stops abruptly
        with the work half-done.
        """

        inner = _RecordingTool()
        guard = ToolBudgetGuard(
            middleware=ToolBudgetMiddleware(
                [_budget(org_id=None, tool_name="echo", max_calls_per_run=4)]
            ),
            ledger=ToolCallLedger(run_id="run-note-1"),
        )
        token = ToolBudgetGuard.bind_for_run(guard)
        try:
            results = [
                str((await self._adispatch(inner, call_id=f"call-{index}")).content)
                for index in range(4)
            ]
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

        guard = ToolBudgetGuard(
            middleware=ToolBudgetMiddleware(
                [_budget(org_id=None, tool_name="echo", max_calls_per_run=1)]
            ),
            ledger=ToolCallLedger(run_id="run-note-2"),
        )
        token = ToolBudgetGuard.bind_for_run(guard)
        try:
            message = await self._adispatch(_RecordingTool())
        finally:
            ToolBudgetGuard.unbind(token)
        assert "this turn" in str(message.content)

    async def test_ungoverned_tool_gets_no_note(self) -> None:
        """With no budget there is no honest number to report."""

        guard = ToolBudgetGuard(
            middleware=ToolBudgetMiddleware(
                [_budget(org_id=None, tool_name="some_other_tool")]
            ),
            ledger=ToolCallLedger(run_id="run-note-3"),
        )
        token = ToolBudgetGuard.bind_for_run(guard)
        try:
            message = await self._adispatch(_RecordingTool())
        finally:
            ToolBudgetGuard.unbind(token)
        assert message.content == "echo-ok"

    async def test_rejection_names_the_requested_tool_not_the_wildcard(self) -> None:
        """A wildcard budget must still name the tool the model actually called.

        Reporting ``'*'`` names nothing the model can act on, and reads
        as a bug in the logs.
        """

        inner = _RecordingTool()
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
            message = await self._adispatch(inner)
        finally:
            ToolBudgetGuard.unbind(token)

        refusal = self._refusal(message)
        assert "'echo'" in refusal.safe_message
        assert "'*'" not in refusal.safe_message

    async def test_soft_warn_emits_budget_warning_and_admits(self) -> None:
        inner = _RecordingTool()
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
            message = await self._adispatch(inner)
        finally:
            ToolBudgetGuard.unbind(token)
        assert str(message.content).startswith("echo-ok")
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


class ContentAndArtifactBudgetMixin(_SeamDispatchMixin):
    """Builders for the declared-pair regression at the seam."""

    CONTENT: list[dict[str, str]] = [{"title": "T", "link": "https://example.test/a"}]
    ARTIFACT: list[dict[str, str]] = [{"raw": "payload", "secret": "artifact-only"}]
    TOOL_CALL_ID = "call_budget_1"

    def _inner(self) -> _ContentAndArtifactTool:
        return _ContentAndArtifactTool(content=self.CONTENT, artifact=self.ARTIFACT)

    def _bind_notifying_guard(self) -> object:
        # A cap of 3 leaves little headroom, so ``usage_note`` renders a
        # remaining-calls notice — the annotation this test is about.
        guard = ToolBudgetGuard(
            middleware=ToolBudgetMiddleware(
                [_budget(org_id=None, tool_name="echo", max_calls_per_run=3)]
            ),
            ledger=ToolCallLedger(run_id="run-pair"),
        )
        return ToolBudgetGuard.bind_for_run(guard)


class TestContentAndArtifactDispatchAtTheSeam(ContentAndArtifactBudgetMixin):
    """A declared ``content_and_artifact`` pair keeps its halves apart.

    The wrapper had to split the pair itself, because it sat *inside* the tool
    and saw the raw return. The seam sits outside LangChain's own unpacking, so
    by the time it annotates, ``content`` and ``artifact`` are already separate
    fields — and only ``content`` is ever touched. Same invariant, one fewer
    place to get it wrong.
    """

    async def test_the_artifact_never_reaches_the_model_visible_half(self) -> None:
        token = self._bind_notifying_guard()
        try:
            message = await self._adispatch(
                self._inner(), call_id=self.TOOL_CALL_ID, args={}
            )
        finally:
            ToolBudgetGuard.unbind(token)

        assert isinstance(message, ToolMessage)
        assert message.artifact == self.ARTIFACT
        assert "artifact-only" not in str(message.content)

    async def test_the_budget_note_lands_on_the_content_half_only(self) -> None:
        token = self._bind_notifying_guard()
        try:
            message = await self._adispatch(
                self._inner(), call_id=self.TOOL_CALL_ID, args={}
            )
        finally:
            ToolBudgetGuard.unbind(token)

        # Annotated, not replaced: the tool's own content still leads.
        assert "example.test" in str(message.content)
        # The artifact is passed through byte-identically — never annotated.
        assert message.artifact == self.ARTIFACT

    async def test_plain_content_tool_keeps_the_undivided_annotation_path(self) -> None:
        token = self._bind_notifying_guard()
        try:
            message = await self._adispatch(_RecordingTool())
        finally:
            ToolBudgetGuard.unbind(token)

        assert message.artifact is None
        assert str(message.content).startswith("echo-ok")


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
