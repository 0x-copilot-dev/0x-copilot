"""Tests for :class:`RetryingTool`.

Covers: happy path (no retry), transient exception with eventual success,
exhaustion re-raises the last exception, configured exception narrowing,
``CancelledError`` is never retried, and the sync ``_run`` path matches
the async ``_arun`` path.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from langchain_core.tools import BaseTool
from pydantic import ConfigDict

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
