"""Tests for :class:`ToolErrorPolicyTool` and :class:`ToolErrorPolicyRegistry`."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from langchain_core.tools import BaseTool
from pydantic import ConfigDict

from agent_runtime.capabilities.tool_error_policy_tool import (
    ToolErrorPolicyRegistry,
    ToolErrorPolicyTool,
)
from agent_runtime.execution.tool_errors import (
    BudgetExceeded,
    RunFatalToolError,
)


class _ExplosiveTool(BaseTool):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    name: str = "boom"
    description: str = "explodes"
    exc_factory: Any = None
    sync_result: str = "ok"
    async_result: str = "ok"

    def _run(self, *_: Any, **__: Any) -> Any:
        if self.exc_factory is not None:
            raise self.exc_factory("boom!")
        return self.sync_result

    async def _arun(self, *_: Any, **__: Any) -> Any:
        if self.exc_factory is not None:
            raise self.exc_factory("boom!")
        return self.async_result


def _wrap(inner: _ExplosiveTool) -> ToolErrorPolicyTool:
    return ToolErrorPolicyTool(
        name=inner.name,
        description=inner.description,
        inner=inner,
    )


class TestArunPolicy:
    async def test_run_fatal_propagates(self) -> None:
        wrapped = _wrap(_ExplosiveTool(exc_factory=BudgetExceeded))
        with pytest.raises(RunFatalToolError):
            await wrapped._arun()

    async def test_plain_exception_becomes_tool_output_string(self) -> None:
        wrapped = _wrap(_ExplosiveTool(exc_factory=ValueError))
        result = await wrapped._arun()
        assert isinstance(result, str)
        assert "ValueError" in result
        assert "boom!" in result

    async def test_cancelled_error_is_re_raised_not_classified(self) -> None:
        wrapped = _wrap(_ExplosiveTool(exc_factory=asyncio.CancelledError))
        with pytest.raises(asyncio.CancelledError):
            await wrapped._arun()

    async def test_keyboard_interrupt_is_re_raised(self) -> None:
        wrapped = _wrap(_ExplosiveTool(exc_factory=KeyboardInterrupt))
        with pytest.raises(KeyboardInterrupt):
            await wrapped._arun()

    async def test_success_path_returns_inner_value_unchanged(self) -> None:
        wrapped = _wrap(_ExplosiveTool(exc_factory=None, async_result="hello"))
        assert await wrapped._arun() == "hello"


class TestRunSyncPolicy:
    def test_plain_exception_becomes_tool_output_string(self) -> None:
        wrapped = _wrap(_ExplosiveTool(exc_factory=ValueError))
        result = wrapped._run()
        assert isinstance(result, str)
        assert "ValueError" in result

    def test_run_fatal_propagates(self) -> None:
        wrapped = _wrap(_ExplosiveTool(exc_factory=BudgetExceeded))
        with pytest.raises(RunFatalToolError):
            wrapped._run()


class TestRegistry:
    def test_registry_wraps_basetools_only(self) -> None:
        class _InnerRegistry:
            def list_available_tools(self, _ctx: object) -> tuple[object, ...]:
                return (_ExplosiveTool(), object())  # second one is not a BaseTool

        wrapped_registry = ToolErrorPolicyRegistry(inner=_InnerRegistry())
        tools = wrapped_registry.list_available_tools(None)
        assert isinstance(tools[0], ToolErrorPolicyTool)
        assert not isinstance(tools[1], BaseTool)

    def test_registry_does_not_double_wrap(self) -> None:
        already = _wrap(_ExplosiveTool())

        class _InnerRegistry:
            def list_available_tools(self, _ctx: object) -> tuple[object, ...]:
                return (already,)

        wrapped_registry = ToolErrorPolicyRegistry(inner=_InnerRegistry())
        tools = wrapped_registry.list_available_tools(None)
        assert tools[0] is already
