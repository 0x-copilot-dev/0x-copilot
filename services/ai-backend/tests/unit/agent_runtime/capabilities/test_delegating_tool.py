"""Wrapper tools must hand LangChain's ``RunnableConfig`` to the tool they wrap.

Regression cover for a silent, total break of the built-in tool surface: every
model tool is wrapped by ``ToolBudgetGuardedTool`` (``guard_model_tools`` is
applied unconditionally in ``execution.factory``), and the wrappers used to
delegate with a bare ``*args, **kwargs`` signature.

LangChain injects the config into ``_run`` / ``_arun`` only when the method
declares a parameter annotated exactly ``RunnableConfig``
(``_get_runnable_config_param``). A bare signature declares none, so LangChain
skipped the injection and the wrapper then called an inner
``StructuredTool._arun`` whose ``config`` is keyword-only and REQUIRED:

    TypeError: StructuredTool._arun() missing 1 required keyword-only
    argument: 'config'

Every tool built by ``factory._structured_tool`` — ``call_mcp_tool``,
``ask_a_question``, ``auth_mcp``, ... — failed its whole run that way, while
tools taking ``**kwargs`` kept working, so nothing caught it.
"""

from __future__ import annotations

from typing import Any

import pytest
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool, StructuredTool
from pydantic import BaseModel

from agent_runtime.capabilities.citation_capturing_tool import CitationCapturingTool
from agent_runtime.capabilities.delegating_tool import DelegatingTool
from agent_runtime.capabilities.retrying_tool import RetryingTool
from agent_runtime.capabilities.tool_budget_guard import ToolBudgetGuardedTool
from agent_runtime.capabilities.tool_error_policy_tool import ToolErrorPolicyTool


class _Echo(BaseModel):
    value: str


class WrappedToolMixin:
    """Builds the same ``StructuredTool`` shape ``factory._structured_tool`` does."""

    @staticmethod
    def _inner() -> StructuredTool:
        async def invoke_adapter(**kwargs: Any) -> object:
            return {"echoed": kwargs.get("value")}

        return StructuredTool.from_function(
            coroutine=invoke_adapter,
            name="echo",
            description="Echo the input.",
            args_schema=_Echo,
        )

    @staticmethod
    def _config_ignoring_tool() -> BaseTool:
        """A tool whose ``_arun`` takes ``**kwargs`` — must NOT receive ``config``."""

        class _KwargsOnlyTool(BaseTool):
            name: str = "kwargs_only"
            description: str = "Takes **kwargs."
            seen: dict[str, Any] = {}

            def _run(self, *args: Any, **kwargs: Any) -> Any:
                self.seen.clear()
                self.seen.update(kwargs)
                return "ok"

            async def _arun(self, *args: Any, **kwargs: Any) -> Any:
                self.seen.clear()
                self.seen.update(kwargs)
                return "ok"

        return _KwargsOnlyTool()

    WRAPPERS = (
        ToolBudgetGuardedTool,
        RetryingTool,
        ToolErrorPolicyTool,
        CitationCapturingTool,
    )

    @classmethod
    def _wrap(cls, wrapper: type[DelegatingTool], inner: BaseTool) -> DelegatingTool:
        return wrapper(
            name=inner.name,
            description=inner.description,
            args_schema=inner.args_schema,
            inner=inner,
        )


class TestConfigReachesTheWrappedTool(WrappedToolMixin):
    @pytest.mark.parametrize("wrapper", WrappedToolMixin.WRAPPERS)
    async def test_wrapper_invokes_a_config_requiring_inner(self, wrapper) -> None:
        # ``ainvoke`` is the path LangGraph's ToolNode uses. Before the fix this
        # raised TypeError and failed the run.
        tool = self._wrap(wrapper, self._inner())
        result = await tool.ainvoke({"value": "hello"})
        assert result == {"echoed": "hello"}

    @pytest.mark.parametrize("wrapper", WrappedToolMixin.WRAPPERS)
    async def test_wrapper_declares_the_config_parameter(self, wrapper) -> None:
        # The annotation is what makes LangChain inject the config at all —
        # it must be exactly ``RunnableConfig``, not ``RunnableConfig | None``.
        from typing import get_type_hints

        for method in (wrapper._run, wrapper._arun):
            hints = get_type_hints(method)
            assert hints.get("config") is RunnableConfig, (wrapper.__name__, method)

    @pytest.mark.parametrize("wrapper", WrappedToolMixin.WRAPPERS)
    async def test_inner_that_takes_kwargs_is_not_handed_a_config(
        self, wrapper
    ) -> None:
        # deep-agents builtins accept ``**kwargs``; forwarding ``config`` to them
        # would land as a bogus tool argument.
        inner = self._config_ignoring_tool()
        tool = self._wrap(wrapper, inner)
        await tool.ainvoke({"value": "hello"})
        assert "config" not in inner.seen, inner.seen


class TestNestedWrappersStillForward(WrappedToolMixin):
    async def test_full_wrapper_stack_reaches_the_inner_tool(self) -> None:
        # Wrappers compose in production; config must survive every hop.
        tool: BaseTool = self._inner()
        for wrapper in self.WRAPPERS:
            tool = self._wrap(wrapper, tool)
        assert await tool.ainvoke({"value": "deep"}) == {"echoed": "deep"}
