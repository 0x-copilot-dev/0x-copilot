"""Tests for :class:`RetryingTool`.

Covers: happy path (no retry), transient exception with eventual success,
exhaustion re-raises the last exception, configured exception narrowing,
``CancelledError`` is never retried, the sync ``_run`` path matches the async
``_arun`` path, the tenacity-driven full-jitter exponential backoff schedule,
and the structured ``tool_retry`` log emitted before each backoff.
"""

from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace
from typing import Any

import pytest
from langchain_core.tools import BaseTool
from pydantic import ConfigDict
from tenacity import wait_random_exponential

from agent_runtime.capabilities.retrying_tool import RetryingTool


class _FlakyTool(BaseTool):
    """Test tool that raises ``fail_first`` times before returning ``ok``."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    name: str = "flaky"
    description: str = "test"
    fail_first: int = 0
    exc_factory: Any = ValueError
    calls: int = 0

    def _run(self, *_: Any, **__: Any) -> str:
        self.calls += 1
        if self.calls <= self.fail_first:
            raise self.exc_factory(f"transient #{self.calls}")
        return "ok"

    async def _arun(self, *_: Any, **__: Any) -> str:
        self.calls += 1
        if self.calls <= self.fail_first:
            raise self.exc_factory(f"transient #{self.calls}")
        return "ok"


def _wrap(tool: _FlakyTool, **overrides: Any) -> RetryingTool:
    return RetryingTool(
        name=tool.name,
        description=tool.description,
        inner=tool,
        max_attempts=overrides.pop("max_attempts", 3),
        initial_backoff_seconds=overrides.pop("initial_backoff_seconds", 0.0),
        max_backoff_seconds=overrides.pop("max_backoff_seconds", 0.0),
        **overrides,
    )


class TestArun:
    async def test_no_retry_when_inner_succeeds(self) -> None:
        inner = _FlakyTool(fail_first=0)
        wrapped = _wrap(inner)
        assert await wrapped._arun() == "ok"
        assert inner.calls == 1

    async def test_recovers_within_attempts(self) -> None:
        inner = _FlakyTool(fail_first=2)
        wrapped = _wrap(inner, max_attempts=3)
        assert await wrapped._arun() == "ok"
        assert inner.calls == 3

    async def test_reraises_last_exception_after_exhaustion(self) -> None:
        inner = _FlakyTool(fail_first=99)
        wrapped = _wrap(inner, max_attempts=2)
        with pytest.raises(ValueError, match="transient #2"):
            await wrapped._arun()
        assert inner.calls == 2

    async def test_does_not_retry_outside_configured_types(self) -> None:
        inner = _FlakyTool(fail_first=99, exc_factory=TypeError)
        wrapped = _wrap(inner, retry_exceptions=(ValueError,))
        with pytest.raises(TypeError):
            await wrapped._arun()
        assert inner.calls == 1

    async def test_cancelled_error_is_never_retried(self) -> None:
        inner = _FlakyTool(fail_first=99, exc_factory=asyncio.CancelledError)
        wrapped = _wrap(inner, retry_exceptions=(BaseException,))
        with pytest.raises(asyncio.CancelledError):
            await wrapped._arun()
        assert inner.calls == 1


class TestRunSync:
    def test_recovers_within_attempts(self) -> None:
        inner = _FlakyTool(fail_first=1)
        wrapped = _wrap(inner, max_attempts=3)
        assert wrapped._run() == "ok"
        assert inner.calls == 2

    def test_reraises_last_exception_after_exhaustion(self) -> None:
        inner = _FlakyTool(fail_first=99)
        wrapped = _wrap(inner, max_attempts=2)
        with pytest.raises(ValueError):
            wrapped._run()
        assert inner.calls == 2


class TestSurfacePropagation:
    def test_wrapper_inherits_inner_name_and_description(self) -> None:
        inner = _FlakyTool(fail_first=0)
        wrapped = RetryingTool(
            name=inner.name,
            description="custom",
            inner=inner,
        )
        assert wrapped.name == inner.name
        assert wrapped.description == "custom"


class TestBackoffSchedule:
    """The retry loop uses tenacity's full-jitter exponential backoff.

    ``wait_random_exponential`` yields ``uniform(0, min(initial * 2 ** (n-1),
    max))``. The assertions below pin that exact envelope — capped, jittered,
    and geometrically widening — so a regression to a fixed or un-jittered wait
    (or a wrong multiplier/cap) fails loudly.
    """

    def test_wait_strategy_is_full_jitter_exponential(self) -> None:
        wrapped = _wrap(
            _FlakyTool(),
            initial_backoff_seconds=0.5,
            max_backoff_seconds=4.0,
        )
        wait = wrapped._wait()
        assert isinstance(wait, wait_random_exponential)
        assert wait.multiplier == 0.5
        assert wait.max == 4.0

    def test_backoff_envelope_is_capped_jittered_and_growing(self) -> None:
        wrapped = _wrap(
            _FlakyTool(),
            initial_backoff_seconds=0.5,
            max_backoff_seconds=4.0,
        )
        wait = wrapped._wait()
        # (attempt_number, previous cap, this attempt's cap).
        schedule = [
            (1, 0.0, 0.5),
            (2, 0.5, 1.0),
            (3, 1.0, 2.0),
            (4, 2.0, 4.0),
            (5, 4.0, 4.0),  # capped: initial * 2**4 == 8.0 clamps to max.
        ]
        for attempt, prev_cap, cap in schedule:
            samples = [
                wait(SimpleNamespace(attempt_number=attempt)) for _ in range(200)
            ]
            # Every draw sits inside the exponential cap for this attempt.
            assert all(0.0 <= s <= cap for s in samples)
            # Jittered, not a fixed wait: draws vary across the window.
            assert len({round(s, 9) for s in samples}) > 1
            # The window widened this attempt (until the cap is reached).
            if cap > prev_cap:
                assert max(samples) > prev_cap


class TestRetryLogging:
    """The ``before_sleep`` hook emits a structured ``tool_retry`` event."""

    async def test_logs_tool_retry_before_each_backoff(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        inner = _FlakyTool(fail_first=2)
        wrapped = _wrap(inner, max_attempts=3)
        with caplog.at_level(
            logging.INFO, logger="agent_runtime.capabilities.retrying_tool"
        ):
            assert await wrapped._arun() == "ok"
        retries = [r for r in caplog.records if r.msg == "tool_retry"]
        # One log per retried (failed-then-slept) attempt — not the final success.
        assert [r.metadata["attempt"] for r in retries] == [1, 2]
        assert all(r.metadata["max_attempts"] == 3 for r in retries)
        assert all(r.metadata["error_class"] == "ValueError" for r in retries)
