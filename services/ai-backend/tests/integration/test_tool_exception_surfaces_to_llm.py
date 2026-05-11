"""Repro of PRD §1.2 — tool exceptions become observable input to the LLM.

Before this PRD: an uncaught exception from any tool short-circuited
the agent loop straight to ``run_failed``. The LLM never saw the
failure as a ``ToolMessage`` and could not decide to retry, switch
tools, or give up gracefully.

After this PRD: the tool wrapper catches the exception and returns a
sanitized error string as the tool's output. LangChain treats it as a
normal tool result so the agent's next model step sees a ``ToolMessage``
with the error class + sanitized message + structured hints. The run
continues. Only typed :class:`RunFatalToolError` subclasses still end
the run.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from langchain_core.tools import BaseTool
from pydantic import BaseModel, ConfigDict

from agent_runtime.capabilities.tool_error_policy_tool import ToolErrorPolicyTool
from agent_runtime.execution.tool_errors import (
    BudgetExceeded,
    RunFatalToolError,
)


class _ExplodingSearchArgs(BaseModel):
    query: str


class _ExplodingSearchTool(BaseTool):
    """Stand-in for ``DuckDuckGoSearchResults`` that always raises."""

    model_config = ConfigDict(arbitrary_types_allowed=True)
    name: str = "web_search"
    description: str = "Searches the public web."
    args_schema: type[BaseModel] = _ExplodingSearchArgs
    exc_factory: Any = None

    def _run(self, query: str) -> str:
        if self.exc_factory is not None:
            raise self.exc_factory("inner failure")
        return "no-op"

    async def _arun(self, query: str) -> str:
        if self.exc_factory is not None:
            raise self.exc_factory("inner failure")
        return "no-op"


def _wrap(inner: _ExplodingSearchTool) -> ToolErrorPolicyTool:
    return ToolErrorPolicyTool(
        name=inner.name,
        description=inner.description,
        args_schema=inner.args_schema,
        inner=inner,
    )


class TestPlainExceptionSurfacesToLlm:
    async def test_runtime_error_becomes_llm_visible_tool_message_content(
        self,
    ) -> None:
        wrapped = _wrap(_ExplodingSearchTool(exc_factory=RuntimeError))
        result = await wrapped._arun(query="langchain deep agents")
        assert isinstance(result, str)
        # Format used by ToolErrorClassification.to_llm_message_content():
        # "<error_class>: <sanitized_message>" optionally followed by
        # "Hints: {...}". The LLM sees this as the tool's output.
        assert "RuntimeError" in result
        assert "inner failure" in result

    async def test_validation_error_includes_actionable_hints(self) -> None:

        class _Model(BaseModel):
            limit: int

        def _raise_validation(_: str) -> Any:
            _Model.model_validate({"limit": "not-an-int"})

        class _StubTool(BaseTool):
            name: str = "stub"
            description: str = ""

            def _run(self, query: str) -> Any:
                _raise_validation(query)

            async def _arun(self, query: str) -> Any:
                _raise_validation(query)

        wrapped = ToolErrorPolicyTool(name="stub", description="", inner=_StubTool())
        result = await wrapped._arun(query="x")
        assert "ValidationError" in result
        assert "Hints:" in result
        assert "limit" in result

    async def test_sanitizer_strips_internals_from_surfaced_message(self) -> None:
        wrapped = _wrap(
            _ExplodingSearchTool(
                exc_factory=lambda _msg: RuntimeError(
                    "boom at /Users/dev/secret.py with token=hunter2"
                )
            )
        )
        result = await wrapped._arun(query="x")
        assert "/Users/dev/" not in result
        assert "hunter2" not in result
        # The class name + a sanitized fragment of the message survives.
        assert "RuntimeError" in result


class TestTypedFatalErrorEndsTheRun:
    async def test_budget_exceeded_propagates_as_run_fatal_tool_error(self) -> None:
        wrapped = _wrap(_ExplodingSearchTool(exc_factory=BudgetExceeded))
        with pytest.raises(RunFatalToolError):
            await wrapped._arun(query="x")


class TestCancellationPassesThrough:
    async def test_cancelled_error_is_not_classified(self) -> None:
        wrapped = _wrap(_ExplodingSearchTool(exc_factory=asyncio.CancelledError))
        with pytest.raises(asyncio.CancelledError):
            await wrapped._arun(query="x")
