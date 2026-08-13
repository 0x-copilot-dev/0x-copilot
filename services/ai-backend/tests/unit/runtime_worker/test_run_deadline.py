"""The run-level wall clock: it fires, it is distinguishable, and it still seals.

``ModelConfig.timeout_seconds`` and ``run_deadline_seconds`` both surface as
:class:`TimeoutError` from the same ``await``. That is exactly why the second one
is easy to ship broken — a deadline that fires but reports ``run_timeout`` is
indistinguishable from a slow model call, and nothing in a green suite would say
so. Hence the pair of worker tests below: one drives the run-level clock with a
*generous* per-call timeout, the other drives the per-call timeout with a
*generous* deadline, and each asserts the reason the OTHER one must not produce.

The seal assertions matter for a separate reason. The run's terminal event seals
the causal prefix ``[1..N]`` (:mod:`agent_runtime.api.ledger_seal`), and SSE close
semantics hang off it. A deadline that terminated a run by some shortcut around
:class:`RunTerminationCoordinator` would still turn the run red — and would still
look correct in a status assertion — while leaving the stream unsealed.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from datetime import datetime, timedelta, timezone

import pytest

from agent_runtime.api.run_termination import TerminationReason
from agent_runtime.execution.contracts import AgentRuntimeContext, RuntimeDependencies
from agent_runtime.execution.factory import RuntimeHarness
from agent_runtime.execution.run_deadline import RunDeadline
from agent_runtime.settings import RuntimeSettings
from runtime_adapters.in_memory import InMemoryRuntimeApiStore
from runtime_api.schemas import AgentRunStatus
from runtime_worker.handlers.run import RuntimeRunHandler
from runtime_worker.loop import RuntimeWorker
from tests.unit.runtime_worker.test_runtime_worker import _TestHelpers


class TestRunDeadlineBudget:
    """The pure budget object, exercised without a worker in sight."""

    def test_disabled_deadline_never_fires(self) -> None:
        deadline = RunDeadline.disabled()

        assert deadline.seconds is None
        assert deadline.remaining_seconds() is None
        assert deadline.expired is False

    def test_remaining_is_measured_from_the_anchor_not_from_now(self) -> None:
        """A re-claim must inherit spent time, not restart the budget.

        This is the difference between a deadline and a per-attempt timeout: if
        ``remaining`` were measured from "now" on each claim, a wedged run would
        get a fresh full budget forever while a deadline appeared configured.
        """

        anchor = datetime.now(timezone.utc) - timedelta(seconds=30)
        deadline = RunDeadline(seconds=100.0, anchor=anchor)

        remaining = deadline.remaining_seconds()

        assert remaining is not None
        assert 69.0 < remaining < 71.0

    def test_remaining_clips_at_zero_rather_than_going_negative(self) -> None:
        anchor = datetime.now(timezone.utc) - timedelta(seconds=500)
        deadline = RunDeadline(seconds=10.0, anchor=anchor)

        assert deadline.remaining_seconds() == 0.0

    async def test_an_already_blown_deadline_fails_before_running_the_body(
        self,
    ) -> None:
        """``asyncio.timeout(0)`` only fires at a suspension point.

        A body that returns without awaiting would sail straight past an
        already-exceeded deadline, so the guard has to refuse up front.
        """

        anchor = datetime.now(timezone.utc) - timedelta(seconds=500)
        deadline = RunDeadline(seconds=10.0, anchor=anchor)
        body_ran = False

        with pytest.raises(TimeoutError):
            async with deadline.scope():
                body_ran = True

        assert body_ran is False
        assert deadline.expired is True

    async def test_expired_stays_false_when_an_inner_timeout_fires(self) -> None:
        """The single assertion the whole distinguishability story rests on.

        An inner (per-call) timeout raises the same ``TimeoutError`` from the
        same ``await``. If ``expired`` were merely "a TimeoutError happened",
        every slow model call would be reported as a blown run deadline.
        """

        deadline = RunDeadline(seconds=30.0)

        with pytest.raises(TimeoutError):
            async with deadline.scope():
                async with asyncio.timeout(0.01):
                    await asyncio.sleep(5)

        assert deadline.expired is False

    async def test_expired_is_true_when_the_outer_deadline_fires(self) -> None:
        deadline = RunDeadline(seconds=0.05)

        with pytest.raises(TimeoutError):
            async with deadline.scope():
                await asyncio.sleep(5)

        assert deadline.expired is True

    def test_a_non_positive_budget_is_rejected_rather_than_silently_disabling(
        self,
    ) -> None:
        with pytest.raises(ValueError):
            RunDeadline(seconds=0)


def _settings(*, run_deadline_seconds: float) -> RuntimeSettings:
    """Worker settings with the run-level clock set explicitly.

    ``SURFACES_V2`` is off for the same reason the core worker suite turns it
    off: this asserts the base lifecycle sequence, and the v2 receipt path adds
    events of its own.
    """

    return RuntimeSettings.load(
        environ={
            "OPENAI_API_KEY": "sk-test",
            "RUNTIME_DEFAULT_PROVIDER": "openai",
            "RUNTIME_DEFAULT_MODEL": "gpt-5.4-mini",
            "RUNTIME_MAX_RETRIES": "1",
            "SURFACES_V2": "false",
            "COPILOT_HP__EXECUTION__RUN_DEADLINE_SECONDS": str(run_deadline_seconds),
        }
    )


def _fake_agent_factory(
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


#: How long the stream below takes to produce anything. Must sit strictly
#: between the tight budget under test (0.2s) and the generous one (30s / 600s):
#: long enough that the tight clock always wins, short enough that removing the
#: clock under test makes the run *complete* in about two seconds rather than
#: racing the generous budget it was supposed to be safely under.
_STREAM_DELAY_SECONDS = 2.0


async def _slow_streamer(
    _harness: RuntimeHarness,
    _messages: Sequence[object],
):
    """A stream that outlives the tight budget but not the generous one.

    Deliberately yields nothing before sleeping: the run must terminate on a
    clock, not on the stream running dry.
    """

    await asyncio.sleep(_STREAM_DELAY_SECONDS)
    yield {"type": "values", "ns": (), "data": {"messages": []}}


async def _drive_run(
    *,
    run_deadline_seconds: float,
    model_timeout_seconds: float,
) -> tuple[InMemoryRuntimeApiStore, str]:
    store = InMemoryRuntimeApiStore()
    settings = _settings(run_deadline_seconds=run_deadline_seconds)
    run_id = await _TestHelpers.create_queued_run(
        store,
        settings,
        model={
            "provider": "openai",
            "model_name": "gpt-5.4-mini",
            "timeout_seconds": model_timeout_seconds,
        },
    )
    worker = RuntimeWorker(
        persistence=store,
        event_store=store,
        queue=store,
        settings=settings,
        retry_delay_seconds=0,
        run_handler=RuntimeRunHandler(
            persistence=store,
            event_store=store,
            settings=settings,
            agent_factory=_fake_agent_factory,
            runtime_streamer=_slow_streamer,
        ),
    )

    await worker.run_until_idle()

    return store, run_id


class TestRunDeadlineTerminatesTheRun:
    async def test_a_run_past_its_wall_clock_fails_with_the_distinct_reason(
        self,
    ) -> None:
        """Fails on unpatched code by *completing*.

        With no run-level clock the per-call timeout is the only bound, and here
        it is deliberately generous (30s) — so the unpatched worker sits through
        the whole stream and ends ``run_completed``. There is no assertion to
        weaken into a pass: the terminal event is the wrong event entirely.
        """

        store, run_id = await _drive_run(
            run_deadline_seconds=0.2,
            model_timeout_seconds=30.0,
        )

        events = store.events_by_run[run_id]
        terminal = events[-1]
        assert terminal.event_type == "run_failed"
        assert terminal.payload["reason"] == TerminationReason.RUN_DEADLINE_EXCEEDED
        # The reason the per-call timeout would have produced. Asserted as a
        # non-equality so a future collapse of the two reasons into one fails
        # here rather than quietly degrading the user-facing message.
        assert terminal.payload["reason"] != TerminationReason.RUN_TIMEOUT
        assert store.runs[run_id].status == AgentRunStatus.TIMED_OUT

    async def test_the_deadline_terminal_event_still_seals_the_causal_prefix(
        self,
    ) -> None:
        """A shortcut around ``RunTerminationCoordinator`` would pass a status
        assertion and still leave the stream unsealed, so assert the seal shape
        directly: one terminal event, last, at the end of a contiguous [1..N].

        The seal shape alone holds for a *completed* run too, so it cannot tell
        on its own whether the deadline did anything — it has to be pinned to
        the deadline's own terminal event, or this passes with the wall clock
        removed entirely.
        """

        store, run_id = await _drive_run(
            run_deadline_seconds=0.2,
            model_timeout_seconds=30.0,
        )

        events = store.events_by_run[run_id]
        assert events[-1].event_type == "run_failed"
        assert events[-1].payload["reason"] == TerminationReason.RUN_DEADLINE_EXCEEDED
        sequences = [event.sequence_no for event in events]
        assert sequences == list(range(1, len(events) + 1))

        terminal_types = {"run_failed", "run_completed", "run_cancelled"}
        terminal_positions = [
            index
            for index, event in enumerate(events)
            if event.event_type in terminal_types
        ]
        # Exactly one terminal event, and it is the last thing in the ledger —
        # nothing causal may land after the seal.
        assert terminal_positions == [len(events) - 1]

    async def test_a_slow_single_call_still_reports_run_timeout(self) -> None:
        """The other half of the distinction, and the regression guard.

        Same stream, same worker; only which clock is tight changes. If the
        deadline scope were to swallow the per-call timeout — or if ``expired``
        were implemented as "a TimeoutError reached the handler" — this run
        would mis-report as ``run_deadline_exceeded``.
        """

        store, run_id = await _drive_run(
            run_deadline_seconds=600.0,
            model_timeout_seconds=0.2,
        )

        events = store.events_by_run[run_id]
        terminal = events[-1]
        assert terminal.event_type == "run_failed"
        assert terminal.payload["reason"] == TerminationReason.RUN_TIMEOUT
        assert store.runs[run_id].status == AgentRunStatus.TIMED_OUT
